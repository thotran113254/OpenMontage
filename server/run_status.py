"""Build the normalized `GET /api/runs/{run_id}` shape.

Split out from `api_runs.py` so the exact same status dict can be built from
two very different contexts: an HTTP request (which has a `Request` to derive
a base URL from) and the queue worker's background thread (which does not,
and fires the webhook payload from here too). Importing `server.api_jobs` from
here would cycle back through `server.queue_worker` -- see that module's
import of this one -- so the tiny bits this needs (`_read_json`, `STAGES`) are
kept local instead.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from lib.talking_head_edit.job_store import STAGES

_DEFAULT_PORT = "8861"
_TAIL_MAX_LINES = 500
_TAIL_CHUNK_BYTES = 8192
TERMINAL_STATUSES = {"completed", "completed_with_warnings", "failed", "cancelled"}
STAGES_WITHOUT_RENDER = [name for name in STAGES if name not in ("render", "verify")]


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def resolve_base_url(request: Any | None = None) -> str:
    """`AUTOEDIT_PUBLIC_HOST` wins when set (this is how the VPS documents
    itself in CLAUDE.md); otherwise the inbound request's own host:port;
    otherwise -- no request, e.g. from the queue worker's webhook thread --
    fall back to the bind address."""
    public_host = os.environ.get("AUTOEDIT_PUBLIC_HOST", "").strip()
    port = os.environ.get("AUTOEDIT_PORT", "").strip() or _DEFAULT_PORT
    if public_host:
        return f"http://{public_host}:{port}"
    if request is not None:
        return str(request.base_url).rstrip("/")
    bind = os.environ.get("AUTOEDIT_BIND", "").strip() or "127.0.0.1"
    host = "127.0.0.1" if bind == "0.0.0.0" else bind
    return f"http://{host}:{port}"


def _current_stage(stages: dict[str, Any]) -> str | None:
    last = None
    for name in STAGES:
        status = (stages.get(name) or {}).get("status")
        if status == "running":
            return name
        if status in ("completed", "failed"):
            last = name
    return last


def _relevant_stages(state: dict[str, Any]) -> list[str]:
    """The stage list this run was actually queued with. An automation run
    created with `render: false` (see `api_runs.py`) never touches
    `render`/`verify` — dividing by all 9 `STAGES` would cap it at ~78% even
    once every requested stage is done."""
    if (state.get("options") or {}).get("render") is False:
        return STAGES_WITHOUT_RENDER
    return list(STAGES)


def _percent(stages: dict[str, Any], render_percent: int | None,
             relevant: list[str]) -> int:
    total = len(relevant)
    done = 0.0
    for name in relevant:
        status = (stages.get(name) or {}).get("status")
        if status == "completed":
            done += 1
        elif name == "render" and status == "running" and render_percent is not None:
            done += max(0.0, min(1.0, render_percent / 100))
    return int(round(done / total * 100)) if total else 0


def _tail_lines(path: Path, max_lines: int = _TAIL_MAX_LINES,
                chunk_size: int = _TAIL_CHUNK_BYTES) -> list[str]:
    """The last `max_lines` lines of `path`, reading backwards from the end in
    chunks rather than the whole file -- a long-running job's event log can
    run into the thousands of lines, and this is read on every status poll."""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            remaining = handle.tell()
            block = b""
            while remaining > 0 and block.count(b"\n") <= max_lines:
                read_size = min(chunk_size, remaining)
                remaining -= read_size
                handle.seek(remaining)
                block = handle.read(read_size) + block
    except OSError:
        return []
    text = block.decode("utf-8", errors="replace")
    return text.splitlines()[-max_lines:]


def _render_percent(job: Any) -> int | None:
    """Last `render` progress percent emitted, if any."""
    if not job.events_path.exists():
        return None
    for line in reversed(_tail_lines(job.events_path)):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "progress" and event.get("stage") == "render":
            percent = event.get("percent")
            return int(percent) if isinstance(percent, (int, float)) else None
    return None


def _map_state(status: str, has_final: bool) -> str:
    if status in ("queued", "created"):
        return "queued"
    if status == "running":
        return "running"
    if status == "cancelled":
        return "cancelled"
    if status == "failed":
        return "failed"
    if status in ("completed", "completed_with_warnings"):
        # Only the file decides: a render stage left "running" by an earlier
        # cancel must not make a preview-only run look delivered.
        return "succeeded" if has_final else "awaiting_render"
    return "queued"


def _error(status: str, stages: dict[str, Any]) -> dict[str, str] | None:
    if status != "failed":
        return None
    for name in reversed(STAGES):
        stage = stages.get(name) or {}
        if stage.get("status") == "failed" and stage.get("error"):
            return {"code": f"{name}_failed", "message": str(stage["error"])}
    return {"code": "internal_error", "message": "Job thất bại (không rõ stage)"}


def _duration_seconds(job: Any, version: int) -> float | None:
    verify = _read_json(job.dir / f"verify_report_v{version}.json")
    if verify and verify.get("duration_actual"):
        return float(verify["duration_actual"])
    props = _read_json(job.props_path(version))
    if props and props.get("durationSeconds"):
        return float(props["durationSeconds"])
    return None


def _mp4_url(job: Any, base_url: str, auth_on: bool) -> str | None:
    if not job.final_path.exists():
        return None
    # Prefer a presigned R2 URL when sync is enabled and the object actually
    # made it there -- it survives this VPS's disk/server, and offloads
    # bandwidth. Falls straight through to the local media route on any
    # config/network hiccup: a broken presign must never hide a finished
    # render behind a 500.
    try:
        from lib.r2_storage.config import resolve as resolve_r2_settings
        from lib.r2_storage.presign import get_url, object_exists

        settings = resolve_r2_settings()
        if settings.enabled:
            key = f"{settings.prefix}/autoedit-jobs/{job.job_id}/final.mp4"
            if object_exists(key, settings=settings):
                return get_url(key, settings.presign_expiry_seconds, settings=settings)
    except Exception:  # noqa: BLE001 -- R2 is a bonus path, never a blocker
        pass

    path = f"/api/media/{job.job_id}/final.mp4"
    if auth_on:
        from server.auth import sign_path

        expiry, sig = sign_path(path)
        if expiry is not None:
            return f"{base_url}{path}?exp={expiry}&sig={sig}"
    return f"{base_url}{path}"


def build_run_status(job: Any, *, base_url: str, auth_on: bool) -> dict[str, Any]:
    """The shape both `GET /api/runs/{id}` and the terminal-state webhook send."""
    state = job.load()
    stages = state.get("stages") or {}
    status = str(state.get("status") or "queued")
    version = int(state.get("current_version", 0))
    has_final = job.final_path.exists()

    verify = _read_json(job.dir / f"verify_report_v{version}.json")
    outputs = {
        "mp4_url": _mp4_url(job, base_url, auth_on),
        "duration_seconds": _duration_seconds(job, version),
        "verify": {
            "passed": verify.get("passed") if verify else None,
            "issues": verify.get("issues", []) if verify else [],
        },
    }
    return {
        "run_id": job.job_id,
        "state": _map_state(status, has_final),
        "stage": _current_stage(stages),
        "percent": _percent(stages, _render_percent(job), _relevant_stages(state)),
        "error": _error(status, stages),
        "outputs": outputs,
        "current_version": version,
        "versions": state.get("versions", []),
        "created_at": state.get("created_at", 0.0),
        "updated_at": state.get("updated_at", 0.0),
    }
