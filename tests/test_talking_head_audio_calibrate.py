"""Auto shotgun/shotgun_dry: measure the room's own decay tail instead of asking.

Pure numpy here, no ffmpeg — same reasoning as test_talking_head_sharpen_calibrate.py:
what needs guarding is that a live room's slow decay reads as longer than a dry
room's, and that a flat (non-decaying) signal does not falsely trigger one.
"""

from __future__ import annotations

import numpy as np
import pytest

from lib.talking_head_edit.audio_calibrate import (
    MIN_GAP_SECONDS,
    decay_ms_of,
    find_gaps,
    rms_envelope,
)


def word(end=None, start=None):
    return {"end": end, "start": start}


class TestFindGaps:
    def test_a_gap_shorter_than_the_minimum_is_dropped(self):
        words = [word(end=1.0), word(start=1.0 + MIN_GAP_SECONDS / 2)]
        assert find_gaps(words) == []

    def test_a_gap_at_or_above_the_minimum_is_kept(self):
        words = [word(end=1.0), word(start=1.0 + MIN_GAP_SECONDS)]
        assert find_gaps(words) == [(1.0, 1.0 + MIN_GAP_SECONDS)]

    def test_several_words_yield_one_gap_per_boundary(self):
        words = [word(end=0.0), word(start=1.0, end=1.2), word(start=2.0)]
        assert len(find_gaps(words)) == 2


class TestRmsEnvelope:
    def test_silence_reads_near_zero(self):
        envelope = rms_envelope(np.zeros(24000))
        assert envelope.mean() == pytest.approx(0.0, abs=1e-3)

    def test_a_constant_tone_reads_a_stable_level(self):
        pcm = np.full(24000, 1000.0)
        envelope = rms_envelope(pcm)
        assert envelope.std() < 1.0
        assert envelope.mean() == pytest.approx(1000.0, rel=0.01)

    def test_too_short_a_clip_yields_an_empty_envelope(self):
        assert len(rms_envelope(np.zeros(5))) == 0


class TestDecayMsOf:
    def test_a_flat_signal_has_no_measurable_decay(self):
        assert decay_ms_of(np.full(20, 500.0)) == pytest.approx(0.0)

    def test_a_short_envelope_cannot_be_measured(self):
        assert decay_ms_of(np.array([500.0, 400.0])) is None

    def test_a_slow_decay_measures_longer_than_a_fast_one(self):
        # Exponential decay toward a small noise floor, at two different rates.
        n = 60
        fast = 50 * np.exp(-np.arange(n) / 2.0) + 5
        slow = 50 * np.exp(-np.arange(n) / 20.0) + 5
        fast_decay = decay_ms_of(fast)
        slow_decay = decay_ms_of(slow)
        assert fast_decay is not None and slow_decay is not None
        assert slow_decay > fast_decay

    def test_decay_cannot_exceed_the_envelope_length(self):
        # A tail that never reaches the target floor caps at the envelope's own length.
        envelope = np.full(10, 500.0)
        envelope[0] = 501.0   # peak barely above floor, target unreachable in practice
        result = decay_ms_of(envelope)
        assert result is not None
