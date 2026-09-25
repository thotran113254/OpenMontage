"""HTTP API for projects: a shared studio, many creator workspaces, many clips.

Its own router rather than more endpoints in `api_jobs`, which is already near
300 lines. Nothing here executes a pipeline — creating a build hands the job to
the same queue the job API uses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from server.schemas import (
    AddSourceFromPathRequest, AddSourcesResponse, CreateBatchJobsRequest,
    CreateBatchJobsResponse, CreateProjectJobRequest, CreateProjectJobResponse,
    CreateProjectRequest, ProjectDetailResponse, ProjectSummaryResponse,
    SourceDetailResponse, UpdateProjectSettingsRequest, UpdateSourceRequest,
)

from lib.talking_head_edit import assembly_config
from lib.talking_head_edit.project_store import ProjectError, ProjectStore, clean_folder
from server.paths_guard import check_input_path
from server.queue_worker import QueuedRun

router = APIRouter()
store = ProjectStore()


def _project(project_id: str):
    try:
        return store.get(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


def _bad(exc: ProjectError) -> HTTPException:
    return HTTPException(400, str(exc))


@router.get("/projects", response_model=list[ProjectSummaryResponse])
def list_projects() -> list[dict[str, Any]]:
    return store.list()


@router.post("/projects", response_model=ProjectDetailResponse)
def create_project(payload: CreateProjectRequest | None = None) -> dict[str, Any]:
    if payload is None:
        payload = CreateProjectRequest(title="")
    title = str(payload.title or "").strip()
    if not title:
        raise HTTPException(400, "Thiếu tên project")
    assembly = payload.assembly or {}
    if assembly:
        # Validate now: a bad mode stored here would fail at probe time on every
        # build instead of at the moment someone typed it.
        try:
            assembly_config.validate({**assembly_config.global_defaults(), **assembly})
        except assembly_config.AssemblyConfigError as exc:
            raise HTTPException(400, str(exc)) from exc
    project = store.create(title, assembly=assembly,
                           keyterms=payload.keyterms or [],
                           defaults=payload.defaults or {},
                           folder=clean_folder(payload.folder))
    return project.load()


@router.get("/projects/{project_id}", response_model=ProjectDetailResponse)
def get_project(project_id: str) -> dict[str, Any]:
    project = _project(project_id)
    state = project.load()
    resolved = assembly_config.resolve(state.get("assembly"))
    return {
        **state,
        "assembly_resolved": resolved,
        # Which layer each value came from: someone editing project settings has
        # to see what they are overriding and what is merely inherited.
        "assembly_origin": assembly_config.sources_of(resolved, state.get("assembly")),
        "builds": project.builds(),
    }


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, confirm: bool = Query(False)) -> dict[str, Any]:
    """Deletes the builds too, so it insists on `?confirm=true`."""
    project = _project(project_id)
    builds = len(project.load().get("jobs") or [])
    if not confirm:
        raise HTTPException(
            409,
            f"Xoá project sẽ xoá cả {builds} bản dựng bên trong. "
            "Gọi lại với ?confirm=true nếu chắc chắn.")
    return store.delete(project_id)


@router.post("/projects/{project_id}/sources", response_model=AddSourcesResponse)
async def add_sources(project_id: str, files: list[UploadFile]) -> dict[str, Any]:
    """Upload one or more sources, streamed to disk.

    One failure does not abort the batch: with five 200 MB files, losing four
    good uploads because the fifth was a `.mov` the build rejects is the wrong
    trade. Errors come back per file.
    """
    project = _project(project_id)
    added: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for upload in files:
        try:
            added.append(project.add_source(upload.file, upload.filename or "upload.mp4"))
        except ProjectError as exc:
            failed.append({"filename": upload.filename or "?", "error": str(exc)})
        finally:
            await upload.close()
    return {"added": added, "failed": failed}


def register_local_path(project, source: Path, label: str = "") -> dict[str, Any]:
    """Register a file already on disk as a project source, instead of
    uploading it. Shared by `POST .../sources/from-path` and the automation
    API's `POST /api/runs` (a `path`/downloaded `url` source becomes a project
    source the same way either caller reaches it).

    Copying a 700 MB take that is already on the same machine is pure waste, so
    the file is hardlinked when the filesystem allows and copied otherwise.
    """
    from lib.talking_head_edit.project_uploads import safe_filename

    source_id = project.next_source_id()
    try:
        target = project.sources_dir / f"{source_id}_{safe_filename(source.name)}"
    except Exception as exc:  # noqa: BLE001 — UploadError shape varies
        raise HTTPException(400, str(exc)) from exc

    project.sources_dir.mkdir(parents=True, exist_ok=True)
    try:
        target.hardlink_to(source)
    except (OSError, NotImplementedError):
        import shutil
        shutil.copyfile(source, target)
    try:
        return project.register_source(
            target, source_id=source_id,
            # The ORIGINAL filename, not the stored one: the stored name carries
            # an `s0_` prefix, and take-group detection compares names — with the
            # prefix, "s0_intro-take1" and "s1_intro-take2" reduce to different
            # subjects, and the group ends up named after the prefix.
            label=str(label or "") or source.stem)
    except ProjectError as exc:
        raise _bad(exc) from exc


@router.post("/projects/{project_id}/sources/from-path", response_model=SourceDetailResponse)
def add_source_from_path(project_id: str, payload: AddSourceFromPathRequest) -> dict[str, Any]:
    """Register a file already on disk instead of uploading it."""
    project = _project(project_id)
    source = check_input_path(str(payload.path or ""))
    return register_local_path(project, source, label=str(payload.label or ""))


@router.patch("/projects/{project_id}/sources/{source_id}", response_model=SourceDetailResponse)
def update_source(project_id: str, source_id: str, payload: UpdateSourceRequest) -> dict[str, Any]:
    project = _project(project_id)
    keys = (
        "role", "order", "take_group", "label",
        "tags", "notes", "meta", "bgm_name", "bgm_volume",
    )
    dumped = payload.model_dump(exclude_unset=True)
    try:
        return project.update_source(source_id, **{
            key: dumped[key] for key in keys if key in dumped
        })
    except ProjectError as exc:
        raise _bad(exc) from exc


@router.get("/projects/{project_id}/sources/{source_id}/file")
def source_file(project_id: str, source_id: str) -> FileResponse:
    """Stream the source media so the UI can preview voice / mix with BGM."""
    project = _project(project_id)
    spec = next((s for s in project.load().get("sources", [])
                 if str(s["id"]) == source_id), None)
    if not spec:
        raise HTTPException(404, f"Không có nguồn {source_id}")
    path = project.source_path(spec)
    if not path.exists():
        raise HTTPException(404, "File nguồn đã bị xoá")
    return FileResponse(path)


@router.delete("/projects/{project_id}/sources/{source_id}")
def remove_source(project_id: str, source_id: str,
                  force: bool = Query(False)) -> dict[str, Any]:
    project = _project(project_id)
    try:
        return project.remove_source(source_id, force=force)
    except ProjectError as exc:
        # A source still used by a build is a conflict, not a bad request: the
        # client can retry with ?force=true.
        status = 409 if "đang được dùng" in str(exc) else 400
        raise HTTPException(status, str(exc)) from exc


@router.get("/projects/{project_id}/sources/{source_id}/thumb")
def source_thumb(project_id: str, source_id: str) -> FileResponse:
    project = _project(project_id)
    spec = next((s for s in project.load().get("sources", [])
                 if str(s["id"]) == source_id), None)
    if not spec or not spec.get("thumb"):
        raise HTTPException(404, "Không có thumbnail cho nguồn này")
    path = project.dir / str(spec["thumb"])
    if not path.exists():
        raise HTTPException(404, "Thumbnail đã bị xoá")
    return FileResponse(path)


@router.get("/projects/{project_id}/jobs")
def list_builds(project_id: str) -> list[dict[str, Any]]:
    return _project(project_id).builds()


def _parse_source_ids(payload: dict[str, Any]) -> list[str] | None:
    if "source_ids" not in payload:
        return None
    raw = payload.get("source_ids")
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise HTTPException(400, "source_ids phải là danh sách id video")
    return [str(item).strip() for item in raw if str(item).strip()]


def _enqueue_build(project_id: str, job, stages, should_run: bool) -> dict[str, Any]:
    if not should_run:
        job.update(status="created")
        job.emit("job_created", message="Đã tạo bản dựng — chưa chạy stage (run=false)")
        return {
            "job_id": job.job_id,
            "project_id": project_id,
            "queue_position": None,
            "run": False,
            "stages": stages,
        }

    from server.api_jobs import job_queue

    position = job_queue.submit(QueuedRun(job.job_id, stages=stages))
    return {
        "job_id": job.job_id,
        "project_id": project_id,
        "queue_position": position,
        "run": True,
        "stages": stages,
    }


@router.post("/projects/{project_id}/jobs", response_model=CreateProjectJobResponse)
def create_build(project_id: str, payload: CreateProjectJobRequest | None = None) -> dict[str, Any]:
    """A build inheriting the project's assembly config, keyterms and defaults."""
    if payload is None:
        payload = CreateProjectJobRequest()
    project = _project(project_id)
    data = payload.model_dump(exclude_unset=True)
    try:
        job = project.create_job(
            payload.options or {},
            title=str(payload.title or ""),
            source_ids=_parse_source_ids(data),
            include_broll=getattr(payload, "include_broll", True),
        )
    except ProjectError as exc:
        raise _bad(exc) from exc

    return _enqueue_build(
        project_id, job, payload.stages, payload.run)


@router.post("/projects/{project_id}/jobs/batch", response_model=CreateBatchJobsResponse)
def create_builds_batch(project_id: str, payload: CreateBatchJobsRequest | None = None) -> dict[str, Any]:
    """One independent build per talking-head clip — daily 10-video path."""
    if payload is None:
        payload = CreateBatchJobsRequest(source_ids=[])
    project = _project(project_id)
    data = payload.model_dump(exclude_unset=True)
    source_ids = _parse_source_ids(data)
    if not source_ids:
        raise HTTPException(400, "Thiếu source_ids — mỗi phần tử là một video để dựng riêng")

    stages = payload.stages
    should_run = payload.run
    include_broll = getattr(payload, "include_broll", True)
    options = payload.options or {}
    created: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for source_id in source_ids:
        try:
            job = project.create_job(
                options,
                title=str(payload.title or ""),
                source_ids=[source_id],
                include_broll=include_broll,
            )
        except ProjectError as exc:
            errors.append({"source_id": source_id, "error": str(exc)})
            continue
        created.append(_enqueue_build(project_id, job, stages, should_run))
    if not created:
        raise HTTPException(400, errors[0]["error"] if errors else "Không tạo được bản dựng")
    return {"jobs": created, "failed": errors}


@router.get("/projects/{project_id}/media/{name}")
def project_media(project_id: str, name: str) -> FileResponse:
    """Serve a source file for the UI's preview player."""
    from server.paths_guard import safe_media_name

    project = _project(project_id)
    candidate = project.sources_dir / safe_media_name(name)
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(404, f"Không có file {name}")
    return FileResponse(candidate)


@router.put("/projects/{project_id}/settings")
def update_settings(project_id: str, payload: UpdateProjectSettingsRequest | None = None) -> dict[str, Any]:
    project = _project(project_id)
    if payload is None:
        raise HTTPException(400, "Không có gì để cập nhật")
    dumped = payload.model_dump(exclude_unset=True)
    if not dumped:
        raise HTTPException(400, "Không có gì để cập nhật")
    fields: dict[str, Any] = {}
    if "title" in dumped and str(dumped["title"] or "").strip():
        fields["title"] = str(dumped["title"]).strip()
    if "assembly" in dumped and dumped["assembly"] is not None:
        try:
            assembly_config.validate({**assembly_config.global_defaults(),
                                      **(dumped.get("assembly") or {})})
        except assembly_config.AssemblyConfigError as exc:
            raise HTTPException(400, str(exc)) from exc
        fields["assembly"] = dumped.get("assembly") or {}
    if "keyterms" in dumped and dumped["keyterms"] is not None:
        fields["keyterms"] = [str(t).strip() for t in (dumped.get("keyterms") or [])
                              if str(t).strip()][:100]
    if "defaults" in dumped and dumped["defaults"] is not None:
        fields["defaults"] = dumped.get("defaults") or {}
    if "folder" in dumped and dumped["folder"] is not None:
        fields["folder"] = clean_folder(dumped.get("folder"))
    if not fields:
        raise HTTPException(400, "Không có gì để cập nhật")
    return project.update(**fields)


@router.get("/projects/{project_id}/take-suggestions")
def take_suggestions(project_id: str) -> list[dict[str, Any]]:
    """Which sources look like takes of the same content — suggestion only.

    Applying a wrong grouping makes `select` delete real content, so this endpoint
    reports and the human decides (see `take_detect: "suggest"`).
    """
    from lib.talking_head_edit import sources as sources_mod

    project = _project(project_id)
    specs = [
        sources_mod.SourceSpec(
            id=str(spec["id"]), path=str(project.source_path(spec)),
            duration=float(spec.get("duration") or 0.0),
            role=str(spec.get("role") or "aroll"),
            order=int(spec.get("order") or 0),
            speech=bool(spec.get("speech", True)),
            # The label is the name the user uploaded; the filename on disk
            # carries an `s0_` prefix that would break the grouping.
            label=str(spec.get("label") or ""),
        )
        for spec in project.load().get("sources", [])
    ]
    return sources_mod.suggest_take_groups(specs)
