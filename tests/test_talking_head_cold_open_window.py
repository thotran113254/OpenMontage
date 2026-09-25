"""Where the cold-open teaser is cut on the timeline — pure arithmetic, no media."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.talking_head_edit.resolve_events import HOOK_LEAD_MAX, TimeMapper, cold_open_window
from lib.talking_head_edit.resolve_spans import Span

FPS = 30


def mapper(timings: list[tuple[float, float]], total: float = 20.0) -> TimeMapper:
    words = [{"word": f"w{i}", "start": a, "end": b, "src": "s0"} for i, (a, b) in enumerate(timings)]
    return TimeMapper(words, [Span("s0", Path("a.mp4"), 0.0, total)], total, 1.0)


def on_grid(t: float) -> bool:
    return abs(t * FPS - round(t * FPS)) < 1e-4   # values are rounded to 6 decimals


def test_the_teaser_starts_in_the_breath_before_its_first_word():
    m = mapper([(1.0, 1.4), (2.0, 2.5), (2.55, 3.0), (3.05, 3.6), (4.4, 4.8)])
    start, _ = cold_open_window(m, 1, 3, 20.0, FPS)
    assert 2.0 - HOOK_LEAD_MAX - 1 / FPS <= start < 2.0
    assert on_grid(start)


def test_the_teaser_keeps_a_breath_after_its_last_word():
    m = mapper([(1.0, 1.4), (2.0, 2.5), (2.55, 3.0), (3.05, 3.6), (4.4, 4.8)])
    _, end = cold_open_window(m, 1, 3, 20.0, FPS)
    assert 3.6 + 0.06 <= end <= 3.6 + 0.22 + 1 / FPS
    assert on_grid(end)


def test_a_run_on_speaker_never_has_the_next_word_swallowed():
    """The old 60ms minimum tail ran into a next word 30ms away."""
    m = mapper([(1.0, 1.4), (2.0, 2.5), (2.55, 3.0), (3.05, 3.6), (3.63, 4.0)])
    _, end = cold_open_window(m, 1, 3, 20.0, FPS)
    assert end <= 3.63


def test_the_frame_snap_rounds_up_so_the_last_word_is_not_clipped():
    m = mapper([(1.0, 1.4), (2.0, 2.5), (2.55, 3.0), (3.05, 3.601), (4.4, 4.8)])
    start, end = cold_open_window(m, 1, 3, 20.0, FPS)
    assert end >= 3.601 and on_grid(end - start)


@pytest.mark.parametrize("w0", [0, 1])
def test_the_teaser_never_starts_before_the_timeline(w0):
    m = mapper([(0.02, 0.4), (0.45, 0.9), (0.95, 1.4), (1.45, 2.0), (2.6, 3.0)])
    start, _ = cold_open_window(m, w0, 3, 20.0, FPS)
    assert start >= 0.0


def test_the_mapper_adds_up_whole_frames_like_the_encoded_segments():
    """Each segment is round(len / tempo * fps) frames on disk; the mapper must
    add the same numbers or captions creep off the words cut after cut."""
    words = [{"word": "a", "start": 5.0, "end": 5.2, "src": "s0"}]
    spans = [Span("s0", Path("a.mp4"), 0.0, 1.01) for _ in range(40)] + \
            [Span("s0", Path("a.mp4"), 5.0, 6.0)]
    exact = TimeMapper(words, spans, 100.0, 1.0, fps=FPS)
    assert exact.at(0) == pytest.approx(40 * 30 / FPS, abs=1e-3)   # 40 x 30 frames
    assert TimeMapper(words, spans, 100.0, 1.0).at(0) == pytest.approx(40 * 1.01, abs=1e-3)
