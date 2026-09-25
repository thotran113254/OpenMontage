"""The automation API: one `POST` to create + queue a whole build, one `GET`
to poll it, `revise`/`cancel` alongside it.

Deliberately thin: source resolution/download lives in `source_fetch.py`,
status shaping in `run_status.py`, idempotency in `idempotency.py`. Job/build
creation itself is NOT reimplemented here — a `project_id` run reuses
`api_projects.register_local_path` + `Project.create_job`; a plain run reuses
the exact `JobStore` + `JobQueue` instance `api_jobs` already owns, so an
automation-created job is indistinguishable from a UI-created one everywhere
else in the codebase (media routes, SSE, cancel, revise).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Header, Request

from lib.talking_head_edit.job_store import REPO_ROOT, STAGES, merge_options
from lib.talking_head_edit.project_store import ProjectError
from lib.talking_head_edit.project_uploads import UploadError, safe_filename
from server.api_jobs import _check_input_path, _job, _run_chat_turn, _submit, job_queue, store
from server.api_projects import _project as _project_or_404
from server.api_projects import register_local_path
from server.auth import auth_enabled
from server.errors import CodedHTTPException
from server.idempotency import IdempotencyConflict, IdempotencyInProgress, IdempotencyStore
from server.queue_worker import QueuedRun
from server.run_status import build_run_status, resolve_base_url
from server.schemas import (
    CancelRunResponse, CreateRunRequest, CreateRunResponse, ReviseRunRequest,
    ReviseRunResponse, RunSource, RunStatusResponse,
)
from server.source_fetch import (
    BlockedURLError, SourceUnreachableError, download_url_to, is_http_url,
    validate_public_url,
)

router = APIRouter()

_STAGES_WITHOUT_RENDER = [name for name in STAGES if name not in ("render", "verify")]
_DEFAULT_MAX_DOWNLOAD_MB = 2048
_IDEMPOTENCY_RETRY_AFTER_SECONDS = "30"

_idempotency_store = IdempotencyStore(REPO_ROOT / "projects" / "automation-idempotency.json")
_download_staging = REPO_ROOT / "projects" / "_automation_downloads"


def _canonical_body_hash(payload: CreateRunRequest) -> str:
    data = payload.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _order_sources(sources: list[RunSource]) -> list[RunSource]:
    """A-roll before b-roll, stable otherwise — mirrors `Project.create_job`'s
    "speaking sources first" convention (see `project_store.py`)."""
    return sorted(sources, key=lambda item: 1 if item.role == "broll" else 0)


def _max_download_mb() -> int:
    raw = (os.environ.get("AUTOEDIT_MAX_DOWNLOAD_MB") or "").strip()
    try:
        return int(raw) if raw else _DEFAULT_MAX_DOWNLOAD_MB
    except ValueError:
        return _DEFAULT_MAX_DOWNLOAD_MB


def _download_source(url: str, index: int) -> Path:
    raw_name = Path(urlparse(url).path).name or f"source-{index}.mp4"
    try:
        filename = safe_filename(raw_name)
    except UploadError as exc:
        raise CodedHTTPException(400, str(exc), code="bad_request") from exc

    _download_staging.mkdir(parents=True, exist_ok=True)
    dest = _download_staging / f"{int(time.time() * 1000)}_{index}_{filename}"
    try:
        download_url_to(url, dest, max_mb=_max_download_mb())
    except BlockedURLError as exc:
        raise CodedHTTPException(400, str(exc), code="bad_request") from exc
    except SourceUnreachableError as exc:
        raise CodedHTTPException(502, str(exc), code="source_unreachable") from exc
    return dest


def _materialize(spec: RunSource, index: int) -> tuple[Path, bool]:
    """Resolve one `RunSource` to a local path. Returns `(path, is_downloaded)`
    so the caller knows whether the path is a temp file it should clean up
    once a project has its own copy."""
    if spec.path:
        return _check_input_path(spec.path), False
    if spec.url:
        if not is_http_url(spec.url):
            raise CodedHTTPException(400, "url phải là http/https", code="bad_request")
        return _download_source(spec.url, index), True
    raise CodedHTTPException(400, "Mỗi source cần 'path' hoặc 'url'", code="bad_request")


def _run_response(job_id: str) -> dict[str, Any]:
    job = _job(job_id)
    state = job.load()
    return {
        "run_id": job_id,
        "status": state.get("status", "queued"),
        "status_url": f"/api/runs/{job_id}",
        "events_url": f"/api/jobs/{job_id}/events",
    }


def _relocate_downloads_into_job(job: Any, materialized: list[tuple[Path, bool]]) -> None:
    """Move a downloaded (not user-path) source from the shared staging dir
    into the job's own directory, so the job owns its full lifetime — nothing
    is left in `_automation_downloads/` once the job exists (M14 in the
    automation-API review). A `path`-type source is never touched: it points
    at a file the caller owns elsewhere.
    """
    state = job.load()
    paths = list(state.get("input_paths") or [])
    moved = False
    for index, (original, is_downloaded) in enumerate(materialized):
        if not is_downloaded or index >= len(paths):
            continue
        target = job.dir / original.name
        shutil.move(str(original), str(target))
        paths[index] = str(target)
        moved = True
    if moved:
        state["input_paths"] = paths
        state["input_path"] = paths[0] if paths else state.get("input_path")
        job.save(state)


@router.post("/runs", response_model=CreateRunResponse, status_code=202)
def create_run(
    payload: CreateRunRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    body_hash = _canonical_body_hash(payload)
    reserved = False
    if idempotency_key:
        try:
            reservation = _idempotency_store.reserve(idempotency_key, body_hash)
        except IdempotencyConflict as exc:
            raise CodedHTTPException(
                409, "Idempotency-Key đã dùng cho một yêu cầu khác nội dung",
                code="idempotency_conflict") from exc
        except IdempotencyInProgress as exc:
            raise CodedHTTPException(
                409, "Yêu cầu với Idempotency-Key này đang được xử lý — thử lại sau",
                code="idempotency_in_progress",
                headers={"Retry-After": _IDEMPOTENCY_RETRY_AFTER_SECONDS}) from exc
        if not reservation.reserved:
            return _run_response(reservation.job_id)
        reserved = True

    try:
        if payload.webhook_url:
            if not is_http_url(payload.webhook_url):
                raise CodedHTTPException(400, "webhook_url phải là http/https", code="bad_request")
            try:
                validate_public_url(payload.webhook_url)
            except BlockedURLError as exc:
                raise CodedHTTPException(400, str(exc), code="bad_request") from exc

        # `current={}` on purpose: this is a fresh job, not an update onto
        # stored options, so merge_options is used only for its
        # manual-implies-auto-off transform (see job_store.merge_options) —
        # merging against DEFAULT_OPTIONS here would blow away a project's own
        # defaults once this dict reaches `Project.job_options()`.
        options: dict[str, Any] = merge_options({}, payload.options)
        if payload.prompt is not None:
            options["prompt"] = payload.prompt
        if payload.topic is not None:
            options["topic"] = payload.topic
        options["render_scale"] = payload.render_scale
        # Not a pipeline option — read back by `POST /runs/{id}/revise` to
        # decide whether a revise should re-queue render too.
        options["render"] = payload.render
        if payload.webhook_url:
            options["webhook_url"] = payload.webhook_url

        stages = None if payload.render else _STAGES_WITHOUT_RENDER
        ordered = _order_sources(payload.sources)
        materialized = [_materialize(spec, index) for index, spec in enumerate(ordered)]

        if payload.project_id:
            project = _project_or_404(payload.project_id)
            source_ids: list[str] = []
            try:
                for local_path, _is_downloaded in materialized:
                    registered = register_local_path(project, local_path, label=local_path.stem)
                    source_ids.append(str(registered["id"]))
            finally:
                # The project now owns a hardlink/copy of its own; the shared
                # staging file has no further purpose either way.
                for local_path, is_downloaded in materialized:
                    if is_downloaded:
                        local_path.unlink(missing_ok=True)
            try:
                job = project.create_job(options, title=payload.title, source_ids=source_ids)
            except ProjectError as exc:
                raise CodedHTTPException(400, str(exc), code="bad_request") from exc
        else:
            paths = [str(path) for path, _ in materialized]
            job = store.create(paths, options, title=payload.title)
            _relocate_downloads_into_job(job, materialized)

        _submit(QueuedRun(job.job_id, stages=stages))
    except Exception:
        if reserved:
            _idempotency_store.release(idempotency_key)
        raise

    if reserved:
        _idempotency_store.complete(idempotency_key, job.job_id)
    return _run_response(job.job_id)


@router.get("/runs/{run_id}", response_model=RunStatusResponse)
def get_run(run_id: str, request: Request) -> dict[str, Any]:
    job = _job(run_id)
    return build_run_status(job, base_url=resolve_base_url(request), auth_on=auth_enabled())


@router.post("/runs/{run_id}/revise", response_model=ReviseRunResponse)
def revise_run(run_id: str, payload: ReviseRunRequest) -> dict[str, Any]:
    """Reuses the exact chat-turn code `POST /jobs/{id}/chat` runs — see
    `api_jobs._run_chat_turn`. The one thing an automation-created run adds:
    if it was created with `render=true`, a revise re-queues render too.

    Defaults to False, not True: a job that never went through `POST
    /api/runs` (created via the UI or `POST /api/jobs`) has no `render` option
    at all, and re-rendering by default on a job nobody asked this endpoint to
    manage would be a surprise render most callers of a UI-created job did not
    ask for.
    """
    job = _job(run_id)
    state = job.load()
    render_flag = bool((state.get("options") or {}).get("render", False))
    extra_stages = ["render", "verify"] if render_flag else None
    result = _run_chat_turn(job, payload.message, extra_stages=extra_stages)
    return {"turn": result, "applied": bool(result.get("applied")),
            "status_url": f"/api/runs/{run_id}"}


@router.post("/runs/{run_id}/cancel", response_model=CancelRunResponse)
def cancel_run(run_id: str) -> dict[str, Any]:
    _job(run_id)  # 404 for an unknown run before touching the queue
    return {"run_id": run_id, "cancelled": job_queue.cancel(run_id)}
