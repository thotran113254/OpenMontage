"""Version history, deterministic Cắt/Giữ, and rollback for one job.

Split out of `api_jobs.py` (which was pushing 500 lines) once it grew a
non-LLM cuts endpoint and an options-aware rollback — both operate on a job's
version history specifically, not the job lifecycle itself. Shares `_job`,
`_submit`, `_ensure_not_busy` and the `job_queue` instance `api_jobs.py`
already owns rather than re-deriving them, so a build made through either
router is the same job everywhere else in the codebase.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from server.api_jobs import _ensure_not_busy, _job, _read_json, _submit
from server.errors import CodedHTTPException
from server.queue_worker import QueuedRun
from server.schemas import (
    CutsRequest, CutsResponse, RollbackRequest, RollbackResponse,
)

router = APIRouter()


@router.get("/jobs/{job_id}/versions")
def list_versions(job_id: str) -> dict[str, Any]:
    job = _job(job_id)
    state = job.load()
    current = int(state.get("current_version", 0))
    versions = []
    for entry in state.get("versions", []):
        number = int(entry["version"])
        versions.append({
            **entry,
            "is_current": number == current,
            "has_props": job.props_path(number).exists(),
            "diff": _read_json(job.dir / f"revise_diff_v{number}.json"),
        })
    return {"current_version": current, "versions": versions}


def _validate_word_pairs(pairs: list[Any], field_name: str) -> list[list[int]]:
    cleaned: list[list[int]] = []
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise CodedHTTPException(
                400, f"{field_name}: mỗi khoảng cần đúng 2 chỉ số [w0, w1]", code="bad_request")
        try:
            w0, w1 = int(pair[0]), int(pair[1])
        except (TypeError, ValueError) as exc:
            raise CodedHTTPException(
                400, f"{field_name}: chỉ số phải là số nguyên", code="bad_request") from exc
        if w0 < 0 or w1 < 0 or w0 > w1:
            raise CodedHTTPException(
                400, f"{field_name}: khoảng không hợp lệ ({w0}, {w1})", code="bad_request")
        cleaned.append([w0, w1])
    return cleaned


@router.post("/jobs/{job_id}/cuts", response_model=CutsResponse)
def apply_cuts(job_id: str, payload: CutsRequest) -> dict[str, Any]:
    """Deterministic Cắt/Giữ from the transcript editor — no LLM in the loop.

    The editor used to send free text through `/chat`, and whatever the model
    decided for `nguon` (user vs. director) decided whether the verifier and
    lexicon gate could be bypassed — a model choosing wrong let it silently
    re-block a cut the user explicitly asked for (H3 in the automation-API
    review). This calls `apply_user_cuts` directly instead: `nguon` is always
    `khach`, unconditionally.
    """
    from lib.talking_head_edit.versions import apply_user_cuts

    job = _job(job_id)
    _ensure_not_busy(job_id)
    cut = _validate_word_pairs(payload.cut, "cut")
    keep = _validate_word_pairs(payload.keep, "keep")
    try:
        result = apply_user_cuts(job, cut, keep)
    except ValueError as exc:
        raise CodedHTTPException(400, str(exc), code="bad_request") from exc
    position = _submit(QueuedRun(job_id, stages=["audit", "resolve"], use_cache=False))
    return {"version": result["version"], "queue_position": position,
            "report": result["report"]}


@router.post("/jobs/{job_id}/rollback", response_model=RollbackResponse)
def rollback(job_id: str, payload: RollbackRequest) -> dict[str, Any]:
    """Make an earlier version current again, options included.

    `versions.rollback_to` copies the spec forward (history stays
    append-only) and restores every option a later version changed (frame,
    bgm, cut_level, …) — copying `props_vN` too, as this endpoint used to,
    would leave `src.mp4` (one cut file per job) out of sync with the restored
    spec (H4 in the automation-API review), so a resolve is queued instead.
    """
    from lib.talking_head_edit.versions import rollback_to

    job = _job(job_id)
    try:
        result = rollback_to(job, int(payload.version))
    except FileNotFoundError as exc:
        raise CodedHTTPException(404, str(exc), code="not_found") from exc
    position = _submit(QueuedRun(job_id, stages=["resolve"], use_cache=False))
    return {"version": result["version"], "job_id": job_id,
            "reverted_to": result["reverted_to"], "restored_from": result["reverted_to"],
            "options_changed": result["options_changed"], "queue_position": position}
