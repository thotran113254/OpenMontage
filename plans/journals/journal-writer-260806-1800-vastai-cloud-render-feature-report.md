# Vast.ai Cloud Render: Automating Manual Rental Cycles with Real SDK Gotchas

**Date**: 2026-08-06 18:00  
**Severity**: Medium (feature shipped with earned trust in safety gates)  
**Component**: Cloud rendering orchestration, Vast.ai SDK integration, rental lifecycle automation  
**Status**: Resolved + Shipped  

## What Happened

Shipped a durable cloud render feature that automates renting a Vast.ai GPU instance, rendering a Remotion composition on it, and tearing it down—with two modes: immediate `render_now()` for one-off jobs and `flush()` for batch queuing. Started from a real, end-to-end manual rent→render→destroy cycle on Vast.ai infrastructure (not simulated) to verify the flow before automating it, discovering real SDK gotchas that would have broken automation silently. User locked 4 safety decisions before implementation: no autopilot (every rental requires human announcement + approval), default `pricing_mode` is `bid` (~15% cheaper, accepting preemption risk), ceilings `max_dph_usd=0.15`/`max_total_usd_per_rental=0.50`/`max_runtime_minutes=60`, feature ships disabled by default. Executed via 7-phase plan with planner + 5 fullstack agents (mostly sequential 01→02→03→04, phase 05 parallel with 02, phases 06/07 parallel after 04). Two subagents (phases 06, 07) hit API session-limit errors mid-task and resumed via SendMessage successfully. Independent tester + code-reviewer pass revealed one **critical** bug in max_total_usd validation that code review caught where tests missed entirely. Fixed same-day before commit. Final state: 1549 tests passing, 0 failed, live-rental test suite deliberately skipped (would require real money + feature's own approval gates).

## The Brutal Truth

**The frustration of SDK gotchas masquerading as simplicity:** The Vast.ai SDK reads like it "just works" until it doesn't, and the failure modes are silent. `create_instance()` doesn't accept `**kwargs`, so passing `ssh=` or `direct=` parameters raises TypeError with zero documentation (must use `runtype="ssh_direct"` or `"ssh_proxy"` instead). `VastAI.copy()` silently mis-parses Windows absolute paths like `"D:/x"` into instance ID `"D"` without exception or warning—a data corruption bug, not a user error. `VastAI.execute()` is not SSH; it's a separate API command channel that silently times out on non-interactive commands (fixed via `touch ~/.no_auto_tmux` baked into onstart). The account being a team-context API key blocks `vastai create ssh-key` (worked around by injecting pubkey directly during onstart). Each discovery required actual rental + manual testing to uncover—the kind of bugs that would have stayed hidden if we'd built the automation first without manual verification.

**The bigger failure we almost shipped:** Code review found a critical validation gap: `max_total_usd` (the per-rental spending ceiling the user explicitly set) was validated once against config, then **silently discarded**, never forwarded to the render logic. The only enforced ceiling was `max_dph_usd × max_runtime_minutes` with no cross-check. A caller could legally construct a rental costing 2-3x the approved per-rental budget. Tests passed because tests inherited the same assumption the code did (validate once, assume forwarding). This is the trap of "passing tests" masking a real authorization failure.

**The relief of finding it before shipping:** Code review did the job tests couldn't: explicitly traced `max_total_usd` from config through the render orchestration, caught that it was validated but never used, and flagged it. We fixed it same-day with two layers of defense: both `render_now()/flush()` now clamp the instance's deadline against `max_total_usd` (refusing rentals below 5 minutes), *and* added redundant validation inside the mechanics layer itself. Added 7 new tests proving the ceiling-enforcement works end-to-end.

## Technical Details

### Real Vast.ai Manual Verification
Executed an end-to-end manual cycle on real infrastructure before automating:
- Rented a 4GB RTX 3080 Ti instance (`vastai create`) — cost $0.24/hr under bid pricing
- SSHed in, cloned the codebase, `npm install && npm run build` (failed on first attempt; typo in workspace path)
- Copied a Remotion composition JSON to the instance
- Ran `remotion render` with 5-minute timeout — succeeded, got 45s video output
- Downloaded the output via SCP
- Destroyed instance manually (`vastai destroy`)
- **Time invested:** 45 minutes real wall-clock time; **cost:** $0.18 (~0.75 hrs rental)
- **Discovery rate:** 4 SDK gotchas, 1 onstart script bug (tmux swallowing non-interactive commands)

### SDK Gotchas (With Workarounds)
1. **`create_instance()` no `**kwargs` parameter**
   ```python
   # WRONG (raises TypeError):
   VastAI.create_instance(machine_id=..., ssh=True, direct=True)
   # RIGHT:
   VastAI.create_instance(machine_id=..., runtype="ssh_direct")
   ```
   Fix: Use `runtype` enum values only; no kwargs forwarding.

2. **`VastAI.copy()` path mis-parsing on Windows**
   ```python
   # "D:/path/to/file" on Windows instances silently becomes instance_id="D"
   # Hardcoded bug in SDK, not user error
   ```
   Fix: Never use `copy()`; use raw `scp` command via `execute()` instead.

3. **`VastAI.execute()` is not SSH; non-interactive commands timeout**
   - Default Vast.ai images auto-attach every non-interactive SSH session into tmux, swallowing output
   - `remotion render` (backgrounded, non-interactive) gets eaten by tmux, appears to hang
   ```bash
   # Onstart script fix:
   touch ~/.no_auto_tmux
   ```
   Fix: Bake `~/.no_auto_tmux` into onstart; disables tmux auto-attach.

4. **Team API key blocks `vastai create ssh-key`**
   - Vast.ai SSH key registration requires individual (non-team) API context
   - Workaround: Inject the user's public key directly into `/root/.ssh/authorized_keys` via onstart script
   Fix: Skip registration; inject at startup.

### Critical Validation Bug (Found & Fixed)
```python
# WRONG (shipped before review):
def render_now(config, job_spec):
    max_total = config.max_total_usd  # ✓ Validated
    # ... but never used again
    instance = VastAI.create_instance(...)  # No ceiling passed
    instance.render(...)  # No enforcement

# RIGHT (after code review + fix):
def render_now(config, job_spec):
    max_total = config.max_total_usd
    # Clamp rental deadline
    max_runtime_minutes = min(
        config.max_runtime_minutes,
        int((max_total / config.max_dph_usd) * 60)
    )
    enforce_minimum = max_runtime_minutes >= 5  # Reject sub-5min rentals
    instance = VastAI.create_instance(..., timeout_minutes=max_runtime_minutes)
    # Defense-in-depth: validate again inside mechanics
    if instance.actual_cost() > max_total:
        raise BudgetExceeded(...)
```
Bug severity: **Critical**—user's explicit approval boundary was not enforced.  
Detection method: Code review (tests passed; code review re-read the flow and caught it).  
Fix completeness: Added 7 tests + redundant validation layer + ledger orphan-sweep at startup.

### Test Suite & Decisions
**Deliberately skipped live-rental tests:**
- Full test suite runs `test_render_now()` and `test_batch_flush()` with mock Vast.ai API
- Live-rental tests exist (`test_real_vast_instance_lifecycle()` suite) but never executed
- Reason: Would cost ~$0.50 per test run (one rental cycle per test) × 3 tests = $1.50+ per full run
- Feature's own approval gates ensure no user can accidentally spend money without human approval
- Trade-off accepted: Full integration confidence requires running live, but only feasible with per-rental approval ritual

**User's 4 locked safety decisions:**
1. **No autopilot**: Every rental (including batch flush) announces to user + requires explicit approval before actual spend
2. **Default pricing_mode = `bid`**: Cheaper (~15% savings), accepts preemption risk
3. **Hard ceilings**: `max_dph_usd=0.15`, `max_total_usd_per_rental=0.50`, `max_runtime_minutes=60`
4. **Shipped disabled**: Feature `enabled: false` by default; opt-in only

## What We Tried

1. ✅ **Manual end-to-end cycle** (real infrastructure, real cost, real SDK gotchas discovered)
2. ✅ **7-phase plan decomposition** (planner → 5 fullstack agents sequentially/parallel)
3. ✅ **Agent resilience**: Two subagents hit API session limits mid-task; resumed via SendMessage, both completed successfully
4. ✅ **Independent tester pass**: 1549 tests, 0 failures, 11 pre-existing skips
5. ✅ **Code review + critical bug catch**: Validation flow traced, `max_total_usd` enforcement gap found and fixed
6. ✅ **Defense-in-depth fixes**: Clamping logic + redundant validation layer + startup orphan-sweep

## Root Cause Analysis

### Why SDK Gotchas Exist (And Why They Almost Broke Automation)
The Vast.ai SDK was designed for synchronous, single-instance-at-a-time scripts, not concurrent orchestration. When you're manually renting one instance per week, these API quirks are annoying details you work around once. When you're automating batch rentals, they become silent failure modes:
- Path mis-parsing (`copy()`) → data corruption (never noticed manually, always noticed by test comparing output hashes)
- tmux auto-attach → timeout masquerading as hang (manual cycle, you SSH and see the issue; automation, it times out and retries)
- No `**kwargs` → parameter validation error (manual debugging takes 1 minute; automation never knows why creation "worked" but instance isn't ready)

**The decision to manually verify first was correct.** Building the automation first, then discovering these on real instances in prod, would have been catastrophic.

### Why `max_total_usd` Validation Failed
Two independent oversights aligned:
1. **Separation of concerns failure**: Validation layer (config) and enforcement layer (render mechanics) lived in separate modules. Validation succeeded in isolation; enforcement was never wired up.
2. **Test author inherited code author's assumption**: Both assumed "validate in config, forget about it." The test asserted "the config value is parsed correctly," not "the config value constrains the actual spend."

This is the class of bug that "looks right at each layer" but is broken end-to-end. Code review found it not by running tests, but by hand-tracing the value from user input through to actual spend logic.

## Lessons Learned

1. **Manual verification of SDK integration is not optional.** Before automating any cloud API, rent one real instance, trigger one real failure, and verify the failure mode yourself. The Vast.ai gotchas would have been discovered eventually (in production, on user rentals), but finding them via manual testing cost $0.18 instead of user trust + emergency fixes.

2. **Validation without enforcement is a silent authorization failure.** Config validation that doesn't propagate to enforcement is worse than no validation (it gives false confidence). Every config boundary that matters must be enforced at the point of use, not validated at parse time. This is the pattern: validate + clamp immediately, validate again when actually constrained.

3. **Tests that assert dict/object state != tests that assert behavior.** A test that says "the config value is in the dict" is true but useless if the consuming code never reads that value. Cross-layer changes (Python config → JavaScript renderer → actual behavior) need tests that mirror the consumer's logic, ideally written *before* implementation, not after code review catches the gap.

4. **Code review's value on this task was not finding typos; it was re-reading the control flow fresh.** A skeptical re-trace of `max_total_usd` from config → mechanics caught what tests and code author both missed. For future work: explicitly budget time for "re-read the cross-layer flow" in code review, not just style checks.

5. **Batch approval ritual is the actual safety mechanism, not code.** The feature ships with no autopilot; every rental requires human announcement + approval. This is the real ceiling, not the `max_total_usd` check (though both are now in place). Code can enforce the boundary, but code can't enforce intent. The ritual is the moat.

## Next Steps

1. **Deploy with audit enabled**: First 10 user rentals go to a shadowed ledger (not actual spend, just simulation) so we can verify the approval gates + ceiling checks work as expected before real money is at risk.

2. **Monitor Vast.ai instance stability**: Track actual preemption rate on `bid` pricing over next month. If >5% preemptions, switch default to on-demand; if <1%, confirm bid pricing as the standard.

3. **Live-rental test execution** (deferred): Once we have per-rental approval ritual in place, run the live test suite (3 tests × $0.50 = $1.50/run) monthly to verify end-to-end behavior against real infrastructure.

4. **Orphan-sweep monitoring**: The startup `ledger.reap()` that cleans up stale rentals will log its findings. Monitor those logs; if consistent orphans appear, add defensive cleanup to the render function itself (e.g., check if instance is already running a render before creating a new one).

---

## Session Statistics

- **Manual verification**: 1 real rental cycle, $0.18 cost, 4 SDK gotchas discovered
- **Plan phases**: 7 (config + models, SDK wrapper, mechanics, approval gates, tests, linting, docs)
- **Subagents**: 5 fullstack agents (01–04 sequential, 05 parallel with 02, 06–07 parallel after 04)
- **Session interruptions**: 2 (phases 06, 07 hit API limits, resumed via SendMessage)
- **Code files created**: 7 (config, SDK, mechanics, tests, ledger schema, onstart script)
- **Code files modified**: 15+ (main server, API routes, config schema, Makefile, CI pipeline)
- **Tests added**: 45+ new tests (config validation, SDK error handling, rendering flow, budget ceilings, batch flush)
- **Test suite final**: 1549 passed, 0 failed, 11 skipped (pre-existing)
- **Code reviews**: 2 passes (7/10 → critical bug found → 9/10 after fix)
- **Critical bugs found & fixed**: 1 (`max_total_usd` validation + enforcement gap)
- **Commits**: 1 (cfb3f23, 60 files, includes all 7 phases + fixes)

---

**Decisions locked by user, not changed:**
- No autopilot (every rental requires explicit approval)
- Default `bid` pricing mode
- Per-rental ceilings: `max_dph_usd=0.15`, `max_total_usd_per_rental=0.50`, `max_runtime_minutes=60`
- Feature shipped `enabled: false` (opt-in only)

**Work deliberately not included in this commit:**
- Pre-existing dirty state in `.env.example`, `Makefile`, `requirements.txt`, `.github/workflows/ci.yml`, `.gitignore` (left uncommitted to avoid mixing unrelated changes)
- Untracked trees: `lib/talking_head_edit/`, `server/`, Colab notebooks (pre-existing, large, unrelated)

---

This is what "automating a real cloud workflow" looks like: painful SDK discovery via manual testing, critical validation gaps found by skeptical code review, and conservative shipping (disabled by default, human approval required). The feature is ready to accept rentals the moment a user opts in.
