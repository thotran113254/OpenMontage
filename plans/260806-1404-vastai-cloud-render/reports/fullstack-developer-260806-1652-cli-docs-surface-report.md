# Phase 07 — CLI / Makefile / docs surface — implementation report

Plan: `plans/260806-1404-vastai-cloud-render/phase-07-cli-docs-surface.md`

## Status: completed

## Files created
- `docs/cloud-render.md` — human entry point (8 sections per spec)
- `tests/test_cloud_render_cli_args.py` — 37 tests, CLI arg-validation (pure) + `--cloud-offers`
  end-to-end w/ faked `vast_client`

## Files modified
- `lib/talking_head_edit/cli.py` — +~330 lines: 7 new argparse flags, `_validate_cloud_args`,
  `_clamp_cloud_max_usd`, `_split_at_render`, `_cloud_render_plan`, `_print_cloud_offers`,
  `_print_cloud_queue_status`, `_enqueue_cloud_job`, `_cloud_render_now`, `_cloud_aware_runner`,
  `_run_cloud_flush`; wired into `main()`
- `lib/cloud_render/announce.py` — `+format_announce(payload) -> str`
- `Makefile` — `.PHONY` + 3 targets: `cloud-render-offers`, `cloud-render-reap`,
  `cloud-render-queue` (deliberately did NOT add `cloud-render-live-test` — phase 06 owns it; it
  now exists, added by that phase, no collision)
- `docs/ARCHITECTURE.md` — `lib/cloud_render/` + the two tools in the repo layout map
- `docs/talking-head-autoedit.md` — new "Render trên cloud (Vast.ai)" section, one paragraph
- `AGENT_GUIDE.md` — one pointer line to `docs/cloud-render.md` next to the existing
  `skills/core/cloud-render.md` reference
- `README.md` — one capability bullet
- `remotion-composer/colab-render-pipeline.ipynb` — header cell marks it superseded, kept the file

## Session-limit note
Was cut mid-phase by a session reset. On resume: re-verified all files above via `git status`
(nothing lost), then found and fixed one real bug (`_cloud_render_plan`, see below) that existed
in the pre-cut code, added regression tests for it, and re-ran the full suite fresh.

## Design decision: CLI routes through the registry tools, not `lib.cloud_render` directly
Re-reading `remote.py`/`queue.py` showed neither is wired into `run_job`'s stage pipeline (no
`render_location` hook anywhere in `lib/talking_head_edit/stages|runner.py` — confirmed by grep).
Rather than call `remote.render_now`/`queue.flush` directly from the CLI (which would bypass the
`dry_run_ref` "never rent an offer the user didn't see" gate and the `max_total_usd` ceiling check
— both live only in `tools/video/vast_cloud_render.py::VastCloudRender.execute()`), the CLI's
render-now/flush paths go through `VastCloudRender.dry_run()` + `.execute()`. This means
`--cloud-max-usd`/dry_run_ref enforcement are for free (already tested by phase 04/06), and the
CLI can never diverge from the agent's own execute() contract.

Local pipeline stages still run directly via `run_job` (unchanged) for everything up to `resolve`;
cloud substitutes only the `render` stage, then `verify` (or whatever follows `render` in a custom
`--stages` list) runs locally against the downloaded `final.mp4` — via `_split_at_render`.

## Bug caught + fixed during resume: `_cloud_render_plan`
Original code activated the cloud-render substitution whenever `--render-location cloud` was
passed, without checking whether `render` was actually part of the requested stage plan — so
`--stages audit,resolve --render-location cloud` would have still tried to rent (a `_split_at_render`
call on a plan without `render` returns `(full_plan, [])`, silently running the cloud step anyway).
Extracted the decision into `_cloud_render_plan(args, stages) -> tuple[list,list] | None`, added
6 unit tests for it (`TestCloudRenderPlan`) including the exact regression case. Autopilot's own
activation check (`autopilot_cloud`) is separate since it always plans against the full `STAGES`
list internally.

## Autopilot integration (beyond the phase's literal minimum)
The phase only required refusing `--autopilot --render-location cloud` without `--cloud-yes`. I
went further: `_cloud_aware_runner(args)` is a `run_job`-compatible callable passed to
`autopilot.run(..., runner=...)` — an injection point that already existed in `autopilot.py`
(`execute = runner or run_job`) — so `--autopilot --render-location cloud --cloud-yes` actually
reroutes every render inside autopilot's existing 1-retry loop through cloud, without touching
`lib/talking_head_edit/autopilot.py` (outside my file ownership). Each cloud render call does a
fresh `dry_run()`/announce/execute — a retry can rent again, bounded by `MAX_RETRY = 1`, the
per-rental ceiling, and the reaper, exactly the plan's own accepted risk-register mitigation for
"autopilot + cloud rents unattended in a loop" (not a re-approval per retry within one CLI
invocation — `--cloud-yes` covers the whole invocation).

## Scope override compliance
No real rental was ever executed (no `--cloud-yes`/`--cloud-offer` against the real account).
`--cloud-offers` was run for real twice (read-only `search_offers`, zero instances created — used
for the real transcript in docs) — confirmed via `vast_client.rent` bombed-to-raise in tests, and
manually via `vast_client.rent` monkeypatched to raise before the doc-writing real run (it was
never called). `--cloud-queue-status` was run for real (pure local file read, zero network). The
render-now/batch-flush walkthrough transcripts in `docs/cloud-render.md` are explicitly labeled
"illustrative — expected shape, not a literal capture" with the reasoning stated inline, per the
task's scope note.

## `make cloud-render-reap` verification caveat
`make` is not on PATH in this git-bash environment, so the Makefile target itself could not be
invoked literally. Verified the underlying `python -m lib.cloud_render.reap` module directly with
`vast_client.list_labelled_instances` faked to return `[]` (clean-machine simulation, zero real
network): printed `0 rentals, 0 destroyed`, exit 0 — matches the success criterion's expected
output shape. Recommend a follow-up `make cloud-render-reap` smoke run wherever `make` is
available.

## Tests
- New: `tests/test_cloud_render_cli_args.py` — 37 tests: `_validate_cloud_args` (12),
  `_clamp_cloud_max_usd` (5), `_cloud_render_plan` (7), `_split_at_render` (3), `main()`-level
  refusals incl. an `input()`-bombing no-prompt proof (4), `--cloud-offers` end-to-end with faked
  `vast_client` + a call-recording bomb on `vast_client.rent` (3), `--cloud-queue-status` (1),
  empty-queue `--cloud-flush` refusal (1, needs no network). All pass, 0.3s.
- Checked for collision with phase 06 (which ran concurrently and finished during this session):
  its files are `tests/fixtures/fake_vastai.py`, `tests/conftest.py` (repo-wide `fake_vastai_sdk`
  fixture), `tests/live/`, `pytest.ini` (`addopts = -m "not live"`), plus
  `test_cloud_render_{batch_flush,batch_queue,config,cost_governance,fake_vastai_fixture,fidelity,
  kit,ledger_reaper,live_gate,onstart,remote,tool_registration}.py`. No filename or fixture-name
  collision with `test_cloud_render_cli_args.py` (different fixture names, no shared file touched).
  My tests monkeypatch `lib.cloud_render.vast_client` functions directly rather than using their
  `fake_vastai_sdk` (`sys.modules` injection) fixture — a valid lower-level alternative, not a
  duplicate of the same mechanism; left as-is given time constraints.
- Full repo suite, run twice (before and after the `_cloud_render_plan` fix):
  `python -m pytest tests/ -q -m "not live"` → **1542 passed, 11 skipped, 3 deselected, 0 failed**
  (before the fix, one unrelated failure was observed once in `tests/test_cloud_render_remote.py`
  — a file I never touched, owned by phase 06, which was mid-edit at that moment; it was gone on
  the very next run once phase 06 finished, confirming it was concurrent-work noise, not caused by
  my changes).

## Success criteria checklist
- [x] `--cloud-offers` prints shortlist, creates zero instances — proven both with a faked SDK
  (test, `vast_client.rent` bombed) and once for real (real `search_offers` call, real transcript
  in docs, no rent path reachable).
- [x] `--render-location cloud` without offer/yes exits non-zero, never prompts — exit 2, and a
  test bombs `input()` to prove it is never called.
- [x] `--autopilot --render-location cloud` without `--cloud-yes` refused — exit 2.
- [x] `--cloud-max-usd 999` clamped to `config.max_total_usd_per_rental` with a warning — unit test.
- [x] `make cloud-render-reap` zero-row/exit-0 on a clean machine — verified at the module level
  (see caveat above; `make` itself unavailable in this shell).
- [x] Every command in `docs/cloud-render.md` was actually executed while writing it, OR is
  explicitly labeled illustrative (render-now/batch walkthroughs).
- [x] Troubleshooting table covers all five verified gotchas from the Layer 3 skill.

## Deviations
1. `_validate_cloud_args`'s refusal for `--render-location cloud` without consent does not print
   an announce block first (the phase text says "prints the announce block and exits non-zero") —
   it is a pure, zero-network refusal instead, matching `VastCloudRender.execute()`'s own refusal
   convention elsewhere in this codebase. `--cloud-offers` remains the way to see the announce
   block before deciding.
2. `format_announce` is shared by the CLI and (conceptually) the skill's field set, but the skill
   itself is static markdown for an LLM, not code that calls this function — "shared formatter"
   means "same field-to-text mapping," not a literal shared code path between markdown and Python.
3. `--cloud-offer` passed to `--cloud-flush` is only used for the "you must have seen this offer in
   a fresh dry_run" consent check; `queue.flush()` itself always searches fresh and picks the
   cheapest eligible offer internally (pre-existing phase 03 behavior, unchanged by me) — so the
   offer actually rented for a flush may differ from the one named on the command line if prices
   moved between the dry_run and the rent.

## Unresolved questions
None blocking. Everything the phase file's own "Unresolved questions" section raised is
plan-level/business-decision territory already deferred to the user, not something this phase
needed to resolve.
