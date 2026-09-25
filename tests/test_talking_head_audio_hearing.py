"""The blind hearing check — the thing that decides whether an ear is trusted.

Model calls are not made here. What is tested is the scoring: it has to call a
model that hears, and fail one that guesses or one that invents differences.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.talking_head_edit.blind_probe import LABEL_ORDER
from lib.talking_head_edit.audio_hearing_check import (
    ALL_DEFECTS, PROMPT_EN, PROMPT_VI, defect_filter, score_hearing,
)


def probes(tmp_path: Path):
    """(label, path, injected) with the two clean copies named like the real ones."""
    entries = [("m1", tmp_path / "s100_ctl.mp3", None)]
    entries += [(f"d{i}", tmp_path / f"s100_x_{d}.mp3", d)
                for i, d in enumerate(ALL_DEFECTS)]
    entries.append(("m9", tmp_path / "s100_ctl2.mp3", None))
    return entries


def grid(tmp_path, control_score: float, injected_score: float,
         twin_score: float | None = None):
    """A severity grid where every defect scores `control_score` unless injected."""
    rows = []
    for label, _, injected in probes(tmp_path):
        base = control_score if twin_score is None or label != "m9" else twin_score
        row = {"ban": label, **{d: base for d in ALL_DEFECTS}}
        if injected:
            row[injected] = injected_score
        rows.append(row)
    return rows


class TestScoring:
    def test_a_model_that_hears_passes(self, tmp_path):
        result = score_hearing(grid(tmp_path, 1, 8), probes(tmp_path))
        assert result["so_loi_nhan_ra"] == len(ALL_DEFECTS)
        assert result["lift_trung_binh"] == 7.0
        assert result["ket_luan"].startswith("NGHE ĐƯỢC")

    def test_a_model_that_guesses_fails(self, tmp_path):
        """Same score everywhere: the injected defect makes no difference."""
        result = score_hearing(grid(tmp_path, 5, 5), probes(tmp_path))
        assert result["so_loi_nhan_ra"] == 0
        assert result["lift_trung_binh"] == 0.0
        assert "KHÔNG NGHE ĐƯỢC" in result["ket_luan"]

    def test_inventing_differences_between_identical_files_is_caught(self, tmp_path):
        """The twin is the same bytes as the control; scoring it apart is fabrication."""
        result = score_hearing(grid(tmp_path, 1, 8, twin_score=6), probes(tmp_path))
        assert result["sai_lech_hai_ban_giong_nhau"] == 5.0
        assert not result["ket_luan"].startswith("NGHE ĐƯỢC")
        assert "ổn định" in result["ket_luan"]

    def test_a_lift_below_two_points_does_not_count_as_hearing(self, tmp_path):
        result = score_hearing(grid(tmp_path, 5, 6.5), probes(tmp_path))
        assert result["so_loi_nhan_ra"] == 0

    def test_partial_hearing_is_reported_as_partial(self, tmp_path):
        rows = grid(tmp_path, 1, 1)
        for row in rows:
            if row["ban"] in ("d0", "d1", "d2"):
                for defect in ALL_DEFECTS:
                    row[defect] = 1
        # only the first three injected defects are actually detected
        injected = {label: d for label, _, d in probes(tmp_path) if d}
        for row in rows:
            defect = injected.get(row["ban"])
            if defect and row["ban"] in ("d0", "d1", "d2"):
                row[defect] = 9
        result = score_hearing(rows, probes(tmp_path))
        assert result["so_loi_nhan_ra"] == 3
        assert "một phần" in result["ket_luan"]

    def test_missing_rows_do_not_crash(self, tmp_path):
        assert score_hearing([], probes(tmp_path))["ket_luan"] != ""


class TestProbeDesign:
    def test_labels_carry_no_information(self):
        """Naming a sample after its defect hands over the answer."""
        for label in LABEL_ORDER:
            assert label not in ALL_DEFECTS
            assert not any(d in label for d in ALL_DEFECTS)

    def test_there_is_a_label_for_every_sample(self):
        # every defect, plus the control and its twin
        assert len(LABEL_ORDER) == len(ALL_DEFECTS) + 2

    def test_strength_scales_the_injected_defect(self):
        strong, _ = defect_filter("u_am_tram", 1.0)
        weak, _ = defect_filter("u_am_tram", 0.25)
        assert "g=18" in strong and "g=4.5" in weak

    def test_hiss_is_mixed_in_rather_than_filtered_out(self):
        chain, needs_complex = defect_filter("xi_nen", 1.0)
        assert needs_complex, "không thể tạo tiếng xì bằng cách lọc giọng"
        assert "anoisesrc" in chain

    def test_both_prompts_ask_for_the_same_defects(self):
        for defect in ALL_DEFECTS:
            assert defect in PROMPT_VI, defect
            assert defect in PROMPT_EN, defect

    def test_neither_prompt_hints_which_sample_has_what(self):
        for prompt in (PROMPT_VI, PROMPT_EN):
            for label in LABEL_ORDER:
                assert f'"{label}"' not in prompt
