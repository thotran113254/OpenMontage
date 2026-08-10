"""Cut, crossfade-transition, and zoom operations for the local footage-edit pipeline.

All operations shell out to ffmpeg directly (frame-accurate re-encoding) rather than
going through video_trimmer's stream-copy concat, which is not frame-accurate.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


def extract_segments(source: Path, segments: list[dict], work_dir: Path) -> list[Path]:
    """Cut each {start, end} window out of source with a frame-accurate re-encode."""
    work_dir.mkdir(parents=True, exist_ok=True)
    clips = []
    for i, seg in enumerate(segments):
        out_path = work_dir / f"clip_{i:03d}.mp4"
        cmd = [
            "ffmpeg", "-y", "-i", str(source),
            "-ss", str(seg["start"]), "-to", str(seg["end"]),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", "-preset", "medium",
            "-c:a", "aac", "-ar", "44100",
            str(out_path), "-loglevel", "error",
        ]
        subprocess.run(cmd, check=True)
        clips.append(out_path)
    return clips


def crossfade_concat(clips: list[Path], output_path: Path, transition_duration: float) -> list[float]:
    """Concatenate clips with an xfade/acrossfade chain. Returns transition center
    timestamps (in the final timeline) for downstream SFX placement."""
    if len(clips) == 1:
        subprocess.run(["ffmpeg", "-y", "-i", str(clips[0]), "-c", "copy",
                         str(output_path), "-loglevel", "error"], check=True)
        return []

    durations = [probe_duration(c) for c in clips]
    inputs = []
    for c in clips:
        inputs += ["-i", str(c)]

    filter_parts = []
    transition_centers = []
    cum_duration = durations[0]
    v_label, a_label = "0:v", "0:a"
    for i in range(1, len(clips)):
        offset = cum_duration - transition_duration
        transition_centers.append(offset + transition_duration / 2)
        next_v, next_a = f"v{i}", f"a{i}"
        filter_parts.append(
            f"[{v_label}][{i}:v]xfade=transition=fade:duration={transition_duration}:"
            f"offset={offset:.3f}[{next_v}]"
        )
        filter_parts.append(
            f"[{a_label}][{i}:a]acrossfade=d={transition_duration}[{next_a}]"
        )
        v_label, a_label = next_v, next_a
        cum_duration = cum_duration + durations[i] - transition_duration

    filter_complex = ";".join(filter_parts)
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", f"[{v_label}]", "-map", f"[{a_label}]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", "-preset", "medium", "-c:a", "aac",
        str(output_path), "-loglevel", "error",
    ]
    subprocess.run(cmd, check=True)
    return transition_centers


def apply_zoom(input_path: Path, output_path: Path, period: float, intensity: float,
                width: int, height: int, fps: int, duty: float = 0.5) -> None:
    """Hard-cut punch zoom: snaps instantly between 1.0 (normal) and 1.0+intensity
    (zoomed in) every `period` seconds — no ease-in/ease-out ramp. `duty` is the
    fraction of each period spent zoomed in."""
    zoomed_frac = max(0.0, min(1.0, duty))
    normal_span = period * (1.0 - zoomed_frac)
    expr = f"if(lt(mod(time,{period}),{normal_span}),1.0,1.0+{intensity})"
    vf = (
        f"zoompan=z='{expr}':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':"
        f"d=1:s={width}x{height}:fps={fps}"
    )
    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", "-preset", "medium",
        "-c:a", "copy", str(output_path), "-loglevel", "error",
    ]
    subprocess.run(cmd, check=True)
