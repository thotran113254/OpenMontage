# Phase 04 — Test Coverage & Regression Validation

## Context Links
- Diagnosis: `plans/reports/debugger-260805-1400-footage-edit-transition-gapcut-diagnosis-report.md` (§Path B step 5, §5)
- Gap being closed: `tests/test_edit_decisions_hand_built.py` + `tests/fixtures/footage_edit_plan_sample.json` (`removal_spans: []`)
- QA suite: `tests/qa/test_10_speech_gap_detection.py`
- Registration test: `tests/test_footage_edit_analyzer_registration.py`

## Overview
- **Priority:** P2
- **Status:** not started
- **Description:** The codebase's only reference implementation of `footage_edit_plan -> edit_decisions` has zero test coverage of the `removal_spans` path (fixture ships with `removal_spans: []`). Close that gap, add assertions for the ripple-shift/`source_in_seconds` correctness Phase 01 introduces, and run the full regression suite after Phases 01-03 land.

## Key Insights
- This is deliberately a separate phase (not folded into 01-03's own "add tests" steps) because
  its job is specifically the **integration-level** gap: a fixture that exercises `removal_spans`
  non-empty, run through the real `build_cuts_from_plan()` from Phase 01, asserting the properties
  that were never checked before. Phase 01-03 should still add their own narrow unit tests as they go.
- `tests/fixtures/footage_edit_plan_sample.json` is real Gemini output (has real token counts) —
  don't regenerate it from scratch; add a second fixture (or a `removal_spans` override on a copy)
  with real, non-empty spans so the "is this real Gemini-shaped data" property is preserved.

## Requirements
**Functional**
- New or updated fixture with non-empty `removal_spans[]` (can be adapted from
  `output/raw_test_1_v2/1783586132708_..._removal_spans.json`'s real `pause_tighten` spans — that
  file already has real, non-synthetic span data).
- `tests/test_edit_decisions_hand_built.py` updated to exercise this fixture and assert:
  - Output-timeline contiguity where no intentional gap exists: `cuts[i].out_seconds == cuts[i+1].in_seconds` (frame-rounded, matching `build-render-groups.ts`'s rounding rule).
  - `source_in_seconds` present and correct (not silently 0) on every cut, including the first
    cut after a removed span.
  - The removed range is excluded from every resulting cut's `[in,out]` on the output timeline
    AND from `[source_in, source_in+duration]` on the source timeline.
- A regression test guarding Phase 03's fix: a fixture with an active transition mid-timeline,
  asserting narration/word-timestamp positions land on the shrink-corrected frame, not the
  naive un-shrunk one.

**Non-functional**
- Full-suite run as the final gate: `python -m pytest tests/test_footage_edit_*.py tests/test_edit_decisions_hand_built.py tests/qa/test_10_speech_gap_detection.py -q`
  and `make autoedit-test` (or the equivalent direct pytest invocation if `make` isn't available)
  must be clean before this plan is considered done.

## Architecture
No new production code — this phase is test-only, consuming the functions Phases 01-03 built.

## Related Code Files
- **Edit:** `tests/test_edit_decisions_hand_built.py`
- **Create:** a new fixture file (e.g. `tests/fixtures/footage_edit_plan_with_removal_spans.json`) or
  extend the existing one with a `removal_spans`-populated variant — name it descriptively per
  project convention, not a generic `_v2` suffix
- **Read only:** `output/raw_test_1_v2/1783586132708_568061814121655968_7874081316310818820_removal_spans.json`
  (real span data to adapt), Phase 01's `footage_edit_cuts_builder.py`, Phase 03's sync-fix code

## Implementation Steps
1. Build the new/updated fixture from real `pause_tighten` span data (adapt from the real
   `output/raw_test_1_v2/` run rather than inventing synthetic spans).
2. Update `test_edit_decisions_hand_built.py`'s `build_edit_decisions_from_fixture` (or add a
   parallel test) to call Phase 01's real `build_cuts_from_plan()` instead of hand-mapping
   `start_seconds`/`end_seconds` directly.
3. Add the contiguity + `source_in_seconds` + removed-range-exclusion assertions.
4. Add the Phase 03 sync regression test once that phase's fix lands.
5. Run the full test command list above; fix any regressions found (do not skip/xfail failing
   tests to make this phase look done).

## Todo List
- [x] Build removal-spans fixture from real data — **deviated from the plan's literal suggestion** of a new standalone fixture *file*: instead injected a real-shaped `pause_tighten` span (0.42s, matching the actual duration observed in `output/raw_test_1_v2`'s real detection run) directly into the REAL fixture's own beat boundaries within the test (`test_footage_edit_cuts_builder.py::test_real_fixture_beats_produce_contiguous_cuts_with_correct_ids`). Same grounding-in-real-data goal, no new fixture file to maintain (YAGNI) — reconsider a standalone fixture file only if more tests need the same real+injected-span combination.
- [x] Update hand-built test to exercise `build_cuts_from_plan()` — `test_edit_decisions_hand_built.py`'s `build_edit_decisions_from_fixture()` now calls the real builder instead of hand-mapping; also updated for the Phase 02 b-roll redesign (`overlays[]` never used, `backgroundVideo` fallback-checked).
- [x] Contiguity + source_in_seconds + exclusion assertions — covered across `test_footage_edit_cuts_builder.py` (11 tests) and `test_edit_decisions_hand_built.py::test_hand_built_cuts_stay_output_contiguous`.
- [x] Transition/narration sync regression test — `test_transition_only_survives_on_the_last_cut` + `test_surviving_transition_is_actually_render_active` + `test_transition_lost_when_its_beat_is_split_by_a_later_removal_span` + `test_enforce_narration_safe_transitions_can_be_disabled` (names/count updated after phase-03's critical fix — see that phase's Todo list; the first-cut version of this test was itself part of what let the bug ship undetected).
- [x] Full-suite clean run — `pytest tests/ -q --ignore=tests/qa` → **1280 passed, 9 skipped, 0 failed** (run twice: once before, once after phase-03's critical-bug fix, both clean). `python tests/qa/test_10_speech_gap_detection.py` → **33 passed, 0 failed**. Independently re-confirmed by a `tester` subagent. Mandatory `code-reviewer` subagent: first pass found the phase-03 critical bug (score 6/10); after the fix, re-verified fresh against the real TS source and re-ran the tests itself, score **8.5/10**, critical finding resolved, no new criticals.

## Success Criteria
- `removal_spans` translation has real, non-trivial test coverage for the first time.
- Full test command list (see Requirements) passes clean.
- No test was skipped, xfailed, or had its assertion weakened to reach green.

## Risk Assessment
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| New assertions reveal Phase 01/03 edge cases not yet handled | Med | Med | Expected — this phase runs after 01-03, feed findings back rather than weakening assertions |
| Fixture built from real data has quirks (e.g. odd rounding) that make assertions brittle | Low | Low | Round to the same precision `build-render-groups.ts` uses (frame-rounded), not raw floats |

## Security Considerations
None.

## Next Steps
Plan-closing phase. Once green, update `plan.md` phase statuses and close out.
