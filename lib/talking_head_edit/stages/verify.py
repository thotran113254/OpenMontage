"""Stage 7 — verify the rendered file by measuring it.

Deliberately measures rather than infers. Two earlier heuristics in this repo
gave false passes: PNG file size to detect black frames (a dark navy frame is
~55KB and sailed through) and "audio exists therefore music is present" (true
on a narration-only mix). So: real luma via signalstats, real loudness via
ebur128, real stream durations.

Three things cannot be measured here — broken overlays, missing assets,
unreadable text. They are listed as `requires_human_review` with sampled frame
paths so a "false" is never mistaken for "checked and passed".
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from lib.talking_head_edit.job_store import SHARED_PUBLIC
from lib.talking_head_edit.resolve_media import probe_duration, stream_durations

SAMPLE_COUNT = 8
BLACK_LUMA_THRESHOLD = 12.0      # mean luma 0-255; below this the frame reads as black
DURATION_TOLERANCE = 0.4         # seconds between props and the rendered file
AV_TOLERANCE = 0.35
SPEECH_LUFS_RANGE = (-16.5, -12.0)   # loudnorm targets -14 LUFS
# A caption gap with a music bed under it measures well above this; a gap with
# no music at all sits near the encoder's noise floor (below -60 dB).
MUSIC_GAP_FLOOR_DB = -50.0
# Healthy A-roll repeats nothing; a stuttering render repeated ~20% of frames.
MAX_DUPLICATE_RATIO = 0.08

# --- seam inspection ------------------------------------------------------
# Hard cap on generated images: 40 seams x 1 image per verify is landfill.
MAX_TIMELINE_VIEWS = 6
SEAM_PEAK_WINDOW = 0.05          # a click lives in a few milliseconds
SEAM_PEAK_LIFT_DB = 12.0         # peak this far above the local reference reads as a pop
SEAM_LEVEL_WINDOW = 3.0          # loudnorm's own window, so the two sides are comparable
SEAM_LEVEL_STEP_DB = 1.5         # audible as the volume "stepping" at an edit
SEAM_VIEW_RADIUS = 1.5           # +/- seconds shown around a seam


# Every issue carries a stable code so a remedy can be looked up (see
# lib/talking_head_edit/remedies.py). The Vietnamese message stays for the human;
# the code is what autopilot matches on. Adding an issue without a code means
# autopilot can only stop and report it, which is the safe default.
CODE_STUTTER = "stutter"
CODE_BGM_MISSING = "bgm_missing"
CODE_AV_DRIFT = "av_drift"
CODE_LOUDNESS_OFF = "loudness_off"
CODE_DURATION_MISMATCH = "duration_mismatch"
CODE_BLACK_FRAME = "black_frame"
CODE_SFX_MISSING = "sfx_missing"
CODE_SEAM_SUSPECT = "seam_suspect"
CODE_BROLL_MISSING = "broll_missing"
CODE_UNMEASURED = "unmeasured"


def issue(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """One machine-matchable, human-readable finding."""
    return {"code": code, "message": message, **extra}


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _spine_words(job) -> list[dict[str, Any]]:
    """Words for labelling a seam view, as the director saw them.

    Filtered by the selection so the indices line up with what the output-time
    mapper expects. Empty on failure — labels are a nicety, the waveform is not.
    """
    from lib.talking_head_edit import spine_build

    spine = _read_json(job.spine_path) or {}
    return spine_build.filter_words(
        spine.get("word_timestamps") or [],
        (job.load().get("selection") or {}).get("kept_word_ranges"))


def _run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=300)
    return (result.stdout or "") + (result.stderr or "")


def frame_luma(video: Path, at_seconds: float) -> float | None:
    """Mean luma (YAVG, 0-255) of the frame at a timestamp."""
    output = _run(["ffmpeg", "-ss", f"{at_seconds:.3f}", "-i", str(video), "-frames:v", "1",
                   "-vf", "signalstats,metadata=print:file=-", "-f", "null", "-"])
    for line in output.splitlines():
        if "signalstats.YAVG" in line:
            try:
                return float(line.split("=")[-1].strip())
            except ValueError:
                return None
    return None


def window_loudness(video: Path, start: float, duration: float) -> float | None:
    """Mean volume (dBFS) of one window. Used to hear into a speech gap."""
    output = _run(["ffmpeg", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(video),
                   "-af", "volumedetect", "-f", "null", "-"])
    for line in output.splitlines():
        if "mean_volume:" in line:
            try:
                return float(line.split("mean_volume:")[1].replace("dB", "").strip())
            except ValueError:
                return None
    return None


def speech_gaps(events: list[dict[str, Any]], total: float,
                min_length: float = 0.6, limit: int = 6) -> list[tuple[float, float]]:
    """Windows where no caption is on screen, i.e. where the music is exposed."""
    spans = sorted(
        (float(e["at"]), float(e["end"]))
        for e in events if e.get("type") == "caption" and e.get("end") is not None
    )
    gaps: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in spans:
        if start - cursor >= min_length:
            gaps.append((cursor, start - cursor))
        cursor = max(cursor, end)
    if total - cursor >= min_length:
        gaps.append((cursor, min(3.0, total - cursor)))
    # longest gaps first: those give the cleanest read on the music bed
    gaps.sort(key=lambda gap: gap[1], reverse=True)
    return [(round(start, 3), round(min(length, 3.0), 3)) for start, length in gaps[:limit]]


def measure_music_bed(video: Path, props: dict[str, Any], total: float) -> dict[str, Any]:
    """Was the declared music actually rendered into the mix?

    Measured, never assumed: an older check in this repo reported music present
    whenever the file had any audio at all, which is true of a narration-only
    mix. Here the level is read inside caption gaps — if a bed is playing it is
    audible there; if the music silently failed, those windows are near-silent.
    """
    declared = (props.get("bgm") or {}).get("name")
    report: dict[str, Any] = {"declared": declared, "gaps_measured": [], "issues": []}
    if not declared:
        report["music_present"] = None      # nothing claimed, nothing to verify
        return report

    gaps = speech_gaps(props.get("events") or [], total)
    if not gaps:
        report["music_present"] = None
        report["issues"].append(issue(
            CODE_UNMEASURED, "Không có khoảng ngắt lời đủ dài để đo nhạc nền"))
        return report

    levels: list[float] = []
    for start, length in gaps:
        level = window_loudness(video, start, length)
        report["gaps_measured"].append({"at": start, "length": length, "mean_db": level})
        if level is not None:
            levels.append(level)

    if not levels:
        report["music_present"] = None
        report["issues"].append(issue(
            CODE_UNMEASURED, "Không đo được mức âm trong khoảng ngắt lời"))
        return report

    loudest_gap = max(levels)
    report["loudest_gap_db"] = round(loudest_gap, 1)
    report["music_present"] = loudest_gap > MUSIC_GAP_FLOOR_DB
    if not report["music_present"]:
        report["issues"].append(issue(
            CODE_BGM_MISSING,
            f"Khai báo nhạc '{declared}' nhưng các khoảng ngắt lời chỉ đạt "
            f"{loudest_gap:.1f} dB (ngưỡng {MUSIC_GAP_FLOOR_DB} dB) — nhạc có thể đã không vào mix",
            declared=declared, loudest_gap_db=round(loudest_gap, 1)))
    return report


def aroll_windows(props: dict[str, Any], total: float,
                  length: float = 4.0, limit: int = 3) -> list[tuple[float, float]]:
    """Stretches where the raw footage fills the frame.

    Cards and the endcard are near-static by design, so measuring frame
    duplication inside them would flag healthy renders. Only fullscreen A-roll
    tells you whether motion is actually advancing.
    """
    covered = sorted(
        (float(e["at"]), float(e["end"]))
        for e in props.get("events") or []
        if e.get("type") in ("card", "endcard") and e.get("end") is not None
    )
    free: list[tuple[float, float]] = []
    cursor = 1.0        # skip the cold-open seam, which legitimately holds
    for start, end in covered:
        if start - cursor >= length:
            free.append((cursor, start))
        cursor = max(cursor, end)
    if total - cursor >= length:
        free.append((cursor, total))

    windows: list[tuple[float, float]] = []
    for start, end in free:
        # sample from the middle of each stretch, where motion is representative
        middle = start + (end - start - length) / 2
        windows.append((round(middle, 3), length))
        if len(windows) >= limit:
            break
    return windows


def duplicate_ratio(video: Path, start: float, duration: float, fps: int = 30) -> float | None:
    """Share of frames in a window that merely repeat the previous one.

    mpdecimate drops near-identical frames; what survives is the count of
    genuinely new pictures. A healthy A-roll window drops nothing.
    """
    output = _run(["ffmpeg", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(video),
                   "-vf", "mpdecimate=hi=64*12:lo=64*5:frac=0.33", "-an", "-f", "null", "-"])
    if "frame=" not in output:
        return None
    try:
        kept = int(output.rsplit("frame=", 1)[1].split()[0])
    except (ValueError, IndexError):
        return None
    expected = duration * fps
    if expected <= 0:
        return None
    return max(0.0, round(1 - kept / expected, 3))


def measure_motion(video: Path, props: dict[str, Any], total: float, fps: int = 30) -> dict[str, Any]:
    """Catch judder: footage that repeats frames instead of advancing.

    A render can be the right length, the right loudness and free of black
    frames while still stuttering, because the renderer served the same video
    frame several times in a row. That happened here on a machine under heavy
    CPU contention — the file passed every other check and still looked broken
    to a human. Hence this check.
    """
    report: dict[str, Any] = {"windows": [], "issues": []}
    windows = aroll_windows(props, total)
    if not windows:
        report["duplicate_ratio"] = None
        report["issues"].append(issue(
            CODE_UNMEASURED, "Không có đoạn A-roll đủ dài để đo độ mượt"))
        return report

    ratios: list[float] = []
    for start, length in windows:
        ratio = duplicate_ratio(video, start, length, fps)
        report["windows"].append({"at": start, "length": length, "duplicate_ratio": ratio})
        if ratio is not None:
            ratios.append(ratio)

    if not ratios:
        report["duplicate_ratio"] = None
        report["issues"].append(issue(CODE_UNMEASURED, "Không đo được độ mượt"))
        return report

    worst = max(ratios)
    report["duplicate_ratio"] = worst
    report["smooth"] = worst <= MAX_DUPLICATE_RATIO
    if not report["smooth"]:
        report["issues"].append(issue(
            CODE_STUTTER,
            f"Video bị giật: {worst * 100:.0f}% frame trong đoạn A-roll chỉ lặp lại frame trước "
            f"(ngưỡng {MAX_DUPLICATE_RATIO * 100:.0f}%). Render lại khi máy rảnh, "
            f"hoặc giảm concurrency.",
            duplicate_ratio=worst))
    return report


def peak_level(video: Path, start: float, duration: float) -> float | None:
    """Max sample level (dBFS) in a window. A pop shows up here, not in the mean."""
    output = _run(["ffmpeg", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(video),
                   "-af", "volumedetect", "-f", "null", "-"])
    for line in output.splitlines():
        if "max_volume:" in line:
            try:
                return float(line.split("max_volume:")[1].replace("dB", "").strip())
            except ValueError:
                return None
    return None


def suspect_seams(video: Path, seams: list[float], total: float,
                  motion: dict[str, Any] | None = None,
                  limit: int = MAX_TIMELINE_VIEWS) -> list[dict[str, Any]]:
    """Rank seams by how likely something went wrong at them.

    Three signals, each answering a different failure:

    1. a peak much louder than the surrounding audio — a click from cutting
       mid-waveform
    2. duplicate frames reported near the seam — judder at the join
    3. a loudness step between the two sides — the volume jumping at an edit,
       which is what per-span loudness normalisation would cause

    Capped because 40 seams times one image each is landfill, not a report.
    """
    if not seams:
        return []

    ranked: list[dict[str, Any]] = []
    stutter_windows = [
        float(w["at"]) for w in ((motion or {}).get("windows") or [])
        if w.get("duplicate_ratio") is not None
        and float(w["duplicate_ratio"]) > MAX_DUPLICATE_RATIO
    ]

    for seam in seams:
        if not (0.05 < seam < total - 0.05):
            continue
        reasons: list[str] = []
        score = 0.0

        peak = peak_level(video, max(0.0, seam - SEAM_PEAK_WINDOW / 2), SEAM_PEAK_WINDOW)
        reference = peak_level(video, max(0.0, seam - 1.5), 1.0)
        if peak is not None and reference is not None and peak - reference > SEAM_PEAK_LIFT_DB:
            score += (peak - reference)
            reasons.append(
                f"đỉnh biên độ {peak:.1f} dB cao hơn nền {peak - reference:.1f} dB — nghi pop")

        if any(abs(seam - at) <= 2.0 for at in stutter_windows):
            score += 12.0
            reasons.append("mpdecimate báo frame lặp quanh mối này")

        before = window_loudness(video, max(0.0, seam - SEAM_LEVEL_WINDOW), SEAM_LEVEL_WINDOW)
        after = window_loudness(video, seam, SEAM_LEVEL_WINDOW)
        if before is not None and after is not None and abs(before - after) > SEAM_LEVEL_STEP_DB:
            score += abs(before - after)
            reasons.append(
                f"mức âm hai bên lệch {abs(before - after):.1f} dB "
                f"({before:.1f} → {after:.1f})")

        if reasons:
            ranked.append({"at": round(seam, 3), "score": round(score, 2),
                           "reasons": reasons})

    ranked.sort(key=lambda row: -row["score"])
    return ranked[:limit]


def build_timeline_views(job, video: Path, suspects: list[dict[str, Any]],
                         words: list[dict[str, Any]], time_of: Any = None
                         ) -> list[dict[str, Any]]:
    """One composite image per suspect seam, so the report is actionable.

    Without this, `verify` can report "15% duplicate frames" and nothing about
    where — which is a fact nobody can act on.
    """
    if not suspects:
        return []
    from lib.talking_head_edit.timeline_view import TimelineViewError, around

    out_dir = job.dir / "preview" / "timeline"
    views: list[dict[str, Any]] = []
    for suspect in suspects:
        at = float(suspect["at"])
        try:
            path = around(
                video, at, radius=SEAM_VIEW_RADIUS, words=words, time_of=time_of,
                out=out_dir / f"seam_{at:07.2f}.png",
                title=f"mối nối {at:.2f}s — {'; '.join(suspect['reasons'])}")
        except (TimelineViewError, OSError, ImportError) as exc:
            job.emit("warning", "verify",
                     f"Không dựng được ảnh soi mối {at:.2f}s: {str(exc)[:120]}")
            continue
        views.append({"at": at, "path": job.rel(path), "score": suspect["score"],
                      "reasons": suspect["reasons"]})
    return views


def frame_signature(video: Path, at_seconds: float, work: Path) -> str | None:
    """A coarse fingerprint of one frame, for "are these two the same picture".

    A tiny greyscale thumbnail as hex: enough to tell a b-roll clip apart from the
    speaker's face, without pulling in an image-hashing dependency for one check.
    """
    out = work / f"sig_{at_seconds:08.3f}.pgm"
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-y", "-ss", f"{at_seconds:.3f}", "-i", str(video),
          "-frames:v", "1", "-vf", "scale=16:16,format=gray",
          str(out), "-loglevel", "error"])
    if not out.exists():
        return None
    try:
        return out.read_bytes()[-256:].hex()
    finally:
        out.unlink(missing_ok=True)


def frame_difference(left: str | None, right: str | None) -> float:
    """0.0 = identical fingerprints, 1.0 = nothing in common."""
    if not left or not right or len(left) != len(right):
        return 1.0
    differing = sum(1 for a, b in zip(left, right) if a != b)
    return round(differing / len(left), 4)


def measure_broll(video: Path, source_video: Path, clips: list[dict[str, Any]],
                  work: Path, min_difference: float = 0.25) -> dict[str, Any]:
    """Did the b-roll actually make it into the picture?

    Measured, not assumed, for the same reason the music check is measured: a clip
    can be prepared, listed in props and silently not composited, and every other
    check would still pass. The test is whether the frame in the middle of the
    overlay differs from the A-roll frame at the same moment.
    """
    report: dict[str, Any] = {"clips": [], "issues": []}
    if not clips:
        report["broll_present"] = None
        return report

    present = 0
    for clip in clips:
        middle = float(clip["start"]) + float(clip["duration"]) / 2
        rendered = frame_signature(video, middle, work)
        # Compare against the cut A-roll at the same moment: that is exactly what
        # would be on screen if the overlay had not been drawn.
        underneath = frame_signature(source_video, middle, work)
        difference = frame_difference(rendered, underneath)
        visible = difference >= min_difference
        present += int(visible)
        report["clips"].append({
            "src": clip.get("src"), "at": round(middle, 3),
            "difference": difference, "visible": visible,
        })
        if not visible:
            report["issues"].append(issue(
                CODE_BROLL_MISSING,
                f"B-roll '{clip.get('src')}' ở giây {middle:.1f} không thấy khác "
                f"frame A-roll cùng lúc (lệch {difference:.0%} < {min_difference:.0%}) "
                "— có thể lớp phủ đã không được vẽ",
                src=clip.get("src"), at=round(middle, 3)))
    report["broll_present"] = present == len(clips)
    return report


def integrated_loudness(video: Path) -> float | None:
    """Integrated loudness in LUFS over the whole file."""
    output = _run(["ffmpeg", "-i", str(video), "-af", "ebur128=framelog=quiet",
                   "-f", "null", "-"])
    summary = output.rsplit("Integrated loudness", 1)
    if len(summary) < 2:
        return None
    for line in summary[1].splitlines():
        if "I:" in line and "LUFS" in line:
            parts = line.replace("I:", " ").replace("LUFS", " ").split()
            try:
                return float(parts[0])
            except (ValueError, IndexError):
                return None
    return None


def run(job, options: dict[str, Any]) -> dict[str, Any]:
    state = job.load()
    version = int(state["current_version"])
    video = job.final_path
    if not video.exists():
        return {"skipped": "chưa có final.mp4"}

    props = json.loads(job.props_path(version).read_text(encoding="utf-8"))
    expected = float(props.get("durationSeconds") or 0.0)
    actual = probe_duration(video)
    durations = stream_durations(video)
    av_drift = abs(durations.get("video", 0.0) - durations.get("audio", 0.0))

    frames_dir = job.dir / "verify_frames"
    frames_dir.mkdir(exist_ok=True)
    samples: list[dict[str, Any]] = []
    for index in range(SAMPLE_COUNT):
        at = actual * (index + 0.5) / SAMPLE_COUNT
        luma = frame_luma(video, at)
        frame_path = frames_dir / f"frame_{index:02d}.jpg"
        _run(["ffmpeg", "-y", "-ss", f"{at:.3f}", "-i", str(video), "-frames:v", "1",
              "-q:v", "4", str(frame_path), "-loglevel", "error"])
        samples.append({
            "at": round(at, 2),
            "mean_luma": luma,
            "frame": job.rel(frame_path) if frame_path.exists() else None,
        })

    loudness = integrated_loudness(video)
    music = measure_music_bed(video, props, actual)
    motion = measure_motion(video, props, actual, int(options.get("fps", 30)))
    issues: list[str] = list(music["issues"]) + list(motion["issues"])

    # Seam inspection: turn "something is wrong somewhere" into "look here".
    resolve_report = _read_json(job.dir / f"resolve_report_v{version}.json") or {}
    seams = [float(s) for s in (resolve_report.get("seams") or [])]
    suspects = suspect_seams(video, seams, actual, motion)
    from lib.talking_head_edit.timeline_view import output_time_mapper

    timeline_views = build_timeline_views(
        job, video, suspects, _spine_words(job),
        time_of=output_time_mapper(job, version))

    broll = measure_broll(video, job.src_path, props.get("broll") or [],
                          job.dir / "preview" / "broll_check")
    issues.extend(broll["issues"])
    for suspect in suspects:
        issues.append(issue(
            CODE_SEAM_SUSPECT,
            f"Mối nối {suspect['at']:.2f}s đáng soi: {'; '.join(suspect['reasons'])}",
            at=suspect["at"]))

    if expected and abs(actual - expected) > DURATION_TOLERANCE:
        issues.append(issue(
            CODE_DURATION_MISMATCH,
            f"Thời lượng lệch: props {expected:.2f}s vs file {actual:.2f}s "
            f"(ngưỡng {DURATION_TOLERANCE}s)",
            expected=round(expected, 3), actual=round(actual, 3)))
    if av_drift >= AV_TOLERANCE:
        issues.append(issue(CODE_AV_DRIFT, f"Lệch video/audio {av_drift:.2f}s",
                            drift=round(av_drift, 3)))
    dark = [s for s in samples if s["mean_luma"] is not None and s["mean_luma"] < BLACK_LUMA_THRESHOLD]
    for sample in dark:
        issues.append(issue(
            CODE_BLACK_FRAME,
            f"Frame {sample['at']}s gần như đen (luma {sample['mean_luma']:.1f}/255)",
            at=sample["at"]))
    if loudness is not None and not (SPEECH_LUFS_RANGE[0] <= loudness <= SPEECH_LUFS_RANGE[1]):
        issues.append(issue(
            CODE_LOUDNESS_OFF,
            f"Loudness tổng {loudness:.1f} LUFS nằm ngoài khoảng mong đợi {SPEECH_LUFS_RANGE}",
            lufs=loudness))

    missing_audio = [
        event["name"] for event in props.get("events", [])
        if event.get("type") == "sfx"
        and not (SHARED_PUBLIC / event["name"]).exists()
    ]
    if missing_audio:
        issues.append(issue(CODE_SFX_MISSING,
                            f"Thiếu file sfx khi render: {sorted(set(missing_audio))}",
                            names=sorted(set(missing_audio))))

    report = {
        "version": version,
        "duration_expected": round(expected, 3),
        "duration_actual": round(actual, 3),
        "av_drift": round(av_drift, 3),
        "integrated_lufs": loudness,
        "music": music,
        "motion": motion,
        "broll": broll,
        "frames": samples,
        "seams": seams,
        "suspect_seams": suspects,
        # Composite images (filmstrip + waveform + word labels) at the seams
        # worth looking at. The UI shows these; they are what makes a measured
        # complaint actionable.
        "timeline_views": timeline_views,
        "issues": issues,
        "passed": not issues,
        "requires_human_review": [
            "overlay có bị vỡ/che nhau không",
            "chữ có đọc được trên nền không",
            "asset có đúng nội dung không",
        ],
    }
    (job.dir / f"verify_report_v{version}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    if issues:
        for finding in issues:
            job.emit("warning", "verify", finding["message"], code=finding["code"])
    else:
        music_note = (
            f"nhạc '{music['declared']}' nghe được trong khoảng ngắt lời "
            f"({music.get('loudest_gap_db')} dB)"
            if music.get("music_present")
            else "không khai báo nhạc nền" if music["declared"] is None else "nhạc: chưa kết luận được"
        )
        job.emit("log", "verify",
                 f"Đo được: {actual:.1f}s, lệch A/V {av_drift:.2f}s, "
                 f"{loudness if loudness is None else round(loudness, 1)} LUFS, {music_note}, "
                 f"frame lặp {motion.get('duplicate_ratio')}")
    return report
