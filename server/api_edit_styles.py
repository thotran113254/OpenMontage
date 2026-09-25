"""Edit-style packs and BGM library — reusable across every project."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from server.schemas import CreateStyleRequest, EditStyleResponse, UpdateStyleRequest
from lib.talking_head_edit import bgm_library, edit_styles
from lib.talking_head_edit.bgm_library import BgmLibraryError
from lib.talking_head_edit.edit_styles import EditStyleError
from lib.talking_head_edit.job_store import SHARED_PUBLIC
from server.paths_guard import safe_media_name

router = APIRouter()


def _style_bad(exc: EditStyleError) -> HTTPException:
    return HTTPException(400, str(exc))


def _bgm_bad(exc: BgmLibraryError) -> HTTPException:
    return HTTPException(400, str(exc))


@router.get("/edit-styles", response_model=list[EditStyleResponse])
def list_styles() -> list[dict[str, Any]]:
    return edit_styles.load_all()


@router.post("/edit-styles", response_model=EditStyleResponse)
def create_style(payload: CreateStyleRequest) -> dict[str, Any]:
    try:
        return edit_styles.save(
            payload.title,
            payload.options or {},
            source_project_id=str(payload.source_project_id or ""),
        )
    except EditStyleError as exc:
        raise _style_bad(exc) from exc


@router.put("/edit-styles/{style_id}", response_model=EditStyleResponse)
def update_style(style_id: str, payload: UpdateStyleRequest) -> dict[str, Any]:
    try:
        return edit_styles.save(
            payload.title,
            payload.options or {},
            style_id=style_id,
            source_project_id=str(payload.source_project_id or ""),
        )
    except EditStyleError as exc:
        raise _style_bad(exc) from exc

@router.delete("/edit-styles/{style_id}")
def delete_style(style_id: str) -> dict[str, Any]:
    if not edit_styles.delete(style_id):
        raise HTTPException(404, f"Không có kiểu '{style_id}'")
    return {"deleted": style_id}


@router.post("/edit-styles/from-project/{project_id}", response_model=EditStyleResponse)
def style_from_project(project_id: str, payload: CreateStyleRequest | None = None) -> dict[str, Any]:
    """Snapshot another project's defaults into a reusable style."""
    from server.api_projects import store as project_store
    try:
        project = project_store.get(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    if payload is None:
        payload = CreateStyleRequest(title="")
    state = project.load()
    defaults = dict(state.get("defaults") or {})
    if isinstance(payload.options, dict):
        defaults.update(payload.options)
    title = str(payload.title or "").strip() or f"Kiểu từ {state.get('title') or project_id}"
    try:
        return edit_styles.save(title, defaults, source_project_id=project_id)
    except EditStyleError as exc:
        raise _style_bad(exc) from exc

@router.get("/bgm")
def list_bgm() -> dict[str, Any]:
    from lib.talking_head_edit.resources import inventory
    data = inventory()
    return {
        "tracks": bgm_library.list_tracks(),
        "groups": data.get("group_labels") or {},
    }


@router.post("/bgm")
async def upload_bgm(file: UploadFile = File(...)) -> dict[str, Any]:
    raw = await file.read()
    try:
        return bgm_library.save_upload(file.filename or "track.mp3", raw)
    except BgmLibraryError as exc:
        raise _bgm_bad(exc) from exc


@router.delete("/bgm/{name}")
def delete_bgm(name: str) -> dict[str, Any]:
    try:
        ok = bgm_library.delete_track(safe_media_name(name))
    except BgmLibraryError as exc:
        raise _bgm_bad(exc) from exc
    if not ok:
        raise HTTPException(404, f"Không có file {name}")
    return {"deleted": name}


@router.get("/bgm/file/{name}")
def play_bgm(name: str) -> FileResponse:
    safe = safe_media_name(name)
    path = SHARED_PUBLIC / safe
    if not path.is_file():
        raise HTTPException(404, f"Không có file {name}")
    return FileResponse(path)
