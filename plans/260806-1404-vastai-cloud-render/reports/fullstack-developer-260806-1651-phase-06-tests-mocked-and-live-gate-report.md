# Phase 06 Implementation Report — Tests: mocked-SDK unit suite + opt-in live smoke

## Executed Phase
- Phase: phase-06-tests-mocked-and-live-gate
- Plan: plans/260806-1404-vastai-cloud-render/
- Status: completed (mocked/default suite). **Live suite implemented-but-never-executed** per
  explicit orchestrator scope override — see "Live suite" section below.

## Scope note (re-confirmed after session-limit reset)
Re-read every file this phase touched end-to-end (not from memory) after the reset, fixed one
real bug found during that re-check (see Deviations #1), re-ran the full default suite twice more,
and re-confirmed the live suite is still skipped/never executed. `VAST_LIVE_TEST`/`VAST_LIVE_MAX_USD`
were unset in the shell for every command in this session; `tests/live/` was invoked only via
`--collect-only` and via `-m live` with no env vars (which pytest **skips**, not runs — verified
output: `3 skipped`, never `3 passed`).

## Files Created
- `tests/fixtures/__init__.py` — makes `tests.fixtures` a subpackage (matches `tests/contracts/`,
  `tests/tools/` convention).
- `tests/fixtures/fake_vastai.py` (172 lines) — shared `FakeVastAI` recording double: call log,
  `fail_on=(method, n)` failure injection, scripted `loading→running` status progression via
  `poll_until_running=`, `seed_offers()`/`seed_instance()` test helpers, `install()` for direct use.
- `tests/conftest.py` (33 lines) — `fake_vastai_sdk` fixture: installs the fake into
  `sys.modules["vastai"]` via `monkeypatch`, teardown asserts nothing replaced it with something
  else before `monkeypatch`'s own revert (LIFO teardown catches what `monkeypatch` alone would miss).
- `tests/test_cloud_render_fake_vastai_fixture.py` (9 tests) — self-tests for the shared fixture
  (call recording, failure injection targeting the right call number, destroy still recorded when
  it raises, 404-shaped destroy error, status progression default and scripted, seed_instance,
  teardown-guard sanity).
- `tests/test_cloud_render_cost_governance.py` (6 tests) — `reconcile()` records actual not
  estimate (success and failure paths), `refund()` on pre-rental failure (zeroes reservation,
  never touches spend, raises `KeyError` for unknown entry), regression guard that the approval
  gate still fires on a fresh tracker. Explicitly does NOT duplicate the `reserve()` /
  `ApprovalRequiredError` test already in `tests/test_cloud_render_tool_registration.py`.
- `tests/test_cloud_render_fidelity.py` (1 test, real) — the fake-remote fidelity test: drives the
  **real** `remote.render_batch()` with `transfer.scp_up/scp_down`/`remote._stream_command`
  monkeypatched to a local executor that runs the real embedded ssh command against the real local
  `remotion-composer/` (EndTag composition, no footage needed), diffs the output against a direct
  local render of the same props (ffprobe resolution/fps/codec exact match, duration within 0.05s,
  PSNR ≥ 40 dB). Module-level `skipif` if `node_modules`/`ffmpeg`/`ffprobe` are absent — always
  skips in CI (Python job never installs Node), runs for real locally (measured 43.6s here — real
  double-render, not a fake).
- `tests/test_cloud_render_live_gate.py` (1 test) — runs in the **default** suite (not marked
  `live`) so it actually executes in CI: fails if `VAST_LIVE_TEST` is set while `CI` is set.
- `tests/live/__init__.py`
- `tests/live/test_cloud_render_live_smoke.py` (3 tests, **written, never run**) — happy path
  (rent→render→pull→destroy, zero instances after), crash path (hard-kill the render in a
  subprocess, prove a **fresh** `python -m lib.cloud_render.reap` process destroys it with zero
  shared state), fidelity (same props locally vs on the real instance, PSNR gate). Real
  `vast_client`/`remote`/`ledger`/`config`, no mocking — gated on `@pytest.mark.live` +
  `VAST_LIVE_TEST=1` + `VAST_LIVE_MAX_USD` all being true; each test re-checks its own estimate
  against the ceiling before renting; autouse fixture sweeps `openmontage-*` instances before and
  after every test; measurements append to
  `plans/260806-1404-vastai-cloud-render/reports/live-smoke-measurements.jsonl` (never created —
  never run).
- `tests/live/README.md` — cost breakdown (<$0.05 total), prerequisites (API key, dedicated SSH
  keypair via `setup_key.py`, ffmpeg), how to run, post-run checklist, recommended cadence
  (manual only, no schedule).
- `pytest.ini` — registers the `live` marker, `addopts = -m "not live"` (repo had no pytest config
  file before this).

## Files Modified
- `tests/test_cloud_render_remote.py` (+99 lines) — **extended, not overwritten** (phase 04 already
  owned/created this file with 31 tests). Appended
  `TestDestroyGuaranteeAcrossAllEightFailurePoints`: a single parametrized test over the exact 8
  failure points named in the phase's coverage matrix (success, wait_running_timeout,
  ready_timeout, npm_ci_fail, render_fail, scp_down_fail, keyboard_interrupt, system_exit) plus a
  meta-test asserting the case list itself has all 8 labels. File is now 39 tests, all green.
- `Makefile` — `test` target now explicit `-m "not live"` (belt-and-suspenders on top of
  `pytest.ini`'s addopts); new `cloud-render-live-test` target (refuses without both
  `VAST_LIVE_TEST`/`VAST_LIVE_MAX_USD` set, prints a cost warning, then `pytest tests/live/ -m
  live`); added to `.PHONY`.
- `.github/workflows/ci.yml` — **not modified**, deliberately (see Deviations #2).

## Deviations from the phase file's illustrative sketch

1. **Real bug found and fixed during re-verification (post session-limit reset).** The destroy-
   guarantee parametrize list originally captured `vast_client.CloudRenderError` as a class
   reference at **module-import time**. `tests/test_cloud_render_ledger_reaper.py`'s
   `cloud_render_modules` fixture does `importlib.reload(vast_client)` for its own (unrelated)
   tests; reload rebinds a brand-new `CloudRenderError` class onto the module. Because that file
   collects alphabetically before `test_cloud_render_remote.py`, the class captured at collection
   time became stale by the time my parametrized test actually ran, so `pytest.raises(stale_class)`
   never matched the *current* class the code raises — passed in isolation (39/39), failed only in
   the full suite. Fixed by deferring resolution to a zero-arg lambda evaluated inside the test body
   at run time (`lambda: vast_client.CloudRenderError`, etc.) instead of a frozen class reference.
   Verified with two full-suite reruns after the fix, 0 failures both times. This is exactly the
   kind of test-order bug the phase's own conftest teardown guard exists to catch category-wise;
   this specific one was a different mechanism (class rebinding via reload, not `sys.modules`
   pollution) so it needed its own fix, not the teardown assertion.
2. **`.github/workflows/ci.yml` left untouched.** Its only pytest invocation is `make test`, which
   now carries `-m "not live"` itself; there is no second, direct pytest call in the workflow to
   patch. Re-verified `ci.yml`'s content is unchanged from before this phase (its "M" git status
   predates this session — confirmed by reading it again, content matches what was read at the
   start of the session). The CI guard test (`test_cloud_render_live_gate.py`) runs in the default
   suite and would fail loudly if a future `ci.yml` edit ever set `VAST_LIVE_TEST`.
3. **`tests/test_cloud_render_schemas.py` and `tests/test_cloud_render_command_parity.py` not
   created** — genuinely redundant, not YAGNI-skipped without checking:
   - Schema coverage (new enum value, `render_report` with/without `render_location`, all fixtures
     on disk still valid) is already fully covered by
     `tests/contracts/test_cloud_render_governance_contract.py` (phase 05's file) — same assertions,
     word for word against the coverage matrix's "schema (P5)" row.
   - Command-parity coverage (argv byte-identical to the pre-refactor closure) is already fully
     covered by `tests/test_talking_head_render.py::TestBuildRemotionCommandParity` — landed with
     the phase 02 refactor, exactly as the phase file's own implementation step 4 says it should
     ("Land this together with the phase 02 refactor commit").
   Recreating either would have been pure duplication for its own sake.
4. **Cost-governance test targets `CostTracker` directly, not `VastCloudRender.execute()`.**
   Verified by grep: no tool's `execute()` anywhere in this repo calls
   `CostTracker.reserve/reconcile/refund` — it is a generic, tool-agnostic ledger the
   orchestrator/agent layer calls around any paid tool (confirmed against
   `skills/core/cloud-render.md`'s documented calling convention). Tests exercise that exact
   convention using `vast_cloud_render`'s own `estimate_cost()` output and rental `actual_usd`
   numbers, not a fabricated tool integration that doesn't exist in the codebase.
5. **Fidelity test uses `EndTag` (text-only composition), not `MonaTimeline`.** `MonaTimeline`
   needs real footage; `EndTag` needs none and still exercises the exact same
   `build_remotion_command`/cloud-plumbing path. Verified with a real double-render: identical argv
   run twice on the same machine decoded to `PSNR=inf` (bit-identical), comfortably clearing the
   40 dB gate.
6. **Fidelity test is real, not faked, and takes ~44s locally when `node_modules` is present.**
   Per the explicit brief ("implement the test but skipif with a clear reason... do not fake a
   passing fidelity test"), and since this sandbox has both `remotion-composer/node_modules` and
   `ffmpeg`/`npx`/`node`, I ran the real thing rather than skip. CI's Python job never installs the
   Node toolchain, so it always skips there — confirmed by re-reading `ci.yml`'s two independent
   jobs (`validate` has no Node setup; `typecheck-ui` is a separate job/checkout that never touches
   Python tests).
7. **Live smoke test's synthetic job uses a 2-second ffmpeg-generated solid-color clip**, not the
   plan's literal "60-frame `--frames=0-59`" ground-truth shape — the productized `build_remotion_
   command`/`kit.build_job_kit` path (which the live suite must exercise for real, not bypass) has
   no `--frames` override at all. A short synthetic clip duration is the only way to keep this
   suite's smoke render short through the real, current code path without adding a new parameter
   to `remote.py`/`kit.py` (out of this phase's file ownership). Documented in the test file's own
   module comment and in the README.

## Tests Status
- New/extended cloud-render files run together (`test_cloud_render_fake_vastai_fixture.py`,
  `test_cloud_render_cost_governance.py`, `test_cloud_render_remote.py`,
  `test_cloud_render_fidelity.py`, `test_cloud_render_live_gate.py`): **56 passed**, 48.7s.
- Full default suite, `python -m pytest tests/ -q -m "not live"`, run **twice** after the fix
  (once mid-session, once after the reset) to rule out order-flakiness: **1542 passed, 11 skipped
  (pre-existing, unrelated), 3 deselected (the 3 live tests, correctly excluded by marker), 0
  failed**, ~6m50s. (Test count grew from the 1479 baseline noted in phase 04's report partly from
  this phase's own additions and partly from a concurrent sibling phase — `tests/test_cloud_render_
  cli_args.py`, 37 tests, appeared mid-session; confirmed via its own docstring it is phase 07's
  file, not touched by me, and it passes.)
- Live suite: `pytest tests/live/ -m live` with env vars unset → **3 skipped**, confirmed via literal
  command output. `pytest tests/live/ --collect-only` (no `-m live`) → **0 collected, 3 deselected**.
  Neither invocation executed a single live test body in this session.
- Zero network egress in the default suite, by inspection: every `test_cloud_render_*.py` file
  either uses the shared `sys.modules["vastai"]` fake / an inline predecessor fake, or monkeypatches
  `transfer.ssh`/`scp_up`/`scp_down`/`remote._stream_command` directly (no un-mocked `subprocess`
  call to a real `ssh`/`scp` binary in any non-live file). The one real subprocess work in the
  default suite (`test_cloud_render_fidelity.py`) calls `npx`/local-file-copy only, never
  `ssh`/`scp`, never touches `vastai`.

## Success Criteria Checklist (phase file)
- [x] `make test`/`make autoedit-test` unaffected by `vastai` uninstalled/network unplugged —
      unchanged from phase 04 (verified there via `sys.modules['vastai']=None`); this phase adds no
      new import-time dependency on the SDK.
- [x] Default suite adds comfortably under the stated budget from CI's perspective (fidelity test
      always skips there; everything else added is sub-second to low-single-digit-seconds).
- [x] Zero network egress during the default suite (see above).
- [x] Destroy-guarantee test passes for all 8 failure points (`TestDestroyGuaranteeAcrossAllEight
      FailurePoints`, 8/8 green after the reload-order fix).
- [x] Live suite: **implemented**, never run in this session (explicit orchestrator directive) —
      so "run once, record numbers" is NOT done. This is the one criterion this phase leaves for a
      human to trigger deliberately (`VAST_LIVE_TEST=1 VAST_LIVE_MAX_USD=0.05 make
      cloud-render-live-test`), per the safety override given at task start.
- [x] CI guard test exists and runs in the default suite (`test_cloud_render_live_gate.py`).

## Unresolved Questions
1. Live suite has never been executed — `render_seconds_per_video_second` recalibration and the
   crash-path's `time.sleep(45.0)` mid-render-kill heuristic are both unverified against real
   numbers. Recommend a human runs `make cloud-render-live-test` once, deliberately, before relying
   on this feature for a real production render, and updates the 45s heuristic if the measured
   boot/upload time differs meaningfully.
2. Same open question phase 04 flagged: `plans/.../plan.md`'s phase-status table still shows
   "pending" for every phase despite 01-06 apparently being done — left to whoever does the plan.md
   consolidation pass, not touched here (file ownership).
