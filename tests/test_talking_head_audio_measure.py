"""Objective audio measurement — the part that replaces an ear that failed.

ffmpeg is not invoked here; these cover the logic that decides WHERE to measure
and how changes are reported. The filter-design lessons (band edges, slopes)
are recorded as assertions on the constants so they cannot quietly regress.
"""

from __future__ import annotations

from lib.talking_head_edit.audio_measure import (
    HIGH_BAND, LOW_BAND, VOICE_BAND, compare, find_gaps,
)


def words(*spans):
    return [{"word": f"w{i}", "start": s, "end": e} for i, (s, e) in enumerate(spans)]


class TestGapFinding:
    def test_finds_the_silence_between_two_words(self):
        gaps = find_gaps(words((0.0, 0.5), (1.5, 2.0)), (0.0, 3.0))
        assert len(gaps) == 1
        start, end = gaps[0]
        assert start > 0.5 and end < 1.5, "phải chừa mép để không dính vào từ"

    def test_ignores_gaps_too_short_to_measure(self):
        assert find_gaps(words((0.0, 0.5), (0.6, 1.0)), (0.0, 2.0)) == []

    def test_returns_the_longest_gaps_first(self):
        found = find_gaps(words((0.0, 0.2), (1.0, 1.2), (3.0, 3.2)), (0.0, 4.0))
        assert found[0][1] - found[0][0] > found[1][1] - found[1][0]

    def test_stays_inside_the_window(self):
        assert find_gaps(words((0.0, 0.2), (1.0, 1.2)), (5.0, 9.0)) == []

    def test_caps_how_many_it_returns(self):
        many = words(*[(i, i + 0.2) for i in range(20)])
        assert len(find_gaps(many, (0.0, 25.0), limit=3)) == 3


class TestBandDesign:
    """These constants were each wrong once; the reasons are worth pinning."""

    def test_widths_are_in_hz_not_q(self):
        """ffmpeg's bandpass defaults to width_type=q — a bare w=2700 is a Q."""
        assert "width_type=h" in VOICE_BAND

    def test_the_rumble_band_sits_below_the_vocal_fundamental(self):
        """At 150 Hz the reading is mostly the speaker's own voice, not rumble."""
        assert "lowpass=f=80" in LOW_BAND

    def test_band_filters_are_cascaded_for_a_steep_skirt(self):
        """One pole leaks so much voice that a highpass appeared to do nothing."""
        assert LOW_BAND.count("lowpass") >= 3
        assert HIGH_BAND.count("highpass") >= 2


class TestComparing:
    def test_negative_means_the_problem_shrank(self):
        delta = compare({"u_am_duoi_80hz_db": -10.0}, {"u_am_duoi_80hz_db": -18.0},
                        keys=("u_am_duoi_80hz_db",))
        assert delta["u_am_duoi_80hz_db"] == -8.0

    def test_a_missing_measurement_is_reported_as_missing_not_zero(self):
        delta = compare({"duoi_vang_db": None}, {"duoi_vang_db": -5.0},
                        keys=("duoi_vang_db",))
        assert delta["duoi_vang_db"] is None

    def test_word_tails_are_compared_by_default(self):
        """Without this key an optimiser buys quiet gaps by clipping word ends."""
        assert "duoi_tu_con_lai_db" in compare({}, {})
