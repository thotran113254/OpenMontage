"""Local `.r2sync.json` manifest -- read/write/compare, md5 helper.

ETag cannot be used to detect "already uploaded": a multipart upload's ETag
is `md5(md5(part1)+...+md5(partN))-N`, not the file's md5. This manifest is
the cheap, local, network-free alternative -- a no-change sync costs zero
API calls.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

MANIFEST_NAME = ".r2sync.json"
MANIFEST_VERSION = 1
_MD5_CHUNK = 8 * 1024 * 1024


def _manifest_path(local_dir: Path) -> Path:
    return Path(local_dir) / MANIFEST_NAME


def _empty(bucket: str, prefix: str) -> dict[str, Any]:
    return {"version": MANIFEST_VERSION, "bucket": bucket, "prefix": prefix, "files": {}}


def _read_raw(local_dir: Path) -> dict[str, Any] | None:
    path = _manifest_path(local_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load(local_dir: Path, bucket: str, prefix: str) -> dict[str, Any]:
    """Load the manifest; a bucket/prefix mismatch is treated as stale (discarded)
    so a bucket switch can never silently produce an empty sync plan."""
    data = _read_raw(local_dir)
    if data is None or data.get("bucket") != bucket or data.get("prefix") != prefix:
        return _empty(bucket, prefix)
    data.setdefault("files", {})
    return data


def save(local_dir: Path, data: dict[str, Any]) -> None:
    """Atomic write: tmp file + os.replace, so a crash mid-write never corrupts it."""
    local_dir = Path(local_dir)
    path = _manifest_path(local_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def file_md5(path: Path, chunk: int = _MD5_CHUNK) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def entry_matches(entry: dict[str, Any] | None, size: int, mtime_ns: int) -> bool:
    """Cheap gate: size+mtime_ns match, no hashing needed."""
    if entry is None:
        return False
    return entry.get("size") == size and entry.get("mtime_ns") == mtime_ns


def record_external_upload(local_dir: Path, relpath: str, key: str, settings: Any) -> None:
    """Mark `relpath` as already present in R2 (e.g. pushed by a Vast.ai render
    box) without uploading it, by stat-ing the local copy.

    Takes `settings` (not just dir/relpath/key) so the manifest's bucket/prefix
    stay consistent with what the next `plan_sync` call will check -- writing a
    wrong bucket here would make that sync discard the whole manifest as stale.
    """
    local_dir = Path(local_dir)
    suffix = "/" + relpath
    if not key.endswith(suffix):
        raise ValueError(f"key {key!r} không khớp relpath {relpath!r}")
    prefix = key[: -len(suffix)]

    data = load(local_dir, settings.bucket, prefix)
    full_path = local_dir / relpath
    stat = full_path.stat()
    data["files"][relpath] = {
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "md5": file_md5(full_path),
        "key": key,
        "uploaded_at": time.time(),
    }
    save(local_dir, data)
