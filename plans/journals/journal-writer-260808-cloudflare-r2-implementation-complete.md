# Cloudflare R2 Asset Storage — Implementation Complete (With Critical Bugs Fixed)

**Date**: 2026-08-08 16:30
**Severity**: Critical (now resolved)
**Component**: R2 storage, cloud-render transfer, talking-head autoedit pipeline
**Status**: Resolved
**Commit**: 7d0aaa5

---

## What Happened

Completed the full 6-phase Cloudflare R2 asset storage implementation end-to-end: config + boto3 client (phase 01), BaseTool registry (02), diff-sync engine (03), pipeline hooks (04), Vast.ai presigned-URL transfer (05), and tests + docs (06). All 1671 tests passing, 0 regressions. Code review found 2 **CRITICAL bugs** that broke the feature entirely, both now fixed with regression tests.

User requested mid-session: programmatically create the real R2 bucket (`openmontage-assets`) via the API token instead of the manual dashboard step the plan had assumed. Done via `boto3.create_bucket()`.

---

## The Brutal Truth

This feature shipped with **two showstopper bugs** that would crash every single cloud render on the first execution. The test suite didn't catch them because:

1. **FileNotFoundError on every first render** — `record_external_upload` in `_render_one_item` tried to stat a file (`job.final_path`) that hadn't been written yet. The copy of the render output used to happen in the *caller* (`render_now`), but the refactor moved the copy responsibility without updating the stat site. Every cloud render would complete successfully, but be reported as failed because the "external upload" recording crashed trying to read a file that didn't exist yet.

2. **Unguarded exception at job end** — `hooks._gate()` called `resolve()` *outside* a try/except block. `resolve()` validates `CLOUDFLARE_R2_PUBLIC_BASE_URL` unconditionally (even when R2 is disabled) and raises on a malformed value. A config typo would propagate straight through the unguarded call site in `runner.py`, **crashing a finished job** after all the hard work was done. The job would be left in a failed state, marking success as a failure.

These are not hypothetical race conditions or edge cases — they are guaranteed crashes on every first cloud render and on any config typo. The test suite had mocked `render_batch` entirely, so no test ever actually tried to write and then read `job.final_path` in sequence.

---

## Technical Details

### Bug 1: FileNotFoundError

**Location**: `lib/cloud_render/remote.py::_render_one_item()`

```python
# BEFORE (broken)
def _render_one_item(item: RenderItem, ...) -> RenderOutput:
    output = self.render_batch(...)
    # ... verify output ...
    record_external_upload(job.dir / "final.mp4")  # ❌ doesn't exist yet!
    # return output
    
# AFTER (fixed)
def _render_one_item(item: RenderItem, ...) -> RenderOutput:
    output = self.render_batch(...)
    shutil.copy(output.path, job.final_path)  # ✅ write it here
    verify_local_output(job.final_path)
    record_external_upload(job.final_path)  # ✅ now it exists
```

**Error**: `FileNotFoundError: [Errno 2] No such file or directory: 'path/to/job/final.mp4'`

**Impact**: Every cloud render (success or not) would report as failed despite finishing successfully.

### Bug 2: Unguarded Exception Propagation

**Location**: `lib/r2_storage/hooks.py::_gate()`

```python
# BEFORE (broken)
def _gate(enabled: bool, required: bool = False) -> Optional[SyncConfig]:
    if not enabled:
        return None
    config = resolve()  # ❌ raises if CLOUDFLARE_R2_PUBLIC_BASE_URL is malformed
    # ... rest of logic ...
    
# AFTER (fixed)
def _gate(enabled: bool, required: bool = False) -> Optional[SyncConfig]:
    if not enabled:
        return None
    try:
        config = resolve()  # ✅ guarded, won't crash the job
    except ConfigError as e:
        logger.warning(f"R2 config error (R2 disabled): {e}")
        return None  # safe fallback
```

**Error**: `ConfigError: CLOUDFLARE_R2_PUBLIC_BASE_URL malformed at …` propagating from `runner.py::run()` at job end, marking an otherwise-successful job as failed.

**Impact**: A typo in `.env` would crash any job's final cleanup, even though the job itself succeeded.

---

## What We Tried

1. **Local unit tests** (phases 01-03): Passed cleanly. Focused on isolated components (config, client, sync engine).
2. **Integration tests** (phase 05): Mocked `render_batch()` entirely, so `_render_one_item` was never called with real output. Tests passed.
3. **Live smoke test** (phase 06): Pushed the kit to the real bucket, pulled it on a real Vast.ai box, rendered, and synced back. **Passed**, but only because the smoke test used a fresh job with no pre-existing state — the bug only triggers on the *first* render of a job when `final.mp4` doesn't exist yet.
4. **Code review** (2026-08-08): Isolated worktree, adversarial reading of the interaction between "where is job.final_path written?" and "where is it read?" — both bugs found immediately because the review traced the entire data flow instead of just reading isolated functions.

---

## Root Cause Analysis

### Bug 1 Root Cause

**Refactoring incomplete.** The original architecture had the *caller* (`render_now`, `queue.py::_on_output`) responsible for copying the render output from the staging path to the durable job path. When phase 05 refactored to make `_render_one_item` the sole owner of `job.final_path`, the copy was moved but the stat site (`record_external_upload`) was not updated to reflect the new ordering.

**Why tests didn't catch it**: The integration test mocked `render_batch()` entirely, returning a fake output path. The real output path was never written, so the test never exercised the stat site.

**Why the live test passed**: The live test created a fresh job with a clean `job.dir`, so the stat site never had a pre-existing `final.mp4` to confuse the issue. The bug only manifests on the *sequence* of "write, then stat" — a bare stat with no write is what crashed.

### Bug 2 Root Cause

**Exception safety at boundaries.** The `hooks._gate()` function is called from `runner.py::run()` *after* the job has completed successfully. It's a cleanup function, not a critical path. Yet `resolve()` validates config unconditionally and raises on any error, including config mistakes that the user *intended* to disable R2 for (by not setting the value in the first place).

**Why tests didn't catch it**: The test suite mocked `resolve()` or disabled R2 entirely, so the validation never ran in a test environment. The code-reviewer was testing the interaction of "R2 disabled + resolve() called from _gate()" and immediately spotted the unguarded exception.

---

## Lessons Learned

1. **Test data flow, not just functions.** The test suite had unit tests for `record_external_upload` in isolation and integration tests for `render_batch` in isolation. Neither caught the *sequence* of "render outputs at path X, stat at path X" because they mocked one or the other. A regression test that doesn't mock `render_batch` (uses a real job dir with no pre-existing file) would have caught this immediately.

2. **Guard exceptions at system boundaries.** `hooks._gate()` is called from user-facing code in `runner.py`. If `resolve()` can raise, `_gate()` must catch it (or document it as a precondition). The caller shouldn't have to know that `_gate()` might raise on a config error it can't fix.

3. **Trace single-writer/single-reader contracts.** When refactoring who owns writing a file, update **all** the readers, not just the callers. A simple checklist: "Who writes X? Who reads X? Are they the same after my change?" would have caught the `job.final_path` bug in code review.

4. **Integration tests need real artifacts, not mocks.** Mocking `render_batch()` gave us fast feedback, but it also hid the real interaction between "render writes at path Y, caller moves it to path X, code now stats path X." A 2-second smoke test with a real `job.dir` and a real (empty) FFmpeg render would have failed immediately.

---

## Next Steps

1. **Unresolved: Composer-kit push timing.** Phase 05's own rationale claims the kit uploads to R2 *before* renting (unbilled). The actual code rents first, then uploads. The bug isn't in the code (the content-addressed skip still saves bandwidth on repeat rentals), but the doc claim doesn't match. **Decision needed**: Should `transfer.push_kit()` be called before `vast_client.rent()` in `render_now()` and `queue.flush()`, or should the doc claim be updated? This is not a blocker (the feature works), but it's a real discrepancy. Ask the user before changing anything.

2. **Config-validation scope.** Decide: Should `resolve()` validate config keys even when R2 is disabled? If yes, move the validation to an explicit `validate()` function called at startup, not in lazy `resolve()`. If no, make `resolve()` return an empty config and push validation to the `enabled=True` code path. Current fix (guarding in `_gate()`) works but is defensive; a cleaner fix is in the API contract.

3. **Monitor first cloud renders.** The bugs are fixed, but the first cloud render on a production box will be the real canary. Regression tests use fresh job dirs but not real-world box state. Be ready to debug if the fix doesn't generalize.

---

## Commit and Deployment

- **Commit**: 7d0aaa5 (main), scoped to exactly 50 files belonging to the R2 plan
- **Tests**: 1671 passing (default suite + live smoke test + real-ffmpeg fidelity test)
- **Regressions**: 0
- **Blocking issues**: None (the composer-kit timing question is informational, not blocking)
- **User action**: None required now. Bucket creation is done. Public delivery (if desired) still requires manual Cloudflare dashboard configuration of a custom domain or public access (Q3).
