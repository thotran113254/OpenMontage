---
title: "Cloudflare R2 as project asset storage"
description: "S3-compatible object storage for OpenMontage project assets — boto3 client, BaseTool, diff-sync engine, pipeline hooks, and R2-mediated Vast.ai render transfer."
status: completed
priority: P2
effort: 15h
branch: main
tags: [storage, cloudflare-r2, boto3, infrastructure, cloud-render, talking-head-autoedit]
created: 2026-08-07
---

# Cloudflare R2 Asset Storage

R2 becomes the durable store for `projects/**` (input footage, intermediate artifacts,
final renders) **and** the transfer medium for cloud rendering: the rented Vast.ai box
pulls its kit from R2 and pushes its output back to R2 via short-lived presigned URLs.
Local stays the working store; R2 is the archive, the delivery surface, and the
compute-transfer hub. Opt-in — ships `enabled: false`.

**Non-goals:** replacing the local filesystem as the pipeline's working store; a CDN
tier; multi-tenant auth; automating Cloudflare dashboard setup.

## Phases

| # | Phase | Status | Effort | Blocked by |
|---|-------|--------|--------|-----------|
| 01 | [R2 client + config](phase-01-r2-client-and-config.md) | completed | 2h | — |
| 02 | [`cloudflare_r2` BaseTool](phase-02-r2-storage-tool.md) | completed | 2h | 01 |
| 03 | [Diff-sync engine + CLI](phase-03-project-sync-engine.md) | completed | 3h | 01 |
| 04 | [Pipeline sync hook (job end)](phase-04-pipeline-sync-hooks.md) | completed | 1.5h | 03 |
| 05 | [Vast.ai transfer via R2 presigned URLs](phase-05-vastai-transfer-via-r2.md) | completed | 4h | 01, 02, 03 |
| 06 | [Tests + docs](phase-06-tests-and-docs.md) | completed | 3h | 02, 03, 04, 05 |

All 6 phases implemented, tested (1671 tests passing in the default suite, 0 regressions,
plus a live smoke test against the real bucket and a real-ffmpeg fidelity test), and
code-reviewed. The code review found 2 CRITICAL bugs (both fixed, with regression tests
added) and 1 MEDIUM + 1 LOW (both fixed) — see "Post-review fixes" below.

02 and 03 are parallelizable after 01 (disjoint file ownership). 04 and 05 are
parallelizable after 03.

## Dependency graph

```
01 (lib/r2_storage/{config,client}.py, config/r2-storage.json, requirements*, .env.example)
 ├── 02 (tools/storage/cloudflare_r2.py) ───────────────┐
 └── 03 (lib/r2_storage/{manifest,sync,cli}.py) ────────┤
       ├── 04 (lib/r2_storage/hooks.py, runner.py, cli.py)
       └── 05 (lib/r2_storage/presign.py, lib/cloud_render/*, config/cloud-render.json)
                                                         └── 06 (tests/, docs/)
```

## File ownership (no overlap between parallel phases)

| Phase | Owns |
|---|---|
| 01 | `lib/r2_storage/{__init__,config,client}.py`, `config/r2-storage.json`, `requirements.txt`, `requirements-dev.txt`, `.env.example` |
| 02 | `tools/storage/{__init__,cloudflare_r2}.py` |
| 03 | `lib/r2_storage/{manifest,sync,cli}.py` |
| 04 | `lib/r2_storage/hooks.py`, `lib/talking_head_edit/runner.py`, `lib/talking_head_edit/cli.py` |
| 05 | `lib/r2_storage/presign.py`, `lib/cloud_render/{transfer,remote}.py`, `config/cloud-render.json`, `.agents/skills/vastai/SKILL.md`, `tests/test_cloud_render_remote.py` |
| 06 | `tests/test_r2_*.py`, `tests/test_cloud_render_r2_transfer.py`, `tests/live/*`, `docs/r2-storage.md`, `docs/cloud-render.md`, `docs/PROVIDERS.md`, `AGENT_GUIDE.md` |

## Key layout (single bucket, two top-level prefixes)

```
projects/autoedit-jobs/<job_id>/…        durable archive   (phases 03/04)
projects/autoedit/<project_id>/…         durable archive   (phases 03/04)
render-kits/composer/<kit_hash>/…        transient, content-addressed, reused (phase 05)
render-kits/jobs/<job_id>/…              transient staging (phase 05)
```

## Locked decisions

- **Q1 — Vast.ai transfer goes through R2; `scp` is removed.** Local pushes the kit to R2
  before renting; the box pulls it and pushes its output back, both via short-lived
  presigned URLs. SSH stays, but only to run commands. The rented box **never** receives
  long-lived R2 credentials — it is destroyed and re-leased to strangers.
- **Q2 — sync only at job end.** No per-stage sync call site (avoids 7 round-trips inside
  the interactive prompt-iterate loop). `auto_sync` still ships `false`.
- **Q3 — public delivery via custom domain / Public Development URL.** `delivery_url`
  returns `https://<CLOUDFLARE_R2_PUBLIC_BASE_URL>/<key>` when configured, else falls back
  to a presigned URL with a warning. Enabling public access is a **manual dashboard step**
  the plan never automates. `presigned_url` / `presigned_put_url` stay strictly signed for
  the Vast.ai path.
- **Q4 — no lifecycle policy.** Manual `--prune`; `render-kits/jobs/**` staging is deleted
  after each render.
- **Q5 — one bucket** (`openmontage-assets`), separated by prefix.
- `region_name="auto"` — R2 rejects AWS region codes with a 301.
- boto3 ≥1.36 checksum defaults must be set to `when_required` or R2 rejects PUT/UploadPart.
- Credentials only in `.env`; `config/r2-storage.json` mechanically rejects credential keys.
- Diff via a local `.r2sync.json` manifest, not `head_object` — 0 API calls when nothing changed.
- Render output is staged, verified locally, then **server-side copied** to the archive key —
  a failed render never overwrites the archive.

## Manual prerequisites (user, in the Cloudflare dashboard)

1. ~~Create bucket `openmontage-assets`~~ — **done** (created programmatically via the API
   token, which had bucket-create permission; user request 2026-08-08, no dashboard click
   needed after all).
2. Enable public access or attach a custom domain, then set
   `CLOUDFLARE_R2_PUBLIC_BASE_URL` in `.env`. Still **not done** — needed only for public
   delivery (Q3); everything else works without it.

## Post-review fixes (code-reviewer, 2026-08-08)

- **CRITICAL** — `record_external_upload` stated `job.dir/"final.mp4"` before that file
  existed (the copy used to happen in the *caller*, after `render_batch` returned) →
  `FileNotFoundError` on every first-ever cloud render, reported as a failed item despite a
  successful render+verify+archive-copy. Fixed by making `_render_one_item` the sole writer
  of `job.final_path` (removed the now-redundant copy from `render_now` and
  `queue.py::_on_output`). Regression test:
  `tests/test_cloud_render_remote.py::test_final_mp4_exists_before_record_external_upload_reads_it`.
- **CRITICAL** — `hooks._gate()` called `resolve()` outside its `try` block; `resolve()`
  validates `CLOUDFLARE_R2_PUBLIC_BASE_URL` unconditionally (even when R2 is disabled) and
  raises on a malformed value, which would propagate straight out of the unguarded
  `runner.py` call site — a config typo could crash a finished job. Fixed by wrapping the
  `resolve()` call itself in try/except. Regression test:
  `tests/test_r2_hooks_never_raise.py::test_resolve_raising_inside_the_gate_never_propagates`.
- **MEDIUM** — `actions.upload`'s md5 used `Path.read_bytes()` (loads the whole file into
  RAM) instead of the sync engine's own chunked `file_md5`. Fixed to reuse it.
- **LOW** — `remote.py`'s `push_kit` error wrapping didn't mask the R2 endpoint (account id)
  the way `tools/storage/cloudflare_r2.py` already does. Fixed with a `_masked()` helper.

## Unresolved questions

- **Composer-kit push timing (informational, not blocking).** Phase 05's own "why this is a
  net win" section claims the composer kit uploads to R2 *before* renting (unbilled). In the
  actual code, both `render_now` and `queue.flush` call `vast_client.rent(...)` first, and
  `render_batch` (called after) is what calls `transfer.push_kit(composer.kit_dir, ...)` —
  so for a brand-new (not-yet-cached) composer kit, that upload happens *inside* the billed
  rental window, not before it. The content-addressed skip still saves bandwidth on repeat
  rentals of an unchanged kit, so nothing is broken, but the specific "unbilled upload" cost
  claim doesn't match the implementation. Not fixed in this pass — flagged by the
  code-reviewer as a real discrepancy needing a decision (push the composer kit right after
  `kit.build_composer_kit()`, before renting, in both call sites) rather than a silent doc
  edit or a silent code change. Ask the user before picking either.

See [`unresolved-questions.md`](unresolved-questions.md) for the original (now fully
resolved) Q1-Q5 decision log.
