"""Cutting execution: spans in, one graded file out. Every ffmpeg call lives here.

Three modules meet at this point and each owns one failure mode:

* `resolve_media` owns the look/sound *recipes* — a wrong recipe looks bad
* `resolve_spans` owns WHICH pieces to take — a wrong span lands in the wrong
  second of the wrong file
* this module spends those spans on ffmpeg — a wrong command loses quality or
  produces a file the next stage cannot read

The whole design is one sentence: **video is encoded exactly once**. Each kept
span is encoded, the segments are concatenated with `-c copy`, and the master
audio pass copies the video stream. Audio stays PCM until that master pass,
which is the one place it becomes AAC. The obvious alternative — concat then
re-encode so loudnorm can run — encodes twice and throws away every sharpness
gain this pipeline measured (2.85 -> ~1.5 on a face crop). `apply_master_audio`
therefore *asserts* the copy rather than trusting it.

Dependency direction is one-way: this module reads from `resolve_media` and
`resolve_spans`, and neither of them knows anything about ffmpeg.
"""

from __future__ import annotations

import json
import math
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
from lib.talking_head_edit.resolve_spans import Span, merge_adjacent_spans, timeline_frames

AV_TOLERANCE = 0.35  # max acceptable |video - audio| duration after cutting
# Concurrency for per-span encodes. Deliberately low: over-parallelising has
# already been shown to make a subsequent Remotion render drop frames on this
# machine (see the traps table in docs/talking-head-autoedit.md).
MAX_SPAN_WORKERS = 4
# A slice shorter than this spends more on seeking and x264 warm-up than the
# parallelism wins back.
MIN_SLICE_SECONDS = 8.0
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
#
# Audio is PCM (in .mov) until the master pass. AAC pieces carry 1024 samples
# of encoder priming at a negative timestamp and round their length up to a
# whole 1024-sample frame; the concat demuxer answered by starting every
# `src.mp4` video 21ms after its audio, and a cold-open teaser left a 54ms hole
# in the video at its join. PCM is sample-exact, so pieces butt together.
_SEGMENT_FORMAT = ["-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
                   "-video_track_timescale", "90000"]
SEGMENT_SUFFIX = ".mov"   # mp4 has no widely supported PCM audio


def timeline_grid_filter(source_fps: float | None, fps: int, tempo: float,
                         start_time: float = 0.0) -> str | None:
    """`fps=` filter that puts source frames on the timeline grid BEFORE the grade chain.

    The timeline is `fps` (30) but phones shoot 60: left to `-r` at the output,
    every filter in the grade chain ran on twice the frames `-r` then threw
    away — for the skin/blemish chain, half of the whole resolve. A tempo
    speed-up packs more source time into each output second, so frames are
    taken at `fps / tempo`: after `setpts=PTS/tempo` exactly `fps` remain and
    `-r` has nothing left to drop or pad. On footage already at the timeline
    rate the filter passes every frame through.

    `start_time` (post-seek input seconds) says where the grid starts; the
    filter rounds it to whole output periods from the seek point, which is why
    `extract_video_slice` seeks a whole number of periods early.
    Unknown source fps → no filter; `-r` does the job as before.
    """
    if not source_fps or tempo <= 0:
        return None
    # A ratio, not a rounded decimal: 30/1.08 must stay exact over a long span.
    rate = f"{fps}" if abs(tempo - 1.0) < 1e-6 else f"{fps}/{tempo:g}"
    return f"fps=fps={rate}:start_time={start_time:.6f}"


def _video_filters(grade_chain: str, tempo: float, fps: int,
                   source_fps: float | None, grid_start: float = 0.0) -> str:
    grid = timeline_grid_filter(source_fps, fps, tempo, grid_start)
    filters = [grid] if grid else []
    if grade_chain:
        filters.append(grade_chain)
    filters.append("setpts=PTS-STARTPTS")
    if abs(tempo - 1.0) > 1e-6:
        filters.append(f"setpts=PTS/{tempo}")
    return ",".join(filters)


def plan_slices(durations: list[float], workers: int,
                min_slice: float = MIN_SLICE_SECONDS) -> list[int]:
    """How many pieces each span's video is encoded in, side by side.

    The grade chain's filters run one after another, so one ffmpeg cannot fill
    the budget: 32s of 60fps footage with the skin/blemish chain averaged 3.4
    of 6 cores in one piece, 5.7 in four (634s → 128s, with the frame grid and
    `lut2` mask). With fewer spans than workers the rest of the budget sat
    idle. Spare workers go to whichever span has the longest pieces, while
    every piece stays at least `min_slice` long.
    """
    counts = [1] * len(durations)
    for _ in range(max(0, workers - len(durations))):
        best = max(range(len(durations)),
                   key=lambda i: durations[i] / (counts[i] + 1), default=None)
        if best is None or durations[best] / (counts[best] + 1) < min_slice:
            break
        counts[best] += 1
    return counts


def slice_bounds(span: Span, pieces: int, fps: int,
                 tempo: float) -> list[tuple[float, float, int]]:
    """(source start, source end, frames) per piece, cut on the output frame grid.

    Every boundary sits exactly on an output frame, and each piece is capped at
    its own frame count, so the pieces add up to the frames the unsplit span
    would have — none doubled at a join, none lost.
    """
    total = timeline_frames(span.duration, fps, tempo)
    step = tempo / fps   # source seconds per output frame
    edges = [round(total * index / pieces) for index in range(pieces + 1)]
    bounds = []
    for index in range(pieces):
        start = span.start + edges[index] * step
        end = span.end if index == pieces - 1 else span.start + edges[index + 1] * step
        bounds.append((start, end, edges[index + 1] - edges[index]))
    return bounds


def extract_video_slice(span: Span, start: float, end: float, frames: int, out_path: Path,
                        grade_chain: str, tempo: float, fps: int, preset: str, crf: int,
                        parallel_jobs: int, source_fps: float | None) -> Path:
    """Video only, `frames` frames from `start`: one piece of a split span.

    The `fps` filter rounds its grid to whole output periods counted from the
    seek point, so a piece must seek a whole number of periods before `start`
    for its grid to line up with the unsplit span's. It seeks at least one
    source frame early — the frame nearest the first grid point may sit just
    before it, and the unsplit span would have used that one. The first piece
    seeks exactly where the unsplit span does. `-frames:v` ends every piece on
    the grid.
    """
    grid = timeline_grid_filter(source_fps, fps, tempo)
    period = tempo / fps   # source seconds per output frame
    lead_periods = 0
    if grid and start - span.start > 1e-9:
        lead_periods = min(math.ceil(1.0 / source_fps / period - 1e-9),
                           math.floor(start / period + 1e-9))
    seek = start - lead_periods * period
    result = subprocess.run(
        ["ffmpeg", "-y", *ffmpeg_decode_threads(parallel_jobs),
         "-ss", f"{seek:.6f}", "-to", f"{_read_until(end, tempo, fps):.6f}", "-i", str(span.path),
         "-vf", _video_filters(grade_chain, tempo, fps, source_fps, lead_periods * period),
         "-an",
         "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
         *ffmpeg_threads(parallel_jobs), "-pix_fmt", "yuv420p",
         "-video_track_timescale", "90000", "-r", str(fps), "-frames:v", str(frames),
         str(out_path), "-loglevel", "error"],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not out_path.exists():
        raise ResolveError(
            f"ffmpeg cắt lát {span.src_id} [{start:.2f}-{end:.2f}] thất bại: "
            f"{result.stderr.strip()[:400]}")
    return out_path


# Spans are read this many output frames past their end. `-frames:v` and
# `atrim` cut both streams to the exact frame length, and the extra means
# neither comes up short when that length rounded up. (Padding with `apad`
# instead hangs ffmpeg 8 when a video stream shares the command.)
_TAIL_MARGIN_FRAMES = 2


def _read_until(end: float, tempo: float, fps: int) -> float:
    return end + _TAIL_MARGIN_FRAMES * tempo / fps


def span_audio_chain(span: Span, tempo: float, fps: int) -> str:
    """Seam fades + tempo, then cut to the span's exact frame length.

    The video is a whole number of frames; audio after atempo is not. The concat
    demuxer starts the next piece after the longer of the two, so the timeline
    crept by up to half a frame per cut and captions drifted off the words.
    Past the fade-out the audio is silent, so the few samples read beyond the
    span's end (see `_read_until`) are silence.
    """
    length = timeline_frames(span.duration, fps, tempo) / fps
    return build_audio_span_chain(tempo, span.duration) + f",atrim=duration={length:.6f}"


def extract_span_audio(span: Span, out_path: Path, tempo: float, fps: int = 30) -> Path:
    """The whole span's audio in one pass, so splitting the video adds no audio seam."""
    result = subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{span.start:.6f}", "-to", f"{_read_until(span.end, tempo, fps):.6f}",
         "-i", str(span.path), "-vn", "-af", span_audio_chain(span, tempo, fps),
         "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(out_path), "-loglevel", "error"],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not out_path.exists():
        raise ResolveError(
            f"ffmpeg tách tiếng span {span.src_id} thất bại: {result.stderr.strip()[:400]}")
    return out_path


def join_span_slices(video_slices: list[Path], audio: Path, out_path: Path) -> Path:
    """Video pieces + the span's audio → one segment, both streams copied."""
    list_file = out_path.with_name(f"{out_path.stem}_slices.txt")
    list_file.write_text(
        "".join(f"file '{p.resolve().as_posix()}'\n" for p in video_slices), encoding="utf-8")
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
             "-i", str(audio), "-map", "0:v", "-map", "1:a", "-c", "copy",
             "-video_track_timescale", "90000", str(out_path), "-loglevel", "error"],
            capture_output=True, text=True,
        )
    finally:
        list_file.unlink(missing_ok=True)
    if result.returncode != 0 or not out_path.exists():
        raise ResolveError(f"Ghép các lát của span thất bại: {result.stderr.strip()[:400]}")
    for piece in (*video_slices, audio):
        piece.unlink(missing_ok=True)
    return out_path


def extract_span(span: Span, out_path: Path, grade_chain: str, tempo: float,
                 fps: int = 30, preset: str = "medium", crf: int = 17,
                 copy_streams: bool = False, parallel_jobs: int = 1,
                 source_fps: float | None = None) -> Path:
    """Encode one span. With `extract_video_slice` (the same encode, for one
    piece of a span split across workers), THE ONLY PLACE VIDEO IS ENCODED.

    Everything after this point copies the video stream. Two encodes would undo
    the whole sharpness effort measured on this pipeline: the deliverable's face
    crop scored 2.85 with one encode and around 1.5 with two, and no amount of
    sharpening gets that back.
    """
    if copy_streams:
        result = subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{span.start:.3f}", "-to", f"{span.end:.3f}",
             "-i", str(span.path), "-c:v", "copy", "-c:a", "pcm_s16le", "-ar", "48000",
             "-ac", "2", "-avoid_negative_ts", "make_zero",
             str(out_path), "-loglevel", "error"],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and out_path.exists():
            return out_path
        # Fall through to a real encode: a copy that failed is not worth
        # diagnosing here, and the encode path always works.

    audio_chain = span_audio_chain(span, tempo, fps)

    # `-frames:v` stops `-r` padding the tail: halving 60fps at the output made
    # a 2.000s span 62 frames (2.067s of video over 2.000s of audio).
    result = subprocess.run(
        ["ffmpeg", "-y", *ffmpeg_decode_threads(parallel_jobs),
         "-ss", f"{span.start:.6f}", "-to", f"{_read_until(span.end, tempo, fps):.6f}",
         "-i", str(span.path),
         "-vf", _video_filters(grade_chain, tempo, fps, source_fps), "-af", audio_chain,
         "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
         *ffmpeg_threads(parallel_jobs), *_SEGMENT_FORMAT, "-r", str(fps),
         "-frames:v", str(timeline_frames(span.duration, fps, tempo)),
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
                        on_log: Any = None,
                        keep_joined: Path | None = None) -> tuple[float, list[float]]:
    """Spans from any number of sources → one graded, cut, loudness-managed file.

    Returns (duration, seam offsets on the output timeline). The seam list is
    what `verify` later uses to know where to look for pops and level jumps —
    without it a report can say "15% duplicate frames" but not where.

    `grade_chains` is per source id (with `"__all__"` as the default) because two
    phones in one shoot do not need the same correction, and `auto_sharpen`
    measures a different softness for each.

    `keep_joined` keeps the timeline as it was before the master audio pass
    (PCM, .mov): `prepend_teaser` joins onto that, then the master pass runs
    again over the whole programme.
    """
    if not spans:
        raise ResolveError("Không có đoạn nào để giữ — kiểm tra lại danh sách cut.")

    spans = merge_adjacent_spans(spans)
    segments_dir = (work_dir or out_path.parent) / "_segments"
    segments_dir.mkdir(parents=True, exist_ok=True)

    def chain_for(span: Span) -> str:
        return grade_chains.get(span.src_id, grade_chains.get("__all__", ""))

    def source_fps_of(span: Span) -> float | None:
        try:
            return float(((probes or {}).get(span.src_id) or {}).get("fps") or 0) or None
        except (TypeError, ValueError):
            return None

    # Fast path only when EVERY span qualifies: mixing copied and encoded
    # segments produces files the concat demuxer will not join.
    copy_all = bool(target_size) and all(
        can_copy_span(span, chain_for(span), tempo, target_size,
                      (probes or {}).get(span.src_id))
        for span in spans)

    segments: list[Path] = []
    # Parallel pieces share the CPU budget rather than each taking every core.
    cap = max(1, min(max_workers, cpu_budget()))
    # A span whose source fps is unknown gets no pinned frame grid, so its
    # pieces could not be joined frame-exactly: it stays in one piece.
    pieces = ([1] * len(spans) if copy_all
              else plan_slices([span.duration if source_fps_of(span) else 0.0
                                for span in spans], cap, MIN_SLICE_SECONDS))
    workers = max(1, min(cap, sum(pieces)))
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            planned: list[tuple[Path, list[Any], Any]] = []
            for index, (span, count) in enumerate(zip(spans, pieces)):
                segment = segments_dir / f"seg_{index:04d}{SEGMENT_SUFFIX}"
                if count == 1:
                    planned.append((segment, [pool.submit(
                        extract_span, span, segment, chain_for(span), tempo, fps,
                        preset, crf, copy_all, workers, source_fps_of(span))], None))
                    continue
                slices = [pool.submit(
                    extract_video_slice, span, start, end, frames,
                    segments_dir / f"seg_{index:04d}_v{part:02d}.mp4", chain_for(span),
                    tempo, fps, preset, crf, workers, source_fps_of(span))
                    for part, (start, end, frames)
                    in enumerate(slice_bounds(span, count, fps, tempo))]
                audio = pool.submit(extract_span_audio, span,
                                    segments_dir / f"seg_{index:04d}_a.wav", tempo, fps)
                planned.append((segment, slices, audio))
            # Ordered by index, not by completion: the timeline is the span order.
            for segment, video, audio in planned:
                if audio is None:
                    segments.append(video[0].result())
                else:
                    segments.append(join_span_slices(
                        [future.result() for future in video], audio.result(), segment))

        durations = [probe_duration(segment) for segment in segments]
        seams: list[float] = []
        running = 0.0
        for duration in durations[:-1]:
            running += duration
            seams.append(round(running, 3))

        joined = segments_dir / f"joined{SEGMENT_SUFFIX}"
        concat_segments(segments, joined)
        total = apply_master_audio(joined, out_path, audio_preset)
        if keep_joined:
            shutil.move(str(joined), str(keep_joined))

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
                   + (f", {sum(pieces)} lát song song" if sum(pieces) > len(spans) else "")
                   + f", {len(seams)} mối nối")
        return total, seams
    finally:
        shutil.rmtree(segments_dir, ignore_errors=True)


def prepend_teaser(timeline: Path, start: float, end: float, fps: int = 30,
                   preset: str = "medium", crf: int = 17) -> float:
    """Cut [start,end] out of the pre-master timeline and glue it on the front.

    `timeline` is the PCM file `cut_and_grade_multi(keep_joined=...)` kept; the
    caller runs `apply_master_audio` over it afterwards, so teaser and
    programme are levelled together and audio still becomes AAC only once.

    Only the teaser is encoded (a cut off-keyframe has to be); it is encoded in
    the segment format and joined with `-c copy`, so the main timeline keeps its
    single encode instead of paying a second full-length one. It is snapped to
    whole frames and capped with `-frames:v`, so its video and its PCM audio end
    together and the programme starts exactly where the teaser stops.

    Returns the joined duration; the caller subtracts the old one to get the
    offset every event must shift by.
    """
    teaser = timeline.with_name(f"_teaser_tmp{SEGMENT_SUFFIX}")
    base = timeline.with_name(f"_base_tmp{SEGMENT_SUFFIX}")
    timeline.replace(base)
    frames = max(1, round((end - start) * fps))
    end = start + frames / fps
    fade_start = max(0.0, (end - start) - 0.02)
    try:
        # 20ms fades only — 80ms used to dump a hole of silence onto the
        # first phoneme of the main take (shotgun expander then crushed it).
        # The fade-in lands in the lead-in breath `cold_open_window` keeps.
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{start:.6f}", "-to", f"{end:.6f}", "-i", str(base),
             "-af", f"afade=t=in:st=0:d=0.02,afade=t=out:st={fade_start:.3f}:d=0.02",
             "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
             *ffmpeg_threads(), *_SEGMENT_FORMAT, "-r", str(fps), "-frames:v", str(frames),
             str(teaser), "-loglevel", "error"],
            check=True, capture_output=True, text=True,
        )
        concat_segments([teaser, base], timeline)
    except (subprocess.CalledProcessError, ResolveError) as exc:
        base.replace(timeline)
        detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        raise ResolveError(f"Ghép cold-open thất bại: {str(detail)[:300]}") from exc
    finally:
        teaser.unlink(missing_ok=True)
        base.unlink(missing_ok=True)
    return probe_duration(timeline)


# The one deliberate exception to "video is encoded exactly once" above: this
# file is never staged into a render (stage_assets only ever reads src_path),
# so re-encoding it a second time costs nothing the deliverable pays for.
#
# The delivery-quality cut (`deliverable_src`, intermediate_crf 12) runs tens
# of Mbps, because that is what the renderer reads frames from during the real
# render. Streamed straight to a browser <video> tag it is a different problem: Chrome
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
