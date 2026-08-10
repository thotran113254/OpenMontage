"""HTTP API for the auto-edit jobs.

Media routing is the part worth understanding: the <Player> in the browser and
the Remotion CLI must read the SAME bytes, or a preview means nothing. The
renderer gets them from the job's staged public dir; the browser gets them from
`/api/media/<job_id>/<name>`, which serves job files first and falls back to
the shared audio library. Props therefore carry bare filenames in both worlds.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from lib.talking_head_edit.job_store import (
    DEFAULT_OPTIONS, SHARED_PUBLIC, JobStore, find_job, list_all_jobs,
)
from lib.talking_head_edit.resources import inventory
from server.paths_guard import check_input_path, safe_media_name
from server.queue_worker import AlreadyRunningError, JobQueue, QueuedRun
from server.sse import event_stream

router = APIRouter()
store = JobStore()
job_queue = JobQueue(store)

UPLOAD_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}


def _check_input_path(raw: str) -> Path:
    """`projects/` counts as an allowed root here because uploads land there."""
    return check_input_path(raw, extra_root=store.root.parent)


def _projects_root() -> Path:
    """The project store's root, read at call time.

    Imported lazily so `api_projects` can import this module's queue without a
    circular import at module load, and read per call so a test that swaps the
    store is honoured.
    """
    from server.api_projects import store as project_store

    return project_store.root


def _job(job_id: str):
    """A job by id from either layout — legacy root or inside a project."""
    try:
        return find_job(job_id, legacy_root=store.root,
                        projects_root=_projects_root())
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


def _submit(run: QueuedRun) -> int:
    """`job_queue.submit`, with a restart-orphaned run surfaced as a 409.

    Without this, POSTing run/render/revise/autopilot for a job whose previous
    server died mid-render would start a SECOND process writing the same
    `src.mp4` / `final.mp4` at the same time as the still-alive first one.
    """
    try:
        return job_queue.submit(run)
    except AlreadyRunningError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/resources")
def get_resources() -> dict[str, Any]:
    data = inventory()
    return {
        "sfx": data["sfx"],
        "bgm": data["bgm"],
        "group_labels": data["group_labels"],
        "warnings": data["warnings"],
        "defaults": DEFAULT_OPTIONS,
    }


@router.get("/queue")
def get_queue() -> dict[str, Any]:
    return job_queue.status()


@router.get("/jobs")
def list_jobs() -> list[dict[str, Any]]:
    """Every build, from the legacy root and from inside every project.

    One list rather than two so a legacy job stays openable at the same URL it
    always had — that is the point of reading both layouts instead of migrating.
    """
    return list_all_jobs(legacy_root=store.root, projects_root=_projects_root())


@router.post("/jobs")
async def create_job(request: Request) -> dict[str, Any]:
    payload = await request.json()
    input_path = _check_input_path(payload.get("input_path", ""))
    options = {**DEFAULT_OPTIONS, **(payload.get("options") or {})}
    job = store.create(input_path, options, title=payload.get("title", ""))
    position = _submit(QueuedRun(job.job_id, stages=payload.get("stages")))
    return {"job_id": job.job_id, "queue_position": position}


@router.post("/jobs/upload")
async def upload_job(file: UploadFile, title: str = "", prompt: str = "") -> dict[str, Any]:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in UPLOAD_SUFFIXES:
        raise HTTPException(400, f"Định dạng không hỗ trợ: {suffix or 'không rõ'}")

    staging = store.root / "_uploads"
    staging.mkdir(parents=True, exist_ok=True)
    target = staging / (file.filename or "upload.mp4")
    with open(target, "wb") as out:
        shutil.copyfileobj(file.file, out)

    job = store.create(target, {**DEFAULT_OPTIONS, "prompt": prompt}, title=title)
    position = _submit(QueuedRun(job.job_id))
    return {"job_id": job.job_id, "queue_position": position}


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = _job(job_id)

    state = job.load()
    version = int(state.get("current_version", 0))
    return {
        **state,
        "audit_report": _read_json(job.dir / f"audit_report_v{version}.json"),
        "resolve_report": _read_json(job.dir / f"resolve_report_v{version}.json"),
        "verify_report": _read_json(job.dir / f"verify_report_v{version}.json"),
        "props": _read_json(job.props_path(version)),
        "has_final": job.final_path.exists(),
        "media_base": f"/api/media/{job_id}/",
    }


@router.get("/jobs/{job_id}/spine")
def get_spine(job_id: str) -> dict[str, Any]:
    """The words the director saw, for the transcript editor.

    Filtered by the selection and carrying `_orig_index`, so a word index the UI
    shows is the same index a revise instruction can name.
    """
    from lib.talking_head_edit.stages.direct import director_words

    job = _job(job_id)
    if not job.spine_path.exists():
        return {"words": [], "boundaries": [], "cut_ranges": []}

    words, boundaries = director_words(job)
    state = job.load()
    version = int(state.get("current_version", 0))
    spec = _read_json(job.spec_path(version)) or {}
    cuts = [[int(pair[0]), int(pair[1])]
            for pair in (spec.get("cut_remove") or [])
            if isinstance(pair, (list, tuple)) and len(pair) >= 2]
    return {
        "words": [{k: v for k, v in word.items() if k != "_orig_index"}
                  for word in words],
        "boundaries": boundaries,
        "cut_ranges": cuts,
        "speakers": (_read_json(job.spine_path) or {}).get("speakers") or [],
    }


@router.get("/jobs/{job_id}/events")
def stream_events(job_id: str, request: Request, offset: int = Query(0)) -> StreamingResponse:
    job = _job(job_id)
    last_event_id = request.headers.get("last-event-id")
    start = int(last_event_id) if last_event_id and last_event_id.isdigit() else offset
    return StreamingResponse(
        event_stream(job, start),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/jobs/{job_id}/run")
async def run_stages(job_id: str, request: Request) -> dict[str, Any]:
    payload = await request.json() if await request.body() else {}
    job = _job(job_id)
    if payload.get("options"):
        state = job.load()
        state["options"] = {**state.get("options", {}), **payload["options"]}
        job.save(state)
    position = _submit(QueuedRun(
        job_id, stages=payload.get("stages"), use_cache=payload.get("use_cache", True)
    ))
    return {"job_id": job_id, "queue_position": position}


@router.post("/jobs/{job_id}/render")
def render_job(job_id: str, scale: float = 1.0) -> dict[str, Any]:
    job = _job(job_id)
    state = job.load()
    state["options"] = {**state.get("options", {}), "render_scale": scale}
    job.save(state)
    position = _submit(QueuedRun(job_id, stages=["render"], use_cache=False))
    return {"job_id": job_id, "queue_position": position}


@router.post("/jobs/{job_id}/revise")
async def revise_job(job_id: str, request: Request) -> dict[str, Any]:
    """Patch the current spec from a plain-language instruction, then re-cut.

    Render is deliberately NOT queued: the point of patching is a fast preview
    loop, and the user decides when a version is worth six minutes of render.
    """
    payload = await request.json()
    instruction = (payload.get("instruction") or "").strip()
    if not instruction:
        raise HTTPException(400, "Thiếu nội dung yêu cầu sửa")
    if len(instruction) > 2000:
        raise HTTPException(400, "Yêu cầu quá dài (tối đa 2000 ký tự)")

    job = _job(job_id)
    state = job.load()
    state["options"] = {**state.get("options", {}), "revise_instruction": instruction}
    job.save(state)
    position = _submit(QueuedRun(
        job_id, stages=["revise", "audit", "resolve"], use_cache=False
    ))
    return {"job_id": job_id, "queue_position": position}


@router.post("/jobs/{job_id}/autopilot")
async def autopilot_job(job_id: str, request: Request) -> dict[str, Any]:
    """Run the chain and fix one known problem, in the background.

    Queued rather than awaited: a full pass is minutes, and holding an HTTP
    connection open for a render is how a UI ends up with a spinner that never
    resolves. Progress arrives through the job's SSE stream like every other run.
    """
    payload = await request.json() if await request.body() else {}
    job = _job(job_id)
    if payload.get("options"):
        state = job.load()
        state["options"] = {**state.get("options", {}), **payload["options"]}
        job.save(state)
    position = _submit(QueuedRun(
        job_id,
        # The CLI owns the retry loop, so the queue just runs it as one command.
        extra_args=["--autopilot"] + (
            ["--stages", ",".join(payload["stages"])] if payload.get("stages") else []),
        use_cache=payload.get("use_cache", True),
    ))
    return {"job_id": job_id, "queue_position": position,
            "follow": f"/api/jobs/{job_id}/events"}


@router.get("/jobs/{job_id}/chat")
def chat_history(job_id: str) -> dict[str, Any]:
    from lib.talking_head_edit.chat_revise import read_history

    job = _job(job_id)
    return {"job_id": job_id, "turns": read_history(job)}


@router.post("/jobs/{job_id}/chat")
async def chat_turn(job_id: str, request: Request) -> dict[str, Any]:
    """One conversational revise turn.

    Runs inline, unlike autopilot: a revise is one small model call (~8k tokens by
    design), and the caller wants the diff back to decide what to say next.
    """
    from lib.talking_head_edit.chat_revise import ChatReviseError, chat

    payload = await request.json()
    job = _job(job_id)
    try:
        return chat(job, str(payload.get("message") or ""),
                    dry_run=bool(payload.get("dry_run")))
    except ChatReviseError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — a model refusal is a 400, not a 500
        raise HTTPException(400, f"Revise thất bại: {exc}") from exc


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


@router.post("/jobs/{job_id}/rollback")
async def rollback(job_id: str, request: Request) -> dict[str, Any]:
    """Make an earlier version current again by copying it forward.

    Copying rather than rewinding keeps the history append-only: you can always
    get back to what you rolled away from.
    """
    payload = await request.json()
    target = int(payload.get("version", 0))
    job = _job(job_id)
    if not job.spec_path(target).exists():
        raise HTTPException(404, f"Không có phiên bản v{target}")

    state = job.load()
    new_version = int(state.get("current_version", 0)) + 1
    shutil.copyfile(job.spec_path(target), job.spec_path(new_version))
    if job.props_path(target).exists():
        shutil.copyfile(job.props_path(target), job.props_path(new_version))
    state["current_version"] = new_version
    state.setdefault("versions", []).append({
        "version": new_version, "kind": "rollback",
        "instruction": f"quay lại v{target}", "rolled_back_from": target,
    })
    job.save(state)
    job.emit("log", "edit", f"Quay lại v{target} (tạo v{new_version})")
    return {"version": new_version, "restored_from": target}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    return {"cancelled": job_queue.cancel(job_id)}


@router.put("/jobs/{job_id}/props")
async def save_props(job_id: str, request: Request) -> dict[str, Any]:
    """Save hand-edited props as the next version — no model call involved."""
    job = _job(job_id)
    props = await request.json()
    state = job.load()
    version = int(state.get("current_version", 0)) + 1
    job.props_path(version).write_text(
        json.dumps(props, indent=2, ensure_ascii=False), encoding="utf-8")
    # the spec that produced it stays the parent of this version
    previous_spec = job.spec_path(version - 1)
    if previous_spec.exists():
        shutil.copyfile(previous_spec, job.spec_path(version))
    state["current_version"] = version
    state.setdefault("versions", []).append({
        "version": version, "kind": "manual", "instruction": "sửa tay trong UI",
    })
    job.save(state)
    job.emit("log", "edit", f"Lưu bản chỉnh tay thành v{version}")
    return {"version": version}


@router.get("/media/{job_id}/{name}")
def get_media(job_id: str, name: str) -> FileResponse:
    """Job file first, then the shared audio library. Never escapes either."""
    name = safe_media_name(name)
    job = _job(job_id)
    # `preview/timeline` is in the list because the seam images live there and the
    # UI refers to them by bare filename, like every other medium.
    for candidate in (job.dir / name, job.render_public_dir / name,
                      job.dir / "preview" / "timeline" / name,
                      job.dir / "preview" / name, SHARED_PUBLIC / name):
        if candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
    raise HTTPException(404, f"Không có file {name}")


@router.get("/media/{job_id}/frames/{name}")
def get_frame(job_id: str, name: str) -> FileResponse:
    job = _job(job_id)
    path = job.dir / "verify_frames" / safe_media_name(name)
    if not path.exists():
        raise HTTPException(404, "Không có frame này")
    return FileResponse(path)
