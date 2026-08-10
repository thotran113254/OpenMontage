"""Unit tests for tools/analysis/footage_edit_broll_matcher.py.

Covers the deterministic matching decision only (`match_broll_to_beats`) --
`describe_broll_clip` needs a real GEMINI_API_KEY + network call and is
exercised manually per `AGENT_GUIDE.md`'s user-visible-verification pattern,
not in the automated suite. The one thing worth unit-testing about it without
a live API is that it never raises.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.analysis.footage_edit_broll_matcher import (
    describe_broll_clip,
    match_broll_to_beats,
)


def _beat(index: int, needed: bool, suggested_visual: str = "") -> dict:
    return {
        "beat_index": index,
        "b_roll": {"needed": needed, "suggested_visual": suggested_visual, "reason": "test"},
    }


def test_no_candidates_produces_no_matches():
    beats = [_beat(0, needed=True, suggested_visual="phone screen with chatbot reply")]
    assert match_broll_to_beats(beats, []) == {}


def test_beat_not_needing_broll_is_never_matched_even_with_a_perfect_candidate():
    beats = [_beat(0, needed=False, suggested_visual="phone screen with chatbot reply")]
    candidates = [{"path": "clip.mp4", "content_description": "a phone screen with a chatbot reply"}]
    assert match_broll_to_beats(beats, candidates) == {}


def test_matching_clip_above_threshold_is_returned():
    beats = [_beat(0, needed=True, suggested_visual="close-up of hands typing on a laptop keyboard")]
    candidates = [
        {"path": "typing.mp4", "content_description": "hands typing quickly on a laptop keyboard"},
        {"path": "unrelated.mp4", "content_description": "a cat sleeping on a windowsill"},
    ]
    matches = match_broll_to_beats(beats, candidates)
    assert 0 in matches
    assert matches[0]["path"] == "typing.mp4"
    assert matches[0]["score"] > 0.35


def test_below_threshold_match_is_dropped_not_forced():
    beats = [_beat(0, needed=True, suggested_visual="close-up of hands typing on a laptop keyboard")]
    candidates = [{"path": "cat.mp4", "content_description": "a cat sleeping on a windowsill"}]
    assert match_broll_to_beats(beats, candidates, threshold=0.35) == {}


def test_each_candidate_clip_used_at_most_once():
    beats = [
        _beat(0, needed=True, suggested_visual="hands typing on a laptop keyboard"),
        _beat(1, needed=True, suggested_visual="hands typing quickly on a keyboard"),
    ]
    # Only one real clip available -- it should go to the single best beat,
    # not be reused for both.
    candidates = [{"path": "typing.mp4", "content_description": "hands typing on a laptop keyboard"}]

    matches = match_broll_to_beats(beats, candidates)
    assert len(matches) == 1
    matched_paths = [m["path"] for m in matches.values()]
    assert matched_paths.count("typing.mp4") == 1


def test_describe_broll_clip_never_raises_on_a_broken_client():
    class BrokenClient:
        class models:
            @staticmethod
            def generate_content(**kwargs):
                raise RuntimeError("network unavailable in test")

    result = describe_broll_clip(BrokenClient(), "gemini-3.1-flash-lite", object(), Path("nonexistent.mp4"))
    assert result is None
