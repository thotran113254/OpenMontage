"""HTTP API for the cloud batch queue and paid cloud render.

Two surfaces share this router:

1. Free local queue ops (`list` / `enqueue` / `remove` / `flush_check`) —
   pure disk, zero network, always safe. Powers the "lên lịch batch" UX.
2. Paid ops (`preview` = dry_run, `execute` = background rent) — require an
   explicit `confirm: true` on execute, and refuse without a prior dry_run_ref
   so an offer the user never saw cannot be rented.

There is no cron/overnight auto-flush: scheduling means "sit in the batch
queue until a human (or agent with consent) flushes".
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from server.schemas import (
    ClearCloudQueueRequest, CloudQueueResponse, CloudStatusResponse,
    EnqueueCloudRequest, EnqueueCloudResponse, ExecuteCloudRequest,
    PreviewCloudRequest,
)
from server.cloud_worker import AlreadyRunningError, cloud_worker

router = APIRouter()


def _legacy_jobs_root():
    from server.api_jobs import store
    return store.root


def _projects_root():
    from server.api_projects import store as project_store
    return project_store.root


def _find_job(job_id: str):
    from lib.talking_head_edit.job_store import find_job
    try:
        return find_job(job_id, legacy_root=_legacy_jobs_root(),
                        projects_root=_projects_root())
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


def _config_snapshot() -> dict[str, Any]:
    from lib.cloud_render import config as cloud_config

    try:
        resolved = cloud_config.resolve()
        return {
            "enabled": bool(resolved.get("enabled", False)),
            "pricing_mode": resolved.get("pricing_mode"),
            "max_dph_usd": resolved.get("max_dph_usd"),
            "max_total_usd_per_rental": resolved.get("max_total_usd_per_rental"),
            "max_runtime_minutes": resolved.get("max_runtime_minutes"),
            "max_batch_runtime_minutes": resolved.get("max_batch_runtime_minutes"),
            "batch": dict(resolved.get("batch") or {}),
            "render_seconds_per_video_second": resolved.get(
                "render_seconds_per_video_second", 1.9),
            "reason": None if resolved.get("enabled") else (
                "enabled: false trong config/cloud-render.json — "
                "có thể xếp lịch batch, nhưng chưa thuê máy được cho đến khi bật"),
            "valid": True,
            "error": None,
        }
    except cloud_config.CloudRenderConfigError as exc:
        return {
            "enabled": False,
            "pricing_mode": None,
            "max_dph_usd": None,
            "max_total_usd_per_rental": None,
            "max_runtime_minutes": None,
            "max_batch_runtime_minutes": None,
            "batch": {},
            "render_seconds_per_video_second": 1.9,
            "reason": str(exc),
            "valid": False,
            "error": str(exc),
        }


def _enrich_entry(raw: dict[str, Any]) -> dict[str, Any]:
    """Attach job title / status / has_final for the management table."""
    out = dict(raw)
    job_id = str(raw.get("job_id") or "")
    out.setdefault("title", job_id)
    out.setdefault("job_status", "unknown")
    out.setdefault("has_final", False)
    out.setdefault("has_props", False)
    try:
        job = _find_job(job_id)
        state = job.load()
        out["title"] = state.get("title") or job_id
        out["job_status"] = state.get("status") or "unknown"
        out["has_final"] = job.final_path.exists()
        version = int(state.get("current_version") or 0)
        out["has_props"] = version > 0 and job.props_path(version).exists()
        out["current_version"] = version
        if not out.get("project_id"):
            out["project_id"] = state.get("project_id")
    except HTTPException:
        out["job_status"] = "missing"
    except Exception:  # noqa: BLE001 — enrichment is best-effort
        pass
    return out


def _enqueue_job(job_id: str, note: str = "") -> dict[str, Any]:
    from lib.cloud_render import config as cloud_config
    from lib.cloud_render import cost_estimate
    from lib.cloud_render import queue as cloud_queue

    job = _find_job(job_id)
    state = job.load()
    version = int(state.get("current_version") or 0)
    if version < 1:
        raise HTTPException(
            400, "Job chưa có bản dựng (props) — chạy stage resolve trước khi xếp lịch render")

    duration_seconds = cost_estimate.job_duration_seconds(job_id)
    try:
        resolved = cloud_config.resolve()
        rate = float(resolved.get("render_seconds_per_video_second", 1.9))
    except cloud_config.CloudRenderConfigError:
        rate = 1.9
    estimated = duration_seconds * rate

    try:
        entry = cloud_queue.enqueue(
            job_id,
            version=version,
            estimated_render_seconds=estimated,
            duration_seconds=duration_seconds,
            note=note,
            project_id=state.get("project_id"),
        )
    except cloud_queue.QueueLockError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _enrich_entry(entry.to_dict())


@router.get("/cloud/status", response_model=CloudStatusResponse)
def cloud_status() -> dict[str, Any]:
    from lib.cloud_render import config as cloud_config
    from lib.cloud_render import queue as cloud_queue

    cfg = _config_snapshot()
    try:
        entries = [e.to_dict() for e in cloud_queue.list_entries()]
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Đọc batch queue lỗi: {exc}") from exc

    flush_check: dict[str, Any] = {
        "job_count": len(entries),
        "estimated_render_minutes": 0,
        "oldest_age_minutes": 0,
        "thresholds": cfg.get("batch") or {},
        "thresholds_met": [],
        "estimated_cost_usd": 0,
        "estimated_cost_if_rendered_separately_usd": 0,
        "amortization_note": "",
    }
    if cfg.get("valid"):
        try:
            flush_check = cloud_queue.flush_check(cloud_config.resolve())
        except Exception:  # noqa: BLE001 — still return entries if flush_check fails
            pass

    return {
        "config": cfg,
        "queue": {
            "count": len(entries),
            "entries": [_enrich_entry(e) for e in entries],
        },
        "flush_check": flush_check,
        "operation": cloud_worker.status(),
        "ready_to_flush": bool(flush_check.get("thresholds_met")),
    }


@router.get("/cloud/queue", response_model=CloudQueueResponse)
def list_cloud_queue() -> dict[str, Any]:
    from lib.cloud_render import config as cloud_config
    from lib.cloud_render import queue as cloud_queue

    entries = [_enrich_entry(e.to_dict()) for e in cloud_queue.list_entries()]
    try:
        report = cloud_queue.flush_check(cloud_config.resolve())
    except Exception as exc:  # noqa: BLE001
        report = {"error": str(exc), "job_count": len(entries), "thresholds_met": []}
    return {"entries": entries, "flush_check": report}


@router.post("/cloud/queue", response_model=EnqueueCloudResponse)
def enqueue_cloud(payload: EnqueueCloudRequest) -> dict[str, Any]:
    """Schedule a job for later batch cloud render (free, local-only)."""
    job_id = str(payload.job_id or "").strip()
    if not job_id:
        raise HTTPException(400, "Thiếu job_id")
    note = str(payload.note or "")
    entry = _enqueue_job(job_id, note=note)
    return {"entry": entry, "message": f"Đã xếp {job_id} vào lịch batch cloud"}

@router.delete("/cloud/queue/{job_id}")
def remove_from_cloud_queue(job_id: str) -> dict[str, Any]:
    from lib.cloud_render import queue as cloud_queue

    try:
        removed = cloud_queue.remove(job_id)
    except cloud_queue.QueueLockError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not removed:
        raise HTTPException(404, f"Job {job_id} không có trong batch queue")
    return {"removed": job_id}


@router.post("/cloud/queue/clear")
def clear_cloud_queue(payload: ClearCloudQueueRequest | None = None) -> dict[str, Any]:
    if payload is None or not payload.confirm:
        raise HTTPException(400, "Cần confirm: true để xoá cả batch queue")
    from lib.cloud_render import queue as cloud_queue
    try:
        cloud_queue.clear()
    except cloud_queue.QueueLockError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"cleared": True}


@router.post("/cloud/preview")
def preview_cloud(payload: PreviewCloudRequest | None = None) -> dict[str, Any]:
    """dry_run only: offer shortlist + cost. Never rents."""
    from lib.cloud_render import announce
    from tools.video.vast_cloud_render import VastCloudRender

    if payload is None:
        payload = PreviewCloudRequest()
    mode = str(payload.mode or "render_now")
    if mode not in ("render_now", "flush"):
        raise HTTPException(400, "mode phải là render_now hoặc flush")
    inputs: dict[str, Any] = {"mode": mode}
    if mode == "render_now":
        job_id = str(payload.job_id or "").strip()
        if not job_id:
            raise HTTPException(400, "Thiếu job_id cho mode=render_now")
        _find_job(job_id)  # 404 if missing
        inputs["job_id"] = job_id
    else:
        from lib.cloud_render import queue as cloud_queue
        entries = cloud_queue.list_entries()
        if not entries:
            raise HTTPException(400, "Batch queue rỗng — không có gì để flush")
        # Optional subset; default = whole queue
        requested = payload.job_ids
        if requested:
            wanted = {str(j) for j in requested}
            job_ids = [e.job_id for e in entries if e.job_id in wanted]
            if not job_ids:
                raise HTTPException(400, "Không job_id nào khớp batch queue")
        else:
            job_ids = [e.job_id for e in entries]
        inputs["job_ids"] = job_ids

    pricing_mode = getattr(payload, "pricing_mode", None)
    if pricing_mode in ("bid", "on-demand"):
        inputs["pricing_mode"] = pricing_mode

    tool = VastCloudRender()
    dry = tool.dry_run(inputs)
    return {
        **dry,
        "announce_text": announce.format_announce(dry),
        "config": _config_snapshot(),
    }


@router.post("/cloud/execute")
def execute_cloud(payload: ExecuteCloudRequest | None = None) -> dict[str, Any]:
    """Start a paid cloud rent in the background after explicit confirm.

    Body must include confirm:true, dry_run_ref, offer_id, mode, and either
    job_id (render_now) or job_ids (flush).
    """
    from tools.video.vast_cloud_render import VastCloudRender

    if payload is None or not payload.confirm:
        raise HTTPException(
            400,
            "Cần confirm: true — cloud render tốn tiền và đẩy footage ra máy lạ")

    mode = str(payload.mode or "")
    if mode not in ("render_now", "flush"):
        raise HTTPException(400, "mode phải là render_now hoặc flush")

    offer_id = getattr(payload, "offer_id", None)
    dry_run_ref = getattr(payload, "dry_run_ref", None)
    if offer_id is None or not dry_run_ref:
        raise HTTPException(
            400, "Thiếu offer_id hoặc dry_run_ref — gọi /cloud/preview trước")

    inputs: dict[str, Any] = {
        "mode": mode,
        "offer_id": int(offer_id),
        "dry_run_ref": str(dry_run_ref),
    }
    if mode == "render_now":
        job_id = str(payload.job_id or "").strip()
        if not job_id:
            raise HTTPException(400, "Thiếu job_id")
        _find_job(job_id)
        inputs["job_id"] = job_id
        job_ids = [job_id]
    else:
        from lib.cloud_render import queue as cloud_queue
        requested = payload.job_ids
        if requested:
            job_ids = [str(j) for j in requested]
        else:
            job_ids = [e.job_id for e in cloud_queue.list_entries()]
        if not job_ids:
            raise HTTPException(400, "Batch queue rỗng")
        inputs["job_ids"] = job_ids

    pricing_mode = getattr(payload, "pricing_mode", None)
    if pricing_mode in ("bid", "on-demand"):
        inputs["pricing_mode"] = pricing_mode

    # Ceiling: client may only lower, never raise above config.
    from lib.cloud_render import config as cloud_config
    try:
        resolved = cloud_config.resolve()
    except cloud_config.CloudRenderConfigError as exc:
        raise HTTPException(400, f"config/cloud-render.json không hợp lệ: {exc}") from exc

    ceiling = float(resolved["max_total_usd_per_rental"])
    requested_max = getattr(payload, "max_total_usd", None)
    if requested_max is None:
        max_total_usd = ceiling
    else:
        max_total_usd = min(float(requested_max), ceiling)
    inputs["max_total_usd"] = max_total_usd

    def work() -> dict[str, Any]:
        tool = VastCloudRender()
        result = tool.execute(inputs)
        # If single-job cloud render finished, drop it from the batch queue so
        # a later flush does not re-upload and re-render it.
        if mode == "render_now" and result.success:
            try:
                from lib.cloud_render import queue as cloud_queue
                cloud_queue.remove(inputs["job_id"])
            except Exception:  # noqa: BLE001 — dequeue is best-effort
                pass
        return {
            "success": result.success,
            "error": result.error,
            "data": result.data,
            "cost_usd": result.cost_usd,
        }

    try:
        op = cloud_worker.start(
            mode=mode,
            job_ids=job_ids,
            work=work,
            message=f"Đang thuê máy + render ({mode}, {len(job_ids)} job)…",
        )
    except AlreadyRunningError as exc:
        raise HTTPException(409, str(exc)) from exc

    return {
        "started": True,
        "operation": op,
        "message": "Đã bắt đầu cloud render — theo dõi ở trang Lịch render",
    }


@router.get("/cloud/operation")
def cloud_operation() -> dict[str, Any]:
    return cloud_worker.status()


@router.post("/cloud/operation/clear")
def clear_cloud_operation() -> dict[str, Any]:
    try:
        return cloud_worker.clear_finished()
    except AlreadyRunningError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/cloud/job/{job_id}/queued")
def job_queue_membership(job_id: str) -> dict[str, Any]:
    """Whether this job is already sitting in the batch queue (job-detail UX)."""
    from lib.cloud_render import queue as cloud_queue

    _find_job(job_id)
    for entry in cloud_queue.list_entries():
        if entry.job_id == job_id:
            return {"queued": True, "entry": _enrich_entry(entry.to_dict())}
    return {"queued": False, "entry": None}
