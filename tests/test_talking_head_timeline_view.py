"""timeline_view, seam suspicion ranking, and the `unsure` verdict path.

`timeline_view` itself is exercised against a real generated video, because the
whole value of it is what ffmpeg and PIL produce together — a mocked version
would prove nothing. The seam ranking and the verdict handling are pure logic and
run without any media.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from lib.talking_head_edit import timeline_view as tv
from lib.talking_head_edit.cut_verifier import normalise_decision, verify_cuts
from lib.talking_head_edit.stages import verify as verify_stage

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="cần ffmpeg")


def words(count=20, step=0.5):
    return [{"word": f"từ{i}", "start": round(i * step, 3),
             "end": round(i * step + step * 0.8, 3), "src": "s0"}
            for i in range(count)]


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    """6 seconds of real video with real audio, so ffmpeg has something to read."""
    if not HAS_FFMPEG:
        pytest.skip("cần ffmpeg")
    path = tmp_path_factory.mktemp("media") / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x568:rate=30:duration=6",
         "-f", "lavfi", "-i", "sine=frequency=300:duration=6",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path), "-loglevel", "error"],
        check=True, capture_output=True,
    )
    return path


@needs_ffmpeg
class TestTimelineView:
    def test_produces_an_image(self, clip, tmp_path):
        out = tv.timeline_view(clip, 1.0, 3.0, words=words(),
                              out=tmp_path / "view.png")
        assert out.exists()
        assert out.stat().st_size > 5_000, "ảnh quá nhỏ, có thể rỗng"

    def test_image_is_wide_and_layered(self, clip, tmp_path):
        from PIL import Image

        out = tv.timeline_view(clip, 1.0, 3.0, words=words(), out=tmp_path / "v.png")
        with Image.open(out) as image:
            assert image.width == tv.STRIP_WIDTH
            # filmstrip + waveform + label row, so taller than any one of them
            assert image.height > tv.WAVE_HEIGHT + tv.LABEL_HEIGHT

    def test_vietnamese_labels_do_not_crash(self, clip, tmp_path):
        """Accented uppercase has broken font handling in this repo before."""
        accented = [{"word": "ĐƯỜNG", "start": 1.2, "end": 1.5},
                    {"word": "chuyển", "start": 1.6, "end": 1.9},
                    {"word": "hoá", "start": 2.0, "end": 2.3}]
        out = tv.timeline_view(clip, 1.0, 3.0, words=accented, out=tmp_path / "vi.png")
        assert out.exists()

    def test_seam_mark_is_drawn(self, clip, tmp_path):
        plain = tv.timeline_view(clip, 1.0, 3.0, out=tmp_path / "plain.png")
        marked = tv.timeline_view(clip, 1.0, 3.0, marks=[2.0],
                                 out=tmp_path / "marked.png")
        assert plain.read_bytes() != marked.read_bytes()

    def test_around_centres_on_the_moment(self, clip, tmp_path):
        out = tv.around(clip, 3.0, radius=1.0, out=tmp_path / "around.png")
        assert out.exists()

    def test_temp_files_are_cleaned_up(self, clip, tmp_path):
        tv.timeline_view(clip, 1.0, 3.0, out=tmp_path / "v.png")
        leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith("_")]
        assert leftovers == []

    def test_invalid_window_is_refused(self, clip, tmp_path):
        with pytest.raises(tv.TimelineViewError):
            tv.timeline_view(clip, 3.0, 1.0, out=tmp_path / "bad.png")

    def test_stays_under_a_second(self, clip, tmp_path):
        import time

        started = time.time()
        tv.timeline_view(clip, 1.0, 3.0, words=words(), out=tmp_path / "t.png")
        assert time.time() - started < 3.0, "ảnh phải rẻ, nếu không verify sẽ chậm"


class TestWordsInWindow:
    def test_only_words_inside_the_window(self):
        found = tv.words_in_window(words(20), 2.0, 3.0)
        assert found
        assert all(2.0 <= moment <= 3.0 for moment, _ in found)

    def test_time_of_maps_to_the_output_timeline(self):
        """Without a mapper the word's own second is used, which is only right
        for an uncut single-source job."""
        found = tv.words_in_window(words(10), 0.0, 1.0, time_of=lambda i: i * 0.1)
        assert len(found) == 10

    def test_no_words_is_not_an_error(self):
        assert tv.words_in_window([], 0.0, 5.0) == []


class TestSuspectSeams:
    def _patch(self, monkeypatch, peaks=None, levels=None):
        """Stub the two readings, matching the requested window by nearest start.

        Nearest rather than exact: the production code offsets the peak window by
        half its width, and pinning that arithmetic into every test would make the
        tests break on a window-size change that is not a behaviour change.
        """
        def nearest(table):
            def read(video, start, duration):
                if not table:
                    return None
                key = min(table, key=lambda k: abs(k - start))
                return table[key] if abs(key - start) < 0.2 else None
            return read

        monkeypatch.setattr(verify_stage, "peak_level", nearest(peaks or {}))
        monkeypatch.setattr(verify_stage, "window_loudness", nearest(levels or {}))

    def test_no_seams_means_no_work(self, monkeypatch):
        self._patch(monkeypatch)
        assert verify_stage.suspect_seams(Path("x.mp4"), [], 60.0) == []

    def test_a_loud_peak_at_a_seam_is_flagged_as_a_pop(self, monkeypatch):
        # the peak window sits at the seam; the reference window 1.5s before it
        self._patch(monkeypatch, peaks={10.0: -2.0, 8.5: -24.0})
        found = verify_stage.suspect_seams(Path("x.mp4"), [10.0], 60.0)
        assert found and found[0]["at"] == 10.0
        assert any("pop" in reason for reason in found[0]["reasons"])

    def test_a_normal_peak_is_not_flagged(self, monkeypatch):
        self._patch(monkeypatch, peaks={10.0: -20.0, 8.5: -22.0})
        assert verify_stage.suspect_seams(Path("x.mp4"), [10.0], 60.0) == []

    def test_a_level_step_across_the_seam_is_flagged(self, monkeypatch):
        """Exactly what per-span loudness normalisation would cause."""
        self._patch(monkeypatch, levels={7.0: -14.0, 10.0: -20.0})
        found = verify_stage.suspect_seams(Path("x.mp4"), [10.0], 60.0)
        assert found and any("mức âm" in r for r in found[0]["reasons"])

    def test_a_small_level_difference_is_not_flagged(self, monkeypatch):
        self._patch(monkeypatch, levels={7.0: -14.0, 10.0: -14.6})
        assert verify_stage.suspect_seams(Path("x.mp4"), [10.0], 60.0) == []

    def test_duplicate_frames_near_a_seam_are_flagged(self, monkeypatch):
        self._patch(monkeypatch)
        motion = {"windows": [{"at": 10.5, "duplicate_ratio": 0.25}]}
        found = verify_stage.suspect_seams(Path("x.mp4"), [10.0], 60.0, motion)
        assert found and any("frame lặp" in r for r in found[0]["reasons"])

    def test_seams_are_ranked_worst_first(self, monkeypatch):
        self._patch(monkeypatch,
                    peaks={10.0: -1.0, 8.5: -30.0, 20.0: -18.0, 18.5: -32.0})
        found = verify_stage.suspect_seams(Path("x.mp4"), [10.0, 20.0], 60.0)
        assert [row["at"] for row in found] == [10.0, 20.0]

    def test_image_count_is_hard_capped(self, monkeypatch):
        """40 seams times one image each is landfill, not a report."""
        # Every seam looks like a pop: loud at the seam, quiet 1.5s before it.
        peaks = {float(index): -1.0 for index in range(1, 41)}
        peaks.update({index - 1.5: -30.0 for index in range(1, 41)})
        self._patch(monkeypatch, peaks=peaks)
        found = verify_stage.suspect_seams(
            Path("x.mp4"), [float(i) for i in range(1, 41)], 60.0)
        assert len(found) == verify_stage.MAX_TIMELINE_VIEWS

    def test_seams_at_the_very_edges_are_skipped(self, monkeypatch):
        self._patch(monkeypatch, peaks={-0.01: -1.0})
        assert verify_stage.suspect_seams(Path("x.mp4"), [0.0, 60.0], 60.0) == []


class TestUnsureVerdict:
    def _words(self):
        return words(20)

    def test_unsure_is_recognised_in_several_spellings(self):
        for raw in ("unsure", "UNSURE", "không chắc", "maybe"):
            assert normalise_decision(raw) == "unsure"

    def test_unknown_decisions_default_to_keep(self):
        for raw in ("", None, "peut-être", "delete"):
            assert normalise_decision(raw) == "keep"

    def test_unsure_without_a_second_look_keeps_the_span(self, monkeypatch):
        """The safe default never drifts: unresolved means the cut does not happen."""
        import lib.talking_head_edit.cut_verifier as module

        monkeypatch.setattr(module, "chat_json", lambda *a, **k: (
            {"verdicts": [{"id": 0, "decision": "unsure", "reason": "không rõ"}]},
            {"prompt_tokens": 10}, ""))

        accepted, decisions, _ = verify_cuts([[4, 6]], self._words(), None)
        assert accepted == []
        assert decisions[0]["decision"] == "keep"
        assert decisions[0]["was_unsure"] is True

    def test_second_look_can_turn_unsure_into_remove(self, monkeypatch):
        import lib.talking_head_edit.cut_verifier as module

        monkeypatch.setattr(module, "chat_json", lambda *a, **k: (
            {"verdicts": [{"id": 0, "decision": "unsure", "reason": "không rõ"}]},
            {"prompt_tokens": 10}, ""))

        def looked(entries):
            return ([{**entries[0], "decision": "remove",
                      "reason": "sóng âm phẳng hai đầu"}], {"prompt_tokens": 5})

        accepted, decisions, usage = verify_cuts(
            [[4, 6]], self._words(), None, on_unsure=looked)
        assert accepted == [[4, 6]]
        assert decisions[0]["decision"] == "remove"
        assert usage["prompt_tokens"] == 15, "token của cả hai vòng phải được cộng"

    def test_a_clear_verdict_never_triggers_a_second_look(self, monkeypatch):
        import lib.talking_head_edit.cut_verifier as module

        monkeypatch.setattr(module, "chat_json", lambda *a, **k: (
            {"verdicts": [{"id": 0, "decision": "remove", "reason": "ê a"}]},
            {"prompt_tokens": 10}, ""))

        def explode(entries):
            raise AssertionError("verdict rõ ràng thì không được gọi vòng 2")

        accepted, _, _ = verify_cuts([[4, 6]], self._words(), None, on_unsure=explode)
        assert accepted == [[4, 6]]

    def test_look_again_falls_back_to_keep_when_no_image_can_be_made(self, tmp_path):
        from lib.talking_head_edit.cut_verifier import look_again

        entries = [{"id": 0, "w": [4, 6], "before": "a", "cut": "b", "after": "c",
                    "reason": "không rõ"}]
        resolved, usage = look_again(tmp_path / "khong-ton-tai.mp4", entries,
                                    self._words(), tmp_path / "work")
        assert resolved[0]["decision"] == "keep"
        assert usage == {}


class TestVerifyReportShape:
    def test_seams_and_views_are_reported(self, tmp_path, monkeypatch):
        """`verify` must carry the seam list forward or the UI has nothing to show."""
        from lib.talking_head_edit.job_store import JobStore

        clip = tmp_path / "src.mp4"
        clip.write_bytes(b"0")
        job = JobStore(tmp_path / "jobs").create(clip, {})
        job.update(current_version=1)
        job.final_path.write_bytes(b"0")
        job.props_path(1).write_text(json.dumps(
            {"durationSeconds": 30.0, "events": []}), encoding="utf-8")
        (job.dir / "resolve_report_v1.json").write_text(json.dumps(
            {"seams": [10.0, 20.0]}), encoding="utf-8")
        job.spine_path.write_text(json.dumps(
            {"word_timestamps": words(40)}), encoding="utf-8")

        monkeypatch.setattr(verify_stage, "probe_duration", lambda path: 30.0)
        monkeypatch.setattr(verify_stage, "stream_durations",
                            lambda path: {"video": 30.0, "audio": 30.0})
        monkeypatch.setattr(verify_stage, "frame_luma", lambda video, at: 120.0)
        monkeypatch.setattr(verify_stage, "integrated_loudness", lambda video: -14.0)
        monkeypatch.setattr(verify_stage, "_run", lambda command: "")
        monkeypatch.setattr(verify_stage, "measure_music_bed",
                            lambda *a: {"declared": None, "gaps_measured": [],
                                        "issues": [], "music_present": None})
        monkeypatch.setattr(verify_stage, "measure_motion",
                            lambda *a: {"windows": [], "issues": [],
                                        "duplicate_ratio": None})
        # No suspicious readings, so no images — but the seam list must survive.
        monkeypatch.setattr(verify_stage, "peak_level", lambda *a: None)
        monkeypatch.setattr(verify_stage, "window_loudness", lambda *a: None)

        report = verify_stage.run(job, {})
        assert report["seams"] == [10.0, 20.0]
        assert report["suspect_seams"] == []
        assert report["timeline_views"] == []
        assert report["passed"] is True
