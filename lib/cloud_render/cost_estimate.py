"""Local-only cost/size estimation helpers for `VastCloudRender`.

Every function here reads local job/props files or the on-disk queue --
never the network/SDK -- so calling any of them (even repeatedly, even with
`vastai` uninstalled) is free and side-effect-free. Kept separate from
`tools/video/vast_cloud_render.py` so that file stays a thin registry
wrapper (see its module docstring) instead of growing a second copy of the
size-accounting logic `kit.py` already owns.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.cloud_render import kit
from lib.cloud_render.queue import OVERHEAD_MINUTES

__all__ = [
    "OVERHEAD_MINUTES",
    "job_duration_seconds",
    "queued_job_ids",
    "estimate_kit_size_bytes",
    "total_render_minutes",
]


def job_duration_seconds(job_id: str | None) -> float:
    """`durationSeconds` from a job's current props file, or 0.0 if the job
    or its props do not exist yet -- a missing/unstaged job degrades the
    estimate, it never raises."""
    if not job_id:
        return 0.0
    try:
        from lib.talking_head_edit.job_store import find_job
        job = find_job(job_id)
        version = int(job.load().get("current_version") or 0)
        props_path = job.props_path(version)
        if props_path.exists():
            props = json.loads(props_path.read_text(encoding="utf-8"))
            return float(props.get("durationSeconds") or 0.0)
    except Exception:  # noqa: BLE001 -- a missing/unstaged job degrades the estimate, never crashes
        pass
    return 0.0


def queued_job_ids() -> list[str]:
    """Every job_id currently sitting in the batch queue, or `[]` on any
    read failure -- a queue read must never crash an estimate."""
    from lib.cloud_render import queue as cloud_queue
    try:
        return [entry.job_id for entry in cloud_queue.list_entries()]
    except Exception:  # noqa: BLE001
        return []


def _path_size_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    if path.is_dir():
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return 0


def _composer_kit_size_bytes() -> int:
    return sum(_path_size_bytes(kit.COMPOSER_DIR / name) for name in kit.COMPOSER_ALLOWLIST)


def _job_kit_size_bytes(job_id: str | None) -> int:
    if not job_id:
        return 0
    try:
        from lib.talking_head_edit.job_store import find_job
        job = find_job(job_id)
        version = int(job.load().get("current_version") or 0)
        return (_path_size_bytes(job.props_path(version))
                + _path_size_bytes(job.render_public_dir))
    except Exception:  # noqa: BLE001 -- a missing/unstaged job just yields a smaller estimate
        return 0


def estimate_kit_size_bytes(mode: str, inputs: dict[str, Any]) -> int:
    """Composer kit (shared) + per-job kit(s), summed straight off disk --
    never actually builds a kit (that requires `stage_assets` to have run
    and would be a real, if temporary, side effect)."""
    composer = _composer_kit_size_bytes()
    if mode == "flush":
        job_ids = inputs.get("job_ids") or queued_job_ids()
        return composer + sum(_job_kit_size_bytes(jid) for jid in job_ids)
    return composer + _job_kit_size_bytes(inputs.get("job_id"))


def total_render_minutes(mode: str, inputs: dict[str, Any],
                         render_seconds_per_video_second: float) -> float:
    """Sum of the target job(s)' own duration, scaled by the config's
    ledger-calibrated `render_seconds_per_video_second` constant."""
    if mode == "flush":
        job_ids = inputs.get("job_ids") or queued_job_ids()
        total_seconds = sum(job_duration_seconds(jid) for jid in job_ids)
    else:
        total_seconds = job_duration_seconds(inputs.get("job_id"))
    return total_seconds * render_seconds_per_video_second / 60.0
