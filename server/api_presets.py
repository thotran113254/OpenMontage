"""HTTP API for named look presets — global, reusable across every job.

Unlike `api_prompts.py`, no route here needs a job: a preset is a grade
fragment a user saves once and applies to any video later.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from lib.talking_head_edit import look_presets
from lib.talking_head_edit.look_presets import LookPresetError

router = APIRouter()


def _bad(exc: LookPresetError) -> HTTPException:
    return HTTPException(400, str(exc))


@router.get("/look-presets")
def list_presets() -> list[dict[str, Any]]:
    return look_presets.load_all()


@router.post("/look-presets")
async def save_preset(request: Request) -> dict[str, Any]:
    payload = await request.json()
    try:
        return look_presets.save(payload.get("name"), payload.get("grade"))
    except LookPresetError as exc:
        raise _bad(exc) from exc


@router.delete("/look-presets/{name}")
def delete_preset(name: str) -> dict[str, Any]:
    try:
        deleted = look_presets.delete(name)
    except LookPresetError as exc:
        raise _bad(exc) from exc
    if not deleted:
        raise HTTPException(404, f"Không tìm thấy preset '{name}'")
    return {"deleted": name}
