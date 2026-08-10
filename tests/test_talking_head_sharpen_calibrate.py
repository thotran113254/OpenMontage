"""Auto-sharpen: measure this footage's softness instead of reusing a number.

The image maths runs on synthetic arrays here — no ffmpeg — because what needs
guarding is that `detail` rewards structure and `overshoot` punishes halos, and
that the two together stop the search running away.
"""

from __future__ import annotations

import numpy as np
import pytest

from lib.talking_head_edit.sharpen_calibrate import (
    MAX_OVERSHOOT, SAMPLE_FRAMES, TARGET_DETAIL, detail_of, overshoot_of,
)


def edges(height=64, width=64, step=8, low=60.0, high=190.0):
    """Vertical bars — structure a sharpener has something to work with."""
    image = np.full((height, width), low, dtype=np.float32)
    image[:, ::step] = high
    return image


class TestDetail:
    def test_a_flat_field_has_no_detail(self):
        assert detail_of(np.full((32, 32), 128.0, dtype=np.float32)) == pytest.approx(0.0)

    def test_structure_scores_above_flat(self):
        assert detail_of(edges()) > detail_of(np.full((64, 64), 128.0, dtype=np.float32))

    def test_more_contrast_reads_as_more_detail(self):
        assert detail_of(edges(low=20, high=235)) > detail_of(edges(low=110, high=145))

    def test_blurring_lowers_it(self):
        sharp = edges()
        blurred = sharp.copy()
        blurred[:, 1:-1] = (sharp[:, :-2] + sharp[:, 1:-1] + sharp[:, 2:]) / 3
        assert detail_of(blurred) < detail_of(sharp)


class TestOvershoot:
    def test_an_untouched_image_overshoots_nothing(self):
        image = edges()
        assert overshoot_of(image, image) == pytest.approx(0.0)

    def test_a_rim_beyond_the_local_range_is_caught(self):
        """Exactly what a halo is: brighter than anything that was nearby."""
        reference = edges()
        haloed = reference.copy()
        haloed[:, 1] = 255.0          # a bright rim next to each bar
        assert overshoot_of(reference, haloed) > 0

    def test_contrast_already_present_is_not_punished(self):
        """Raising a pixel toward a neighbour's value is not ringing."""
        reference = edges()
        within = np.clip(reference * 1.0, 0, 255)
        assert overshoot_of(reference, within) == pytest.approx(0.0)

    def test_a_bigger_rim_scores_higher(self):
        reference = edges()
        small, large = reference.copy(), reference.copy()
        small[:, 1] = 210.0
        large[:, 1] = 255.0
        assert overshoot_of(reference, large) > overshoot_of(reference, small)

    def test_darkening_below_the_local_minimum_counts_too(self):
        reference = edges()
        dark = reference.copy()
        dark[:, 1] = 0.0
        assert overshoot_of(reference, dark) > 0


class TestAnchors:
    """These were wrong twice; both mistakes had the same shape."""

    def test_the_halo_ceiling_sits_above_the_approved_level(self):
        """An earlier ceiling of 1.10 sat BELOW the approved 1.6 and capped it."""
        assert MAX_OVERSHOOT > 1.22, "trần viền phải cao hơn mức đã được duyệt"

    def test_the_target_is_reachable_on_this_footage(self):
        """Anchored on one frame, compared against a 3-frame mean, it was not."""
        assert TARGET_DETAIL <= 12.6

    def test_more_than_one_frame_is_sampled(self):
        """One frame put the same footage at 1.5 in one moment, 1.94 in another."""
        assert SAMPLE_FRAMES >= 3
