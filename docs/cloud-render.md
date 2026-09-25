# Cloud render (Vast.ai) — human entry point

> Render không tốn tiền thuê máy: dùng Colab TPU v6e-1 tự động — [`colab-render.md`](colab-render.md).

This is the human-facing setup/operate/troubleshoot guide for renting a short-lived Vast.ai CPU
box to render a talking-head autoedit job instead of rendering it on this machine. If you are an
agent instructing the LLM, read `skills/core/cloud-render.md` (decision policy, announce template,
batch policy) and `.agents/skills/vastai/SKILL.md` (raw SDK gotchas) instead — this page is the
CLI/setup reference those two skills point back to.

## 1. What it is

`--render-location cloud` rents a cheap CPU box on Vast.ai, transfers the same render kit the local
path would use, runs the exact same Remotion command there, retrieves the result, and destroys the
instance — always, even on failure. **The transfer goes through Cloudflare R2, not `scp`**: the kit
is pushed to R2 before renting (unbilled), the rental pulls it via a short-lived presigned URL, and
pushes its output back to R2 the same way. The rented box never holds an R2 credential — it is
destroyed and re-leased to strangers — and a repeat rental of an unchanged composer kit skips the
re-upload entirely (content-addressed by `kit_hash`). See `docs/r2-storage.md` for the storage side
of this; this page covers only the cloud-render setup/operate flow. Measured economics (32-vCPU
offer, verified 2026-08-06):
overhead is **~6 minutes** of paid time before the first frame renders (boot ~60s + `npm ci` ~9.2s
+ offer search/ssh-ready wait + teardown); price is **~$0.069/hr** bid (the default) or **~$0.0814/hr**
on-demand; a 93s 1080p render takes **~3 minutes** on a 44-core box. Total cost for the manual
smoke run that measured these numbers was **under $0.01**. It is worth it once the local render
time clears roughly 2× the overhead (~12 minutes), or once ≥3 jobs are queued for a batch.

## 2. Setup

1. Create a Vast.ai account, generate an API key.
2. `pip install vastai` (already pinned in `requirements.txt`: `vastai>=1.0.4,<2`).
3. Put the key at `~/.config/vastai/vast_api_key` (the `vastai` CLI's own path — run
   `vastai set api-key <key>`) **or** `VAST_API_KEY=...` in `.env`. Never put the key on a command
   line — it lands in shell history.
4. Generate the **dedicated** cloud-render SSH keypair — not the shared `~/.ssh/vast_new` key, so a
   compromise or rotation only ever affects cloud-render rentals:
   ```bash
   python -m lib.cloud_render.setup_key
   ```
   Writes `~/.ssh/openmontage_cloud_render` (+ `.pub`). The public key is injected via the
   instance's `onstart` script, not registered with Vast.ai — see gotcha #1 in the troubleshooting
   table below.
5. Flip `"enabled": true` in `config/cloud-render.json`. It **ships disabled** — nothing can rent
   until a human does this explicitly; there is no auto-enable on first successful rental.
6. Configure Cloudflare R2 credentials (`CLOUDFLARE_R2_*` in `.env`) — required now, since the kit
   upload and result download both go through R2 instead of `scp`. See `docs/r2-storage.md` for the
   two manual Cloudflare-dashboard steps (bucket creation, credentials). Cloud render does **not**
   need `config/r2-storage.json`'s own `"enabled"` flag — that flag only gates the separate
   project-archive sync feature; R2 credentials alone are enough for the transfer path.

`config/cloud-render.json` ceilings, and their defaults:

| Key | Default | Meaning |
|---|---|---|
| `pricing_mode` | `"bid"` | Interruptible, ~15% cheaper than on-demand. The default in this system — preemption is designed for as the normal case, not an edge case (requeue + re-announce + retry). |
| `max_dph_usd` | `0.15` | Hard $/hr ceiling, re-checked immediately before every `create_instance` call. |
| `max_total_usd_per_rental` | `0.50` | Hard per-rental spend ceiling. `--cloud-max-usd` on the CLI can only lower this, never raise it. |
| `max_runtime_minutes` | `60` | Deadline baked into the instance's own `onstart` watchdog — compute stops even if every local process dies. |
| `max_batch_runtime_minutes` | `90` | Backstop ceiling for a batch flush; the real batch deadline is derived from the sum of the batch's own render-time estimates. |
| `batch.min_jobs` / `batch.min_total_render_minutes` / `batch.max_wait_minutes` | `3` / `20` / `240` | Thresholds `flush_check` reports as met/unmet — the flush *decision* is still the agent's/human's, never automatic. |

See `docs/PROVIDERS.md` → "Vast.ai — Cloud Render" for the provider-registration view of this same
setup (env var, tools unlocked), and `skills/core/cloud-render.md` for the crossover rule and the
mandatory announce template in full.

## 3. When to use it, when not to

Use it when the local render time clearly clears the ~12-minute crossover, or a batch of ≥3 jobs is
already queued. Do **not** use it for a single short job — the ~6-minute overhead dominates the
total time and there is no benefit. Do **not** use it for confidential footage: **your footage and
render kit transit Cloudflare R2 and are then pulled onto a third-party machine** for the duration
of the rental — deleted from the rented box with the instance afterward, and the transient R2
staging key (`render-kits/**`) is deleted right after the render (manual `--prune` cleans up the
rest; see `docs/r2-storage.md`) — if that is not acceptable for a project, render locally instead. Bid
pricing means the rental can be preempted mid-render; this is treated as the normal case (the job
is requeued and retried on a fresh, re-announced rental), not a failure.

## 4. Walkthroughs

### `--cloud-offers` — see the shortlist without renting anything

Real transcript, captured while writing this doc (`search_offers` is read-only; zero instances were
created):

```
$ python -m lib.talking_head_edit.cli --cloud-offers
RENDER LOCATION: cloud (Vast.ai) -- needs your approval before I rent anything
  Offer           : #36845174 -- 40 vCPU Q RTX 8000, US, reliability 0.9981177
                    (GPU is unused -- this render is CPU-bound)
  Pricing mode    : bid -- $0.0414/hr (on-demand alternative: $0.0747/hr)
  Scope           : sample of 1 job
  Time            : ~6.0 min overhead + ~0 min render(s) = ~6.0 min
  Cost            : ~$0.0041 total  |  per-rental ceiling: $0.5  |  $/hr ceiling: $0.15
  Separately      : ~$0.0041 if each job rented its own instance
  Locally         : ~0.0 min, $0
  Data            : your footage/render kit (551848 bytes) is uploaded to a third-party machine and deleted with the instance.
  Safety          : instance is destroyed on completion, on error, and by a watchdog at the 60-minute deadline even if this process dies.
  Note            : cloud render is disabled in config/cloud-render.json (enabled: false) -- preview only, nothing can be rented until a human enables it
Approve? (yes / use on-demand instead / render locally / pick a different offer)
```

(`~$0.0041`/`~0 min render(s)` above is a sample-with-no-`--job` estimate — pass `--job <id>` to
size the shortlist against a real job's actual duration. The exact offer/price shown will differ
run to run; this is what the account had available at doc-writing time, not a fixed number.)

### Render-now walkthrough (illustrative — expected shape, not a literal capture)

The transcript below is **not** a literal terminal capture: writing this doc must not actually rent
anything (see the phase's scope note; `enabled: false` in this repo's own `config/cloud-render.json`
blocks it anyway). It shows the exact shape a real run produces, assembled from the announce
transcript above plus the `execute()` contract's tested return shape
(`tests/test_cloud_render_tool_registration.py`).

```
$ python -m lib.talking_head_edit.cli --job <job_id> --render-location cloud --cloud-yes
RENDER LOCATION: cloud (Vast.ai) -- needs your approval before I rent anything
  Offer           : #40179084 -- 32 vCPU no GPU used, MX, reliability 0.98
  Pricing mode    : bid -- $0.0690/hr (on-demand alternative: $0.0814/hr)
  Scope           : sample of 1 job
  Time            : ~6.0 min overhead + ~1.5 min render(s) = ~7.5 min
  Cost            : ~$0.0086 total  |  per-rental ceiling: $0.5  |  $/hr ceiling: $0.15
  Separately      : ~$0.0086 if each job rented its own instance
  Locally         : ~3.0 min, $0
  Data            : your footage/render kit (631000 bytes) is uploaded to a third-party machine and deleted with the instance.
  Safety          : instance is destroyed on completion, on error, and by a watchdog at the 60-minute deadline even if this process dies.
Approve? (yes / use on-demand instead / render locally / pick a different offer)
Cloud render xong: $0.0083 (bid, offer #40179084)

Xong. Trạng thái: completed | phiên bản v1 | chi phí $0.0083
Thư mục job: projects/autoedit-jobs/<job_id>
Video: projects/autoedit-jobs/<job_id>/final.mp4
```

`--cloud-offer <id>` instead of `--cloud-yes` rents that exact offer (it must appear in the
shortlist from the `dry_run()` the CLI runs immediately before renting — an offer never shown to
you cannot be rented, mechanically enforced by `lib/cloud_render/dry_run_store.py`).

### Batch walkthrough (illustrative — expected shape)

```
$ python -m lib.talking_head_edit.cli --job <id1> --cloud-queue
Đã xếp job <id1> v1 vào batch queue cloud (~28s render).
Xem trạng thái: --cloud-queue-status  |  buộc render ngay cả batch: --cloud-flush

$ python -m lib.talking_head_edit.cli --cloud-queue-status
Batch queue cloud: 3 job
  <id1>                                        v1  pending    ~28s  xếp lúc 2026-08-06T10:02:11Z
  <id2>                                        v1  pending    ~41s  xếp lúc 2026-08-06T10:05:44Z
  <id3>                                        v2  pending    ~19s  xếp lúc 2026-08-06T10:11:02Z

flush_check: 3 job, ~1.47 phút render, chờ lâu nhất 12.3 phút -- ngưỡng đạt: min_jobs
  Ước tính chi phí: ~$0.0102 (so với ~$0.0225 nếu thuê riêng lẻ)

$ python -m lib.talking_head_edit.cli --cloud-flush --cloud-yes
RENDER LOCATION: cloud (Vast.ai) -- needs your approval before I rent anything
  ...
Xong: 3 render, 0 lỗi, 0 còn treo, 0 bị khoá
  Chi phí thực: $0.0098
```

Real, non-illustrative `--cloud-queue-status` transcript on an empty queue (captured while writing
this doc):

```
$ python -m lib.talking_head_edit.cli --cloud-queue-status
Batch queue cloud: rỗng

flush_check: 0 job, ~0.0 phút render, chờ lâu nhất 0.0 phút -- ngưỡng đạt: chưa đạt ngưỡng nào
  Ước tính chi phí: ~$0.0 (so với ~$0.0 nếu thuê riêng lẻ)
```

## 5. The four safety layers, and what to run if something goes wrong

1. **`try`/`finally` destroy** inside every rental call (`lib/cloud_render/remote.py::render_now`,
   `lib/cloud_render/queue.py::flush`) — the instance is destroyed even on an exception, timeout,
   or `KeyboardInterrupt`.
2. **Local ledger reaper** — every `dry_run()`/`execute()`/CLI entry point runs
   `lib/cloud_render/ledger.py::reap()` first, which reads `projects/cloud-render/active.json` and
   destroys anything past its recorded deadline.
3. **Stateless remote sweep** — every instance is labelled `openmontage-{intent_id}-until-{epoch}`
   at creation. The reaper also destroys any account-visible `openmontage-`-labelled instance past
   its deadline **even if all local state is lost** (a crashed process, a wiped disk). It never
   touches an instance without that label prefix — a human's own manually-rented box is never in
   scope.
4. **On-instance deadline shutdown** — the `onstart` script schedules `shutdown -h` at the deadline
   and wraps the render in `timeout`. Worst case, compute stops with zero local processes alive at
   all.

**If something goes wrong — a hung render, a suspicious bill, "did that instance actually die":**

```bash
make cloud-render-reap
```

(equivalently `python -m lib.cloud_render.reap`). This is safe to run any time, on any machine —
it only ever destroys `openmontage-`-labelled instances, reports what it did, and exits non-zero
if it had to destroy anything (so a script can alert on that). Then check the
[Vast.ai console](https://cloud.vast.ai/instances/) to confirm nothing `openmontage-`-labelled is
still running.

## 6. Cost accounting

Every rental is appended to `projects/cloud-render/rentals.jsonl` (append-only event log —
`pending` → `active` → `closed`/`reaped`), and the currently-live set is mirrored in
`projects/cloud-render/active.json` (what the reaper reads). Neither file is committed to git
(`.gitignore:29`). The tool-level cost governance (`tools/cost_tracker.py`) reads the *actual*
`dph × elapsed / 3600` from a completed rental's `ToolResult.cost_usd` — never the pre-rental
estimate — and appends it to `cost_log.json` like every other paid tool call.

To recalibrate `render_seconds_per_video_second` (the constant `announce.build()` and
`cost_estimate.py` use to turn a job's duration into an estimated render time): collect a handful
of real `rentals.jsonl` entries with known `duration_seconds` and the job's known video duration,
compute `duration_seconds / video_duration_seconds` for each, and update
`config/cloud-render.json`'s `render_seconds_per_video_second` to the new measured average. The
current default (`1.9`) and the underlying per-hour prices are calibrated from the 44-core EPYC
Colab benchmark referenced in `remotion-composer/colab-render-pipeline.ipynb`'s header
(93s 1080p in ~2m51s) and the 2026-08-06 Vast.ai manual smoke run.

## 7. Known limits

- **Autoedit props shape only.** The render kit this tool uploads supports the talking-head
  autoedit pipeline's self-contained `{props.json, staged public dir}` bundle. The `video_compose`
  atelier pipeline's props carry absolute local `file:///` URIs and are refused outright
  (`lib/cloud_render/remote.py::render_pipeline`) — no asset collection/path-rewrite exists yet.
- **Bid-mode caveats.** Bid (interruptible) pricing is the default; a preempted rental is requeued
  and retried on a fresh, re-announced rental rather than silently falling back to on-demand. A
  very long single render close to `max_runtime_minutes` is a better candidate for the explicit
  on-demand opt-in than for bid.
- **Team-key SSH constraint.** A Vast.ai team API key cannot register an SSH key with the account
  (`vastai create ssh-key` fails) — the public key is injected via the `onstart` script instead.
  See gotcha #1 below; it is not a workaround to "fix properly" later, it is the only path
  available to a team key.
- **No autopilot-driven unattended cloud batches.** `--autopilot --render-location cloud` still
  requires `--cloud-yes` on the command line — that flag is the human's one-time explicit consent
  for *that* invocation, not a standing background approval. There is no scheduled/overnight
  auto-flush anywhere in this system.

## 8. Troubleshooting

Every row below cites the verified gotcha in `.agents/skills/vastai/SKILL.md` ("Five verified
gotchas") — that skill has the full symptom/fix writeup; this table is the index into it.

| Symptom | Cause | Fix |
|---|---|---|
| `vastai create ssh-key` (or the equivalent SDK call) errors "Team SSH keys are not supported" | A team API key cannot register a key with the account | Don't register one — the public key is injected via the `onstart` script instead (`onstart.build()`). See gotcha #1. |
| `ssh root@host -p PORT 'npm ci'` (or any non-interactive SSH command) returns instantly with no output, nothing ran | Default Vast.ai images hijack every SSH session into `tmux`, which swallows non-interactive commands | The `onstart` script runs `touch /root/.no_auto_tmux` as one of its first lines, before anything that could hang. See gotcha #2. |
| `create_instance(... , runtype="ssh_direc ssh_proxy")` variant fails with `TypeError: create_instance() got an unexpected keyword argument 'ssh'` (a "VRL"/drive-letter-looking error can also show up here on Windows if a raw path leaked into an id-like argument) | `create_instance`'s real signature has no `**kwargs` — the CLI's `--ssh --direct` combination maps onto the literal string `runtype="ssh_direc ssh_proxy"`, not two booleans | Pass `runtype="ssh_direc ssh_proxy"`, never `ssh=True, direct=True`. See gotcha #3. |
| A local→remote file transfer silently drops zero bytes on the remote side, or a Windows path like `D:/CODE/kit.zip` produces a confusing "instance not found" naming a drive letter | `VastAI.copy()` / `vastai copy` parses on the first `:`, so a Windows path's drive letter becomes the instance-id argument; `storage.copy()` is a server-side rsync between two rented instances anyway, never local↔remote | Transfer goes through Cloudflare R2 presigned URLs, never `VastAI.copy()` (`lib/cloud_render/transfer.py::push_kit`/`fetch_to_remote`/`push_from_remote`). Likewise use raw `ssh` (`transfer.ssh`) for remote commands, never `VastAI.execute()` (it is an async job-queue endpoint, not a shell, and gives no real exit code). See gotcha #4. |
| A render fails with "presign hết hạn" or a bare `curl` exit code (22/28/56) inside a paid rental | A presigned URL expired before the rental used it, or the rental lost network mid-transfer | `_curl_error()` maps the common curl exit codes to a Vietnamese cause; presign TTLs are scoped per operation (kit GET ~30 min, result PUT = item timeout + margin), never one long-lived URL for the whole rental. The rental is destroyed rather than left idling either way. |
| A destroy call in a script hangs waiting for a `y/n` that never comes | The interactive confirm prompt is CLI-only (`vastai destroy instance <id>`); it is not part of the underlying API | Call `VastAI.destroy_instance(id=...)` directly (via `lib/cloud_render/vast_client.py::destroy`) — it never prompts, and it is idempotent (destroying an already-gone instance is treated as success). See gotcha #5. |
| A rental seems to still be running/billing after the process that started it died or was killed | Every safety layer up to the reaper failed to run in-process | `make cloud-render-reap`, then check the [Vast.ai console](https://cloud.vast.ai/instances/) — the reaper destroys any `openmontage-`-labelled instance past its deadline even with zero local state left. |
