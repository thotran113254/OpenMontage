"""Extract a single JPEG still from a job video. ffmpeg only, no decode in Python."""

from __future__ import annotations

import subprocess
from pathlib import Path


def extract_still(video: Path, dest: Path, at_seconds: float) -> bool:
    """Write one JPEG at `at_seconds`. False if ffmpeg cannot produce a real file."""
    if not video.exists() or video.stat().st_size < 1024:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{max(0.0, at_seconds):.3f}", "-i", str(video),
         "-frames:v", "1", "-q:v", "2", str(dest)],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and dest.exists() and dest.stat().st_size > 2000
