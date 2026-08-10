# Phase 06 — Tests: mocked-SDK unit suite + opt-in live smoke

## Context

- [plan.md](plan.md), [phase-04](phase-04-tools-registry-cost-tracker.md)
- Test conventions: pytest, `tmp_path` fixtures, real-process-over-mock where the OS is the thing
  under test — see `tests/test_talking_head_queue_worker_reconcile.py:1-11` (docstring explains why
  it uses a real child process instead of a mock) and `:29-40`.
- Existing suites this must not slow down: `make autoedit-test`, `make test`,
  `.github/workflows/ci.yml`.
- Registration test pattern: `tests/test_footage_edit_analyzer_registration.py`.

## Overview

- **Priority:** P0 — the safety guarantees are only real if they are tested.
- **Status:** completed (mocked/default suite fully green; live suite implemented, never executed
  by design — see report)
- Two suites with a hard wall between them: the default suite **cannot** spend money (the `vastai`
  module is faked at `sys.modules` level, so there is no code path to the network), and a separate
  live suite that spends ~$0.01 and only runs when a human sets an env var.

## Key insights

1. **Mock the module, not the method.** Patching `VastAI.create_instance` still leaves a real
   `VastAI()` that reads the real key and could hit the network from any un-patched path. Injecting
   a fake `vastai` module into `sys.modules` makes network access *impossible*, not merely unlikely.
   That is the difference between "CI probably won't spend" and "CI cannot spend".
2. **The fake SDK must be a recording double, not a stub.** Every safety assertion in phases 01-03
   is of the form "how many times was `create_instance` called" / "was `destroy_instance` called
   exactly once". So the fake records an ordered call log, and tests assert against that log.
3. **Repo precedent says: use the real OS where the OS is the subject.**
   `test_talking_head_queue_worker_reconcile.py:1-11` deliberately spawns a real long-lived child
   because "the queue notices/kills it" must be proven against the OS. Same reasoning applies to the
   destroy-on-crash guarantee: the `finally` path should be proven with a real
   `KeyboardInterrupt`/`SystemExit` and a real process kill, not a mocked exception.
4. **A local "fake remote" gives most of the fidelity coverage for free.** Point `transfer.ssh`/`scp`
   at a local shell that runs the same argv in a temp dir and you can render the same props twice —
   once through the "cloud" path, once through the local path — and diff the outputs. That catches
   flag drift, path rewriting and props-mangling bugs without renting anything. The live test then
   only has to prove the things a local fake cannot: real boot, real apt, real Linux Remotion.
5. **The live test's most important assertion is not "the video rendered".** It is "`show_instances()`
   contains zero `openmontage-*` labels afterwards" — asserted in a `finally`, and again after a
   deliberate mid-render kill. A live test that renders correctly but leaks an instance has failed.
6. **Cost must be bounded by the test itself, not by trust.** The live test sets its own ceilings
   (`max_dph_usd`, `max_runtime_minutes=15`, 60-frame render like the manual run) so even a total
   logic failure caps at cents.

## Requirements

**Default suite (runs in CI, zero spend, zero network)**
- `tests/fixtures/fake_vastai.py` — installable fake: `FakeVastAI` with `search_offers`,
  `create_instance`, `show_instance` (scripted status progression `loading → running`),
  `show_instances`, `destroy_instance`, `label_instance`, plus `calls: list[tuple[str, dict]]` and
  programmable failure injection (`fail_on=("create_instance", 1)`).
- A `conftest.py` fixture that installs it into `sys.modules["vastai"]` and asserts on teardown that
  no test left the real module imported.
- A fake transfer layer: `ssh`/`scp` monkeypatched to a local executor with a scripted command→
  (exit_code, stdout) table, so `npm ci` / render / progress-line parsing are all exercised.

Coverage matrix:

| Area | Cases |
|---|---|
| config (P1) | ceiling validation, 3-layer override, `sources_of`, bad mode → raise |
| onstart (P1) | `no_auto_tmux` precedes apt; pubkey single-quoted; deadline shutdown present |
| ledger (P1) | atomic write survives kill between temp and replace; append-only jsonl ordering |
| reaper (P1) | all 5 rows of the decision table; unknown-label orphan; ledger-lost sweep |
| rent (P1) | adopt-on-retry ⇒ exactly one `create_instance`; ceiling refusal ⇒ zero calls; `runtype` string exact |
| kit (P2) | allowlist only; no `remotion-composer/public`; no `.env`; `kit_hash` stable + changes with props |
| command parity (P2) | `build_remotion_command` argv byte-identical to pre-refactor local argv |
| remote (P2) | destroy called exactly once for each of: success, wait timeout, ready timeout, `npm ci` fail, render fail, scp_down fail, `KeyboardInterrupt`, `SystemExit` |
| batch (P3) | partial failure isolation; deadline stop leaves remainder pending; `npm ci` once for N kits; incremental download; version drift warning; attempts→blocked at 3 |
| queue (P3) | durability across process restart; upsert not duplicate; prune missing job dirs |
| tools (P4) | registration; `vastai` absent from `sys.modules` after `discover()`; refuse without `offer_id`; refuse offer not in `dry_run_ref`; refuse when `enabled:false`; `estimate_cost` math; queue tool works offline |
| cost (P4) | `reserve` raises `ApprovalRequiredError` on first paid use with real `config.yaml` defaults; `reconcile` gets actual not estimate; `refund` on pre-rental failure |
| schema (P5) | new enum value validates; `render_report` valid with and without `render_location`; all existing fixtures still valid |
| fidelity (P2/P3) | "fake remote" render of the same props via cloud path vs local path → same duration/resolution/fps/codec, PSNR ≥ 40 dB |

**Live suite (opt-in, spends real money)**
- File `tests/live/test_cloud_render_live_smoke.py`, marked `@pytest.mark.live`.
- Skipped unless **both** `VAST_LIVE_TEST=1` and `VAST_LIVE_MAX_USD` are set; the test asserts its
  own computed estimate is below `VAST_LIVE_MAX_USD` before renting.
- Excluded from `make test` and from `.github/workflows/ci.yml` by marker deselection
  (`-m "not live"`), plus a CI guard test asserting `VAST_LIVE_TEST` is unset in CI.
- Test 1 — happy path: search → rent cheapest under ceiling → render a 60-frame smoke (the manual
  run's exact shape: `node:22-bookworm`, disk 12, `--frames=0-59`) → pull → assert file exists and
  plays → destroy. `finally`: `show_instances()` has zero `openmontage-*`.
- Test 2 — crash path: rent, start the render, `os._exit`-style kill a child worker mid-render, then
  in a **fresh process** run `python -m lib.cloud_render.reap` and assert the instance is destroyed.
  This is the only way to prove safety layers 2-3 end to end.
- Test 3 — fidelity: render the same props locally and on the instance, compare with the PSNR gate.
  Slowest and most expensive; may be run manually rather than as part of the marker.
- Every live test writes its measured numbers (boot seconds, `npm ci` seconds, render seconds per
  video second, actual $) into the report so `render_seconds_per_video_second` can be recalibrated
  from data.

## Related code files

**Create**
- `tests/fixtures/fake_vastai.py`
- `tests/test_cloud_render_config.py`, `test_cloud_render_onstart.py`,
  `test_cloud_render_ledger_reaper.py`, `test_cloud_render_kit.py`,
  `test_cloud_render_remote.py`, `test_cloud_render_batch_queue.py`,
  `test_cloud_render_batch_flush.py`, `test_cloud_render_tool_registration.py`,
  `test_cloud_render_cost_governance.py`, `test_cloud_render_schemas.py`,
  `test_cloud_render_command_parity.py`
- `tests/live/__init__.py`, `tests/live/test_cloud_render_live_smoke.py`
- `tests/live/README.md` — "these cost real money", how to run, expected spend (~$0.01-0.05)

**Modify**
- `tests/conftest.py` (or create) — the `sys.modules` fake-vastai fixture + teardown assertion
- `pytest.ini` / `pyproject`-equivalent — register the `live` marker; default addopts
  `-m "not live"`
- `.github/workflows/ci.yml` — ensure `-m "not live"` and add the "VAST_LIVE_TEST unset in CI" guard
- `Makefile` — `test` excludes live; new `cloud-render-live-test` target that prints the cost
  warning and requires the env vars

## Implementation steps

1. Build `fake_vastai.py` first — every other test depends on it. Scripted status progression and a
   call log are the whole API surface it needs.
2. `conftest.py` fixture: `monkeypatch.setitem(sys.modules, "vastai", fake_module)`. Teardown asserts
   `"vastai" not in sys.modules or sys.modules["vastai"] is fake_module` so a test cannot
   accidentally import the real SDK for the ones after it.
3. Destroy-guarantee tests: parameterize over the 8 failure points so adding a 9th failure mode
   later forces a case rather than being forgotten.
4. Command-parity test: capture today's argv from `render.py::run` *before* the phase 02 refactor
   into a fixture file, then assert the refactored builder reproduces it. Land this together with
   the phase 02 refactor commit.
5. Fake-remote fidelity test: `transfer.ssh` executor runs the real argv locally in a temp dir; the
   "download" is a file copy. Then diff against a direct local render. This is the highest-value
   test in the suite — it is the one that catches flag drift.
6. Live suite last, and run it exactly once manually before declaring the plan done. Record the real
   numbers in `plans/260806-1404-vastai-cloud-render/reports/`.
7. CI guard: a test that fails if `VAST_LIVE_TEST` is set while `CI` is set. Cheap insurance against
   someone adding the secret to the workflow.

## Todo

- [x] `fake_vastai.py` recording double + failure injection
- [x] `conftest.py` `sys.modules` fixture + teardown assertion
- [x] Config / onstart / ledger / reaper suites (already existed from phases 01-03, verified green)
- [x] Kit + command-parity suites (parity already landed in
      `tests/test_talking_head_render.py` with the phase 02 refactor — verified, not duplicated)
- [x] Remote destroy-guarantee suite, parameterized over 8 failure points
- [x] Batch queue + flush suites (already existed from phases 02-03, verified green)
- [x] Tool registration / refusal / cost-governance suites (registration suite already existed
      from phase 04; added the reconcile-actual/refund-on-pre-rental-failure gap it left open)
- [x] Schema suite incl. all existing fixtures re-validated (already covered by
      `tests/contracts/test_cloud_render_governance_contract.py` from phase 05 — verified, not
      duplicated)
- [x] Fake-remote fidelity test with PSNR gate
- [x] `live` marker, `-m "not live"` defaults, CI guard, `tests/live/README.md`
- [ ] Run the live suite once; record measured numbers in `reports/` — **deliberately left for a
      human**: renting a real Vast.ai instance from an unattended autonomous session, even as a
      "just verify it once" run, was explicitly ruled out for this session (locked safety decision:
      no rental without an in-the-moment human announce+approval). Run
      `VAST_LIVE_TEST=1 VAST_LIVE_MAX_USD=0.05 make cloud-render-live-test` manually.

## Success criteria

- `make test` and `make autoedit-test` pass with `vastai` **uninstalled** and network unplugged.
- Full default suite adds < 60 s to the existing runtime (no sleeps; poll intervals injectable).
- Zero network egress during the default suite (verified by the `sys.modules` fake plus a
  monkeypatched `subprocess` that raises on `ssh`/`scp` unless the fake executor is installed).
- Destroy-guarantee test passes for all 8 failure points.
- Live suite, run once manually: video produced, bytes match expectations, **zero**
  `openmontage-*` instances remain after each of the 3 tests, total spend under $0.05, and the
  measured numbers written into `reports/`.
- CI guard test fails if someone sets `VAST_LIVE_TEST` in the workflow.

## Risks

| Risk | Mitigation |
|---|---|
| Someone adds a Vast.ai secret to CI "to test the real thing" | Explicit guard test + `-m "not live"` default + README warning |
| Fake diverges from the real SDK and hides a breaking change | The live smoke is the contract test; run it whenever `vastai` is upgraded. Pin `vastai>=1.0.4,<2` in `requirements.txt` |
| Live test itself leaks an instance when it fails early | `finally` sweep by label in every live test + a session-scoped autouse fixture that sweeps after the whole live session |
| PSNR gate is flaky across x264 builds | If the first real measurement shows < 40 dB, record the measured value and set the gate just under it rather than deleting the gate |
| Tests with real sleeps make the suite slow | Poll interval and timeouts are constructor/config parameters, set to 0 in tests |

## Security

- No API key in any test file, fixture, or CI variable. The live suite reads the same
  `~/.config/vastai/vast_api_key` / `VAST_API_KEY` a human already has.
- `tests/live/README.md` states the spend and that the test uploads a tiny synthetic props file
  only — never real footage from `projects/`.

## Next

Phase 07 exposes the capability to humans (CLI, Makefile, docs) using the numbers this phase measured.

## Unresolved questions

1. Should the live suite run on a schedule (e.g. weekly, on a dedicated cheap offer) to catch Vast.ai
   API drift, accepting ~$0.05/week? Recommend no scheduled spend; run it on `vastai` upgrades only.
2. Is PSNR the right fidelity metric here, or SSIM / exact frame hash? Depends on the phase 02
   measurement — see that phase's open question.
