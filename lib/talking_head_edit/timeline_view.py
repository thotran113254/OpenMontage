"""A composite picture of one moment on the timeline: filmstrip + waveform + words.

This is NOT a way to get a model to grade quality. A blind test in this repo
already established that it cannot hear or see technical quality — it was reading
its own labels, not the media (see `stages/calibrate.py`). What it CAN answer is
"what is going on around this timestamp", and that is the question the mechanical
measurements cannot: `verify` can say 15% of frames repeat, but not where.

The waveform is the valuable part, not the filmstrip. The real question at a seam
is "is this silence, and did the cut clip the start of the next consonant" — a
waveform with word labels answers it at a glance. The filmstrip is mostly for the
human looking at the same picture.

Cheap on purpose: two ffmpeg calls and some PIL drawing, ~0.5 s, no model call.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

FILMSTRIP_FRAMES = 6
STRIP_WIDTH = 1200
STRIP_HEIGHT = 210
WAVE_HEIGHT = 160
LABEL_HEIGHT = 64
PADDING = 8

# Fonts that carry Vietnamese diacritics. The renderer had font trouble with
# accented uppercase before, so this list is ordered by how reliably each one
# covers the full Latin Extended range on Windows.
FONT_CANDIDATES = (
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/tahoma.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)


class TimelineViewError(RuntimeError):
    pass


def _font(size: int):
    from PIL import ImageFont

    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    # Fallback draws boxes for accented glyphs, which is bad but not fatal —
    # the waveform and the seam line still carry the information.
    return ImageFont.load_default()


def _filmstrip(video: Path, start: float, end: float, out_path: Path,
               frames: int = FILMSTRIP_FRAMES) -> Path | None:
    """`frames` stills evenly spaced across the window, tiled horizontally."""
    span = max(0.04, end - start)
    fps = frames / span
    tile_width = STRIP_WIDTH // frames
    result = subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-t", f"{span:.3f}", "-i", str(video),
         "-vf", f"fps={fps:.4f},scale={tile_width}:-2,tile={frames}x1",
         "-frames:v", "1", str(out_path), "-loglevel", "error"],
        capture_output=True, text=True,
    )
    return out_path if result.returncode == 0 and out_path.exists() else None


def _waveform(video: Path, start: float, end: float, out_path: Path) -> Path | None:
    span = max(0.04, end - start)
    result = subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-t", f"{span:.3f}", "-i", str(video),
         "-filter_complex",
         f"showwavespic=s={STRIP_WIDTH}x{WAVE_HEIGHT}:colors=0x4ade80:split_channels=0",
         "-frames:v", "1", str(out_path), "-loglevel", "error"],
        capture_output=True, text=True,
    )
    return out_path if result.returncode == 0 and out_path.exists() else None


def words_in_window(words: list[dict[str, Any]], start: float, end: float,
                    time_of: Any = None) -> list[tuple[float, str]]:
    """(second, text) for words falling inside the window.

    `time_of(index) -> seconds` maps a word to the OUTPUT timeline; without it the
    word's own `start` is used, which is only correct for a single-source job that
    was not cut. `verify` passes a mapper, so labels line up with the picture.
    """
    found: list[tuple[float, str]] = []
    for index, word in enumerate(words):
        moment = float(time_of(index)) if time_of else float(word.get("start", 0.0))
        if start <= moment <= end:
            found.append((moment, str(word.get("word", "")).strip()))
    return found


def timeline_view(video: Path, start: float, end: float,
                  words: list[dict[str, Any]] | None = None,
                  out: Path | None = None,
                  marks: list[float] | None = None,
                  time_of: Any = None,
                  title: str = "") -> Path:
    """Build the composite. Returns the written path.

    `marks` are timeline seconds to draw a vertical line at — the seam being
    inspected, normally.
    """
    from PIL import Image, ImageDraw

    if end <= start:
        raise TimelineViewError(f"Khoảng không hợp lệ: {start}..{end}")
    out = out or video.with_name(f"timeline_{start:.2f}_{end:.2f}.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    work = out.parent

    strip_path = _filmstrip(video, start, end, work / f"_strip_{out.stem}.png")
    wave_path = _waveform(video, start, end, work / f"_wave_{out.stem}.png")

    try:
        strip = Image.open(strip_path).convert("RGB") if strip_path else None
        wave = Image.open(wave_path).convert("RGB") if wave_path else None
        if strip is None and wave is None:
            raise TimelineViewError(
                f"Không dựng được cả filmstrip lẫn waveform cho {video.name} "
                f"[{start:.2f}-{end:.2f}]")

        strip_height = strip.height if strip else 0
        wave_top = strip_height + (PADDING if strip else 0)
        wave_height = wave.height if wave else 0
        label_top = wave_top + wave_height + PADDING
        total_height = label_top + LABEL_HEIGHT + PADDING

        canvas = Image.new("RGB", (STRIP_WIDTH, total_height), (17, 20, 24))
        if strip:
            canvas.paste(strip.resize((STRIP_WIDTH, strip.height)), (0, 0))
        if wave:
            canvas.paste(wave.resize((STRIP_WIDTH, wave.height)), (0, wave_top))

        draw = ImageDraw.Draw(canvas)
        span = end - start

        def x_of(moment: float) -> int:
            return int((moment - start) / span * (STRIP_WIDTH - 1))

        # Word labels, alternating rows so neighbours do not collide.
        label_font = _font(20)
        for order, (moment, text) in enumerate(
                words_in_window(words or [], start, end, time_of)):
            x = x_of(moment)
            y = label_top + (0 if order % 2 == 0 else 28)
            draw.line([(x, wave_top), (x, label_top)], fill=(70, 80, 92), width=1)
            draw.text((x + 2, y), text, font=label_font, fill=(226, 232, 240))

        # Seam lines last, so nothing is drawn over them.
        for mark in marks or []:
            if not (start <= mark <= end):
                continue
            x = x_of(mark)
            draw.line([(x, 0), (x, total_height)], fill=(248, 113, 113), width=2)
            draw.text((min(x + 4, STRIP_WIDTH - 60), 4), "‖", font=_font(24),
                      fill=(248, 113, 113))

        header = title or f"{video.name}  {start:.2f}s → {end:.2f}s"
        draw.text((6, total_height - 22), header, font=_font(16), fill=(148, 163, 184))
        canvas.save(out)
        return out
    finally:
        for temporary in (strip_path, wave_path):
            if temporary:
                Path(temporary).unlink(missing_ok=True)


def output_time_mapper(job, version: int) -> Any:
    """`index -> second on the rendered timeline`, or None if it cannot be built.

    Word labels are only useful if they sit where the words actually are, and a
    word's own timestamp is a second in its SOURCE file — on a heavily cut edit
    those two are far apart. The resolve report holds the spans and the cold-open
    offset, which is everything needed to place them properly.

    Returns None rather than raising: labels are a convenience, and a seam image
    without them still shows the waveform and the cut position.
    """
    import json

    from lib.talking_head_edit.resolve_events import TimeMapper
    from lib.talking_head_edit.resolve_spans import Span

    report_path = job.dir / f"resolve_report_v{version}.json"
    if not (report_path.exists() and job.spine_path.exists()):
        return None
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        spine = json.loads(job.spine_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    raw_spans = report.get("spans") or []
    if not raw_spans:
        return None

    from lib.talking_head_edit import spine_build

    words = spine_build.filter_words(
        spine.get("word_timestamps") or [],
        (job.load().get("selection") or {}).get("kept_word_ranges"))
    spans = [Span(str(s.get("src") or "s0"), Path("."), float(s["start"]), float(s["end"]))
             for s in raw_spans]
    timeline = float(report.get("timeline_seconds") or 0.0)
    options = job.load().get("options", {})
    mapper = TimeMapper(words, spans, timeline, float(options.get("tempo", 1.06)))
    # The teaser was prepended AFTER mapping, so every event shifted by its
    # length — the labels have to shift with them.
    offset = float(report.get("cold_open_offset") or 0.0)

    def time_of(index: int) -> float:
        return mapper.at(index) + offset

    return time_of


def around(video: Path, moment: float, radius: float = 1.5, **kwargs: Any) -> Path:
    """The ±radius window around one moment, clamped to the file."""
    start = max(0.0, moment - radius)
    return timeline_view(video, start, moment + radius,
                         marks=[moment], **kwargs)
