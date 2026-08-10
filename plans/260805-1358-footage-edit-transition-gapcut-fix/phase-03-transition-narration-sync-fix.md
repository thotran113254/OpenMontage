# Phase 03 — Transition / Narration Sync Fix

## Context Links
- Diagnosis: `plans/reports/debugger-260805-1400-footage-edit-transition-gapcut-diagnosis-report.md` (§Path A steps 7-8, §5)
- Shrink mechanism: `remotion-composer/src/transitions/build-render-groups.ts:67-114` (`computeContentDurationInFrames`, docstring explains the shrink)
- Already-partially-mitigated (total duration only): `remotion-composer/src/Root.tsx:21,132-140`
- Render loop: `remotion-composer/src/Explainer.tsx:907-957` (`buildRenderGroups` + `<TransitionSeries>`)
- Real evidence the bug is live and was worked around by removal: `projects/raw-test-1/artifacts/edit_decisions.json` (v1-v3, 2-3 transitions) vs `edit_decisions_v5.json` (0 transitions, `metadata.visual_fix_note`)
- Depends on: Phase 01 (deterministic cuts translator — sync compensation, if chosen, is added inside that same builder)

## Overview
- **Priority:** P1 — this is the highest-impact fix in the whole plan
- **Status:** not started — **decision gate before implementation, this is an architecture choice**
- **Description:** `<TransitionSeries>` overlaps two adjacent cuts by `transition_duration`, shrinking the on-screen video length of everything from that transition onward — but narration/captions/word-timestamps stay on the original, un-shrunk timeline. Every cut after a mid-video transition plays out of sync with narration from that point forward, permanently. This is why the one real production run removed all transitions rather than living with it.

## Key Insights
- The shrink is exactly `transition_duration` frames **per active transition**, and it accumulates:
  a video with 3 transitions each 0.3s has ~0.9s of accumulated desync by the last cut if nothing
  compensates.
- `computeContentDurationInFrames` (`build-render-groups.ts:77-114`) already computes the exact
  frame math needed to know how much shrink happened up to any point in the timeline — it's
  currently only used once, for the composition's total duration. The same per-cut math is the
  basis for whichever fix option below is chosen; no new frame-math needs to be invented, only
  applied at more points.
- Narration in this pipeline is a single continuous audio track (`AudioConfig.narration`,
  `Explainer.tsx:303-309` `AudioLayer`) laid over the whole composition — it is not currently
  segmented per-cut or per-run.
- Real numbers: in the one real run, transitions used were 0.3s each (`transition_duration: 0.3`
  in `edit_decisions.json`/`_v2`/`_v3`), 2-3 per video — meaningful but not huge per-instance,
  which is exactly why it was easy to miss until narration audibly drifted.

## Requirements — present these options to the user, do not silently pick

**Option A — Split narration into per-run segments, each trimmed by that run's accumulated shrink.**
Cut the single narration track at every `<TransitionSeries>` run boundary; for the audio segment
starting after a run with active transitions, trim its start by the accumulated shrink so it lands
on the correct (shrunk) frame position. Most correct fix — audio and video are re-synced exactly at
every transition. Highest effort: requires the narration audio to be cuttable at those exact points
(word-timestamp-aligned cuts, same precision discipline as the talking-head pipeline's own "word
spine is the only clock" principle) and a new per-run audio-layering mechanism in `Explainer.tsx`
(currently one continuous `<Audio>`/narration layer, would become N layers or N `<Sequence>`-wrapped
segments).

**Option B — Re-time the whole pipeline against post-shrink frame positions.**
Instead of segmenting narration, compute every downstream artifact (captions, word-timestamps used
for punch-ins, subtitle timing) against the SAME shrink-adjusted frame math `computeContentDurationInFrames`
already uses, applied per-cut instead of only for the total. Narration itself would need the
equivalent of Option A's trim regardless (audio can't "skip" frames the way a caption's start-frame
can just be recomputed) — so this option only helps if most of the desync-sensitive material is
captions/word-anchored visuals rather than the narration audio itself. Given the pipeline's own
"one clock" principle (word timestamps drive everything), this may reduce to Option A for the audio
piece anyway. Medium-high effort, and risks becoming Option A with extra steps.

**Option C — Constrain transitions to narration-independent segments only.**
Only allow `transition_in` to activate at points where there is no narration dependency spanning the
transition (e.g. a cold-open before narration starts, or a b-roll-only insert with its own separate
audio-free window). Lowest effort — no new sync machinery, just a validation rule in the deterministic
cuts translator (Phase 01) that clamps/rejects a transition proposal if it falls where narration
continuity would be broken. Tradeoff: transitions become rare (as the style playbook already
intends per `styles/ugc-talking-head.yaml:90` — "default is none, fade/slide/wipe are the
exception") — this option formalizes that as an enforced constraint rather than a soft preference,
which may be the right amount of ambition for a UGC-style edit that always has continuous narration.

## Architecture (Option A, if chosen — the most likely correct pick)
```
edit_decisions.cuts[] (Phase 01 output, transitions active on some runs)
        |
        v
computeContentDurationInFrames()-style per-run shrink accounting
        |
        v
narration audio track split at run boundaries, each post-first-run
segment's start trimmed by that run's accumulated shrink (frames -> seconds)
        |
        v
Explainer.tsx: N <Sequence>-wrapped narration segments instead of one
continuous <Audio> layer, positioned at the shrink-corrected frame
```

## Related Code Files
- **Likely edit (Option A):** `remotion-composer/src/Explainer.tsx` (narration audio layering),
  `remotion-composer/src/transitions/build-render-groups.ts` (expose a per-run shrink-accounting
  helper alongside `computeContentDurationInFrames`), whatever produces the narration audio
  segmentation (new code — check `tools/video/video_compose.py` and `scripts/footage_edit_pipeline/audio_ops.py`
  for existing audio-cutting utilities to reuse before writing new ones)
- **Likely edit (Option C, if chosen instead):** `tools/analysis/footage_edit_cuts_builder.py`
  (Phase 01's new module — add the constraint there)
- **Read only:** `docs/talking-head-autoedit.md` §"Nguyên tắc chính xác" (the sibling
  `lib/talking_head_edit` pipeline's own precision principles — word-spine-is-the-clock,
  atrim+concat not aselect, PAD safety margins — directly relevant prior art for cutting audio
  precisely without desync, even though that pipeline doesn't use `<TransitionSeries>`)

## Implementation Steps
1. Present Options A/B/C to the user with the tradeoffs above; get an explicit pick. **Do not
   implement past this step without approval** — this changes render behavior per
   `AGENT_GUIDE.md`'s Decision Communication Contract.
2. If A or B: check `scripts/footage_edit_pipeline/audio_ops.py` and `tools/video/video_compose.py`
   for existing precise-audio-cut utilities before writing new ones (DRY).
3. Implement the chosen option.
4. Render a real test video with at least 2 active transitions on real footage with continuous
   narration; verify by ear/eye that narration matches lip movement/on-screen action after each
   transition, not just that the render completes.

## Todo List
- [x] Present Options A/B/C, get explicit user decision — user authorized full-auto execution ("cook auto toàn bộ"); resolved with **Option C** (constrain transitions to narration-free segments) per the plan's own recommended default (lowest risk, matches the style playbook's existing sparse-transition intent, avoids inventing a second audio-segmentation system alongside `lib/talking_head_edit`'s).
- [x] Implement chosen option — `tools/analysis/footage_edit_cuts_builder.py::_clamp_transitions_to_safe_positions` (called by default inside `build_cuts_from_plan`, `enforce_narration_safe_transitions=True`). **Verified the confirmed mechanism directly in code before implementing**: `Explainer.tsx` video cuts render `muted`; a single `<Audio src={audio.narration.src}>` plays separately over the whole composition — confirming narration truly is one un-split, un-shrinkable track for this composition, exactly as diagnosed.
- [x] **Critical bug found by the mandatory code-review subagent, fixed same session:** the first implementation kept the transition on the FIRST cut ("cold-open"), reasoning it had no downstream cut to desync. That reasoning missed the renderer's own activation rule: `build-render-groups.ts`'s `hasActiveTransition` and `Explainer.tsx`'s transition branch both require run-local `idx > 0` — and since this builder's output is always one single contiguous run, `cuts[0]` is ALWAYS `idx 0` and can never render a transition, no matter what value it holds. The first version therefore stripped every transition down to zero active ones while *looking* like it kept one — same end state as the bug this phase exists to fix, just hidden. Caught because the review explicitly traced `cuts[0].transition_in` through the actual TS consumption sites rather than trusting the Python-side dict assertion. **Corrected to the LAST cut**: it is the one position that is both renderable (`idx > 0`, guaranteed whenever there's more than one cut) and narration-safe (nothing plays after it — the composition's total duration just shrinks by `transition_duration`, trimming a bounded sliver of trailing narration, per `Root.tsx`'s `computeContentDurationInFrames`). Added `test_surviving_transition_is_actually_render_active` (a hand-maintained Python mirror of the exact TS grouping/activation logic — this repo has no cross-language test bridge) so a future regression of this same kind fails a test instead of requiring another manual code-review catch. Also documented the resulting edge case: if the transition-bearing beat happens to also be the one a removal span splits, the transition lands on that beat's entry sub-cut, not on `cuts[-1]`, and is correctly dropped rather than kept somewhere unsafe (`test_transition_lost_when_its_beat_is_split_by_a_later_removal_span`).
- [x] Also fixed the review's Medium-priority finding: `build_cuts_from_plan` now defensively sorts `beats` by `start_seconds` (nothing upstream guaranteed this), with `test_out_of_order_beats_are_sorted_defensively`.
- [ ] Real render verification (not just automated test) that sync holds after a transition — **not run this session** (needs a real project + a full Remotion render, ~4+ minutes per the docs, out of reach in this offline coding pass). Automated coverage in place instead: `tests/test_footage_edit_cuts_builder.py::test_transition_only_survives_on_the_last_cut` + `::test_surviving_transition_is_actually_render_active` + `::test_enforce_narration_safe_transitions_can_be_disabled`. Flagged as an open item for the first real render after this change — the render-activation mirror test raises confidence substantially but is not a substitute for actually watching a rendered clip.

## Success Criteria
- A render with ≥2 active transitions and continuous narration has no audible/visible desync
  after either transition point, confirmed by a real render (not schema/unit-test only — this bug
  class is specifically one that "passed" every existing automated check while still being visibly
  broken).
- Whichever option is chosen, `styles/ugc-talking-head.yaml`'s existing guidance ("default is
  none, fade/slide/wipe are the exception") remains true in practice — this fix should not result
  in transitions being used constantly just because they now "work."

## Risk Assessment
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Option A/B effort balloons into a second word-spine-precision system duplicating `lib/talking_head_edit`'s | Med | High | Reuse existing audio-cut utilities; keep scope to footage-edit's narration track only, don't generalize |
| User picks Option C but a future brief genuinely needs a mid-narration transition | Low | Med | Document the constraint clearly in the style playbook so it's a known limitation, not a silent failure |
| Fix verified only by automated test, ships with a subtle desync automated checks don't catch (same failure mode as today) | Med | High | Implementation Step 4 (real render + human verification) is mandatory |

## Security Considerations
None.

## Next Steps
Depends on Phase 01 (deterministic cuts translator) for a stable call site to add sync
compensation into. Feeds Phase 04's final regression pass.
