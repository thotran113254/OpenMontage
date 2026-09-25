"""Cutting execution: spans in, one graded file out. Every ffmpeg call lives here.

Three modules meet at this point and each owns one failure mode:

* `resolve_media` owns the look/sound *recipes* — a wrong recipe looks bad
* `resolve_spans` owns WHICH pieces to take — a wrong span lands in the wrong
  second of the wrong file
* this module spends those spans on ffmpeg — a wrong command loses quality or
  produces a file the next stage cannot read

The whole design is one sentence: **video is encoded exactly once**. Each kept
span is encoded, the segments are concatenated with `-c copy`, and the master
audio pass copies the video stream. The obvious alternative — concat then
re-encode so loudnorm can run — encodes twice and throws away every sharpness
gain this pipeline measured (2.85 -> ~1.5 on a face crop). `apply_master_audio`
therefore *asserts* the copy rather than trusting it.

Dependency direction is one-way: this module reads from `resolve_media` and
`resolve_spans`, and neither of them knows anything about ffmpeg.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from lib.talking_head_edit.cpu_budget import cpu_budget, ffmpeg_decode_threads, ffmpeg_threads
from lib.talking_head_edit.resolve_media import (
    ResolveError,
    build_audio_master_chain,
    build_audio_span_chain,
    probe_duration,
    stream_durations,
)
from lib.talking_head_edit.resolve_spans import Span, merge_adjacent_spans

AV_TOLERANCE = 0.35  # max acceptable |video - audio| duration after cutting
# Concurrency for per-span encodes. Deliberately low: over-parallelising has
# already been shown to make a subsequent Remotion render drop frames on this
# machine (see the traps table in docs/talking-head-autoedit.md).
MAX_SPAN_WORKERS = 4
# A video stream whose size per second moves more than this between the joined
# file and the master-audio output was re-encoded rather than copied.
COPY_SIZE_TOLERANCE = 0.02


def can_copy_span(span: Span, grade_chain: str, tempo: float,
                  target_size: tuple[int, int], probe: dict[str, Any] | None = None) -> bool:
    """True when a span can be extracted with `-c copy` — zero loss, near-instant.

    Narrow by design: the moment anything transforms the picture, or the source
    is not already at the output size and codec, a copy would produce a segment
    the concat demuxer refuses to sit next to the others.
    """
    if grade_chain or abs(tempo - 1.0) > 1e-6:
        return False
    if not probe:
        return False
    if (int(probe.get("width") or 0), int(probe.get("height") or 0)) != target_size:
        return False
    return str(probe.get("video_codec") or "") == "h264"


# Every encoded piece of the timeline shares this format, which is what lets the
# concat demuxer join them with `-c copy` instead of a second encode.
_SEGMENT_FORMAT = ["-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2",
                   "-video_track_timescale", "90000"]


def extract_span(span: Span, out_path: Path, grade_chain: str, tempo: float,
                 fps: int = 30, preset: str = "medium", crf: int = 17,
                 copy_streams: bool = False, parallel_jobs: int = 1) -> Path:
    """Encode one span. THE ONLY PLACE VIDEO IS ENCODED.

    Everything after this point copies the video stream. Two encodes would undo
    the whole sharpness effort measured on this pipeline: the deliverable's face
    crop scored 2.85 with one encode and around 1.5 with two, and no amount of
    sharpening gets that back.
    """
    if copy_streams:
        result = subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{span.start:.3f}", "-to", f"{span.end:.3f}",
             "-i", str(span.path), "-c", "copy", "-avoid_negative_ts", "make_zero",
             str(out_path), "-loglevel", "error"],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and out_path.exists():
            return out_path
        # Fall through to a real encode: a copy that failed is not worth
        # diagnosing here, and the encode path always works.

    video_filters = [grade_chain] if grade_chain else []
    video_filters.append("setpts=PTS-STARTPTS")
    if abs(tempo - 1.0) > 1e-6:
        video_filters.append(f"setpts=PTS/{tempo}")
    audio_chain = build_audio_span_chain(tempo, span.duration)

    result = subprocess.run(
        ["ffmpeg", "-y", *ffmpeg_decode_threads(parallel_jobs),
         "-ss", f"{span.start:.3f}", "-to", f"{span.end:.3f}", "-i", str(span.path),
         "-vf", ",".join(video_filters), "-af", audio_chain,
         "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
         *ffmpeg_threads(parallel_jobs), *_SEGMENT_FORMAT, "-r", str(fps),
         str(out_path), "-loglevel", "error"],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not out_path.exists():
        raise ResolveError(
            f"ffmpeg cắt span {span.src_id} [{span.start:.2f}-{span.end:.2f}] thất bại: "
            f"{result.stderr.strip()[:400]}"
        )
    return out_path


def concat_segments(segments: list[Path], out_path: Path) -> float:
    """Glue segments with the concat demuxer and `-c copy`. Returns the duration.

    The demuxer requires identical codec, timebase and SAR across inputs, which
    holds because every segment came out of the same `extract_span` command line.
    The duration check afterwards is not decoration: concat failing partially is
    a real mode, and a short joined file would silently truncate the video.
    """
    if not segments:
        raise ResolveError("Không có segment nào để ghép.")

    list_file = out_path.with_name(f"{out_path.stem}_concat.txt")
    list_file.write_text(
        "".join(f"file '{p.resolve().as_posix()}'\n" for p in segments),
        encoding="utf-8")
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
             "-fflags", "+genpts", "-c", "copy", str(out_path), "-loglevel", "error"],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or not out_path.exists():
            raise ResolveError(
                f"Ghép {len(segments)} segment thất bại: {result.stderr.strip()[:400]}")

        expected = sum(probe_duration(p) for p in segments)
        actual = probe_duration(out_path)
        if expected and abs(actual - expected) > max(0.5, expected * 0.02):
            raise ResolveError(
                f"Ghép xong dài {actual:.2f}s nhưng tổng segment là {expected:.2f}s — "
                "concat đã bỏ mất phần nào đó, dừng lại thay vì dựng tiếp trên file thiếu."
            )
        return actual
    finally:
        list_file.unlink(missing_ok=True)


def apply_master_audio(joined: Path, out_path: Path, audio_preset: str = "shotgun") -> float:
    """Run the master audio chain over the joined timeline, COPYING the video.

    `-c:v copy` is the hard requirement of this whole design, so it is asserted
    rather than trusted: if ffmpeg silently re-encoded (a filter accidentally
    left on the video path, a container forcing a conversion), the video stream's
    bytes-per-second would move, and that is what the check compares.
    """
    before = video_stream_bitrate(joined)
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(joined), "-c:v", "copy",
         "-af", build_audio_master_chain(audio_preset),
         "-c:a", "aac", "-ar", "48000", str(out_path), "-loglevel", "error"],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not out_path.exists():
        raise ResolveError(
            f"Xử lý audio tổng thất bại: {result.stderr.strip()[:400]}")

    after = video_stream_bitrate(out_path)
    if before and after:
        drift = abs(after - before) / before
        if drift > COPY_SIZE_TOLERANCE:
            raise ResolveError(
                f"Luồng video bị encode lại ở bước audio tổng "
                f"({before / 1000:.0f} → {after / 1000:.0f} kbps, lệch {drift:.1%}). "
                "Bước này BẮT BUỘC phải -c:v copy — encode hai lần là mất hết độ nét."
            )
    return probe_duration(out_path)


def video_stream_bitrate(path: str | Path) -> float:
    """Video stream bytes per second. Used to prove a copy really was a copy.

    Reads the stream's own bitrate when the container reports one and falls back
    to size/duration, which is what mp4 from libx264 usually needs.
    """
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=bit_rate,nb_frames,duration",
         "-show_entries", "format=size,duration", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=120,
    ).stdout
    try:
        data = json.loads(out or "{}")
    except json.JSONDecodeError:
        return 0.0
    streams = data.get("streams") or [{}]
    bit_rate = streams[0].get("bit_rate")
    if bit_rate not in (None, "", "N/A"):
        try:
            return float(bit_rate)
        except ValueError:
            pass
    fmt = data.get("format") or {}
    try:
        size = float(fmt.get("size") or 0)
        duration = float(fmt.get("duration") or 0)
    except ValueError:
        return 0.0
    return (size * 8 / duration) if duration else 0.0


def cut_and_grade_multi(spans: list[Span], out_path: Path,
                        grade_chains: dict[str, str], tempo: float, fps: int = 30,
                        preset: str = "medium", crf: int = 17,
                        audio_preset: str = "shotgun",
                        work_dir: Path | None = None,
                        probes: dict[str, dict[str, Any]] | None = None,
                        target_size: tuple[int, int] | None = None,
                        max_workers: int = MAX_SPAN_WORKERS,
                        on_log: Any = None) -> tuple[float, list[float]]:
    """Spans from any number of sources → one graded, cut, loudness-managed file.

    Returns (duration, seam offsets on the output timeline). The seam list is
    what `verify` later uses to know where to look for pops and level jumps —
    without it a report can say "15% duplicate frames" but not where.

    `grade_chains` is per source id (with `"__all__"` as the default) because two
    phones in one shoot do not need the same correction, and `auto_sharpen`
    measures a different softness for each.
    """
    if not spans:
        raise ResolveError("Không có đoạn nào để giữ — kiểm tra lại danh sách cut.")

    spans = merge_adjacent_spans(spans)
    segments_dir = (work_dir or out_path.parent) / "_segments"
    segments_dir.mkdir(parents=True, exist_ok=True)

    def chain_for(span: Span) -> str:
        return grade_chains.get(span.src_id, grade_chains.get("__all__", ""))

    # Fast path only when EVERY span qualifies: mixing copied and encoded
    # segments produces files the concat demuxer will not join.
    copy_all = bool(target_size) and all(
        can_copy_span(span, chain_for(span), tempo, target_size,
                      (probes or {}).get(span.src_id))
        for span in spans)

    segments: list[Path] = []
    # Parallel spans share the CPU budget rather than each taking every core.
    workers = max(1, min(max_workers, cpu_budget(), len(spans)))
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(extract_span, span, segments_dir / f"seg_{index:04d}.mp4",
                            chain_for(span), tempo, fps, preset, crf, copy_all, workers)
                for index, span in enumerate(spans)
            ]
            # Ordered by index, not by completion: the timeline is the span order.
            segments = [future.result() for future in futures]

        durations = [probe_duration(segment) for segment in segments]
        seams: list[float] = []
        running = 0.0
        for duration in durations[:-1]:
            running += duration
            seams.append(round(running, 3))

        joined = segments_dir / "joined.mp4"
        concat_segments(segments, joined)
        total = apply_master_audio(joined, out_path, audio_preset)

        streams = stream_durations(out_path)
        drift = abs(streams.get("video", 0.0) - streams.get("audio", 0.0))
        if drift >= AV_TOLERANCE:
            raise ResolveError(
                f"Lệch video/audio {drift:.2f}s sau khi cắt (ngưỡng {AV_TOLERANCE}s) — "
                f"{streams}. Dừng lại vì mọi mốc thời gian phía sau sẽ trôi."
            )
        if on_log:
            on_log(f"{len(spans)} span → {total:.1f}s"
                   + (" (fast path -c copy)" if copy_all else "")
                   + f", {len(seams)} mối nối")
        return total, seams
    finally:
        shutil.rmtree(segments_dir, ignore_errors=True)


def prepend_teaser(base_video: Path, start: float, end: float, fps: int = 30,
                   preset: str = "medium", crf: int = 17) -> float:
    """Cut [start,end] out of the finished timeline and glue it on the front.

    Only the teaser is encoded (a cut off-keyframe has to be); it is encoded in
    the segment format and joined with `-c copy`, so the main timeline keeps its
    single encode instead of paying a second full-length one.

    Returns the teaser length, i.e. the offset every event must shift by.
    """
    teaser = base_video.with_name("_teaser_tmp.mp4")
    base = base_video.with_name("_base_tmp.mp4")
    base_video.replace(base)
    fade_start = max(0.0, (end - start) - 0.02)
    try:
        # 20ms fade-out only — 80ms used to dump a hole of silence onto the
        # first phoneme of the main take (shotgun expander then crushed it).
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(base),
             "-af", f"afade=t=out:st={fade_start:.3f}:d=0.02",
             "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
             *ffmpeg_threads(), *_SEGMENT_FORMAT, "-r", str(fps),
             str(teaser), "-loglevel", "error"],
            check=True, capture_output=True, text=True,
        )
        concat_segments([teaser, base], base_video)
    except (subprocess.CalledProcessError, ResolveError) as exc:
        base.replace(base_video)
        detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        raise ResolveError(f"Ghép cold-open thất bại: {str(detail)[:300]}") from exc
    finally:
        teaser.unlink(missing_ok=True)
        base.unlink(missing_ok=True)
    return probe_duration(base_video)


# The one deliberate exception to "video is encoded exactly once" above: this
# file is never staged into a render (stage_assets only ever reads src_path),
# so re-encoding it a second time costs nothing the deliverable pays for.
#
# `src_path` is intermediate_crf (12 by default) — tens of Mbps, because that
# quality is what the renderer reads frames from during the real render.
# Streamed straight to a browser <video> tag it is a different problem: Chrome
# falls behind a 50-70 Mbps 1080x1920 stream, `OffthreadVideo`'s
# `pauseWhenBuffering` default keeps pausing to rebuffer, and audio — tied to
# that same paused state — drops out with it. Measured on this pipeline's own
# footage: intermediate ~800 MB / ~55 Mbps vs. the actual deliverable
# (render_crf 17) at 98 MB / 6 Mbps for the same runtime — the gap this
# function exists to close for the Player specifically.
PREVIEW_PROXY_CRF = 24
PREVIEW_PROXY_PRESET = "veryfast"


def make_preview_proxy(src: Path, out_path: Path,
                       crf: int = PREVIEW_PROXY_CRF,
                       preset: str = PREVIEW_PROXY_PRESET) -> None:
    """A browser-friendly re-encode of `src`, same resolution, far lower bitrate.

    Audio is copied rather than re-encoded — it was never the problem, and a
    copy is both instant and lossless. `+faststart` moves the moov atom to the
    front of the file, which is what lets a browser start playing (and lets
    the media route's Range support actually help) before the whole file has
    downloaded.
    """
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src),
         "-c:v", "libx264", "-preset", preset, "-crf", str(crf), *ffmpeg_threads(),
         "-c:a", "copy", "-movflags", "+faststart",
         str(out_path), "-loglevel", "error"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ResolveError(
            f"Dựng bản xem trước nhẹ thất bại: {result.stderr.strip()[:300]}")
