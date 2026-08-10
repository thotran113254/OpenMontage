# Phase 02 — Render kit packager + single-job remote render ("render now")

## Context

- [plan.md](plan.md), [phase-01](phase-01-vast-client-ledger-reaper.md)
- Local autoedit render, the primary cloud target: `lib/talking_head_edit/stages/render.py`
  — `stage_assets` at `:70-94`, `_run_remotion` at `:97-121`, `build_command` at `:150-159`,
  `MAX_CONCURRENCY = 8` at `:31`, `_concurrency` at `:38-58`.
- Pipeline render, the harder target: `tools/video/video_compose.py::_remotion_render` at
  `:1661`, absolute-path→`file:///` rewrite at `:1697-1704`, props temp file at `:1714-1717`,
  composition routing at `:1727-1729`, `--props=` equals-form rationale at `:1737-1744`.
- Manual proof of the flow: kit 631 KB (`package.json` 4K + `package-lock.json` 228K +
  `tsconfig.json` 1K + `src/` 390K, measured), `npm ci` 9.2 s, 60-frame render 14.6 s.
- Prior art for the same idea done by hand: `remotion-composer/colab-render-pipeline.ipynb`
  (44-core EPYC rendered a 93 s 1080p timeline in ~2m51s ≈ 1.84 s render per 1 s of video).

## Overview

- **Priority:** P0 — blocks 03, 04.
- **Status:** completed (2026-08-06)
- One rental, one job, guaranteed destroy. This phase decides *what bytes travel* and *what
  command runs*, which is where output fidelity is won or lost.

## Key insights

1. **Two source shapes, very different difficulty.**
   - *Autoedit* (`lib/talking_head_edit`): already a relocatable bundle. `props["videoSrc"]` is a
     bare filename resolved out of `--public-dir` (`render.py:75`, `:150-159`). Upload
     `{props.json, staging dir, composer kit}` and the same command works verbatim on Linux.
     **Ship this path first.**
   - *Pipeline* (`video_compose`): props contain `file:///D:/...` absolute URIs generated at
     `video_compose.py:1697-1704`. Those must be rewritten to remote paths **and** every referenced
     asset collected and uploaded. Different, larger, and easy to get subtly wrong.
   → Scope this phase to the autoedit shape; the pipeline shape gets an explicit
   `unsupported_reason` and a clean blocker message (per AGENT_GUIDE "Escalate Blockers
   Explicitly") rather than a half-working path.
2. **Fidelity is a flag-set problem, not a machine problem.** The colab notebook used
   `--crf=18 --x264-preset=veryfast`; the local path uses `crf` from options (default 17),
   `--jpeg-quality=100` (because Remotion's default 80 measurably softened a face crop from 2.1 to
   1.44 — `render.py:142-146`) and no `x264-preset`. If cloud uses colab's flags the deliverable
   is *visibly different*. Cloud must build its command from **the same function** as local, with
   only paths and concurrency substituted.
3. **`MAX_CONCURRENCY = 8` (`render.py:31`) would waste 24 of 32 vCPUs.** The cap exists for local
   machines. Parameterize it (`max_concurrency` argument defaulting to 8) rather than raising it
   globally — raising it locally would regress the machine this was tuned on.
4. **`_run_remotion` parses Remotion's `Rendered n/m` stdout for progress (`render.py:110-119`).**
   Over ssh, stdout still streams, so the same parser works if the remote command is not wrapped in
   `nohup`. Keep it in the foreground and stream — the user watching a paid rental deserves live
   progress, and a silent 10-minute gap is indistinguishable from a hang.
5. **Download-then-verify-then-destroy, in that order.** Destroying before verifying the local file
   exists and is non-trivial in size loses the render entirely. `render.py:175-179` already treats
   "render exited 0 but no file" as an error; the cloud path needs the same check *plus* a
   byte-count check on the downloaded copy.
6. **`npm ci`, not `npm install`.** `package-lock.json` is in the kit; `npm ci` is reproducible and
   was measured at 9.2 s. `npm install` can silently resolve different versions than local, which
   is exactly the class of difference that shows up as a visual diff.

## Requirements

**Functional**
- `kit.build_autoedit_kit(job, version) -> KitManifest` producing a temp dir with:
  `package.json`, `package-lock.json`, `tsconfig.json`, `src/` (from `remotion-composer/`),
  `props.json` (from `job.props_path(version)`), `public/` (from `job.render_public_dir`).
  Explicit **allowlist** — never `remotion-composer/public/` (349 MB measured) and never `.env`.
- `KitManifest` reports `{size_bytes, file_count, composition_id, props_path, public_dir,
  expected_output_name, estimated_render_seconds}`. Size goes in the announce message.
- `remote.render_one(rental, kit, *, flags, max_concurrency, timeout_s) -> RemoteRenderResult`:
  wait for `/root/.openmontage-ready` → `scp_up` kit → `ssh npm ci` → `ssh <render cmd>` streaming
  stdout through the same progress parser → `scp_down` output → verify → return.
- `render_now(job, version, *, config, cost_hooks) -> CloudRenderResult` = rent + render_one +
  destroy in `finally`, ledger-closed in `finally`, regardless of exception/timeout/KeyboardInterrupt.
- Flag parity: extract the command builder out of `render.py::run` (currently the closure
  `build_command` at `:150-159`) into a module-level
  `build_remotion_command(*, entry, composition_id, out_path, props_path, public_dir, workers,
  crf, jpeg_quality, scale) -> list[str]` used by **both** local and cloud. No duplicated flag list.
- Pipeline (`video_compose`) shape: return a structured refusal
  `{"supported": false, "reason": "...", "options": [...]}` — never a partial render.

**Non-functional**
- Estimated render seconds = `duration_seconds * config.render_seconds_per_video_second *
  (reference_cores / offer_cores)`. Recorded actuals go to `rentals.jsonl` so the constant can be
  recalibrated from real data instead of guessed again.
- Total remote wall clock is bounded by `min(deadline, timeout_s)`; the render itself is wrapped in
  remote `timeout` so a hung headless Chrome cannot burn to the deadline.
- Progress events use the existing `job.emit("progress", "render", ...)` contract
  (`job_store.py:225`), so the web UI shows cloud progress with no UI change.

## Architecture

```
render_now(job, version, config)
  │ intent_id = uuid4().hex[:6]
  │ deadline  = now + config.max_runtime_minutes
  ├─ ledger.reap()                          ← every entry point reaps first
  ├─ kit = kit.build_autoedit_kit(job, version)
  ├─ offers = vast_client.search(...)  → caller already chose offer_id (phase 04 gate)
  ├─ rental = vast_client.rent(offer_id, intent_id=..., deadline_epoch=..., ceiling_dph=...)
  │  try:
  │    ├─ wait_running(timeout=300)
  │    ├─ ssh: wait for /root/.openmontage-ready  (else abort — apt failed)
  │    ├─ scp_up(kit.dir  -> /root/kit)
  │    ├─ ssh: cd /root/kit && npm ci --no-audit --no-fund
  │    ├─ ssh: cd /root/kit && timeout <s> npx remotion render <SAME FLAGS, remote paths>
  │    ├─ scp_down(/root/kit/out/final.mp4 -> job.final_path)
  │    └─ verify: local file exists, size > 0, duration within 1% of props duration
  │  finally:
  │    ├─ vast_client.destroy(rental.instance_id)
  │    └─ ledger.close(intent_id, actual_usd=dph*elapsed/3600, duration_s=elapsed)
```

Remote paths are fixed and Linux-only: `/root/kit`, `/root/kit/public`, `/root/kit/out/final.mp4`.
No Windows path ever crosses the wire — the only place a drive letter appears is `scp`'s local
argument, passed as a single argv element (never through a shell).

## Related code files

**Create**
- `lib/cloud_render/kit.py`
- `lib/cloud_render/remote.py`
- `tests/test_cloud_render_kit.py`, `tests/test_cloud_render_remote.py`

**Modify**
- `lib/talking_head_edit/stages/render.py` — extract `build_remotion_command()` to module level and
  have the local `run()` call it (behaviour-identical refactor: same argv, asserted by a test that
  snapshots the old argv); add `max_concurrency` parameter to `_concurrency` defaulting to
  `MAX_CONCURRENCY`; export `_run_remotion`'s progress parser as `parse_progress_line(line)` so the
  cloud path reuses it instead of copying the `"Rendered "` string match at `:110-112`.

**Read only (do not modify in this phase)**
- `tools/video/video_compose.py` — the pipeline shape is refused, not adapted, in this phase.

## Implementation steps

1. Refactor `render.py` first, in isolation: extract `build_remotion_command`, extract
   `parse_progress_line`, parameterize concurrency. Land it with a test asserting the *local* argv
   is byte-identical to today's for a fixed set of options. This is a pure refactor commit — no
   cloud code — so a fidelity regression is caught before any cloud complexity exists.
2. `kit.py`: allowlist copy into `tempfile.mkdtemp()`. Hash the kit (`sha256` over sorted
   relative-path + content) → `kit_hash`, used as the idempotency field in phase 04.
3. `remote.py::_wait_ready`: `ssh test -f /root/.openmontage-ready` polled every 5 s up to 240 s.
   Failure → structured abort so `finally` still destroys. Boot in the manual run reached
   `actual_status=running` in ~60 s; apt adds more, hence the generous ceiling.
4. `remote.py::render_one`: `subprocess.Popen` on the ssh command, iterate stdout, feed
   `parse_progress_line`, `job.emit` on change. Do **not** use `nohup`/background — foreground so
   the exit code is real and progress streams.
5. Concurrency for cloud: `min(offer_cores - 2, 32)`. Same shape as `_concurrency`'s default
   (`render.py:47`) but with the remote core count, and cap raised via the new parameter.
6. Verify step: local file exists, `size_bytes > 100_000`, and `ffprobe` duration within 1% of the
   props-derived duration. Reuse whatever the verify stage already does if it is extractable;
   otherwise a local `ffprobe` call is enough here.
7. `render_now`: the `try/finally` skeleton above. Wrap `destroy` in its own try/except that logs
   loudly and leaves the ledger record `active` (so the reaper retries) rather than swallowing.
8. Pipeline refusal: `remote.render_pipeline(...)` raises `CloudRenderUnsupported` with the reason
   text ("props carry absolute local file:// URIs generated at video_compose.py:1697-1704; asset
   collection + path rewrite not implemented") and the option list (render locally / use the
   autoedit pipeline / open a follow-up).

## Todo

- [x] Refactor `render.py`: `build_remotion_command`, `parse_progress_line`, `max_concurrency`
- [x] Test: local argv byte-identical after refactor
- [x] `kit.py` allowlist packer + `kit_hash` + `KitManifest`
- [x] `remote.py` `_wait_ready` / `render_one` / streaming progress
- [x] `render_now` with guaranteed `finally` destroy + ledger close
- [x] Structured refusal for the `video_compose` props shape
- [x] Verify step (exists / size / duration within 1%)
- [x] Tests with fake ssh/scp (subprocess monkeypatched) — no network

## Success criteria

- Refactor test: for a fixed options dict, the argv from `build_remotion_command` equals the argv
  today's `build_command` produces, element for element.
- `kit.build_autoedit_kit` on a real job produces a kit **< 5 MB** for a 90 s job (manual run:
  631 KB composer + per-job staging) and contains no `remotion-composer/public/` entry, no `.env`.
- Simulated failure at every step (wait_running timeout, ready-file timeout, `npm ci` non-zero,
  render non-zero, scp_down failure, `KeyboardInterrupt` mid-render) → `destroy` called exactly
  once in every case; asserted by test.
- A cloud-rendered file and a locally rendered file from the **same props** have the same duration,
  resolution, fps and codec, and PSNR ≥ 40 dB on a sampled frame set (phase 06 runs this against a
  real rental; here it is asserted against a locally simulated "remote").
- `job.emit` progress events appear with the same shape as local renders (UI needs no change).

## Risks

| Risk | Mitigation |
|---|---|
| Cloud flags drift from local → different-looking deliverable | Single shared `build_remotion_command`; a lint test greps for a second `"remotion", "render"` argv literal in the repo and fails |
| Render hangs, burns the whole deadline | Remote `timeout <s>` wrapper + local `Popen` timeout, both < deadline |
| `scp -r` of a directory with spaces in the local path fails on Windows | Pass local path as one argv element, never via shell; test with a temp dir containing a space |
| Font mismatch → different text metrics than local | `fonts-liberation` is in the apt list; `@remotion/google-fonts` fetches at bundle time. Phase 06's PSNR gate is what actually catches this |
| `npm ci` fails on a fresh box (registry hiccup) after paying for boot | One retry; on second failure abort + destroy + report cost spent. Do not fall back to `npm install` (version drift) |
| Destroy itself fails (API 5xx) | Log loud, leave ledger `active`, let the reaper retry; live test asserts eventual zero instances |

## Security

- Kit is an **allowlist**: nothing from `.env`, `~/.config`, `config.yaml`, or `projects/` other
  than the job's own props + staged media. The rented box is untrusted third-party hardware.
- Footage does leave the machine. This must be stated in the announce message (phase 05) and in
  `docs/cloud-render.md` (phase 07): anyone with confidentiality constraints must render locally.
- Output is pulled back over the same ssh key; nothing is uploaded to a third-party file host
  (unlike the colab notebook, which used litterbox.catbox.moe with 1 h self-destruct links).

## Next

Phase 03 generalizes `render_one` to N kits on one rental and adds the durable queue.

## Unresolved questions

1. Is a PSNR ≥ 40 dB gate the right fidelity bar, or should it be exact byte equality? Remotion
   renders should be deterministic given identical inputs and version, but x264 can differ across
   builds — needs one real measurement before the number is fixed.
2. Should the pipeline (`video_compose`) shape be a follow-up plan, or in scope later in this one?
   It needs asset collection + props path rewriting, which is roughly another phase of work.
