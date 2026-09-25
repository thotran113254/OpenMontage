"""The full render re-encodes resolve's cut_plan at delivery quality.

Real ffmpeg on a tiny synthetic clip: the claims are "same frames as the draft,
better quality, built once", and only real files can show them.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.talking_head_edit import deliverable_src
from lib.talking_head_edit.resolve_cut import (
    PREVIEW_PROXY_CRF,
    PREVIEW_PROXY_PRESET,
    apply_master_audio,
    cut_and_grade_multi,
    prepend_teaser,
    span_audio_chain,
)
from lib.talking_head_edit.resolve_media import ResolveError, stream_durations
from lib.talking_head_edit.resolve_spans import Span, timeline_frames

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="cần ffmpeg")


def _job(tmp_path: Path) -> SimpleNamespace:
    events: list[tuple[str, str, str]] = []
    job = SimpleNamespace(dir=tmp_path, src_path=tmp_path / "src.mp4", events=events)
    job.emit = lambda kind, stage="", message="", **_: events.append((kind, stage, message))
    return job


def _draft(job, source: Path, teaser: list[float] | None) -> dict:
    """What resolve does: draft encode + the plan it persists."""
    plan = {
        "spans": [{"src": "s0", "path": str(source), "start": 0.3, "end": 3.1},
                  {"src": "s0", "path": str(source), "start": 4.2, "end": 7.7}],
        "grade_chains": {"__all__": "scale=90:160"},
        "tempo": 1.08, "fps": 30, "target_size": [90, 160], "audio_preset": "shotgun",
        "probes": {"s0": {"fps": 60.0}}, "teaser": teaser,
    }
    spans = [Span("s0", source, s["start"], s["end"]) for s in plan["spans"]]
    premaster = job.dir / "pm.mov"
    cut_and_grade_multi(spans, job.src_path, plan["grade_chains"], 1.08, 30,
                        preset=PREVIEW_PROXY_PRESET, crf=PREVIEW_PROXY_CRF, work_dir=job.dir,
                        probes=plan["probes"], target_size=(90, 160), keep_joined=premaster)
    if teaser:
        prepend_teaser(premaster, teaser[0], teaser[1], 30,
                       preset=PREVIEW_PROXY_PRESET, crf=PREVIEW_PROXY_CRF)
        apply_master_audio(premaster, job.src_path)
    premaster.unlink(missing_ok=True)
    (job.dir / "resolve_report_v1.json").write_text(json.dumps({"cut_plan": plan}))
    return plan


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "phone60.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=180x320:rate=60:duration=8",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(source)],
                   check=True)
    return source


def _bitrate(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=bit_rate", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return float(out)


@pytest.mark.parametrize("teaser", [None, [1.0, 2.8]])
def test_the_deliverable_has_the_drafts_frames_at_higher_quality(tmp_path, teaser):
    job = _job(tmp_path)
    _draft(job, _source(tmp_path), teaser)

    out = deliverable_src.ensure(job, 1, {"intermediate_preset": "ultrafast",
                                          "intermediate_crf": 12})

    assert deliverable_src.count_frames(out) == deliverable_src.count_frames(job.src_path)
    assert _bitrate(out) > 1.5 * _bitrate(job.src_path)
    streams = stream_durations(out)
    assert abs(streams["video"] - streams["audio"]) < 0.1   # loudnorm's 100ms block


def test_the_deliverable_is_built_once_and_rebuilt_when_the_quality_changes(tmp_path):
    job = _job(tmp_path)
    _draft(job, _source(tmp_path), None)
    options = {"intermediate_preset": "ultrafast", "intermediate_crf": 12}
    first = deliverable_src.ensure(job, 1, options)
    built_at = first.stat().st_mtime_ns

    assert deliverable_src.ensure(job, 1, options).stat().st_mtime_ns == built_at
    assert any("Dùng lại" in message for _, _, message in job.events)

    deliverable_src.ensure(job, 1, {**options, "intermediate_crf": 14})
    assert first.stat().st_mtime_ns != built_at


def test_a_frame_count_that_differs_from_the_draft_refuses_to_render(tmp_path):
    job = _job(tmp_path)
    plan = _draft(job, _source(tmp_path), None)
    plan["spans"][1]["end"] = 7.0          # the plan no longer matches the draft on disk
    (tmp_path / "resolve_report_v1.json").write_text(json.dumps({"cut_plan": plan}))

    with pytest.raises(ResolveError, match="khung"):
        deliverable_src.ensure(job, 1, {"intermediate_preset": "ultrafast"})
    assert not (tmp_path / deliverable_src.DELIVERABLE_NAME).exists()


def test_a_report_without_a_plan_asks_for_a_fresh_resolve(tmp_path):
    job = _job(tmp_path)
    (tmp_path / "resolve_report_v1.json").write_text(json.dumps({"spans": []}))
    with pytest.raises(ResolveError, match="chạy lại resolve"):
        deliverable_src.ensure(job, 1, {})


def test_each_segment_is_exactly_its_frames_long_in_audio_too(tmp_path):
    """Audio after atempo is not whole frames; left alone the concat demuxer
    starts the next piece after the longer stream and the timeline creeps."""
    source = _source(tmp_path)
    span = Span("s0", source, 0.37, 2.91)
    out = tmp_path / "seg.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "0.37", "-to", "3.0", "-i", str(source),
                    "-vn", "-af", span_audio_chain(span, 1.08, 30), "-c:a", "pcm_s16le",
                    "-ar", "48000", str(out)], check=True)
    samples = int(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=duration_ts", "-of", "csv=p=0",
         str(out)], capture_output=True, text=True).stdout.strip())
    assert samples == timeline_frames(span.duration, 30, 1.08) * 48000 // 30


def test_editing_a_comment_or_docstring_keeps_the_cache():
    """A docstring edit used to invalidate the deliverable and re-encode it."""
    code = 'def f(x):\n    """Old words."""\n    return x + 1  # note\n'
    reworded = 'def f(x):\n    """New, longer words."""\n    # a new comment\n    return x + 1\n'
    changed = 'def f(x):\n    """Old words."""\n    return x + 2\n'
    fingerprint = deliverable_src._code_fingerprint
    assert fingerprint(code) == fingerprint(reworded)
    assert fingerprint(code) != fingerprint(changed)
