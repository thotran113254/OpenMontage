"""Auto exposure/white-balance: measure this footage's pixels instead of guessing.

Pure numpy here, no ffmpeg — same reasoning as test_talking_head_sharpen_calibrate.py:
what needs guarding is the measurement math and the correction it produces, not
ffmpeg's own frame extraction.
"""

from __future__ import annotations

import numpy as np
import pytest

from lib.talking_head_edit.grade_calibrate import (
    BG_NEUTRAL_CR_CB,
    MAX_BRIGHTNESS_CORRECTION,
    MAX_GAMMA_CORRECTION,
    MAX_WARMTH_CORRECTION,
    TARGET_LUMA,
    cr_minus_cb_of,
    exposure_correction,
    luma_of,
    warmth_correction,
)


def solid(r, g, b, height=32, width=32):
    image = np.empty((height, width, 3), dtype=np.float64)
    image[..., 0], image[..., 1], image[..., 2] = r, g, b
    return image


class TestLumaOf:
    def test_mid_grey_reads_near_its_own_value(self):
        assert luma_of(solid(128, 128, 128)) == pytest.approx(128.0, abs=0.5)

    def test_white_reads_high(self):
        assert luma_of(solid(255, 255, 255)) == pytest.approx(255.0, abs=0.5)

    def test_black_reads_low(self):
        assert luma_of(solid(0, 0, 0)) == pytest.approx(0.0, abs=0.5)


class TestCrMinusCbOf:
    def test_neutral_grey_reads_near_zero(self):
        assert cr_minus_cb_of(solid(128, 128, 128)) == pytest.approx(0.0, abs=1.0)

    def test_a_warm_red_bias_reads_positive(self):
        assert cr_minus_cb_of(solid(180, 128, 100)) > 5

    def test_a_cool_blue_bias_reads_negative(self):
        assert cr_minus_cb_of(solid(100, 128, 180)) < -5


class TestExposureCorrection:
    def test_inside_the_deadband_makes_no_change(self):
        assert exposure_correction(TARGET_LUMA) == (0.0, 1.0)
        assert exposure_correction(TARGET_LUMA + 5) == (0.0, 1.0)
        assert exposure_correction(TARGET_LUMA - 5) == (0.0, 1.0)

    def test_underexposed_lifts_gamma_and_brightness(self):
        brightness, gamma = exposure_correction(TARGET_LUMA - 40)
        assert gamma > 1.0
        assert brightness > 0.0

    def test_overexposed_lowers_gamma_and_brightness(self):
        brightness, gamma = exposure_correction(TARGET_LUMA + 40)
        assert gamma < 1.0
        assert brightness < 0.0

    def test_correction_never_exceeds_its_ceiling(self):
        brightness, gamma = exposure_correction(0.0)
        assert gamma <= 1.0 + MAX_GAMMA_CORRECTION + 1e-9
        assert brightness <= MAX_BRIGHTNESS_CORRECTION + 1e-9

        brightness, gamma = exposure_correction(255.0)
        assert gamma >= 1.0 - MAX_GAMMA_CORRECTION - 1e-9
        assert brightness >= -MAX_BRIGHTNESS_CORRECTION - 1e-9


class TestWarmthCorrection:
    def test_inside_the_deadband_makes_no_change(self):
        assert warmth_correction(BG_NEUTRAL_CR_CB) == 0.0

    def test_a_warm_background_gets_a_cooling_nudge(self):
        assert warmth_correction(BG_NEUTRAL_CR_CB + 20) < 0.0

    def test_a_cool_background_gets_a_warming_nudge(self):
        assert warmth_correction(BG_NEUTRAL_CR_CB - 20) > 0.0

    def test_correction_never_exceeds_its_ceiling(self):
        assert abs(warmth_correction(1000.0)) <= MAX_WARMTH_CORRECTION
        assert abs(warmth_correction(-1000.0)) <= MAX_WARMTH_CORRECTION
