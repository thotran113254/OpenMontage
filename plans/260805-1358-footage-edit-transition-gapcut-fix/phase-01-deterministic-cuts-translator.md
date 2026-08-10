# Phase 01 — Deterministic Cuts Translator

## Context Links
- Diagnosis: `plans/reports/debugger-260805-1400-footage-edit-transition-gapcut-diagnosis-report.md` (§Path B, §5)
- Prose mapping being replaced: `skills/pipelines/hybrid/edit-director.md:60-64`
- Real production evidence of manual bypass: `projects/raw-test-1/artifacts/decision_log.json` decision `d-006`
- Real Gemini output that failed: `output/raw_test_1_v2/footage_edit_plan.json` (`removal_spans: []`)
- Schema: `schemas/artifacts/footage_edit_plan.schema.json`, `schemas/artifacts/edit_decisions.schema.json`
- In-flight WIP to finish: `tools/analysis/speech_gap_detector.py`, `tools/analysis/speech_gap_spans.py` (uncommitted — run `git diff` on both before editing)
- Reference test with the coverage gap: `tests/test_edit_decisions_hand_built.py` (fixture `tests/fixtures/footage_edit_plan_sample.json` has `removal_spans: []`)

## Overview
- **Priority:** P1
- **Status:** not started
- **Description:** Replace the prose-only `footage_edit_plan.beats[] + removal_spans[] -> edit_decisions.cuts[]` translation with a real, unit-tested Python function. Finish wiring the in-flight `pause_tighten` productization so gap-cutting no longer requires a manual decision-log bypass like `d-006`.

## Key Insights
- The translation currently exists only as instructions in `edit-director.md` that an LLM agent
  executes by hand every render. It worked correctly once, by care (`edit_decisions_v5.json` has
  correct `source_in_seconds` on every cut), but there is no code guarantee and no test coverage.
- Gemini's own `removal_spans` detection returned empty (`footage_edit_plan.json`) on the one real
  run — do not build this function assuming Gemini spans will be reliable. Treat `pause_tighten`
  (ffmpeg `silencedetect`-based, already partially wired) as the primary removal-span source;
  Gemini `disfluency_removal_spans` / lexicon `filler_spans` / `dead_air_spans` are secondary,
  additive signals, not the only path.
- `compute_pause_tighten_spans` / `silence_ranges_via_ffmpeg` already exist in uncommitted WIP
  (`speech_gap_spans.py`, `speech_gap_detector.py`) — this phase finishes wiring them into
  `SpeechGapDetector.execute`'s `removal_spans` output (already partially done per the diff) and
  makes sure `footage_edit_artifact.py` actually consumes `speech_gap_detector`'s output as the
  primary removal-span source for `footage_edit_plan.removal_spans`, not just Gemini's.
- Real numbers to validate against: `d-006`'s manual pass removed 3.97s / 93.4s (4.2%) across 16
  splice points on the reference footage. A correct automated run on the same footage should land
  in that neighborhood — use it as a sanity check, not an exact target (different footage will differ).
- Ripple-shift requirement (not previously implemented anywhere): once a span is removed, every
  cut whose `in_seconds`/`out_seconds` falls after it must shift left by the removed duration so
  cuts stay frame-contiguous on the OUTPUT timeline (`Math.round(prev.out_seconds*fps) === Math.round(cur.in_seconds*fps)`,
  matching `build-render-groups.ts:23-27`'s contiguity check). `source_in_seconds` must instead
  track the ORIGINAL (unshifted) source position so the correct footage still plays.

## Requirements
**Functional**
- New function (Python), e.g. `build_cuts_from_plan(beats, removal_spans, fps) -> list[Cut]` in a
  new module (see Architecture below for placement) that:
  1. Sorts and merges `removal_spans` (reuse existing `merge_spans` from `speech_gap_spans.py`).
  2. Walks `beats[]` in order, splitting any beat that a removal span falls inside into two
     sub-cuts (mirroring the real `beat02_s1`/`beat02_s2` splice pattern already seen in
     `edit_decisions_v5.json`).
  3. Computes each cut's OUTPUT `in_seconds`/`out_seconds` by ripple-shifting left by the total
     duration removed so far, frame-rounded so adjacent cuts are contiguous.
  4. Sets `source_in_seconds` = the cut's original (pre-shift) source position — never 0, never
     copied from output `in_seconds` (the bug the hand-built test would currently produce if
     naively generalized).
  5. Carries `transition.type`/`reason` from the beat straight to `transition_in`/`transition_duration`
     unchanged (no bug found here per the diagnosis report — inventory-clamp already works).
- Finish `SpeechGapDetector`/`footage_edit_analyzer` wiring so `footage_edit_plan.removal_spans`
  is populated from `speech_gap_detector`'s merged spans (dead_air + filler + pause_tighten) by
  default, with Gemini's own `disfluency_removal_spans` merged in as an additive, higher-bar signal
  (existing `min_disfluency_confidence=0.75`), not the sole source.
- Update `skills/pipelines/hybrid/edit-director.md:60-64` to point at calling the new function
  instead of describing the mapping in prose (keep a short human-readable summary for agents who
  need to understand *why*, but the mapping itself is no longer hand-executed).

**Non-functional**
- Keep the new module under ~200 lines per project convention; if `footage_edit_artifact.py`
  (currently 193 lines) would cross ~200 after wiring, extract the new translator into its own
  module (e.g. `tools/analysis/footage_edit_cuts_builder.py`) rather than growing it further.
- No code comments referencing "Bug 1"/"Bug 2"/"d-006" — describe the invariant (ripple-shift
  keeps output contiguous, source_in_seconds tracks original position) not the finding's origin.

## Architecture
```
footage_edit_plan.beats[] ----+
footage_edit_plan.removal_spans[] (Gemini, additive) --+--> merge_spans() --+
speech_gap_detector output (dead_air+filler+pause_tighten, primary) -------+          |
                                                                                       v
                                                                     build_cuts_from_plan()
                                                                                       |
                                                                                       v
                                                                        edit_decisions.cuts[]
                                                            (output-contiguous, source_in_seconds set)
```
Placement decision: new function lives in a new `tools/analysis/footage_edit_cuts_builder.py`
(pure, no I/O, unit-testable with plain dicts) rather than inside `footage_edit_artifact.py`,
which stays focused on assembling the `footage_edit_plan` artifact itself.

## Related Code Files
- **Create:** `tools/analysis/footage_edit_cuts_builder.py`
- **Edit:** `tools/analysis/footage_edit_artifact.py` (call the new builder; wire speech_gap
  spans as primary removal-span source), `tools/analysis/speech_gap_detector.py` +
  `speech_gap_spans.py` (finish uncommitted WIP — verify `tighten_pauses` output actually flows
  through to `removal_spans` returned by `execute()`), `skills/pipelines/hybrid/edit-director.md`
- **Read only:** `schemas/artifacts/footage_edit_plan.schema.json`, `schemas/artifacts/edit_decisions.schema.json`,
  `remotion-composer/src/transitions/build-render-groups.ts` (contiguity contract to match)

## Implementation Steps
1. `git diff -- tools/analysis/speech_gap_detector.py tools/analysis/speech_gap_spans.py` — confirm
   current WIP state, finish any incomplete wiring (e.g. confirm `pause_tighten` spans reach the
   `removal_spans` list `SpeechGapDetector.execute` returns, per the diff's `merge_spans(dead_air_spans + filler_spans + pause_spans)` line).
2. Write `build_cuts_from_plan()` in the new module with the ripple-shift + `source_in_seconds`
   logic described above. Pure function, dict in/dict out, no file I/O.
3. Wire `footage_edit_artifact.py` to call `speech_gap_detector` for the primary removal-span
   source and merge Gemini's `disfluency_removal_spans` in additively.
4. Wire whatever currently calls the edit-director prose step (likely nothing in code — this is
   the new call site) to invoke `build_cuts_from_plan()` for the anchor/primary cuts, before any
   b-roll/card cuts from Phase 02 are layered in.
5. Update `edit-director.md` to describe the new deterministic step, keeping the field-level
   "why" documentation but removing the manual-execution instructions for this specific mapping.
6. Add unit tests (can live here or be deferred to Phase 04 — see that phase for the
   non-empty-`removal_spans` fixture fix) asserting: output contiguity, correct `source_in_seconds`,
   correct beat-splitting at a removal span.

## Todo List
- [x] Confirm/finish uncommitted `speech_gap_detector.py`/`speech_gap_spans.py` WIP — confirmed already functionally complete (pause_tighten fully wired into `execute()`'s merged `removal_spans`); it had zero test coverage, so added it (`tests/qa/test_10_speech_gap_detection.py` Test 7, 5 new checks, 33/33 passing) rather than re-implementing.
- [x] Write `footage_edit_cuts_builder.py` with ripple-shift + `source_in_seconds` logic — done. Also caught and fixed a real off-by-one bug during implementation: `beat_index` is 1-based in real Gemini output (verified against `output/raw_test_1_v2/footage_edit_plan.json` and the fixture sample), not 0-based as the schema's `minimum: 0` implied — an earlier `+1` in the id formula would have mislabeled every cut id by one.
- [x] Wire `footage_edit_artifact.py` to use speech_gap spans as primary removal source — was already correct in the existing `build_artifact()`/`load_removal_spans()` code (accepts speech_gap_detector's output as the primary `removal_spans` param, merges Gemini disfluency spans additively); no change needed beyond confirming it.
- [x] Update `edit-director.md` field-mapping section — now instructs calling `build_cuts_from_plan()` directly instead of hand-translating; documents the narration-safe transition clamp (see phase-03) inline since it's part of the same function's contract.
- [x] Unit tests for ripple-shift + source_in_seconds + beat-splitting — 11 tests in `tests/test_footage_edit_cuts_builder.py`, including one integration test against the real fixture data.

## Success Criteria
- Running the new builder against `output/raw_test_1_v2/footage_edit_plan.json` (or an equivalent
  fixture) with a non-empty removal_spans list produces `cuts[]` that are frame-contiguous on the
  output timeline and have correct `source_in_seconds` on every cut — verifiable without needing
  Gemini to return anything, since `pause_tighten` (ffmpeg-based) is deterministic.
- `python -m pytest tests/test_footage_edit_analyzer_registration.py tests/qa/test_10_speech_gap_detection.py -q` passes.

## Risk Assessment
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Ripple-shift math drifts from frame-rounding used by `build-render-groups.ts` | Med | High | Match `Math.round(seconds*fps)` exactly; add a cross-check test comparing Python-computed contiguity against the same rounding rule |
| Beat-splitting at a removal span breaks existing card/caption logic tied to whole beats | Med | Med | Reuse the exact splice pattern already proven in `edit_decisions_v5.json` (`beat02_s1`/`beat02_s2`, `cardContinuation`) |
| Speech-gap primary-source switch changes behavior for footage that previously relied on Gemini's spans | Low | Med | Gemini's spans stay additive/merged, not removed as an input |

## Security Considerations
None — no new external I/O beyond existing ffmpeg/transcript calls already in use.

## Next Steps
Unblocks Phase 03 (transition/narration sync fix needs a deterministic cut-building call site to
add sync compensation into) and Phase 04 (test coverage).
