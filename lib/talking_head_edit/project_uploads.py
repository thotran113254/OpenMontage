"""Turning an untrusted upload into a file on disk.

Split from `project_store` because it is the security- and I/O-sensitive part and
nothing here needs to know what a project is: a filename from a client, a byte
stream of unknown length, and the two things that can go wrong with each.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import unicodedata
from pathlib import Path
from typing import BinaryIO

UPLOAD_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
# 1 MB chunks. A 500 MB upload must not be read into RAM to be hashed and
# written, so the hash is computed during the same single pass as the write.
CHUNK_BYTES = 1 << 20
MAX_STEM = 60


class UploadError(RuntimeError):
    pass


def safe_filename(name: str) -> str:
    """A client-supplied filename made safe to join onto a path.

    Never trust `filename` from an upload: it can carry `..`, an absolute path,
    or characters the filesystem rejects. Diacritics are folded for the same
    reason `slugify` does it — these paths get handed to ffmpeg and Node.
    """
    stem = Path(str(name or "")).name          # strips any directory part
    suffix = Path(stem).suffix.lower()
    if suffix not in UPLOAD_SUFFIXES:
        raise UploadError(
            f"Định dạng không hỗ trợ: {suffix or 'không rõ'}. "
            f"Chấp nhận: {', '.join(sorted(UPLOAD_SUFFIXES))}")
    base = unicodedata.normalize("NFD", Path(stem).stem)
    base = "".join(c for c in base if unicodedata.category(c) != "Mn")
    base = base.replace("đ", "d").replace("Đ", "D")
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-._") or "source"
    return f"{base[:MAX_STEM]}{suffix}"


def stream_to_file(stream: BinaryIO, target: Path) -> tuple[str, int]:
    """Write a stream to `target`, returning (sha256, bytes).

    Written to `.part` first and renamed on success: an upload that dies halfway
    otherwise leaves a truncated mp4 that ffprobe accepts and every later stage
    misreads.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    digest = hashlib.sha256()
    size = 0
    try:
        with open(part, "wb") as out:
            while chunk := stream.read(CHUNK_BYTES):
                digest.update(chunk)
                out.write(chunk)
                size += len(chunk)
        if size == 0:
            raise UploadError(f"File {target.name} rỗng.")
        os.replace(part, target)
    except Exception:
        part.unlink(missing_ok=True)
        raise
    return digest.hexdigest(), size


def thumbnail(source: Path, out_path: Path, at_seconds: float = 1.0) -> Path | None:
    """One frame, ~0.2 s. Cheap enough to do on every upload."""
    result = subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{at_seconds:.2f}", "-i", str(source),
         "-frames:v", "1", "-vf", "scale=320:-2", str(out_path), "-loglevel", "error"],
        capture_output=True, text=True,
    )
    return out_path if result.returncode == 0 and out_path.exists() else None
