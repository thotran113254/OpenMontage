"""Deterministic `footage_edit_plan` -> `edit_decisions.cuts[]` translator.

Replaces the prose-only mapping previously hand-executed by an agent per
`skills/pipelines/hybrid/edit-director.md`'s field-translation table (the
`beats[]`/`removal_spans[]` rows). Confirmed by real production evidence
(`projects/raw-test-1/artifacts/decision_log.json` decision `d-006`) that the
hand-executed version can silently produce zero usable spans and requires a
manual bypass — this module makes the beat-splitting, ripple-shift, and
`source_in_seconds` math a tested function instead.

Scope: anchor `cuts[]` timing + `transition_in`/`transition_duration` only.
Zoom (`beats[].zoom.recommended_scale_range` is Gemini free-text, not
structured) and b-roll/sfx realization stay agent-executed per
`edit-director.md` — out of scope here, not a gap this module needs to close.
"""

from __future__ import annotations

from typing import Any


def _keep_segments(
    removal_spans: list[dict[str, Any]], total_duration: float
) -> list[tuple[float, float]]:
    """Complement of (already-merged, non-overlapping) removal_spans within
    [0, total_duration] -- the source-timeline ranges that survive the cut.
    """
    ordered = sorted(removal_spans, key=lambda s: s["start_seconds"])
    segments: list[tuple[float, float]] = []
    cursor = 0.0
    for span in ordered:
        start = max(0.0, min(span["start_seconds"], total_duration))
        end = max(0.0, min(span["end_seconds"], total_duration))
        if start > cursor:
            segments.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < total_duration:
        segments.append((cursor, total_duration))
    return segments


def _split_against_keep_segments(
    beat_start: float, beat_end: float, keep_segments: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """Intersect one beat's [start, end) source range with the keep segments,
    in order -- yields the surviving sub-intervals for that beat (0, 1, or
    more than one when a removal span falls inside the beat).
    """
    parts: list[tuple[float, float]] = []
    for seg_start, seg_end in keep_segments:
        lo, hi = max(beat_start, seg_start), min(beat_end, seg_end)
        if hi > lo:
            parts.append((lo, hi))
    return parts


def _clamp_transitions_to_safe_positions(cuts: list[dict[str, Any]]) -> None:
    """Mutates `cuts` in place: strips `transition_in`/`transition_duration`
    from every cut except the LAST one.

    Why the last cut, not the first (correction -- an earlier version of this
    function kept the first cut, which is backwards): the renderer only ever
    activates a transition at run-local `idx > 0`
    (`remotion-composer/src/transitions/build-render-groups.ts`
    `hasActiveTransition`; `Explainer.tsx`'s `if (idx > 0)` guard before
    `resolveCutTransition`). Every cut this function produces is one single
    contiguous run (ripple-shift removes all gaps), so `cuts[0]` is always
    run-local `idx 0` -- its `transition_in` can therefore NEVER activate,
    no matter what value it holds. Keeping it on the first cut silently
    disabled every transition, which defeated the point of this function
    without ever surfacing an error (caught via code review, not a test --
    the prior tests only checked the Python dict, never exercised
    `buildRenderGroups`).

    The last cut IS `idx > 0` (whenever there's more than one cut) and is
    the only position where "transitions cost something, but the cost is
    bounded" holds: `<TransitionSeries>` overlaps two adjacent cuts by the
    transition's duration, shrinking the on-screen video from that point on
    -- but narration is ONE continuous, un-split audio track laid over the
    whole timeline (Explainer.tsx: video cuts render `muted`, a single
    `<Audio src={audio.narration.src}>` plays separately, never re-cut to
    match). A transition on any OTHER cut desyncs every cut after it from
    narration, permanently and accumulating -- confirmed by real production
    evidence: a prior render used 2-3 mid-video transitions, then removed
    all of them specifically to fix this (see
    `projects/raw-test-1/artifacts/edit_decisions_v5.json`
    `metadata.visual_fix_note`). A transition entering the LAST cut only
    shrinks the tail of the composition by `transition_duration` -- the
    render ends that much earlier (`Root.tsx`'s `computeContentDurationInFrames`
    already accounts for this), trimming a bounded, one-time sliver of
    trailing narration instead of desyncing anything before it, since
    nothing plays after the last cut to go out of sync. Splitting narration
    into per-run segments so transitions can be used anywhere mid-video is a
    larger, separate fix (tracked in the plan, not implemented here).
    """
    for cut in cuts[:-1]:
        cut.pop("transition_in", None)
        cut.pop("transition_duration", None)


def build_cuts_from_plan(
    beats: list[dict[str, Any]],
    removal_spans: list[dict[str, Any]],
    source: str,
    fps: int = 30,
    splice_scale_bump: float = 0.07,
    enforce_narration_safe_transitions: bool = True,
) -> list[dict[str, Any]]:
    """Build `edit_decisions.cuts[]` (primary/anchor layer) from
    `footage_edit_plan.beats[]`, ripple-shifting past any `removal_spans[]`.

    Every cut's `in_seconds`/`out_seconds` is the OUTPUT-timeline position
    (contiguous, no gaps left by removed spans); `source_in_seconds` is the
    original, un-shifted position in the source file. A beat that a removal
    span falls inside splits into `beatNN_s1`/`beatNN_s2`/... sub-cuts (same
    id convention already used by hand in `edit_decisions_v5.json`); every
    sub-cut after the first on the same beat gets a small static punch-in
    (`transform.scale`) to visually mask the splice point, and never repeats
    the beat's `transition_in` (that belongs to the beat's own entry, not an
    internal pause-tighten seam).

    `fps` only affects rounding precision for downstream contiguity checks
    (`Math.round(seconds*fps)` in `remotion-composer/src/transitions/
    build-render-groups.ts`) -- the ripple-shift itself is exact by
    construction (each cut's out_seconds becomes the next cut's in_seconds).

    `enforce_narration_safe_transitions` (default True): strip
    `transition_in` from every cut except the LAST one -- see
    `_clamp_transitions_to_safe_positions` for why (the first cut can never
    render a transition at all; the last cut is the only render-active
    position that doesn't desync any downstream cut). Only disable this
    once narration is split per-transition-run (not implemented) or the
    render genuinely has no continuous narration track to desync.

    `beats` is assumed sorted by `start_seconds` ascending -- nothing
    upstream (`footage_edit_prompt.validate_and_clamp_beats`) currently
    guarantees this, so it is re-sorted defensively here rather than trusted.
    """
    if not beats:
        return []

    beats = sorted(beats, key=lambda b: b["start_seconds"])
    total_duration = beats[-1]["end_seconds"]
    keep_segments = _keep_segments(removal_spans, total_duration)

    cuts: list[dict[str, Any]] = []
    output_cursor = 0.0

    for beat in beats:
        beat_start = float(beat["start_seconds"])
        beat_end = float(beat["end_seconds"])
        sub_intervals = _split_against_keep_segments(beat_start, beat_end, keep_segments)
        if not sub_intervals:
            continue

        # beat_index is 1-based in real Gemini output (verified against
        # output/raw_test_1_v2/footage_edit_plan.json and the fixture
        # sample) -- used directly, no off-by-one offset.
        beat_id = f"beat{int(beat['beat_index']):02d}"
        transition = beat.get("transition") or {}
        transition_type = transition.get("type", "none")

        for idx, (src_start, src_end) in enumerate(sub_intervals):
            duration = src_end - src_start
            cut: dict[str, Any] = {
                "id": beat_id if len(sub_intervals) == 1 else f"{beat_id}_s{idx + 1}",
                "source": source,
                "in_seconds": round(output_cursor, 3),
                "out_seconds": round(output_cursor + duration, 3),
                "source_in_seconds": round(src_start, 3),
            }
            if idx == 0 and transition_type and transition_type != "none":
                cut["transition_in"] = transition_type
                cut["transition_duration"] = 0.3
            if idx > 0:
                cut["transform"] = {"scale": round(1.0 + splice_scale_bump, 3)}
            cuts.append(cut)
            output_cursor += duration

    if enforce_narration_safe_transitions:
        _clamp_transitions_to_safe_positions(cuts)

    return cuts
