"""Preview helpers — the fast loop that replaces full renders.

A full render is ~5 minutes, so these exist to answer "does it look right?"
in seconds. The tests cover the range maths and the colour metric; the ffmpeg
and Remotion calls themselves are stubbed.
"""

from __future__ import annotations

import json

import pytest

from lib.talking_head_edit import preview
from lib.talking_head_edit.job_store import JobStore


@pytest.fixture()
def job(tmp_path):
    source = tmp_path / "footage.mp4"
    source.write_bytes(b"video")
    store = JobStore(root=tmp_path / "jobs")
    job = store.create(source, {}, title="thu nghiem")
    job.update(current_version=1)
    job.src_path.write_bytes(b"cut video")   # staging links this before previewing
    job.props_path(1).write_text(json.dumps({
        "videoSrc": "src.mp4", "events": [], "durationSeconds": 40.0,
    }), encoding="utf-8")
    return job


class TestWarmBias:
    def test_neutral_image_reads_as_zero(self, monkeypatch):
        monkeypatch.setattr(preview, "_run", lambda *a, **k: type("R", (), {
            "stdout": "lavfi.signalstats.YAVG=120\nlavfi.signalstats.UAVG=128\n"
                      "lavfi.signalstats.VAVG=128\n", "stderr": "", "returncode": 0})())
        stats = preview.measure_cast(preview.Path("x.png"))
        assert stats["warm_bias"] == 0

    def test_orange_cast_reads_positive(self, monkeypatch):
        monkeypatch.setattr(preview, "_run", lambda *a, **k: type("R", (), {
            "stdout": "lavfi.signalstats.YAVG=120\nlavfi.signalstats.UAVG=110\n"
                      "lavfi.signalstats.VAVG=145\n", "stderr": "", "returncode": 0})())
        assert preview.measure_cast(preview.Path("x.png"))["warm_bias"] == 35

    def test_cool_cast_reads_negative(self, monkeypatch):
        monkeypatch.setattr(preview, "_run", lambda *a, **k: type("R", (), {
            "stdout": "lavfi.signalstats.YAVG=120\nlavfi.signalstats.UAVG=140\n"
                      "lavfi.signalstats.VAVG=120\n", "stderr": "", "returncode": 0})())
        assert preview.measure_cast(preview.Path("x.png"))["warm_bias"] == -20


class TestPreviewClip:
    def _stub_render(self, monkeypatch, captured: list):
        def fake_run(command, timeout=300, cwd=None):
            captured.append(command)
            out = command[5]
            preview.Path(out).write_bytes(b"clip")
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        monkeypatch.setattr(preview, "_run", fake_run)

    def test_renders_the_requested_window_at_half_size(self, job, monkeypatch):
        captured: list = []
        self._stub_render(monkeypatch, captured)
        result = preview.preview_clip(job, start_seconds=10, duration=8)
        assert "--frames=300-540" in captured[0]
        assert "--scale=0.5" in captured[0]
        assert result["duration_seconds"] == pytest.approx(8.03, abs=0.05)

    def test_clamps_to_the_end_of_the_timeline(self, job, monkeypatch):
        captured: list = []
        self._stub_render(monkeypatch, captured)
        preview.preview_clip(job, start_seconds=38, duration=30)
        frames = [arg for arg in captured[0] if arg.startswith("--frames=")][0]
        last = int(frames.split("=")[1].split("-")[1])
        assert last <= 40 * 30 - 1, "không được render quá độ dài bản dựng"

    def test_start_past_the_end_is_pulled_back(self, job, monkeypatch):
        captured: list = []
        self._stub_render(monkeypatch, captured)
        result = preview.preview_clip(job, start_seconds=999, duration=5)
        assert result["start_seconds"] <= 39

    def test_missing_props_is_a_clear_error(self, job, monkeypatch):
        job.update(current_version=7)
        with pytest.raises(preview.PreviewError, match="resolve"):
            preview.preview_clip(job)

    def test_writes_into_the_preview_folder_not_the_deliverable(self, job, monkeypatch):
        captured: list = []
        self._stub_render(monkeypatch, captured)
        result = preview.preview_clip(job)
        assert "preview" in result["rel"]
        assert not job.final_path.exists(), "xem thử không được đụng vào final.mp4"


class TestRenderGradeContext:
    """The preview must match what resolve will encode, or approving a look here
    approves a picture the render never produces.

    Measured divergence before this existed: the render applied sharpen 1.5 at
    1012x1800 while the preview applied 0.6 at 1080x1920 — a visibly softer frame.
    """

    def test_size_follows_the_frame_preset(self, job):
        job.update(options={**job.load()["options"], "frame_preset": "dark"})
        ctx = preview.render_grade_context(job)
        assert (ctx["width"], ctx["height"]) == (1012, 1800)

        job.update(options={**job.load()["options"], "frame_preset": "none"})
        ctx = preview.render_grade_context(job)
        assert (ctx["width"], ctx["height"]) == (1080, 1920)

    def test_source_width_comes_from_probe(self, job):
        job.update(probe={"width": 720, "height": 1280})
        assert preview.render_grade_context(job)["source_width"] == 720

    def test_missing_probe_is_none_not_zero(self, job):
        """0 would read as a divide-by-zero upscale; None means "unknown"."""
        assert preview.render_grade_context(job)["source_width"] is None

    def test_uses_the_measured_sharpening_when_auto_sharpen_has_run(self, job):
        (job.dir / "sharpen_report.json").write_text(
            json.dumps({"sharpen": 1.5, "clarity": 0.85}), encoding="utf-8")
        ctx = preview.render_grade_context(job)
        assert ctx["measured_sharpen"] == {"sharpen": 1.5, "clarity": 0.85}
        assert ctx["sharpen_source"] == "measured"

    def test_a_human_sharpen_beats_the_measurement(self, job):
        """Same precedence as stages/resolve.py — auto_sharpen yields to a human."""
        (job.dir / "sharpen_report.json").write_text(
            json.dumps({"sharpen": 1.5, "clarity": 0.85}), encoding="utf-8")
        job.update(options={**job.load()["options"], "grade_overrides": {"sharpen": 0.9}})
        ctx = preview.render_grade_context(job)
        assert ctx["measured_sharpen"] is None
        assert ctx["sharpen_source"] == "human"

    def test_auto_sharpen_off_ignores_the_report(self, job):
        (job.dir / "sharpen_report.json").write_text(
            json.dumps({"sharpen": 1.5}), encoding="utf-8")
        job.update(options={**job.load()["options"], "auto_sharpen": False})
        ctx = preview.render_grade_context(job)
        assert ctx["measured_sharpen"] is None
        assert ctx["sharpen_source"] == "estimated"

    def test_corrupt_report_does_not_break_the_preview(self, job):
        (job.dir / "sharpen_report.json").write_text("{not json", encoding="utf-8")
        assert preview.render_grade_context(job)["measured_sharpen"] is None


class TestGradeFrameCache:
    """Typing a new number should only re-render the variant that changed."""

    @pytest.fixture()
    def stub_ffmpeg(self, monkeypatch):
        built: list[dict] = []

        def fake_still(source, at, grade, out_path, width, height, source_width=None):
            built.append({"grade": dict(grade), "size": (width, height),
                          "source_width": source_width})
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"png")
            return out_path

        monkeypatch.setattr(preview, "grade_still", fake_still)
        monkeypatch.setattr(preview, "measure_cast", lambda p, crop=None: {"luma": 100.0})
        monkeypatch.setattr(preview, "measure_face", lambda p: {"luma": 105.0})
        monkeypatch.setattr(preview, "contact_sheet", lambda images, out, panel_width=380: (
            out.write_bytes(b"sheet"), out)[1])
        monkeypatch.setattr(preview, "probe_duration", lambda p: 100.0)
        return built

    def test_second_identical_call_renders_nothing(self, job, stub_ffmpeg):
        preview.preview_grades(job, {"hien_tai": {"warmth": 2}}, at_seconds=10.0)
        assert len(stub_ffmpeg) == 2          # raw + hien_tai
        report = preview.preview_grades(job, {"hien_tai": {"warmth": 2}}, at_seconds=10.0)
        assert len(stub_ffmpeg) == 2          # nothing new
        assert report["cached"] is True

    def test_changing_one_variant_reuses_the_others(self, job, stub_ffmpeg):
        preview.preview_grades(job, {"hien_tai": {"warmth": 2}}, at_seconds=10.0)
        stub_ffmpeg.clear()
        preview.preview_grades(job, {"hien_tai": {"warmth": 2}, "thu_nghiem": {"warmth": 9}},
                               at_seconds=10.0)
        assert [entry["grade"]["warmth"] for entry in stub_ffmpeg] == [9]

    def test_measured_sharpening_reaches_ffmpeg(self, job, stub_ffmpeg):
        (job.dir / "sharpen_report.json").write_text(
            json.dumps({"sharpen": 1.5, "clarity": 0.85}), encoding="utf-8")
        job.update(probe={"width": 720})
        preview.preview_grades(job, {"hien_tai": {"warmth": 2}}, at_seconds=10.0)
        applied = stub_ffmpeg[-1]
        assert applied["grade"]["sharpen"] == 1.5
        assert applied["grade"]["clarity"] == 0.85
        assert applied["size"] == (1012, 1800)
        assert applied["source_width"] == 720

    def test_a_different_moment_is_a_different_frame(self, job, stub_ffmpeg):
        preview.preview_grades(job, {"hien_tai": {}}, at_seconds=10.0)
        stub_ffmpeg.clear()
        preview.preview_grades(job, {"hien_tai": {}}, at_seconds=20.0)
        assert len(stub_ffmpeg) == 2          # raw and variant both re-shot

    def test_cache_is_trimmed_so_the_folder_cannot_grow_forever(self, job, stub_ffmpeg,
                                                                monkeypatch):
        monkeypatch.setattr(preview, "MAX_CACHED_GRADE_FRAMES", 4)
        for moment in range(6):
            preview.preview_grades(job, {"hien_tai": {}}, at_seconds=float(moment))
        frames = list((job.dir / "preview").glob("grade_*.png"))
        assert len(frames) <= 4
