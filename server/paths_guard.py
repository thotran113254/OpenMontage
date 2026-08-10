"""Which filesystem paths the API may read from.

Extracted so `api_projects` and `api_jobs` share one definition instead of
importing each other. The server is localhost-only and unauthenticated, but it
does take a path from a request body, so "any path on this machine" is still the
wrong answer — a stray request would otherwise read anything the user can.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import HTTPException


def allowed_input_roots(extra: Path | None = None) -> list[Path]:
    """Roots a client may name a file inside.

    `AUTOEDIT_INPUT_ROOTS` is the configurable part; Downloads and Videos are
    there because that is where footage actually arrives on a dev machine.
    """
    configured = os.environ.get("AUTOEDIT_INPUT_ROOTS", "")
    roots = [Path(p).resolve() for p in configured.split(";") if p.strip()]
    roots += [Path.home() / "Downloads", Path.home() / "Videos"]
    if extra:
        roots.append(Path(extra).resolve())
    return [r for r in roots if r.exists()]


def check_input_path(raw: str, extra_root: Path | None = None) -> Path:
    """Resolve a client-supplied path or raise the HTTP error explaining why not."""
    path = Path(raw).resolve()
    if not path.exists():
        raise HTTPException(400, f"Không tìm thấy file: {path}")
    roots = allowed_input_roots(extra_root)
    if not any(_within(path, root) for root in roots):
        raise HTTPException(
            403,
            "File nằm ngoài thư mục được phép. Thêm đường dẫn vào AUTOEDIT_INPUT_ROOTS "
            "trong .env nếu muốn dùng.",
        )
    return path


def _within(path: Path, root: Path) -> bool:
    """True when `path` is inside `root`.

    `is_relative_to` rather than string prefixes: `str(p).startswith(str(root))`
    accepts `/data-secret` for a root of `/data`.
    """
    try:
        return path.is_relative_to(root)
    except (AttributeError, ValueError):
        return False


def safe_media_name(name: str) -> str:
    """A filename from a URL path segment, with traversal refused."""
    if "/" in name or "\\" in name or name.startswith(".") or ".." in name:
        raise HTTPException(400, "Tên file không hợp lệ")
    return name
