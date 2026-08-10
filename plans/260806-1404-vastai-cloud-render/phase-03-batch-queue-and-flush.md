# Phase 03 — Durable batch queue + one-rental flush

## Context

- [plan.md](plan.md), [phase-02](phase-02-render-kit-and-remote-render.md)
- Existing queue, deliberately **not** reused: `server/queue_worker.py` — in-memory
  `queue.Queue` at `:109`, worker thread at `:118-119`, "one job at a time on purpose" rationale
  at `:3-5`, subprocess execution at `:225-245`, orphan reconciliation at `:125-139`.
- Job state persistence conventions to mirror: `lib/talking_head_edit/job_store.py` — two-file
  state (`job.json` snapshot + `events.jsonl` append-only) documented at `:11-15`,
  `emit` at `:225`.

## Overview

- **Priority:** P1 — the whole economic argument for cloud render.
- **Status:** implemented (2026-08-06) — see implementation deviations below
- Batch exists for one reason: the fixed overhead (boot ~60 s + apt + `npm ci` 9.2 s + transfers)
  measured at roughly 5-7 min of the manual run's 7-8 min lifetime. On a single 15-second job the
  overhead **is** the cost. Amortized over 5 jobs it is ~1 min each.

## Key insights

1. **`server/queue_worker.py` cannot be reused, and the reason is structural, not stylistic.**
   Its queue is an in-process `queue.Queue` (`:109`) — restarting the server loses every pending
   item. A batch that waits hours for a threshold must survive restarts, so it needs an on-disk
   queue. It also executes runs as *local* CLI subprocesses (`:226`), which is the opposite of what
   batch flush does. Keep them separate; a batch flush *is* allowed to submit its post-render
   stages back to the existing queue if needed.
2. **The flush *decision* is not Python's.** Python reports facts (`job_count`,
   `estimated_render_minutes`, `oldest_age_minutes`, which thresholds are met); the agent decides
   whether to flush, per AGENT_GUIDE's "Python = tools + persistence" rule at `AGENT_GUIDE.md:119`.
   `flush_check()` therefore returns data and never triggers a rental as a side effect.
3. **One `npm ci` for N jobs is the amortization; sequential renders are the correctness.**
   Two concurrent Remotion renders on one box finish later than the same two in sequence and make
   progress meaningless — the exact reasoning already written down at `queue_worker.py:3-5`. The
   saving comes from sharing the *rental*, not from parallelism.
4. **Pull each output as it finishes.** If all N are pulled at the end, one late failure (or a bid
   preemption) loses every completed render. Incremental download turns a total loss into a partial
   one and makes resume trivial: on retry, jobs whose output already exists locally are skipped.
5. **A queued job's kit must be built at flush time, not enqueue time.** Props change when a user
   revises a job (`--revise`, `chat_revise`), and a kit frozen at enqueue would render a stale
   version. The queue stores `{job_id, version_at_enqueue, ...}` and flush re-reads the job,
   warning if the version moved.
6. **The deadline must scale with the batch.** `max_runtime_minutes` sized for one job would kill a
   5-job flush mid-way. Deadline = `overhead + sum(estimated_render_minutes) * safety` clamped to
   `config.max_batch_runtime_minutes`, and the *label* carries that deadline (phase 01 layer 3).

## Requirements

**Functional**
- `queue.enqueue(job_id, *, version, estimated_render_seconds, note) -> QueueEntry` — idempotent on
  `job_id` (re-enqueue updates in place, does not duplicate).
- `queue.list() -> list[QueueEntry]`, `queue.remove(job_id)`, `queue.clear()`.
- `queue.flush_check(config) -> ThresholdReport`:
  ```jsonc
  { "job_count": 4, "estimated_render_minutes": 23.5, "oldest_age_minutes": 191,
    "thresholds": {"min_jobs": 3, "min_total_render_minutes": 20, "max_wait_minutes": 240},
    "thresholds_met": ["min_jobs", "min_total_render_minutes"],
    "estimated_cost_usd": 0.048, "estimated_cost_if_rendered_separately_usd": 0.19,
    "amortization_note": "overhead ~6 min paid once instead of 4x" }
  ```
  Pure computation. No side effects. No rental.
- `remote.render_batch(rental, kits, *, on_output) -> list[RemoteRenderResult]` — one `npm ci`, then
  per kit: upload → render → download → `on_output(result)` → mark done. A failing kit records its
  error and the batch **continues** with the rest (a bad props file must not cost the other jobs
  their rental).
- `flush(job_ids, *, offer_id, config, cost_hooks) -> BatchResult` — rent once, `render_batch`,
  destroy in `finally`, remove only *successfully rendered* jobs from the queue.
- Force flush = the same `flush()` with an explicit job list; no separate code path.

**Non-functional**
- Queue file writes are atomic (temp + `os.replace`), same as the ledger.
- Concurrent access: the queue file is written by the CLI, the server, and the agent. Use a
  lock file (`batch-queue.lock`) with a stale-lock timeout, or accept last-write-wins and document
  it. **Decision needed** — see unresolved questions.
- A queue entry whose job directory has disappeared is dropped at the next `list()` with a warning,
  not left to explode at flush time.

## Architecture

`projects/cloud-render/batch-queue.json`:

```jsonc
{
  "version": 1,
  "entries": [{
    "job_id": "job_2608_a1", "project_id": "prj_x",
    "version_at_enqueue": 3,
    "enqueued_at": "2026-08-06T09:12:00Z",
    "estimated_render_seconds": 172,
    "duration_seconds": 93.2,
    "note": "shoot 1 take 2",
    "status": "pending",              // pending | rendering | done | failed
    "attempts": 0, "last_error": null
  }]
}
```

Flush sequence:

```
flush(job_ids, offer_id)
  ├─ ledger.reap()
  ├─ kits = [kit.build_autoedit_kit(job, job.current_version) for job in jobs]
  │     (warn per job if current_version != version_at_enqueue)
  ├─ deadline = overhead_min + sum(est_render_min) * 1.5, clamped to max_batch_runtime_minutes
  ├─ rental = rent(offer_id, deadline_epoch=deadline, ...)
  │  try:
  │    ├─ wait_running + wait_ready + scp_up(shared composer kit) + npm ci      ← ONCE
  │    └─ for kit in kits:
  │         ├─ scp_up(kit.props + kit.public -> /root/kit/jobs/<job_id>/)
  │         ├─ ssh render  (progress -> that job's own emit stream)
  │         ├─ scp_down -> job.final_path            ← incremental, immediately
  │         ├─ verify; queue.mark(job_id, done|failed)
  │         └─ if now > deadline - safety_margin: stop, leave the rest pending
  │  finally: destroy + ledger.close(actual_usd)
```

Note the split: the **composer kit** (`package.json`/lock/tsconfig/src, ~623 KB) is uploaded and
`npm ci`-ed once into `/root/kit`; each job contributes only its `props.json` + its `public/`
staging under `/root/kit/jobs/<job_id>/`, and the render command points `--public-dir` there. This
is the mechanical reason one rental can serve N jobs cheaply.

## Related code files

**Create**
- `lib/cloud_render/queue.py`
- `tests/test_cloud_render_batch_queue.py`, `tests/test_cloud_render_batch_flush.py`

**Modify**
- `lib/cloud_render/remote.py` — add `render_batch` sharing `_wait_ready` / `npm ci` with
  `render_one`; `render_one` becomes `render_batch` with a single kit (DRY — one code path)
- `lib/cloud_render/kit.py` — split `build_autoedit_kit` into `build_composer_kit()` (shared,
  cacheable by `package-lock.json` hash) + `build_job_kit(job, version)` (per-job)

## Implementation steps

1. Split the kit first (`build_composer_kit` / `build_job_kit`), then rewrite `render_one` as
   `render_batch([kit])`. Doing it in this order means there is never two render code paths.
2. `queue.py`: entries keyed by `job_id`; `enqueue` upserts. `list()` prunes entries whose job dir
   is gone. Atomic writes.
3. `flush_check`: compute `estimated_render_minutes` from each entry's
   `estimated_render_seconds`; compute `estimated_cost_usd` from `offer.dph` (or the config ceiling
   if no offer chosen yet) × `(overhead + renders)`; compute the separate-rentals comparison so the
   announce message can show the actual saving instead of asserting one.
4. `render_batch`: per-kit try/except recording `last_error`; deadline guard before starting each
   kit (`now + est_render < deadline - margin`), otherwise stop cleanly and leave the remainder
   `pending` — a partial flush is a correct outcome, not a failure.
5. `flush`: only remove `done` entries. `failed` entries stay with `attempts += 1` and their error,
   so a repeat flush retries them and a permanently broken job is visible rather than silently
   re-rented forever. Cap `attempts` at 3, then mark `blocked`.
6. Version-drift warning: if `job.current_version != entry.version_at_enqueue`, emit a warning and
   render the **current** version (rendering a stale version would be the worse surprise).

## Todo

- [x] Split `kit.py` into composer kit + job kit
- [x] Rewrite `render_one` as `render_batch([kit])` — one code path
- [x] `queue.py` durable upsert queue + prune + atomic writes
- [x] `flush_check()` returning pure threshold + cost-comparison data
- [x] `render_batch` incremental download + per-kit isolation + deadline guard
- [x] `flush()` with `finally` destroy and done-only dequeue
- [x] Retry accounting (`attempts`, `blocked` at 3)
- [x] Decide + implement queue file concurrency strategy (lock vs last-write-wins) — lock file
- [x] Tests: partial failure, deadline stop, version drift, restart durability

## Success criteria

- Enqueue 3 jobs, kill the process, re-read the queue → all 3 still present with identical fields.
- Enqueue the same `job_id` twice → one entry, fields updated.
- Simulated batch of 4 kits where kit #2 fails: kits 1, 3, 4 land locally; #2 is `failed` with an
  error string; `destroy` called exactly once; queue retains only #2.
- Simulated batch where the deadline expires after kit #2: kits 1-2 done, 3-4 still `pending`,
  instance destroyed, no exception escapes.
- `npm ci` invoked exactly **once** per flush regardless of kit count (asserted on the fake ssh
  call log).
- `flush_check()` performs zero network calls and creates zero instances (asserted: fake SDK call
  count == 0).

## Risks

| Risk | Mitigation |
|---|---|
| Threshold policy creeping into Python | `flush_check` returns data only; a test asserts it never calls `rent`. Policy lives in `skills/core/cloud-render.md` |
| Batch deadline too short → half the batch unrendered but fully paid | Deadline derives from the sum of estimates × 1.5 and is capped; deadline guard stops before starting a render it cannot finish |
| Bid/preemptible flush loses several completed renders | Incremental download; plus phase 05 rule: bid mode only with explicit user opt-in and only for short per-job renders |
| Three writers (CLI, server, agent) corrupt the queue file | Atomic writes make corruption impossible; *lost updates* are still possible — hence the open question below |
| Queue grows unboundedly, silently spending on a big flush | `flush_check` reports estimated cost every time; the per-rental `max_total_usd` ceiling (phase 01) is checked before create regardless of batch size |
| A `blocked` job is invisible and never renders | `list()` and `flush_check()` both surface `blocked` entries with their error |

## Security

- Same as phase 02: only per-job props + staged media travel. A batch uploads N jobs' footage to
  one third-party box — worth stating explicitly in the announce (phase 05), since it is a larger
  exposure than a single job.
- Job kits live in per-job remote subdirectories so one job's assets are not visible under another
  job's `--public-dir` (avoids a wrong-asset render as much as any privacy concern).

## Next

Phase 04 wraps both `render_now` and the queue operations as registry-visible `BaseTool`s and wires
cost governance.

## Unresolved questions (resolved during implementation)

1. Queue file concurrency: **lock file with stale timeout** — implemented as `batch-queue.lock`
   (`LOCK_STALE_SECONDS=30`, `LOCK_ACQUIRE_TIMEOUT_S=10`) in `queue.py`'s `_locked()` context
   manager, ~25 lines.
2. Batch flush handling local rendering: **no** — `flush()` only ever rents; there is no local
   fallback path (YAGNI, per this file's own recommendation).

## Implementation deviations from this file's illustrative signatures

- `render_batch(rental, composer, items, *, key_path, max_concurrency, ready_timeout_s,
  deadline_epoch, safety_margin_s, on_output)` takes the shared `composer` (`ComposerKitManifest`)
  separately from `items` (`list[BatchItem]`, each pairing a `JobKitManifest` with its own
  `flags`/`timeout_s`/`job`/`expected_duration_seconds`) rather than one flat `kits` list — the
  architecture section's own upload split (composer once, job kit per subdir) needs the composer
  kit passed distinctly from the per-job kits.
- `flush(job_ids, *, config, cost_hooks=None)` — no `offer_id` parameter. Mirrors
  `remote.render_now`'s own convention (search + pick the cheapest eligible offer internally)
  rather than taking a pre-chosen offer, per phase-02's already-documented deviation; keeping one
  convention instead of two.
- Added `max_batch_runtime_minutes` (default 90, validated range `[5, 480]`) to
  `lib/cloud_render/config.py`'s `BUILTIN_DEFAULTS`/`validate()` and to
  `config/cloud-render.json` — this file's architecture section names
  `config.max_batch_runtime_minutes` but phase 01 never defined it.
- `render_now` now builds a `ComposerKitManifest` + `JobKitManifest` pair (via the new
  `kit.build_composer_kit()`/`build_job_kit()`) and calls `render_batch` with a single-item list —
  it no longer calls a `render_one`/`build_autoedit_kit` (both removed, per this file's own "one
  code path" instruction).
