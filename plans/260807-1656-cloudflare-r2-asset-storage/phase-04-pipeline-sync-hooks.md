# Phase 04 — Pipeline sync hooks

## Context links

- Stage completion point: `lib/talking_head_edit/runner.py:171-178` (`set_stage(completed)`
  → `job.emit("stage_end", …)` → `return result`)
- Job completion point: `lib/talking_head_edit/runner.py:200-204`
  (`job.update(status=…)` → `job.emit("job_end")` → `return results`)
- Stage failure path: `runner.py:164-169` — re-raises, so a hook there must not swallow
- CLI flag conventions: `lib/talking_head_edit/cli.py:130-147` (`--cloud-*` family)
- Job/project dirs: `job_store.py:26-31`, `project_store.py:46`
- Blocked by: [phase 03](phase-03-project-sync-engine.md)

## Overview

- **Priority:** P2
- **Status:** completed
- **Effort:** 1.5h
- Wire the sync engine into the talking-head auto-edit runner so a completed job's
  artifacts land in R2 without a human remembering to run the CLI.

## Key insights

1. **A sync hook must never fail a job.** `run_stage` re-raises on stage failure
   (`runner.py:164-169`); a hook that raises would convert an R2 outage into a lost
   render. Every hook call is wrapped: exception → `job.emit("warning", …)` → continue.
2. **Sync only at job end — locked by user (Q2, 2026-08-07).** `sync_after_stage` ships
   `false` and is the only supported behaviour to build against; the per-stage code path is
   **cut** (YAGNI). Reason: 7 network round-trips on a residential uplink would land inside
   the interactive prompt-iterate loop the pipeline is tuned for, and every intermediate
   artifact is regenerable anyway. `auto_sync` (job end) stays `false` on a clean checkout
   per the repo's opt-in-cloud philosophy — flipping that one key is what enables syncing.
   The `trigger` parameter is retained in the hook signature so the stage hook can be added
   later without reshaping callers, but only `"job_end"` is wired.
3. **Only the `render` and `verify` stages produce large new bytes.** If per-stage sync is
   enabled, the earlier stages' syncs are cheap manifest no-ops anyway.
4. **`events.jsonl` is user-visible and persisted.** Hook messages may contain object
   counts and MB — never a presigned URL, never the endpoint unmasked.
5. **Two directory shapes to map.** A job is `projects/autoedit-jobs/<job_id>/`; a project
   is `projects/autoedit/<project_id>/` with jobs nested under it. Map both explicitly
   rather than syncing `projects/` wholesale — a blind `projects/` mirror would push every
   old job and every corpus on the machine.

## Requirements

**Functional**
- `hooks.maybe_sync_job(job, trigger="job_end") -> None`. No-ops (zero cost, zero network)
  when `enabled` or `auto_sync` is false. Only `"job_end"` is wired (Q2).
- Key prefix: `<config.prefix>/autoedit-jobs/<job_id>/`.
- `hooks.maybe_sync_project(project) -> None` → `<config.prefix>/autoedit/<project_id>/`,
  called after a source upload completes.
- CLI: `--r2-sync` (force a sync of this job now, ignores `auto_sync` but still honours
  `enabled`), `--r2-status`.
- On success emit one event: `r2_sync` with `{uploaded, skipped, bytes, prefix}`.
  On failure emit `warning` with the error class and message — job status unaffected.

**Non-functional**
- Added latency when disabled: effectively zero (one dict lookup, no file I/O — check
  `enabled` before loading the manifest).
- Hook code must not import boto3 at module import time (`runner.py` is imported by the
  web server and the CLI on every invocation).

## Architecture

```
run_job()  ──(after emit "job_end", runner.py:203)──► hooks.maybe_sync_job(job, "job_end")
                                                                 │
                                            config gate: enabled && auto_sync
                                                                 ▼
                                          sync.plan_sync(job.dir, prefix) → apply_sync
                                                                 ▼
                                                     job.emit("r2_sync", …)
```

The insert point is **after** the existing `emit` call so the job is already durably marked
complete before any network work starts. If the process dies mid-sync, job state is still
correct and the next sync picks up where the manifest left off.

`hooks.py` is the only place phase 04 adds to `lib/r2_storage/`; `runner.py` gains **1 line**
plus one import, `cli.py` gains 2 flags plus a handler branch.

After a **cloud** render, `final.mp4` is already in R2 (uploaded by the Vast.ai box, phase
05) and its manifest entry was written by `record_external_upload` — so this hook reports
it as skipped and uploads nothing. That interaction is the point of the whole design and is
an explicit success criterion in phase 05.

## Related code files

**Create**
- `lib/r2_storage/hooks.py` (~90 LOC)

**Modify**
- `lib/talking_head_edit/runner.py` — import + 2 call sites (after `:177` and after `:203`)
- `lib/talking_head_edit/cli.py` — `--r2-sync`, `--r2-status` flags + dispatch

**Delete** — none.

## Implementation steps

1. `hooks.py`: `_gate() -> R2Settings | None` — loads config once per process
   (module-level cache with an explicit `reset_cache()` for tests), returns `None` fast
   when `enabled` or `auto_sync` is false.
2. `maybe_sync_job(job, trigger="job_end")`: gate →
   `plan_sync(job.dir, f"{prefix}/autoedit-jobs/{job.job_id}")` → `apply_sync` →
   `job.emit("r2_sync", …)`. Whole body inside `try/except Exception` →
   `job.emit("warning", stage=None, message=…)`.
3. `maybe_sync_project(project)`: same shape, prefix `.../autoedit/<project_id>`.
4. Patch `runner.py:203` only — after `job.emit("job_end", …)`, before `return results`:
   ```python
   hooks.maybe_sync_job(job, "job_end")
   ```
   Import at the top of `runner.py` as `from lib.r2_storage import hooks` — `hooks` itself
   must not import boto3 at module level. **Do not** add a call in `run_stage` (Q2).
5. Add CLI flags in the `--cloud-*` neighbourhood (`cli.py:130-147`) with Vietnamese help
   text matching the surrounding style.
6. Verify the disabled path is truly free:
   ```bash
   python -m lib.talking_head_edit.cli --job <existing_id> --stages verify
   ```
   with `enabled:false` — timing unchanged, no `r2_sync` event in `events.jsonl`.
7. Verify the enabled path end-to-end with `auto_sync: true` on a small finished job.

## Todo list

- [x] 1. `hooks.py` gate with process-level config cache + `reset_cache()`
- [x] 2. `maybe_sync_job` — never raises, always emits
- [x] 3. `maybe_sync_project`
- [x] 4. One call site in `runner.py` (job end only)
- [x] 5. `--r2-sync` / `--r2-status` in `cli.py`
- [x] 6. Disabled-path no-op verification
- [x] 7. Enabled-path end-to-end on one job

## Success criteria

- With `enabled:false`: `grep r2_sync projects/autoedit-jobs/<id>/events.jsonl` → empty;
  job wall-clock unchanged within noise.
- With `enabled:true, auto_sync:true`: exactly **one** `r2_sync` event per job run, and
  `python -m lib.r2_storage.cli --list projects/autoedit-jobs/<id>` shows `final.mp4`.
- `grep -n "maybe_sync_job" lib/talking_head_edit/runner.py` returns **exactly one** line,
  inside `run_job` — not `run_stage` (Q2 guard).
- After a cloud render, the job-end sync reports `final.mp4` skipped, uploaded bytes ≈ 0
  for that file.
- Simulated R2 outage (bad endpoint in env): job still reaches `completed`, a `warning`
  event is present, exit code 0.
- `grep -rn "presigned\|X-Amz-Signature" lib/r2_storage/hooks.py` → no match.
- `grep -c "r2" lib/talking_head_edit/runner.py` → small (≤ 4); the runner stays a runner.

## Risk assessment

| Risk | L×I | Mitigation |
|---|---|---|
| Hook exception kills a finished render | Med × **Critical** | Blanket `try/except Exception` → warning event; explicit outage test in success criteria |
| Sync latency degrades the interactive prompt-iterate loop | Low × Med | Resolved by Q2 — job-end only, no per-stage call site; guarded by a grep success criterion |
| `boto3` imported at `runner.py` import time slows every CLI invocation | Med × Low | `hooks` imports `lib.r2_storage.client` lazily inside the gated branch |
| Blind `projects/` mirror pushes unrelated corpora | Low × High | Prefix built per job/project id; never syncs `projects/` root |
| Presigned URL leaks into `events.jsonl` | Low × High | Hooks never call `presigned_url`; grep check in success criteria |
| Config cache goes stale within a long-lived web-server process after a user edits the JSON | Med × Low | `reset_cache()` exposed; the autoedit server calls it on config reload, or accept staleness until restart (documented) |

**Rollback:** revert the 1 call site in `runner.py` + the 2 CLI flags, delete `hooks.py`.
Phases 01-03 keep working standalone via the CLI.

## Backwards compatibility

- Existing jobs/projects have no `.r2sync.json`. First sync treats every file as new —
  correct, and the announce block warns about total MB before uploading.
- No artifact schema changes. `events.jsonl` gains a new event type `r2_sync`; the web UI
  renders unknown event types generically — **verify** this before shipping (check the
  autoedit UI's event renderer for an exhaustive `switch`/`match` that would throw).
- No changes to `job.json` shape.

## Security considerations

- Hook output goes into `events.jsonl`, which the local web UI serves. Only counts, bytes
  and the key prefix are logged — never the endpoint with the account id, never a URL.
- A job dir can contain the user's raw footage. Enabling `auto_sync` moves personal video
  off-machine; the first-sync announce block (phase 03) states this explicitly.

## Next steps

Feeds phase 06 tests. Pairs with phase 05 via `record_external_upload`.

## Unresolved questions

None — Q2 resolved (job-end only).
