"""Post-render measurement: gap detection and the music-present check.

The music check exists because the previous heuristic in this repo reported
music present on a narration-only mix. These tests pin the honest behaviour:
no claim without a measurement, and no silent "false" that reads as "checked".
"""

from __future__ import annotations

import pytest

from lib.talking_head_edit.stages import verify


def caption(at: float, end: float) -> dict[str, object]:
    return {"type": "caption", "at": at, "end": end, "text": "x"}


class TestSpeechGaps:
    def test_finds_the_hole_between_two_captions(self):
        gaps = verify.speech_gaps([caption(0, 2), caption(5, 7)], total=8.0)
        assert (2.0, 3.0) in gaps

    def test_ignores_gaps_shorter_than_the_minimum(self):
        gaps = verify.speech_gaps([caption(0, 2), caption(2.2, 6)], total=6.0)
        assert all(length >= 0.6 for _, length in gaps)

    def test_includes_the_tail_after_the_last_caption(self):
        gaps = verify.speech_gaps([caption(0, 2)], total=6.0)
        assert any(start == 2.0 for start, _ in gaps)

    def test_wall_to_wall_captions_leave_no_gap(self):
        assert verify.speech_gaps([caption(0, 5), caption(5, 10)], total=10.0) == []

    def test_returns_longest_first_and_caps_the_window(self):
        gaps = verify.speech_gaps(
            [caption(0, 1), caption(2, 3), caption(9, 10)], total=12.0
        )
        assert gaps[0][1] >= gaps[-1][1]
        assert all(length <= 3.0 for _, length in gaps)


class TestMusicBed:
    def test_no_declared_music_means_no_claim(self, monkeypatch, tmp_path):
        report = verify.measure_music_bed(tmp_path / "v.mp4", {"bgm": None, "events": []}, 10.0)
        assert report["music_present"] is None
        assert report["issues"] == []

    def test_audible_gap_confirms_the_bed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(verify, "window_loudness", lambda *a: -33.0)
        props = {"bgm": {"name": "bgm_upbeat_bounce.mp3"},
                 "events": [caption(0, 2), caption(5, 7)]}
        report = verify.measure_music_bed(tmp_path / "v.mp4", props, 8.0)
        assert report["music_present"] is True
        assert report["loudest_gap_db"] == -33.0
        assert report["issues"] == []

    def test_silent_gaps_flag_the_missing_music(self, monkeypatch, tmp_path):
        monkeypatch.setattr(verify, "window_loudness", lambda *a: -78.0)
        props = {"bgm": {"name": "bgm_upbeat_bounce.mp3"},
                 "events": [caption(0, 2), caption(5, 7)]}
        report = verify.measure_music_bed(tmp_path / "v.mp4", props, 8.0)
        assert report["music_present"] is False
        assert report["issues"], "mất nhạc phải được báo, không im lặng bỏ qua"

    def test_unmeasurable_stays_undecided_rather_than_passing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(verify, "window_loudness", lambda *a: None)
        props = {"bgm": {"name": "bgm_upbeat_bounce.mp3"},
                 "events": [caption(0, 2), caption(5, 7)]}
        report = verify.measure_music_bed(tmp_path / "v.mp4", props, 8.0)
        assert report["music_present"] is None
        assert report["issues"]

    def test_no_usable_gap_is_reported_not_treated_as_failure(self, monkeypatch, tmp_path):
        props = {"bgm": {"name": "bgm_upbeat_bounce.mp3"},
                 "events": [caption(0, 10)]}
        report = verify.measure_music_bed(tmp_path / "v.mp4", props, 10.0)
        assert report["music_present"] is None
        assert report["issues"]


class TestRenderStaging:
    """The staged public dir must mirror the CURRENT version, nothing more."""

    def test_stages_only_what_the_props_reference(self, tmp_path, monkeypatch):
        from lib.talking_head_edit.stages import render as render_stage

        shared = tmp_path / "shared"
        shared.mkdir()
        for name in ("sfx_pop.mp3", "bgm_new.mp3", "bgm_old.mp3"):
            (shared / name).write_bytes(b"audio")
        monkeypatch.setattr(render_stage, "SHARED_PUBLIC", shared)

        class FakeJob:
            dir = tmp_path / "job"
            src_path = tmp_path / "job" / "src.mp4"
            render_public_dir = tmp_path / "job" / "render_public"

            def emit(self, *args, **kwargs):
                pass

        FakeJob.dir.mkdir()
        FakeJob.src_path.write_bytes(b"video")
        FakeJob.render_public_dir.mkdir()
        # left over from a previous version
        (FakeJob.render_public_dir / "bgm_old.mp3").write_bytes(b"audio")

        props = {
            "videoSrc": "src.mp4",
            "events": [{"type": "sfx", "name": "sfx_pop.mp3"}],
            "bgm": {"name": "bgm_new.mp3"},
        }
        staging = render_stage.stage_assets(FakeJob(), props)
        staged = sorted(p.name for p in staging.iterdir())
        assert staged == ["bgm_new.mp3", "sfx_pop.mp3", "src.mp4"]
        assert "bgm_old.mp3" not in staged, "nhạc của version cũ phải được dọn"


class TestArollWindows:
    """Judder must be measured on the footage, not on the static cards."""

    def test_skips_card_and_endcard_spans(self):
        props = {"events": [
            {"type": "card", "at": 10.0, "end": 30.0},
            {"type": "endcard", "at": 55.0, "end": 58.0},
        ]}
        windows = verify.aroll_windows(props, total=58.0)
        starts = [start for start, _ in windows]
        assert all(not (10.0 <= s < 30.0) for s in starts)
        assert all(not (55.0 <= s < 58.0) for s in starts)

    def test_skips_the_first_second(self):
        windows = verify.aroll_windows({"events": []}, total=20.0)
        assert windows and windows[0][0] >= 1.0

    def test_no_window_when_cards_cover_everything(self):
        props = {"events": [{"type": "card", "at": 0.0, "end": 20.0}]}
        assert verify.aroll_windows(props, total=20.0) == []

    def test_caps_the_number_of_samples(self):
        props = {"events": [
            {"type": "card", "at": 10.0, "end": 12.0},
            {"type": "card", "at": 22.0, "end": 24.0},
            {"type": "card", "at": 34.0, "end": 36.0},
            {"type": "card", "at": 46.0, "end": 48.0},
        ]}
        assert len(verify.aroll_windows(props, total=60.0)) <= 3


class TestMotionMeasurement:
    def test_smooth_render_passes(self, monkeypatch, tmp_path):
        monkeypatch.setattr(verify, "duplicate_ratio", lambda *a, **k: 0.0)
        report = verify.measure_motion(tmp_path / "v.mp4", {"events": []}, 20.0)
        assert report["smooth"] is True
        assert report["issues"] == []

    def test_repeated_frames_are_reported_as_judder(self, monkeypatch, tmp_path):
        monkeypatch.setattr(verify, "duplicate_ratio", lambda *a, **k: 0.2)
        report = verify.measure_motion(tmp_path / "v.mp4", {"events": []}, 20.0)
        assert report["smooth"] is False
        assert report["duplicate_ratio"] == 0.2
        # Issues carry a machine-matchable code alongside the Vietnamese message,
        # because autopilot looks up a remedy by code (see remedies.py).
        assert report["issues"][0]["code"] == verify.CODE_STUTTER
        assert "giật" in report["issues"][0]["message"]

    def test_worst_window_decides(self, monkeypatch, tmp_path):
        ratios = iter([0.0, 0.3, 0.0])
        monkeypatch.setattr(verify, "duplicate_ratio", lambda *a, **k: next(ratios))
        props = {"events": [
            {"type": "card", "at": 10.0, "end": 12.0},
            {"type": "card", "at": 22.0, "end": 24.0},
        ]}
        report = verify.measure_motion(tmp_path / "v.mp4", props, 40.0)
        assert report["duplicate_ratio"] == 0.3, "một đoạn giật là cả bản render hỏng"

    def test_unmeasurable_is_undecided_not_a_pass(self, monkeypatch, tmp_path):
        monkeypatch.setattr(verify, "duplicate_ratio", lambda *a, **k: None)
        report = verify.measure_motion(tmp_path / "v.mp4", {"events": []}, 20.0)
        assert report["duplicate_ratio"] is None
        assert "smooth" not in report
        assert report["issues"]

    def test_all_cards_reports_rather_than_claiming_smooth(self, tmp_path):
        props = {"events": [{"type": "card", "at": 0.0, "end": 20.0}]}
        report = verify.measure_motion(tmp_path / "v.mp4", props, 20.0)
        assert report["duplicate_ratio"] is None
        assert report["issues"]
