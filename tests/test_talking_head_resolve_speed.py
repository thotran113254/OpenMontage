"""Resolve speed changes that must not change the picture.

These run real ffmpeg on tiny synthetic clips: the claim under test is "same
output, less work", and only the filters themselves can prove the first half.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from lib.talking_head_edit import resolve_cut
from lib.talking_head_edit.resolve_cut import (
    cut_and_grade_multi,
    extract_span,
    extract_span_audio,
    extract_video_slice,
    join_span_slices,
    plan_slices,
    slice_bounds,
    timeline_grid_filter,
)
from lib.talking_head_edit.resolve_media import (
    _blemish_reduce_chain,
    build_grade_chain,
    stream_durations,
)
from lib.talking_head_edit.resolve_spans import Span

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="cần ffmpeg")

# The skin mask exactly as it was built with `geq` before it moved to `lut2`.
_GEQ_MASK = ("[fsmaskin]format=yuv444p,"
             "geq=lum='if(gt(cr(X\\,Y)-cb(X\\,Y)\\,32)\\,255\\,0)':cb=128:cr=128,")


def _framemd5(filtergraph: str, source: str) -> list[str]:
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", source,
         "-filter_complex", f"[0:v]{filtergraph}", "-f", "framemd5", "-"],
        capture_output=True, text=True, check=True)
    return [line.rsplit(",", 1)[-1].strip()
            for line in result.stdout.splitlines() if not line.startswith("#")]


def test_the_lut2_skin_mask_is_bit_identical_to_the_geq_one():
    # testsrc2 has warm and neutral areas, so both mask branches (255 and 0) are hit.
    source = "testsrc2=size=96x160:rate=10:duration=0.5"
    current = _blemish_reduce_chain(0.4)
    reference = re.sub(r"\[fsmaskin\]format=yuv444p,split=2.*?:c1=128:c2=128,",
                       lambda _: _GEQ_MASK, current)
    assert reference != current, "the reference must actually use geq"
    frames = _framemd5(current, source)
    assert frames and frames == _framemd5(reference, source)


class TestTimelineGridFilter:
    def test_60fps_phone_footage_is_taken_at_the_timeline_rate(self):
        assert timeline_grid_filter(60.0, 30, 1.0) == "fps=fps=30:start_time=0.000000"

    def test_a_speed_up_needs_fewer_source_frames_per_second(self):
        assert timeline_grid_filter(59.94, 30, 1.08).startswith("fps=fps=30/1.08:")

    def test_the_grid_can_be_pinned_for_a_piece_of_a_span(self):
        assert timeline_grid_filter(60.0, 30, 1.0, 0.0033).endswith("start_time=0.003300")

    @pytest.mark.parametrize("source_fps", [None, 0])
    def test_unknown_source_rate_leaves_it_to_the_output_rate(self, source_fps):
        assert timeline_grid_filter(source_fps, 30, 1.0) is None


def _encode_source(path: Path, seconds: int = 2, rate: str = "60") -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc2=size=160x284:rate={rate}:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)],
        check=True)


def _frames_and_rate(path: Path) -> tuple[int, str]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=r_frame_rate,nb_read_frames", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True)
    rate, frames = result.stdout.strip().split(",")
    return int(frames), rate


@pytest.mark.parametrize("tempo", [1.0, 1.08])
def test_decimating_before_the_grade_keeps_exactly_the_timeline_frames(tmp_path, tempo):
    """Dropping early must not lose frames the timeline needs, and the tail is
    no longer padded: letting `-r` halve 60fps at the output used to turn a
    2.000s span into 62 frames (2.067s of video over 2.000s of audio)."""
    source = tmp_path / "phone60.mp4"
    _encode_source(source)
    span = Span("s0", source, 0.0, 2.0)
    chain = build_grade_chain({"blemish_reduce": 0.3}, 90, 160)

    out = extract_span(span, tmp_path / "out.mp4", chain, tempo, fps=30,
                       preset="ultrafast", crf=30, source_fps=60.0)

    frames, rate = _frames_and_rate(out)
    assert rate == "30/1"
    assert abs(frames - span.duration / tempo * 30) <= 0.5


class TestPlanSlices:
    def test_one_long_span_is_spread_over_every_worker(self):
        assert plan_slices([93.4], 4) == [4]

    def test_enough_spans_already_fill_the_workers(self):
        assert plan_slices([30.0, 30.0, 30.0, 30.0], 4) == [1, 1, 1, 1]

    def test_spare_workers_go_to_the_longest_span(self):
        assert plan_slices([60.0, 5.0], 4) == [3, 1]

    def test_no_piece_is_shorter_than_the_minimum(self):
        assert plan_slices([12.0], 4, min_slice=8.0) == [1]
        assert plan_slices([20.0], 4, min_slice=8.0) == [2]

    def test_frames_of_the_pieces_add_up_to_the_whole_span(self):
        span = Span("s0", Path("x.mp4"), 3.0, 96.437)
        bounds = slice_bounds(span, 4, 30, 1.08)
        assert sum(frames for _, _, frames in bounds) == round(span.duration / 1.08 * 30)
        assert bounds[0][0] == span.start and bounds[-1][1] == span.end
        # contiguous: every piece starts where the previous one ended
        assert all(a[1] == b[0] for a, b in zip(bounds, bounds[1:]))


def _decoded_md5(path: Path) -> list[str]:
    result = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v",
                             "-f", "framemd5", "-"], capture_output=True, text=True, check=True)
    return [line.rsplit(",", 1)[-1].strip()
            for line in result.stdout.splitlines() if not line.startswith("#")]


@pytest.mark.parametrize("tempo", [1.0, 1.08])
@pytest.mark.parametrize("rate,source_fps", [("60", 60.0), ("30000/1001", 29.97)])
def test_a_span_split_into_pieces_decodes_to_the_same_frames(tmp_path, tempo, rate, source_fps):
    """Lossless (crf 0), so any doubled, dropped or shifted frame at a join shows."""
    source = tmp_path / "phone.mp4"
    _encode_source(source, seconds=6, rate=rate)
    span = Span("s0", source, 0.5, 5.5)
    chain = build_grade_chain({"blemish_reduce": 0.3}, 90, 160)

    whole = extract_span(span, tmp_path / "whole.mp4", chain, tempo, fps=30,
                         preset="ultrafast", crf=0, source_fps=source_fps)
    pieces = [extract_video_slice(span, start, end, frames, tmp_path / f"v{i}.mp4",
                                  chain, tempo, 30, "ultrafast", 0, 1, source_fps)
              for i, (start, end, frames) in enumerate(slice_bounds(span, 3, 30, tempo))]
    audio = extract_span_audio(span, tmp_path / "a.m4a", tempo)
    split = join_span_slices(pieces, audio, tmp_path / "split.mp4")

    assert _decoded_md5(split) == _decoded_md5(whole)
    assert _frames_and_rate(split)[1] == "30/1"


def test_a_resolve_with_one_long_span_uses_every_worker_and_stays_in_sync(tmp_path, monkeypatch):
    monkeypatch.setattr(resolve_cut, "MIN_SLICE_SECONDS", 1.5)
    monkeypatch.setattr(resolve_cut, "cpu_budget", lambda: 4)
    source = tmp_path / "phone60.mp4"
    _encode_source(source, seconds=8)
    spans = [Span("s0", source, 0.4, 7.6)]
    logs: list[str] = []

    total, seams = cut_and_grade_multi(
        spans, tmp_path / "src.mp4", {"__all__": build_grade_chain({}, 90, 160)}, 1.08,
        fps=30, preset="ultrafast", crf=30, work_dir=tmp_path,
        probes={"s0": {"fps": 60.0}}, on_log=logs.append)

    assert "4 lát song song" in logs[-1]
    assert seams == []   # pieces of one span are not seams
    streams = stream_durations(tmp_path / "src.mp4")
    assert abs(streams["video"] - streams["audio"]) < 1 / 30
    frames, rate = _frames_and_rate(tmp_path / "src.mp4")
    assert rate == "30/1" and frames == round(7.2 / 1.08 * 30)
    # container duration: the AAC tail rounds up to whole 1024-sample frames
    assert abs(total - 7.2 / 1.08) < 0.05

