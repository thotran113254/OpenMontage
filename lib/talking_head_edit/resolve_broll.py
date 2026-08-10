"""Prepare b-roll clips to the exact length of the span they cover.

The renderer plays whatever it is given for the number of frames the props say,
so a clip that is the wrong length either freezes on its last frame or gets cut
mid-motion. Fixing that here, in ffmpeg, rather than in the renderer keeps the
renderer dumb — which is the property that makes it predictable.

Three cases, and the choice between them is measured, not arbitrary:

* **longer than needed** — trim. Nothing is lost that anyone asked for.
* **slightly shorter** — change speed, up to ±15%. Safer than looping: a loop is
  obvious the moment the clip contains motion, because the subject jumps back.
* **much shorter** — hold the last frame and warn. Stretching a 3-second clip
  over 8 seconds looks broken in a different way.

Audio is discarded entirely: b-roll is a picture layer over the A-roll's own
sound. Mixing would put the word clock back in play, and that is the one thing
this pipeline defends hardest.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

# Beyond this the speed change is audible as wrongness in the motion itself
# (people walking too fast), so the last-frame hold is the lesser evil.
MAX_SPEED_STRETCH = 0.15
# Below this a length mismatch is not worth an extra encode.
LENGTH_TOLERANCE = 0.04


class BrollError(RuntimeError):
    pass


def _probe_duration(path: Path) -> float:
    from lib.talking_head_edit.resolve_media import probe_duration

    return probe_duration(path)


def prepare_clip(source: Path, needed: float, out_path: Path,
                 width: int, height: int, fps: int = 30,
                 preset: str = "medium", crf: int = 20) -> dict[str, Any]:
    """Render one b-roll clip at exactly `needed` seconds. Returns what it did.

    Encoded at the A-roll's pixel size so the renderer's compositing does not
    resample it — the same reasoning that made `aroll_pixel_size` worth measuring.
    """
    if needed <= 0:
        raise BrollError(f"Khoảng phủ b-roll không hợp lệ: {needed}s")
    available = _probe_duration(source)
    if available <= 0:
        raise BrollError(f"Không đọc được độ dài b-roll {source.name}")

    warnings: list[str] = []
    filters = [f"scale={width}:{height}:force_original_aspect_ratio=increase",
               f"crop={width}:{height}"]

    if abs(available - needed) <= LENGTH_TOLERANCE:
        mode = "as_is"
        trim = ["-t", f"{needed:.3f}"]
    elif available > needed:
        mode = "trim"
        # Start slightly in: the first frames of handheld b-roll are usually the
        # camera settling.
        head = min(0.3, (available - needed) / 2)
        trim = ["-ss", f"{head:.3f}", "-t", f"{needed:.3f}"]
    else:
        shortfall = (needed - available) / needed
        if shortfall <= MAX_SPEED_STRETCH:
            mode = "slowed"
            factor = needed / available
            filters.insert(0, f"setpts={factor:.6f}*PTS")
            trim = ["-t", f"{needed:.3f}"]
        else:
            mode = "hold_last_frame"
            # tpad clones the final frame for the remainder.
            filters.append(f"tpad=stop_mode=clone:stop_duration={needed - available:.3f}")
            trim = ["-t", f"{needed:.3f}"]
            warnings.append(
                f"{source.name} chỉ dài {available:.1f}s cho khoảng {needed:.1f}s "
                f"(thiếu {shortfall:.0%}) — giữ frame cuối. Nên cắt khoảng phủ ngắn hơn "
                "hoặc dùng clip dài hơn.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(source), *trim,
         "-vf", ",".join(filters), "-an",
         "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
         "-pix_fmt", "yuv420p", "-r", str(fps),
         str(out_path), "-loglevel", "error"],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not out_path.exists():
        raise BrollError(
            f"Encode b-roll {source.name} thất bại: {result.stderr.strip()[:300]}")

    return {"mode": mode, "source_seconds": round(available, 3),
            "needed_seconds": round(needed, 3),
            "actual_seconds": round(_probe_duration(out_path), 3),
            "path": out_path, "warnings": warnings}


def resolve_broll(events: list[dict[str, Any]], overlay_pool: list[dict[str, Any]],
                  out_dir: Path, width: int, height: int, fps: int = 30,
                  preset: str = "medium", crf: int = 20,
                  on_warning: Any = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Resolved `broll` events → renderer props + warnings.

    `events` are already in output-timeline seconds (resolve_events did that);
    each must name a `src` that exists in `overlay_pool`. `audit` already rejects
    the ones that do not, so a miss here is a bug rather than a hallucination —
    and it is skipped with a warning rather than failing the render.
    """
    pool = {str(entry["src"]): entry for entry in overlay_pool or []}
    clips: list[dict[str, Any]] = []
    warnings: list[str] = []

    for index, event in enumerate(events):
        src_id = str(event.get("src") or "")
        entry = pool.get(src_id)
        if not entry or not entry.get("path"):
            warnings.append(
                f"Bỏ b-roll: '{src_id}' không có trong overlay_pool của job này")
            continue

        needed = float(event.get("end", 0.0)) - float(event.get("at", 0.0))
        if needed <= 0.2:
            warnings.append(
                f"Bỏ b-roll {src_id}: khoảng phủ chỉ {needed:.2f}s, quá ngắn để thấy")
            continue

        try:
            prepared = prepare_clip(
                Path(entry["path"]), needed,
                out_dir / f"broll_{index:02d}_{src_id}.mp4",
                width, height, fps, preset, crf)
        except BrollError as exc:
            warnings.append(str(exc))
            continue

        warnings.extend(prepared["warnings"])
        clips.append({
            # bare filename: the render stage stages this into the per-job public
            # dir and the web server serves it from the same place, so preview
            # and render read identical bytes
            "src": prepared["path"].name,
            "start": round(float(event["at"]), 3),
            "duration": prepared["actual_seconds"],
            "fit": event.get("fit", "cover"),
            "opacity": float(event.get("opacity", 1.0)),
            "source_id": src_id,
            "mode": prepared["mode"],
        })

    for warning in warnings:
        if on_warning:
            on_warning(warning)
    return clips, warnings


def speaker_segments(words: list[dict[str, Any]], time_of: Any,
                     min_turn: float = 2.5) -> list[dict[str, Any]]:
    """Speaking turns on the output timeline, with a framing per speaker.

    Turns shorter than `min_turn` are folded into the previous one. Fast
    back-and-forth is normal in an interview, and re-framing on every exchange
    makes the picture jump continuously — which looks worse than not doing it at
    all. 2.5 s is long enough that a change reads as a deliberate cut.
    """
    if not words:
        return []

    turns: list[dict[str, Any]] = []
    for index, word in enumerate(words):
        speaker = str(word.get("speaker") or "")
        if not speaker:
            continue
        at = float(time_of(index)) if time_of else float(word.get("start", 0.0))
        if turns and turns[-1]["speaker"] == speaker:
            turns[-1]["end"] = at
            continue
        turns.append({"speaker": speaker, "start": at, "end": at})

    if not turns:
        return []
    turns[-1]["end"] = max(turns[-1]["end"],
                           float(time_of(len(words) - 1)) if time_of
                           else float(words[-1]["end"]))

    merged: list[dict[str, Any]] = []
    for turn in turns:
        if merged and turn["end"] - turn["start"] < min_turn:
            merged[-1]["end"] = turn["end"]
            continue
        merged.append(dict(turn))

    # Framing alternates by speaker, in first-heard order, so the same person
    # always sits on the same side.
    order: list[str] = []
    for turn in merged:
        if turn["speaker"] not in order:
            order.append(turn["speaker"])
    framings = ("left", "right", "center")
    for turn in merged:
        turn["framing"] = framings[order.index(turn["speaker"]) % len(framings)]
        turn["start"] = round(turn["start"], 3)
        turn["end"] = round(turn["end"], 3)
    return merged


def speaker_aware_enabled(setting: Any, words: list[dict[str, Any]],
                          min_share: float = 0.15) -> tuple[bool, str]:
    """(on, why) for `assembly.speaker_aware`.

    `auto` turns it on only when there really are two people: at least two
    speaker labels AND each holding at least `min_share` of the words.
    Diarization is not perfect, and a label covering 2% of a monologue is noise,
    not a second speaker — re-framing for it would be worse than ignoring it.
    """
    if setting is False:
        return False, "speaker_aware tắt trong cấu hình"
    labelled = [str(w.get("speaker")) for w in words if w.get("speaker")]
    if not labelled:
        return False, "ASR không trả nhãn người nói (cần diarization)"

    counts: dict[str, int] = {}
    for speaker in labelled:
        counts[speaker] = counts.get(speaker, 0) + 1
    total = len(labelled)
    substantial = {s: c for s, c in counts.items() if c / total >= min_share}

    if setting is True:
        return (len(counts) >= 2,
                f"speaker_aware bật tay, thấy {len(counts)} nhãn người nói")
    if len(substantial) < 2:
        shares = ", ".join(f"{s}={c / total:.0%}" for s, c in sorted(counts.items()))
        return False, (f"chỉ {len(substantial)} người nói chiếm >= {min_share:.0%} "
                       f"số từ ({shares}) — khả năng cao là nhiễu diarization")
    return True, (f"{len(substantial)} người nói, mỗi người >= {min_share:.0%} số từ")
