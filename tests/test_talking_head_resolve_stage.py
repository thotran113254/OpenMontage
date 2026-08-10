"""The resolve stage end to end, with ffmpeg stubbed.

Everything else about resolve is covered as pure logic in
test_talking_head_resolve.py. What was NOT covered is `run()` itself — a trace
over the whole suite showed the cold-open branch, the bgm branch and the
auto-sharpen guard never executing, so a change to any of them could pass 225
tests while being wrong.

ffmpeg and the sharpening measurement are replaced; the branch logic, the props
that reach the renderer, and the timeline offset maths are real.
"""

from __future__ import annotations

import json

import pytest

from lib.talking_head_edit.job_store import JobStore
from lib.talking_head_edit.stages import resolve as resolve_stage

CUT_DURATION = 40.0        # what the stubbed cut/grade reports
TEASER_TOTAL = 42.5        # what the stubbed teaser prepend reports
BGM_NAME = "bgm_energy_drive.mp3"


def words(count: int = 30, step: float = 1.0) -> list[dict[str, object]]:
    return [{"word": f"w{i}", "start": round(i * step, 3), "end": round(i * step + 0.8, 3)}
            for i in range(count)]


@pytest.fixture()
def job(tmp_path):
    source = tmp_path / "footage.mp4"
    source.write_bytes(b"video")
    store = JobStore(root=tmp_path / "jobs")
    job = store.create(source, {}, title="resolve stage")
    job.update(current_version=1,
               probe={"duration_seconds": 60.0, "width": 720, "height": 1280})
    job.spine_path.write_text(json.dumps({"word_timestamps": words()}), encoding="utf-8")
    return job


@pytest.fixture()
def stubs(monkeypatch):
    """Replace the two ffmpeg calls and the sharpening measurement."""
    calls: dict[str, object] = {"teaser": 0}

    def fake_cut_and_grade_multi(spans, out_path, grade_chains, tempo, fps, **kwargs):
        calls["grade_chains"] = dict(grade_chains)
        calls["grade_chain"] = grade_chains.get("s0", "")
        calls["spans"] = [(s.src_id, s.start, s.end) for s in spans]
        calls["kept"] = [(s.start, s.end) for s in spans]
        calls["preset"] = kwargs.get("preset")
        calls["crf"] = kwargs.get("crf")
        out_path.write_bytes(b"cut video")
        seams = [round(sum(s.duration for s in spans[:i + 1]) / tempo, 3)
                 for i in range(len(spans) - 1)]
        return CUT_DURATION, seams

    def fake_prepend(base_video, start, end, fps, **kwargs):
        calls["teaser"] = int(calls["teaser"]) + 1
        calls["teaser_window"] = (round(start, 3), round(end, 3))
        return TEASER_TOTAL

    monkeypatch.setattr(resolve_stage, "cut_and_grade_multi", fake_cut_and_grade_multi)
    monkeypatch.setattr(resolve_stage, "prepend_teaser", fake_prepend)
    # The bgm branch probes the music file for its real length.
    monkeypatch.setattr(resolve_stage, "probe_duration", lambda path: 90.0)
    # auto_sharpen would decode frames; assert on what it was given instead.
    # Shape must match sharpen_calibrate.calibrate — resolve logs every field.
    monkeypatch.setattr(
        resolve_stage, "calibrate",
        lambda *args, **kwargs: {"sharpen": 1.4, "clarity": 0.8, "detail": 3.0,
                                 "detail_goc": 2.1, "overshoot": 0.9,
                                 "ly_do": "đạt mục tiêu", "so_khung_do": 3,
                                 "target_detail": 2.9, "da_thu": []},
    )
    return calls


def write_spec(job, **overrides):
    spec = {
        "grade": {"brightness": 0.02, "contrast": 1.05},
        "cards": [], "events": [],
        "cut_remove": [],
        "cold_open": {"w0": 2, "w1": 8, "caption": "câu đắt nhất"},
        "endcard": {"title": "chốt", "subtitle": "theo dõi nhé"},
        "bgm": {"name": BGM_NAME, "volume": 0.22},
    }
    spec.update(overrides)
    job.spec_path(1).write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return spec


def props_of(job):
    return json.loads(job.props_path(1).read_text(encoding="utf-8"))


class TestColdOpenSwitch:
    def test_on_by_default(self, job, stubs):
        write_spec(job)
        resolve_stage.run(job, {})
        assert stubs["teaser"] == 1
        assert props_of(job)["durationSeconds"] > CUT_DURATION

    def test_explicit_null_does_not_disable_it(self, job, stubs):
        """A form posting `{"cold_open": null}` must not silently drop the teaser."""
        write_spec(job)
        resolve_stage.run(job, {"cold_open": None})
        assert stubs["teaser"] == 1

    def test_explicit_false_disables_it(self, job, stubs):
        write_spec(job)
        resolve_stage.run(job, {"cold_open": False})
        assert stubs["teaser"] == 0

    def test_events_shift_past_the_teaser(self, job, stubs):
        write_spec(job)
        resolve_stage.run(job, {})
        offset = round(TEASER_TOTAL - CUT_DURATION, 3)
        # The teaser's own overlays sit inside the offset; the endcard is last.
        endcard = [e for e in props_of(job)["events"] if e["type"] == "endcard"][0]
        assert endcard["at"] == pytest.approx(TEASER_TOTAL - 0.15, abs=0.01)
        assert offset > 0


class TestBgmSwitch:
    def test_on_by_default(self, job, stubs):
        write_spec(job)
        resolve_stage.run(job, {})
        assert props_of(job)["bgm"]["name"] == BGM_NAME

    def test_explicit_null_does_not_disable_it(self, job, stubs):
        write_spec(job)
        resolve_stage.run(job, {"bgm": None})
        assert "bgm" in props_of(job)

    def test_explicit_false_disables_it(self, job, stubs):
        write_spec(job)
        resolve_stage.run(job, {"bgm": False})
        assert "bgm" not in props_of(job)

    def test_volume_is_clamped_to_the_safe_range(self, job, stubs):
        write_spec(job, bgm={"name": BGM_NAME, "volume": 5.0})
        resolve_stage.run(job, {})
        assert props_of(job)["bgm"]["volume"] <= 1.0

    def test_a_name_with_no_file_is_dropped_with_a_warning(self, job, stubs):
        write_spec(job, bgm={"name": "khong-co-that.mp3", "volume": 0.2})
        resolve_stage.run(job, {})
        assert "bgm" not in props_of(job)
        warnings = [e for e in job.read_events() if e["type"] == "warning"]
        assert any("khong-co-that.mp3" in e["message"] for e in warnings)


class TestAutoSharpenSwitch:
    def test_measurement_reaches_the_grade_chain_by_default(self, job, stubs):
        write_spec(job)
        resolve_stage.run(job, {})
        assert "unsharp=3:3:1.4" in str(stubs["grade_chain"])

    def test_explicit_null_does_not_disable_the_measurement(self, job, stubs):
        write_spec(job)
        resolve_stage.run(job, {"auto_sharpen": None})
        assert "unsharp=3:3:1.4" in str(stubs["grade_chain"])

    def test_explicit_false_falls_back_to_the_upscale_estimate(self, job, stubs):
        write_spec(job)
        resolve_stage.run(job, {"auto_sharpen": False})
        assert "unsharp=3:3:1.4" not in str(stubs["grade_chain"])

    def test_a_human_sharpen_wins_over_the_measurement(self, job, stubs):
        """The director's guess loses to the measurement; a human's does not."""
        write_spec(job)
        resolve_stage.run(job, {"grade_overrides": {"sharpen": 0.9}})
        assert "unsharp=3:3:0.9" in str(stubs["grade_chain"])


class TestFramePreset:
    def test_grade_targets_the_inset_a_roll_size(self, job, stubs):
        write_spec(job)
        resolve_stage.run(job, {"frame_preset": "dark"})
        assert "scale=1012:1800" in str(stubs["grade_chain"])
        assert props_of(job)["frame"]["background"] == "dark"

    def test_none_encodes_at_full_frame_and_adds_no_frame_props(self, job, stubs):
        write_spec(job)
        resolve_stage.run(job, {"frame_preset": "none"})
        assert "scale=1080:1920" in str(stubs["grade_chain"])
        assert "frame" not in props_of(job)

    def test_unknown_preset_warns_and_falls_back_to_dark(self, job, stubs):
        write_spec(job)
        resolve_stage.run(job, {"frame_preset": "khong-co-that"})
        assert props_of(job)["frame"]["background"] == "dark"
        assert any("khong-co-that" in e["message"] for e in job.read_events()
                   if e["type"] == "warning")


class TestPreviewProxy:
    """Step 1 of splitting "decide the cut" from "encode the cut": `resolve`
    now encodes `src.mp4` directly at draft quality (see PREVIEW_PROXY_CRF/
    PRESET), so a separate re-encode pass into `preview_src.mp4` right after
    would just spend seconds duplicating an already-light file. `videoSrc`
    itself is what the Player plays until `render` (step 2, not yet wired)
    starts re-encoding the SAME decided spans at deliverable quality — at
    which point `previewVideoSrc` needs to do real work again.
    """

    def test_previewVideoSrc_is_absent_player_falls_back_to_videoSrc(self, job, stubs):
        write_spec(job)
        resolve_stage.run(job, {})
        props = props_of(job)
        assert props["previewVideoSrc"] is None
        assert props["videoSrc"] == job.src_path.name

    def test_the_main_cut_runs_at_draft_quality_not_the_deliverable_options(self, job, stubs):
        """`intermediate_preset`/`intermediate_crf` must NOT reach this encode
        — they mean "the deliverable's encode", which does not exist yet."""
        from lib.talking_head_edit.resolve_cut import (
            PREVIEW_PROXY_CRF, PREVIEW_PROXY_PRESET,
        )

        write_spec(job)
        resolve_stage.run(job, {"intermediate_preset": "medium", "intermediate_crf": 12})
        assert stubs["preset"] == PREVIEW_PROXY_PRESET
        assert stubs["crf"] == PREVIEW_PROXY_CRF

    def test_make_preview_proxy_is_not_called_at_all(self, job, stubs, monkeypatch):
        called = []
        monkeypatch.setattr(resolve_stage, "make_preview_proxy",
                            lambda src, out: called.append((src, out)), raising=False)
        write_spec(job)
        resolve_stage.run(job, {})
        assert called == []
