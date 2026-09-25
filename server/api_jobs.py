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

from fastapi import APIRouter, Body, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from server.schemas import (
    AutopilotRequest, AutopilotResponse, ChatRequest, ChatResponse,
    CreateJobRequest, CreateJobResponse, JobDetailResponse, JobSummaryResponse,
    QueueStatusResponse, ResourceInventoryResponse, ReviseRequest,
    RunStagesRequest, RunStagesResponse, SpineResponse, UpdatePropsResponse,
)
from lib.talking_head_edit.job_store import (
    DEFAULT_OPTIONS, SHARED_PUBLIC, JobStore, find_job, list_all_jobs, merge_options,
)
from lib.talking_head_edit.resources import inventory
from server.errors import CodedHTTPException
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


def _ensure_not_busy(job_id: str) -> None:
    """409 `job_busy` while the queue is running/adopting/holding `job_id`.

    Chat, revise, and the deterministic cuts endpoint all `job.load()` ->
    modify -> `job.save()` unlocked; running that against the same job.json a
    queued subprocess is mid-write on can lose the subprocess's update (see M5
    in the automation-API review).
    """
    if job_queue.is_busy(job_id):
        raise CodedHTTPException(
            409, f"Job {job_id} đang chạy hoặc đang trong hàng đợi — thử lại sau",
            code="job_busy")


@router.get("/resources", response_model=ResourceInventoryResponse)
def get_resources() -> dict[str, Any]:
    data = inventory()
    return {
        "sfx": data["sfx"],
        "bgm": data["bgm"],
        "group_labels": data["group_labels"],
        "warnings": data["warnings"],
        "defaults": DEFAULT_OPTIONS,
    }


@router.get("/queue", response_model=QueueStatusResponse)
def get_queue() -> dict[str, Any]:
    return job_queue.status()


@router.get("/jobs", response_model=list[JobSummaryResponse])
def list_jobs() -> list[dict[str, Any]]:
    """Every build, from the legacy root and from inside every project.

    One list rather than two so a legacy job stays openable at the same URL it
    always had — that is the point of reading both layouts instead of migrating.
    """
    return list_all_jobs(legacy_root=store.root, projects_root=_projects_root())


@router.post("/jobs", response_model=CreateJobResponse)
def create_job(payload: CreateJobRequest) -> dict[str, Any]:
    input_path = _check_input_path(payload.input_path)
    options = merge_options(DEFAULT_OPTIONS, payload.options)
    job = store.create(input_path, options, title=payload.title)
    position = _submit(QueuedRun(job.job_id, stages=payload.stages))
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


@router.get("/jobs/{job_id}", response_model=JobDetailResponse)
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
        "has_thumbnail": (job.dir / "thumbnail.jpg").exists(),
        "visuals_report": _read_json(job.dir / "visuals_report.json"),
        "media_base": f"/api/media/{job_id}/",
    }


@router.get("/jobs/{job_id}/spine", response_model=SpineResponse, response_model_exclude_none=True)
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


@router.post("/jobs/{job_id}/run", response_model=RunStagesResponse)
def run_stages(job_id: str, payload: RunStagesRequest | None = None) -> dict[str, Any]:
    if payload is None:
        payload = RunStagesRequest()
    job = _job(job_id)
    if payload.options:
        state = job.load()
        state["options"] = merge_options(state.get("options", {}), payload.options)
        job.save(state)
    position = _submit(QueuedRun(
        job_id, stages=payload.stages, use_cache=payload.use_cache
    ))
    return {"job_id": job_id, "queue_position": position}

@router.post("/jobs/{job_id}/visuals", response_model=RunStagesResponse)
def generate_visuals(job_id: str) -> dict[str, Any]:
    """LLM-design the outro + generate cover stills, then re-render.

    Image generation is a gateway call (30-90s) so this goes through the same
    worker as render — the UI watches the job stream like any other stage.
    """
    job = _job(job_id)
    stages = ["visuals", *render_stages(job.load().get("options") or {})]
    position = _submit(QueuedRun(job_id, stages=stages, use_cache=False))
    return {"job_id": job_id, "queue_position": position}


RENDER_LOCATIONS = ("local", "colab")


def render_stages(options: dict[str, Any]) -> list[str]:
    """Render, then measure the file it produced.

    `verify` reads `final.mp4`; a draft (render_scale < 1) writes
    `preview_<n>.mp4` instead, so verifying after a draft would grade a stale
    full render — or nothing — and report it as this run's result.
    """
    if float(options.get("render_scale", 1.0) or 1.0) >= 1.0:
        return ["render", "verify"]
    return ["render"]


@router.post("/jobs/{job_id}/render", response_model=RunStagesResponse)
def render_job(job_id: str, scale: float = 1.0, location: str = "local") -> dict[str, Any]:
    """Queue a render. `location=colab` renders full size on Colab's TPU v6e-1."""
    if location not in RENDER_LOCATIONS:
        raise HTTPException(400, f"location phải là một trong {RENDER_LOCATIONS}")
    job = _job(job_id)
    state = job.load()
    state["options"] = merge_options(state.get("options", {}),
                                     {"render_scale": scale, "render_location": location})
    job.save(state)
    position = _submit(QueuedRun(job_id, stages=render_stages(state["options"]), use_cache=False))
    return {"job_id": job_id, "queue_position": position}


@router.post("/jobs/{job_id}/revise", response_model=RunStagesResponse)
def revise_job(job_id: str, payload: ReviseRequest) -> dict[str, Any]:
    """Patch the current spec from a plain-language instruction, then re-cut.

    Render is deliberately NOT queued: the point of patching is a fast preview
    loop, and the user decides when a version is worth six minutes of render.
    """
    instruction = (payload.instruction or "").strip()
    if not instruction:
        raise HTTPException(400, "Thiếu nội dung yêu cầu sửa")
    if len(instruction) > 2000:
        raise HTTPException(400, "Yêu cầu quá dài (tối đa 2000 ký tự)")

    job = _job(job_id)
    _ensure_not_busy(job_id)
    state = job.load()
    opts = merge_options(state.get("options", {}), {"revise_instruction": instruction})
    if payload.options:
        opts = merge_options(opts, payload.options)
    state["options"] = opts
    job.save(state)
    position = _submit(QueuedRun(
        job_id, stages=["revise", "audit", "resolve"], use_cache=False
    ))
    return {"job_id": job_id, "queue_position": position}


@router.post("/jobs/{job_id}/autopilot", response_model=AutopilotResponse)
def autopilot_job(job_id: str, payload: AutopilotRequest | None = None) -> dict[str, Any]:
    """Run the chain and fix one known problem, in the background.

    Queued rather than awaited: a full pass is minutes, and holding an HTTP
    connection open for a render is how a UI ends up with a spinner that never
    resolves. Progress arrives through the job's SSE stream like every other run.
    """
    if payload is None:
        payload = AutopilotRequest()
    if payload.dry_run:
        # `lib.talking_head_edit.autopilot.run()` has no dry-run mode (it
        # always executes and can render) — echoing the flag back while
        # running a real pass anyway would silently do the opposite of what
        # was asked (see M11 in the automation-API review).
        raise CodedHTTPException(
            400, "Autopilot chưa hỗ trợ dry_run — CLI luôn chạy thật, có thể render",
            code="unsupported")
    job = _job(job_id)
    if payload.options:
        state = job.load()
        state["options"] = merge_options(state.get("options", {}), payload.options)
        job.save(state)
    position = _submit(QueuedRun(
        job_id,
        extra_args=["--autopilot"] + (
            ["--stages", ",".join(payload.stages)] if payload.stages else []),
        use_cache=payload.use_cache,
    ))
    return {"job_id": job_id, "queue_position": position,
            "follow": f"/api/jobs/{job_id}/events",
            "dry_run": payload.dry_run}

@router.get("/jobs/{job_id}/chat")
def chat_history(job_id: str) -> dict[str, Any]:
    from lib.talking_head_edit.chat_revise import read_history

    job = _job(job_id)
    return {"job_id": job_id, "turns": read_history(job)}


def _run_chat_turn(job: Any, message: str, *, dry_run: bool = False,
                    extra_stages: list[str] | None = None,
                    model: str | None = None) -> dict[str, Any]:
    """One conversational revise turn — shared by `POST /jobs/{id}/chat` and
    the automation API's `POST /runs/{id}/revise`, so the model call, the
    validation, the busy check, and the preview-recut queueing exist in
    exactly one place.

    Revise runs inline (~8k tokens) so the caller gets the diff immediately.
    When the turn is applied, audit+resolve (+ `extra_stages`, e.g. render for
    an automation run created with render=true) are queued next.
    """
    from lib.talking_head_edit.chat_revise import MAX_MESSAGE_CHARS, ChatReviseError, chat

    _ensure_not_busy(job.job_id)
    text = (message or "").strip()
    if not text:
        raise CodedHTTPException(400, "Thiếu tin nhắn chat", code="bad_request")
    # One limit, not two: `chat()` itself enforces MAX_MESSAGE_CHARS and would
    # raise first if this endpoint used a different number.
    if len(text) > MAX_MESSAGE_CHARS:
        raise CodedHTTPException(
            400, f"Tin nhắn quá dài (tối đa {MAX_MESSAGE_CHARS} ký tự)", code="bad_request")

    options = {"model": model} if model else None
    try:
        result = chat(job, text, options=options, dry_run=dry_run)
    except ChatReviseError as exc:
        raise CodedHTTPException(400, str(exc), code="revise_failed") from exc
    except Exception as exc:  # noqa: BLE001 — a model refusal is a 400, not a 500
        raise CodedHTTPException(400, f"Revise thất bại: {exc}", code="model_refused") from exc

    if result.get("applied") and not dry_run:
        stages = ["audit", "resolve"] + list(extra_stages or [])
        position = _submit(QueuedRun(job.job_id, stages=stages, use_cache=False))
        result["preview_queued"] = True
        result["queue_position"] = position
    return result


@router.post("/jobs/{job_id}/chat", response_model=ChatResponse)
def chat_turn(job_id: str, payload: ChatRequest) -> dict[str, Any]:
    """One conversational revise turn. See `_run_chat_turn` for the details."""
    job = _job(job_id)
    return _run_chat_turn(job, payload.message, dry_run=payload.dry_run, model=payload.model)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    return {"cancelled": job_queue.cancel(job_id)}


@router.put("/jobs/{job_id}/props", response_model=UpdatePropsResponse)
def save_props(job_id: str, props: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Save hand-edited props as the next version — no model call involved."""
    job = _job(job_id)
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
    return {"version": version, "job_id": job_id}


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
