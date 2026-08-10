"""Unit tests for the deterministic footage_edit_plan -> edit_decisions.cuts[]
translator (tools/analysis/footage_edit_cuts_builder.py).

Covers exactly the gap the diagnosis found zero coverage for: ripple-shift
across a removed span, source_in_seconds correctness, and beat-splitting --
none of which `tests/test_edit_decisions_hand_built.py` previously exercised
(its fixture shipped with `removal_spans: []`).

`beat_index` is 1-based here, matching real Gemini output (verified against
`output/raw_test_1_v2/footage_edit_plan.json` and
`tests/fixtures/footage_edit_plan_sample.json`, both start at 1).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.analysis.footage_edit_cuts_builder import build_cuts_from_plan

SOURCE = "raw-test/source.mp4"


def _beat(index: int, start: float, end: float, transition: str = "none") -> dict:
    return {
        "beat_index": index,
        "start_seconds": start,
        "end_seconds": end,
        "transition": {"type": transition, "reason": "test"},
    }


def test_no_removal_spans_is_identity_mapping():
    beats = [_beat(1, 0.0, 5.0), _beat(2, 5.0, 10.0)]
    cuts = build_cuts_from_plan(beats, [], SOURCE)

    assert [c["id"] for c in cuts] == ["beat01", "beat02"]
    for cut, beat in zip(cuts, beats):
        assert cut["in_seconds"] == beat["start_seconds"]
        assert cut["out_seconds"] == beat["end_seconds"]
        assert cut["source_in_seconds"] == beat["start_seconds"]
    # No gap between consecutive cuts on the output timeline.
    assert cuts[0]["out_seconds"] == cuts[1]["in_seconds"]


def test_removal_span_inside_a_beat_splits_it():
    beats = [_beat(1, 0.0, 5.0), _beat(2, 5.0, 10.0)]
    removal_spans = [{"start_seconds": 2.0, "end_seconds": 2.5, "kind": "pause_tighten"}]

    cuts = build_cuts_from_plan(beats, removal_spans, SOURCE)

    assert [c["id"] for c in cuts] == ["beat01_s1", "beat01_s2", "beat02"]
    s1, s2, beat02 = cuts

    # Sub-cuts carry the ORIGINAL (unshifted) source position.
    assert s1["source_in_seconds"] == 0.0
    assert s2["source_in_seconds"] == 2.5

    # Output timeline is ripple-shifted and stays contiguous, no gap left
    # by the removed 0.5s.
    assert s1["in_seconds"] == 0.0
    assert s1["out_seconds"] == 2.0
    assert s2["in_seconds"] == 2.0
    assert s2["out_seconds"] == 4.5
    assert beat02["in_seconds"] == 4.5
    assert beat02["out_seconds"] == 9.5  # 10.0 - 0.5 removed


def test_removal_span_ripple_shifts_all_downstream_beats():
    beats = [_beat(1, 0.0, 5.0), _beat(2, 5.0, 10.0), _beat(3, 10.0, 15.0)]
    removal_spans = [{"start_seconds": 1.0, "end_seconds": 2.0, "kind": "dead_air"}]

    cuts = build_cuts_from_plan(beats, removal_spans, SOURCE)
    removed = 1.0

    beat03 = cuts[-1]
    assert beat03["id"] == "beat03"
    assert beat03["source_in_seconds"] == 10.0
    assert beat03["in_seconds"] == 10.0 - removed
    assert beat03["out_seconds"] == 15.0 - removed

    # Every consecutive pair stays contiguous on the output timeline.
    for prev, cur in zip(cuts, cuts[1:]):
        assert prev["out_seconds"] == cur["in_seconds"]


def test_splice_sub_cut_gets_punch_in_and_no_transition():
    """Tests raw attribution (transition belongs to the beat's entry
    sub-cut, not an internal pause-tighten splice) independent of the
    narration-safety clamp -- see test_transition_only_survives_on_the_last_cut
    for what the clamp itself does to a transition placed here."""
    beats = [_beat(1, 0.0, 5.0, transition="fade")]
    removal_spans = [{"start_seconds": 2.0, "end_seconds": 2.3, "kind": "pause_tighten"}]

    cuts = build_cuts_from_plan(beats, removal_spans, SOURCE, enforce_narration_safe_transitions=False)
    s1, s2 = cuts

    # transition_in belongs to the beat's entry point, not an internal splice.
    assert s1["transition_in"] == "fade"
    assert "transition_in" not in s2
    # Second sub-cut gets a static punch-in to mask the splice.
    assert "transform" not in s1
    assert s2["transform"]["scale"] > 1.0


def test_none_transition_produces_no_transition_key():
    beats = [_beat(1, 0.0, 5.0, transition="none")]
    cuts = build_cuts_from_plan(beats, [], SOURCE)
    assert "transition_in" not in cuts[0]


def test_beat_fully_removed_produces_no_cut_but_still_shifts_later_beats():
    beats = [_beat(1, 0.0, 5.0), _beat(2, 5.0, 10.0), _beat(3, 10.0, 15.0)]
    removal_spans = [{"start_seconds": 5.0, "end_seconds": 10.0, "kind": "dead_air"}]

    cuts = build_cuts_from_plan(beats, removal_spans, SOURCE)

    assert [c["id"] for c in cuts] == ["beat01", "beat03"]
    assert cuts[0]["out_seconds"] == 5.0
    assert cuts[1]["in_seconds"] == 5.0  # beat02's 5s fully removed, no gap
    assert cuts[1]["source_in_seconds"] == 10.0


def test_removal_span_across_a_beat_boundary_trims_both_sides():
    beats = [_beat(1, 0.0, 5.0), _beat(2, 5.0, 10.0)]
    removal_spans = [{"start_seconds": 4.5, "end_seconds": 5.5, "kind": "pause_tighten"}]

    cuts = build_cuts_from_plan(beats, removal_spans, SOURCE)

    assert [c["id"] for c in cuts] == ["beat01", "beat02"]
    assert cuts[0]["source_in_seconds"] == 0.0
    assert cuts[0]["out_seconds"] == 4.5
    assert cuts[1]["source_in_seconds"] == 5.5
    assert cuts[1]["in_seconds"] == 4.5  # contiguous, 1.0s removed
    assert cuts[1]["out_seconds"] == 9.0


def test_empty_beats_returns_empty_cuts():
    assert build_cuts_from_plan([], [], SOURCE) == []


def _renderer_would_activate_a_transition(cuts: list[dict], fps: int = 30) -> bool:
    """Minimal Python mirror of the exact renderer logic that decides
    whether ANY transition in `cuts` actually shows up on screen --
    `isContiguous`/`hasActiveTransition` in
    remotion-composer/src/transitions/build-render-groups.ts. Kept in sync
    by hand (no cross-language test bridge in this repo); if that file's
    grouping logic changes, update this mirror too. This is the check whose
    absence let the original (first-cut) version of the narration-safety
    clamp ship silently broken -- a dict-only assertion never caught that
    `cuts[0].transition_in` can never be render-active.
    """
    def is_contiguous(prev: dict, cur: dict) -> bool:
        return round(prev["out_seconds"] * fps) == round(cur["in_seconds"] * fps)

    for i in range(1, len(cuts)):
        if not is_contiguous(cuts[i - 1], cuts[i]):
            continue  # run boundary -- idx resets to 0 for cuts[i] in its own run
        transition_in = cuts[i].get("transition_in")
        if transition_in and transition_in != "none":
            return True
    return False


def test_surviving_transition_is_actually_render_active():
    """End-to-end structural check (mirrors the TS render-group logic) that
    the transition this builder leaves in place would really activate --
    not just that the Python dict happens to carry the field."""
    beats = [_beat(1, 0.0, 5.0, transition="fade"), _beat(2, 5.0, 10.0, transition="slide")]

    cuts_with_clamp = build_cuts_from_plan(beats, [], SOURCE)
    assert _renderer_would_activate_a_transition(cuts_with_clamp) is True

    cuts_first_cut_only = [dict(c) for c in cuts_with_clamp]
    # Simulate the ORIGINAL (buggy) clamp direction to prove this check
    # actually distinguishes the two -- it must report no active transition.
    for c in cuts_first_cut_only[1:]:
        c.pop("transition_in", None)
    if cuts_with_clamp:
        cuts_first_cut_only[0]["transition_in"] = "fade"
    assert _renderer_would_activate_a_transition(cuts_first_cut_only) is False


def test_out_of_order_beats_are_sorted_defensively():
    """Nothing upstream (footage_edit_prompt.validate_and_clamp_beats)
    currently guarantees beats[] arrives sorted by start_seconds -- this
    builder must not silently produce a scrambled/non-contiguous timeline
    if it doesn't."""
    beats = [_beat(2, 5.0, 10.0), _beat(1, 0.0, 5.0)]  # reversed
    cuts = build_cuts_from_plan(beats, [], SOURCE)

    assert [c["id"] for c in cuts] == ["beat01", "beat02"]
    assert cuts[0]["in_seconds"] == 0.0
    assert cuts[1]["in_seconds"] == 5.0


def test_transition_only_survives_on_the_last_cut():
    """The LAST cut, not the first: the renderer only ever activates a
    transition at run-local idx > 0 (build-render-groups.ts
    `hasActiveTransition`; Explainer.tsx's `if (idx > 0)` guard). Every cut
    from this builder is one contiguous run, so cuts[0] is always run-local
    idx 0 and can never render a transition regardless of its value -- the
    last cut is the only position that is both idx > 0 (renderable) and has
    no downstream cut to desync (narration-safe)."""
    beats = [
        _beat(1, 0.0, 5.0, transition="fade"),
        _beat(2, 5.0, 10.0, transition="slide"),
        _beat(3, 10.0, 15.0, transition="fade"),
    ]
    cuts = build_cuts_from_plan(beats, [], SOURCE)

    assert "transition_in" not in cuts[0]
    assert "transition_in" not in cuts[1]
    assert cuts[2]["transition_in"] == "fade"
    assert "transition_duration" not in cuts[0]
    assert "transition_duration" not in cuts[1]


def test_transition_lost_when_its_beat_is_split_by_a_later_removal_span():
    """Documented conservatism, not a bug: a transition is attributed to its
    beat's ENTRY sub-cut (test_splice_sub_cut_gets_punch_in_and_no_transition).
    If that beat is also the last one AND gets split by a removal span, the
    entry sub-cut is no longer cuts[-1] -- the transition has nowhere
    render-safe to land and is correctly dropped, rather than kept somewhere
    unsafe."""
    beats = [_beat(1, 0.0, 5.0), _beat(2, 5.0, 10.0, transition="fade")]
    removal_spans = [{"start_seconds": 7.0, "end_seconds": 7.3, "kind": "pause_tighten"}]

    cuts = build_cuts_from_plan(beats, removal_spans, SOURCE)

    assert [c["id"] for c in cuts] == ["beat01", "beat02_s1", "beat02_s2"]
    assert not any("transition_in" in cut for cut in cuts)


def test_enforce_narration_safe_transitions_can_be_disabled():
    beats = [_beat(1, 0.0, 5.0, transition="fade"), _beat(2, 5.0, 10.0, transition="slide")]
    cuts = build_cuts_from_plan(beats, [], SOURCE, enforce_narration_safe_transitions=False)

    assert cuts[0]["transition_in"] == "fade"
    assert cuts[1]["transition_in"] == "slide"


def test_real_fixture_beats_produce_contiguous_cuts_with_correct_ids():
    """Integration check against the real fixture (real Gemini output,
    23 beats, 1-based beat_index) -- with a real-shaped pause_tighten span
    (0.42s, matching output/raw_test_1_v2's actual detected pause duration)
    injected inside beat 2's real boundaries.
    """
    import json

    fixture_path = Path(__file__).resolve().parent / "fixtures" / "footage_edit_plan_sample.json"
    plan = json.loads(fixture_path.read_text(encoding="utf-8"))
    beats = plan["beats"]

    beat2 = next(b for b in beats if b["beat_index"] == 2)
    midpoint = (beat2["start_seconds"] + beat2["end_seconds"]) / 2
    removal_spans = [{"start_seconds": midpoint, "end_seconds": midpoint + 0.42, "kind": "pause_tighten"}]

    cuts = build_cuts_from_plan(beats, removal_spans, plan["source"]["path"])

    assert [c["id"] for c in cuts[:3]] == ["beat01", "beat02_s1", "beat02_s2"]
    for prev, cur in zip(cuts, cuts[1:]):
        assert prev["out_seconds"] == cur["in_seconds"]
    total_removed = sum(c["out_seconds"] - c["in_seconds"] for c in cuts)
    assert abs(total_removed - (beats[-1]["end_seconds"] - 0.42)) < 0.01
