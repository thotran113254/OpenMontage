# Code Review — footage-edit transition/gap-cut fix (plan 260805-1358)

Score: 6/10 — deterministic cuts-translator + b-roll-matching work is solid and well-tested,
but Phase 03's transition fix does not achieve its stated goal: it is internally consistent
with itself and its own docs, but the renderer never activates a transition on the one cut
position the fix preserves, so transitions remain permanently disabled post-fix, same
end-state as the pre-fix workaround this plan set out to undo. Not a desync/safety
regression — a silent no-op regression against the plan's stated deliverable.

## Critical

1. **`_clamp_transitions_to_safe_positions` preserves `transition_in` on exactly the one cut
   the renderer can never apply it to — net effect: zero transitions ever render.**
   `tools/analysis/footage_edit_cuts_builder.py:57-80` strips `transition_in`/`transition_duration`
   from every cut except `cuts[0]` (the global first cut). But `transition_in` on a cut is only
   ever consumed to build a transition *entering that cut from its predecessor*:
   - `remotion-composer/src/transitions/build-render-groups.ts:49-51` (`hasActiveTransition`)
     only counts a cut as "active" when `idx > 0` within its run.
   - `remotion-composer/src/Explainer.tsx:928` gates `resolveCutTransition(...)` behind
     `if (idx > 0)` inside the `TransitionSeries` render loop.
   `cuts[0]` is, by construction, always local `idx 0` of whatever contiguous run it belongs
   to (it is the first cut in the whole timeline) — so its `transition_in` can never satisfy
   `idx > 0` in either check. The run it's in either falls through to `hasActiveTransition ===
   false` (rendered "flat", no `TransitionSeries` at all) or, even if grouped, `resolveCutTransition`
   is never called for it. Verified directly against the render loop, not inferred — there is no
   other code path in `Explainer.tsx` that reads `cuts[0].transition_in` (grepped for
   `cuts[0]`/`from-black`/similar, only the two `buildRenderGroups` call sites exist).
   Consequence: after this fix, **every** `edit_decisions.cuts[]` produced by
   `build_cuts_from_plan()` (default `enforce_narration_safe_transitions=True`) renders with zero
   active transitions — functionally identical to the pre-fix `edit_decisions_v5.json` workaround
   state the plan's own "Why this plan exists" section says it's replacing, just silent now
   instead of carrying an explicit `visual_fix_note`.
   - Not caught by the new test suite because `tests/test_footage_edit_cuts_builder.py::
     test_transition_only_survives_on_the_first_cut` (lines 140-152) only asserts the Python-side
     dict still has `transition_in` on `cuts[0]` — it never exercises `buildRenderGroups`/
     `Explainer.tsx`, so it can't see that this value is inert.
   - The plan itself (`phase-03-transition-narration-sync-fix.md` Todo list, 3rd item) flags "real
     render verification ... not run this session" as an open risk for exactly this bug class
     ("passed every existing automated check while still being visibly broken") — this review
     confirms that risk actually materialized, it's not just an open question.
   - **Fix direction (not a mandate, needs the same user decision-gate Phase 03 called for):** the
     only cut position where a transition can ever activate is `idx > 0` inside a contiguous run,
     i.e. cuts[1], cuts[2], etc. — the exact opposite of what's currently preserved. But per the
     phase's own diagnosis, a transition at any `idx > 0` position mid-timeline is precisely what
     causes the narration desync this plan is trying to avoid. Taken together, this suggests Option
     C as scoped ("only the very first cut may carry a transition") has **no safe non-empty
     solution** in the current one-track-narration architecture — every position that could
     mechanically render a transition is also a position proven to desync narration, and the one
     "safe" position (index 0) cannot mechanically render one. This is worth surfacing back to the
     user as a finding, not silently patched: Option C may need to be re-scoped to "transitions
     disabled entirely for now" (explicit, documented) rather than "restricted to the first cut"
     (implies capability that doesn't exist).

## High

2. **No test exercises the actual render-group formation for the new builder's output.** All
   coverage for the transition-safety property is Python-side (`transition_in` present/absent on a
   dict). Given `remotion-composer` is TypeScript and there's no cross-language test, this class of
   bug (data says one thing, renderer does another) will keep passing CI. Suggest at minimum a
   comment/TODO in `build-render-groups.ts` or a fixture-driven assertion (even a hand-traced one in
   the phase file) confirming a `build_cuts_from_plan()` output with `enforce_narration_safe_transitions=True`
   actually produces ≥1 `TransitionSeries` group when fed through `buildRenderGroups` — this would
   have caught finding #1 immediately.

## Medium

3. **`build_cuts_from_plan` assumes `beats[]` arrives pre-sorted by timeline position** —
   `tools/analysis/footage_edit_cuts_builder.py:118` (`total_duration = beats[-1]["end_seconds"]`)
   and the main loop process beats in list order with no sort. `validate_and_clamp_beats`
   (`tools/analysis/footage_edit_prompt.py:228-267`, the only upstream pass over `beats[]`) does not
   sort either — it only clamps `transition.type`/`sound_effect.type`. Real Gemini output has been
   observed in-order so far, but nothing in code enforces it, and this module is exactly the place
   that's supposed to make this translation deterministic and defensive rather than "worked in the
   one sample we saw." Cheap fix: `beats = sorted(beats, key=lambda b: b["start_seconds"])` at the
   top of `build_cuts_from_plan`, plus a test for out-of-order input.
4. **Pre-existing, unrelated WIP is mixed into the diff of files this session touched** —
   `tools/analysis/footage_edit_artifact.py`, `footage_edit_analyzer.py`, `footage_edit_prompt.py`,
   `footage_edit_schema.py` all carry a second, unrelated feature (disfluency-span detection,
   `user_style_profile` prompt injection, real-duration coverage-gap check) dated **2026-07-09**
   (`tools/analysis/footage_edit_disfluency_prompt.py` mtime, untracked) — i.e. it predates this
   session (2026-08-05/06) and is not part of this plan's 4 phases. It is internally consistent
   (schema/artifact/prompt all agree on shape) as far as reviewed, but it will ship together with
   this plan's changes if these files are committed as-is, and its own correctness was not in this
   review's scope per the session brief. Flagging so it's a conscious inclusion, not an accidental
   one, when this branch is committed/PR'd.

## Verified correct (no issue — recording so it isn't re-litigated)

- **Off-by-one**: `beat_id = f"beat{int(beat['beat_index']):02d}"` — `beat_index` is 1-based in real
  Gemini output (confirmed against `tests/fixtures/footage_edit_plan_sample.json`), used directly
  with no `+1`/`-1` offset. Correct.
- **Ripple-shift / splitting math**: `_keep_segments` + `_split_against_keep_segments` in
  `footage_edit_cuts_builder.py:22-54` correctly handle overlapping/nested removal spans via
  `cursor = max(cursor, end)` even without an explicit pre-merge step (traced by hand for a
  contained-span case). `source_in_seconds` vs `in_seconds` divergence is preserved correctly
  across ripple shifts and full-beat removal (verified against
  `tests/test_footage_edit_cuts_builder.py`'s 11 cases, all of which are correct as written).
- **`match_broll_to_beats` determinism**: operates over `list`s throughout (`beats`,
  `broll_candidates`, the `scored` accumulator), never a `dict`/`set` for anything that affects
  iteration order before the final stable `sort()` — tie-breaks are reproducible given
  reproducible input order. No dict-iteration-order risk, contrary to what the review brief flagged
  as a thing to check.
- **Backward compatibility**: `build_artifact()` (`footage_edit_artifact.py:92`) is keyword-only
  (`*,` first param) — new params (`real_duration_seconds`, `min_disfluency_confidence`,
  `broll_candidates`) are additive and default-safe, no positional-call breakage possible.
  `INPUT_SCHEMA`'s new `broll_sources`/`min_disfluency_confidence` fields and both schema JSON
  files' new properties are additive with no top-level `additionalProperties: false` blocking them
  (`schemas/artifacts/footage_edit_plan.schema.json`, `edit_decisions.schema.json` both parse and
  were checked structurally). `beat_{idx}` vs `beat{NN}`/`beat{NN}_sK` id-format concern from the
  brief: confirmed no other code in the repo constructs or matches against the old `beat_{idx}`
  format (`grep` for `build_cuts_from_plan`/`match_broll_to_beats` call sites found only the new
  module, its own tests, and `tests/test_edit_decisions_hand_built.py`, which was updated in this
  session to use the new id format).

## Side-effect escalation gate

No destructive or irreversible operation in the reviewed diff (no file deletion, no schema field
removal, no data migration). Schema/artifact changes are additive. Does not require the
no-side-effects escalation gate.

## Unresolved questions
- Should Phase 03 be re-opened with the user given finding #1 (Option C as implemented cannot
  render any transition at all, not just "rare" ones)? Recommend surfacing before the "real render
  verification" step in the phase's own Todo list is attempted, since it will fail to show any
  transition regardless of footage.
- Is the pre-existing disfluency/user-style/duration-coverage WIP (finding #4) intended to ship in
  the same commit/PR as this plan's changes, or should it be split out?
