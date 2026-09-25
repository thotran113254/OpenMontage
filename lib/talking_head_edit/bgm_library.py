"""User-uploaded BGM beds next to the shipped library in remotion-composer/public/.

Renderer and Player already resolve names from SHARED_PUBLIC. Uploads are named
`bgm_user_<slug>.mp3` so they sit in the same allow-list as `bgm_*.mp3` without
editing the git-tracked resource-manifest.json.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

from lib.talking_head_edit.job_store import SHARED_PUBLIC, slugify
from lib.talking_head_edit.resources import inventory, refresh, usable_bgm

AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
MAX_BYTES = 20 * 1024 * 1024


class BgmLibraryError(ValueError):
    pass


def list_tracks() -> list[dict[str, Any]]:
    refresh()
    tracks: list[dict[str, Any]] = []
    for entry in inventory()["bgm"]:
        name = str(entry["name"])
        tracks.append({
            "name": name,
            "group": entry.get("group") or "other",
            "mood": entry.get("mood") or "",
            "custom": name.startswith("bgm_user_"),
        })
    return tracks


def save_upload(filename: str, data: bytes) -> dict[str, Any]:
    if len(data) > MAX_BYTES:
        raise BgmLibraryError("File nhạc tối đa 20 MB")
    suffix = Path(filename).suffix.lower()
    if suffix not in AUDIO_SUFFIXES:
        raise BgmLibraryError(
            f"Định dạng {suffix or '(không có)'} không dùng được. "
            "Dùng mp3 / wav / m4a / ogg.")
    stem = slugify(Path(filename).stem, 28)
    dest = SHARED_PUBLIC / f"bgm_user_{stem}.mp3"
    index = 2
    while dest.exists():
        dest = SHARED_PUBLIC / f"bgm_user_{stem}-{index}.mp3"
        index += 1
    SHARED_PUBLIC.mkdir(parents=True, exist_ok=True)
    if suffix == ".mp3":
        dest.write_bytes(data)
    else:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            src = Path(tmp.name)
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", str(src), "-ac", "2", "-ar", "44100",
                 "-b:a", "192k", str(dest)],
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            src.unlink(missing_ok=True)
        if result.returncode != 0 or not dest.exists():
            dest.unlink(missing_ok=True)
            raise BgmLibraryError(
                "Không chuyển được file sang mp3. " + (result.stderr or "")[:180])
    refresh()
    return {
        "name": dest.name,
        "group": "user",
        "mood": "đã tải",
        "custom": True,
    }


def delete_track(name: str) -> bool:
    if not str(name).startswith("bgm_user_") or "/" in name or "\\" in name:
        raise BgmLibraryError("Chỉ xoá được nhạc tự tải (bgm_user_…)")
    path = SHARED_PUBLIC / name
    if not path.is_file():
        return False
    path.unlink()
    refresh()
    return True


def file_path(name: str) -> Path | None:
    if name not in usable_bgm() and not str(name).startswith("bgm_"):
        return None
    path = SHARED_PUBLIC / name
    return path if path.is_file() else None
