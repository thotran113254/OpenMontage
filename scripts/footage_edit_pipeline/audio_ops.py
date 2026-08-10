"""SFX overlay, subtitle burn-in, and background music mixing for the local edit pipeline."""
from __future__ import annotations

import subprocess
from pathlib import Path


def overlay_sfx_at_transitions(input_path: Path, output_path: Path, sfx_path: Path,
                                transition_centers: list[float], volume_db: float = -6.0) -> None:
    """Mix a short SFX sample into the audio track at each transition center timestamp."""
    if not transition_centers:
        subprocess.run(["ffmpeg", "-y", "-i", str(input_path), "-c", "copy",
                         str(output_path), "-loglevel", "error"], check=True)
        return

    n = len(transition_centers)
    filter_parts = [f"[1:a]asplit={n}" + "".join(f"[s{i}]" for i in range(n))]
    delayed_labels = []
    for i, center in enumerate(transition_centers):
        delay_ms = max(0, int(center * 1000))
        filter_parts.append(
            f"[s{i}]adelay={delay_ms}|{delay_ms},volume={volume_db}dB[d{i}]"
        )
        delayed_labels.append(f"[d{i}]")
    mix_inputs = "[0:a]" + "".join(delayed_labels)
    filter_parts.append(f"{mix_inputs}amix=inputs={n + 1}:normalize=0[aout]")
    filter_complex = ";".join(filter_parts)

    cmd = [
        "ffmpeg", "-y", "-i", str(input_path), "-i", str(sfx_path),
        "-filter_complex", filter_complex,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac",
        str(output_path), "-loglevel", "error",
    ]
    subprocess.run(cmd, check=True)


def burn_subtitles(input_path: Path, output_path: Path, srt_path: Path, font_path: Path,
                    font_size: int = 42, primary_color: str = "&H0000FFFF") -> None:
    """Burn word/phrase-grouped subtitles using a specific bundled font (libass fontsdir)."""
    font_dir = font_path.parent
    font_name = font_path.stem
    style = (
        f"FontName={font_name},FontSize={font_size},PrimaryColour={primary_color},"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=80"
    )
    srt_escaped = str(srt_path).replace("\\", "/").replace(":", "\\:")
    font_dir_escaped = str(font_dir).replace("\\", "/")
    vf = f"subtitles='{srt_escaped}':fontsdir='{font_dir_escaped}':force_style='{style}'"
    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", "-preset", "medium",
        "-c:a", "copy", str(output_path), "-loglevel", "error",
    ]
    subprocess.run(cmd, check=True)


def mix_background_music(input_path: Path, output_path: Path, music_path: Path,
                          volume_db: float = -22.0, fade_seconds: float = 1.5) -> None:
    """Loop/trim a background track under the dialogue at low volume with in/out fades."""
    from .video_ops import probe_duration
    duration = probe_duration(input_path)
    fade_out_start = max(0.0, duration - fade_seconds)
    filter_complex = (
        f"[1:a]aloop=loop=-1:size=2e9,atrim=0:{duration},"
        f"volume={volume_db}dB,"
        f"afade=t=in:d={fade_seconds},afade=t=out:st={fade_out_start}:d={fade_seconds}[music];"
        f"[0:a][music]amix=inputs=2:normalize=0[aout]"
    )
    cmd = [
        "ffmpeg", "-y", "-i", str(input_path), "-i", str(music_path),
        "-filter_complex", filter_complex,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac",
        str(output_path), "-loglevel", "error",
    ]
    subprocess.run(cmd, check=True)
