"""Calibration: probe a sample, let a model choose, then apply to the whole video.

The model calls are stubbed. What matters here is everything around them —
which candidate wins, and whether a choice a human already made survives.
"""

from __future__ import annotations

import json

import pytest

from lib.talking_head_edit.calibrate_candidates import (
    AUDIO_CANDIDATES, GRADE_CANDIDATES, pick_winner, score_of,
)
from lib.talking_head_edit.job_store import STAGES, JobStore
from lib.talking_head_edit.stages import calibrate

GRADE_KEYS = ("do_sang", "mau_da", "ket_cau_da", "tu_nhien")


@pytest.fixture()
def job(tmp_path):
    source = tmp_path / "footage.mp4"
    source.write_bytes(b"video")
    store = JobStore(root=tmp_path / "jobs")
    job = store.create(source, {}, title="thu nghiem")
    job.update(current_version=1)
    job.spec_path(1).write_text(json.dumps({"grade": {"saturation": 1.08, "warmth": 10}}),
                                encoding="utf-8")
    job.spine_path.write_text(json.dumps({"word_timestamps": [
        {"word": "a", "start": 1.0, "end": 1.4}, {"word": "b", "start": 2.0, "end": 2.4}]}),
        encoding="utf-8")
    return job


class TestStageOrder:
    def test_calibrate_runs_after_audit_and_before_resolve(self):
        assert STAGES.index("audit") < STAGES.index("calibrate") < STAGES.index("resolve")


class TestPickWinner:
    def test_uses_the_models_own_choice(self):
        verdict = {"nen_dung": "sang_hon", "danh_gia": []}
        winner, how = pick_winner(verdict, list(GRADE_CANDIDATES), GRADE_KEYS)
        assert winner == "sang_hon"
        assert how == "model chọn"

    def test_falls_back_to_scores_when_the_pick_is_not_a_real_candidate(self):
        verdict = {"nen_dung": "mot_bien_the_khong_ton_tai", "danh_gia": [
            {"ban": "sang_hon", "do_sang": 6, "mau_da": 6, "ket_cau_da": 6, "tu_nhien": 6},
            {"ban": "trung_tinh", "do_sang": 9, "mau_da": 9, "ket_cau_da": 8, "tu_nhien": 9},
        ]}
        winner, how = pick_winner(verdict, list(GRADE_CANDIDATES), GRADE_KEYS)
        assert winner == "trung_tinh"
        assert "trung bình" in how

    def test_returns_nothing_when_the_verdict_is_unusable(self):
        winner, how = pick_winner({"danh_gia": []}, list(GRADE_CANDIDATES), GRADE_KEYS)
        assert winner is None
        assert how

    def test_ignores_rows_for_candidates_that_were_not_offered(self):
        verdict = {"danh_gia": [{"ban": "bia_dat", "do_sang": 10, "mau_da": 10,
                                 "ket_cau_da": 10, "tu_nhien": 10}]}
        assert pick_winner(verdict, list(GRADE_CANDIDATES), GRADE_KEYS)[0] is None

    def test_score_skips_missing_dimensions(self):
        assert score_of({"do_sang": 8, "mau_da": 6}, GRADE_KEYS) == pytest.approx(7.0)


class TestNaturalnessVeto:
    """The cleanest-measuring candidate is repeatedly the worst-sounding one."""

    AUDIO_KEYS = ("do_sach", "do_vang", "do_ro", "tu_nhien")

    def _verdict(self, chosen):
        return {"nen_dung": chosen, "danh_gia": [
            # scores every cleanliness dimension best, but is unnatural
            {"ban": "shotgun_dry", "do_sach": 10, "do_vang": 10, "do_ro": 9, "tu_nhien": 4},
            {"ban": "shotgun", "do_sach": 8, "do_vang": 7, "do_ro": 8, "tu_nhien": 9},
            {"ban": "voice", "do_sach": 6, "do_vang": 5, "do_ro": 7, "tu_nhien": 9},
        ]}

    def test_a_candidate_the_model_called_unnatural_cannot_be_picked(self):
        winner, how = pick_winner(self._verdict("shotgun_dry"), AUDIO_CANDIDATES,
                                  self.AUDIO_KEYS)
        assert winner == "shotgun"
        assert "tu_nhien" in how, "phải nói rõ vì sao đổi lựa chọn của model"

    def test_it_cannot_win_on_average_either(self):
        """9.75 vs 8.0 on the average — the veto is what stops it."""
        winner, _ = pick_winner(self._verdict(""), AUDIO_CANDIDATES, self.AUDIO_KEYS)
        assert winner == "shotgun"

    def test_a_natural_pick_is_still_trusted(self):
        winner, how = pick_winner(self._verdict("voice"), AUDIO_CANDIDATES, self.AUDIO_KEYS)
        assert (winner, how) == ("voice", "model chọn")

    def test_nothing_is_chosen_when_every_option_sounds_processed(self):
        verdict = {"nen_dung": "shotgun", "danh_gia": [
            {"ban": n, "do_sach": 9, "do_vang": 9, "do_ro": 9, "tu_nhien": 3}
            for n in AUDIO_CANDIDATES]}
        winner, how = pick_winner(verdict, AUDIO_CANDIDATES, self.AUDIO_KEYS)
        assert winner is None, "thà giữ mặc định còn hơn áp một bản nghe đã hỏng"
        assert "giữ mặc định" in how

    def test_a_missing_naturalness_score_does_not_veto(self):
        verdict = {"nen_dung": "shotgun", "danh_gia": [
            {"ban": "shotgun", "do_sach": 8, "do_ro": 8}]}
        assert pick_winner(verdict, AUDIO_CANDIDATES, self.AUDIO_KEYS)[0] == "shotgun"


class TestApplyingTheResult:
    # The stage ships disabled after it failed a blind test; these cases are
    # about what it does once someone deliberately turns it back on.
    ON = {"calibrate_grade": True, "calibrate_audio": True}

    def _stub(self, monkeypatch, grade_winner="trung_tinh", audio_winner="shotgun"):
        monkeypatch.setattr(calibrate, "calibrate_grade", lambda *a, **k: {
            "winner": grade_winner, "chosen_by": "model chọn",
            "verdict": {"ly_do": "vì thế"}, "usage": {}, "images": []})
        monkeypatch.setattr(calibrate, "calibrate_audio", lambda *a, **k: {
            "winner": audio_winner, "chosen_by": "model chọn",
            "verdict": {"ly_do": "vì thế"}, "usage": {}, "samples": []})

    def test_winner_is_written_into_the_job_options(self, job, monkeypatch):
        self._stub(monkeypatch)
        calibrate.run(job, {**job.load()["options"], **self.ON})
        options = job.load()["options"]
        assert options["audio_preset"] == "shotgun"
        assert options["grade_overrides"]["saturation"] == 1.0

    def test_a_choice_the_human_already_made_survives(self, job, monkeypatch):
        """The approved sharpening must not be wiped by a colour-only winner."""
        self._stub(monkeypatch)
        options = {**job.load()["options"], **self.ON,
                   "grade_overrides": {"sharpen": 0.8, "clarity": 0.5, "skin_smooth": 0.18}}
        calibrate.run(job, options)
        applied = job.load()["options"]["grade_overrides"]
        assert applied["sharpen"] == 0.8
        assert applied["clarity"] == 0.5
        assert applied["saturation"] == 1.0, "phần màu do calibrate chọn vẫn phải được áp"

    def test_no_winner_leaves_the_settings_alone(self, job, monkeypatch):
        self._stub(monkeypatch, grade_winner=None, audio_winner=None)
        before = dict(job.load()["options"])
        calibrate.run(job, dict(before))
        after = job.load()["options"]
        assert after["audio_preset"] == before["audio_preset"]
        assert "grade_overrides" not in after or after.get("grade_overrides") == before.get("grade_overrides")

    def test_each_half_can_be_switched_off(self, job, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(calibrate, "calibrate_grade",
                            lambda *a, **k: calls.append("grade") or {"winner": None, "chosen_by": "", "verdict": {}, "usage": {}})
        monkeypatch.setattr(calibrate, "calibrate_audio",
                            lambda *a, **k: calls.append("audio") or {"winner": None, "chosen_by": "", "verdict": {}, "usage": {}})
        calibrate.run(job, {**job.load()["options"],
                            "calibrate_grade": False, "calibrate_audio": True})
        assert calls == ["audio"]

    def test_report_is_written_for_inspection(self, job, monkeypatch):
        self._stub(monkeypatch)
        calibrate.run(job, {**job.load()["options"], **self.ON})
        report = json.loads((job.dir / "calibrate_report.json").read_text(encoding="utf-8"))
        assert report["grade"]["winner"] == "trung_tinh"
        assert report["audio"]["winner"] == "shotgun"


class TestCandidates:
    def test_audio_candidates_are_real_presets(self):
        from lib.talking_head_edit.resolve_media import AUDIO_PRESETS
        for name in AUDIO_CANDIDATES:
            assert name in AUDIO_PRESETS

    def test_the_untouched_proposal_is_always_in_the_running(self):
        assert GRADE_CANDIDATES["nhu_de_xuat"] == {}, "phải có bản giữ nguyên đề xuất để so"
