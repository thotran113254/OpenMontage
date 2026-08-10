"""Fast look/sound previews over HTTP — the same functions the CLI drives.

Why only three of the previews in `preview.py` are exposed here:

* The <Player> in the UI already runs the real composition and shows any frame
  instantly, so `composition_still` (~25s for one frame) would be strictly worse.
* What the Player CANNOT show is anything ffmpeg bakes into `src.mp4` during
  resolve — the grade and the audio cleanup preset. Changing either currently
  costs a full ~4 minute resolve before you can look at it. `grade` and `audio`
  here run on the RAW source in seconds, which is the whole point.
* `clip` stays because the Player can't show what the final encode does to
  sharpness, and can't show judder.
* `judge_audio` is deliberately NOT exposed: a blind test established the model
  cannot score technical audio quality (see docs/talking-head-autoedit.md), so
  putting it in the UI would dress up a signal already known to be noise.

All three are sync handlers. FastAPI runs sync defs in a threadpool, so even the
minute-long clip render leaves the event loop (and the SSE progress stream) free.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from lib.talking_head_edit.job_store import JobStore, find_job, primary_input_path
from lib.talking_head_edit.preview import (
    PreviewError, preview_audio, preview_clip, preview_grades,
)
from lib.talking_head_edit.resolve_media import probe_duration

router = APIRouter()
store = JobStore()

# A sample long enough to judge room tone and word tails, short enough that five
# presets stay a few seconds total.
AUDIO_SAMPLE_SECONDS = 8.0


def _projects_root() -> Path:
    """Lazy import, same reason as `api_jobs._projects_root`: avoids a circular
    import at module load, and reads per call so a test that swaps the store
    (or the project store's root) is honoured."""
    from server.api_projects import store as project_store

    return project_store.root


def _job(job_id: str):
    """A job by id from either layout — legacy root or inside a project.

    A bare `store.get(job_id)` only ever checked the legacy root, so any build
    made inside a project 404'd here even though `/api/jobs/{id}` could see it
    fine — the preview buttons looked broken while everything else worked.
    """
    try:
        return find_job(job_id, legacy_root=store.root, projects_root=_projects_root())
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


def _source_of(job) -> Path:
    state = job.load()
    try:
        path = primary_input_path(state)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not path.exists():
        raise HTTPException(400, f"Không còn file nguồn: {path}")
    return path


def _current_grade(job) -> dict[str, Any]:
    """The grade the next resolve would actually apply.

    Same formula as stages/resolve.py: the director's spec underneath, a human's
    `grade_overrides` on top. Previewing only the spec would show a picture the
    render never produces once overrides exist.
    """
    state = job.load()
    version = int(state.get("current_version", 0))
    spec_grade: dict[str, Any] = {}
    spec_path = job.spec_path(version)
    if version and spec_path.exists():
        try:
            spec_grade = json.loads(spec_path.read_text(encoding="utf-8")).get("grade") or {}
        except json.JSONDecodeError:
            spec_grade = {}
    overrides = (state.get("options") or {}).get("grade_overrides") or {}
    return {**spec_grade, **overrides}


def _preview_url(job_id: str, rel: str) -> str:
    """Files under the job's `preview/` dir, served flat by name.

    The media route rejects separators in a name, so previews get their own
    route rather than a path that would have to be allowed through it.
    """
    return f"/api/media/{job_id}/preview/{Path(rel).name}"


def _clamp_at(raw: Any, source: Path, tail: float = 0.0) -> float:
    """Keep the sample window inside the footage — ffmpeg just fails past the end."""
    duration = probe_duration(source)
    at = duration / 2 if raw is None else float(raw)
    return round(max(0.0, min(at, max(0.0, duration - tail))), 3)


@router.post("/jobs/{job_id}/preview/grade")
async def preview_grade(job_id: str, request: Request) -> dict[str, Any]:
    """One frame of raw footage per grade variant, with its colour cast measured.

    Body: {at?: seconds|null, grade?: {...partial grade to try...}}
    Always includes `raw` and `hien_tai`; adds `thu_nghiem` when `grade` is given.
    """
    payload = await request.json() if await request.body() else {}
    job = _job(job_id)
    source = _source_of(job)
    base = _current_grade(job)

    variants: dict[str, dict[str, Any]] = {"hien_tai": base}
    trial = payload.get("grade")
    if trial:
        if not isinstance(trial, dict):
            raise HTTPException(400, "`grade` phải là object")
        variants["thu_nghiem"] = {**base, **trial}

    at = _clamp_at(payload.get("at"), source)
    try:
        report = preview_grades(job, variants, at_seconds=at, options=job.load().get("options"))
    except (PreviewError, OSError) as exc:
        raise HTTPException(400, f"Xem thử màu lỗi: {exc}") from exc

    for item in report["variants"]:
        item["url"] = _preview_url(job_id, item["image"])
    report["contact_sheet_url"] = _preview_url(job_id, report["contact_sheet"])
    report["base_grade"] = base
    return report


@router.post("/jobs/{job_id}/preview/audio")
async def preview_audio_samples(job_id: str, request: Request) -> dict[str, Any]:
    """One short sample per cleanup preset — files to listen to, not a verdict.

    Body: {at?: seconds, duration?: seconds, presets?: [name, …]}
    """
    payload = await request.json() if await request.body() else {}
    job = _job(job_id)
    source = _source_of(job)
    duration = float(payload.get("duration") or AUDIO_SAMPLE_SECONDS)
    at = _clamp_at(payload.get("at"), source, tail=duration)
    presets = payload.get("presets") or None
    if presets is not None and not isinstance(presets, list):
        raise HTTPException(400, "`presets` phải là list")

    try:
        report = preview_audio(job, at_seconds=at, duration=duration, presets=presets)
    except (PreviewError, OSError) as exc:
        raise HTTPException(400, f"Xem thử tiếng lỗi: {exc}") from exc

    current = (job.load().get("options") or {}).get("audio_preset")
    for sample in report["samples"]:
        sample["url"] = _preview_url(job_id, sample["rel"])
        sample["is_current"] = sample["preset"] == current
    report["current_preset"] = current
    report["duration_seconds"] = duration
    return report


@router.post("/jobs/{job_id}/preview/clip")
async def preview_clip_render(job_id: str, request: Request) -> dict[str, Any]:
    """A short half-size render through the real renderer — the approval step.

    Body: {start?: seconds, duration?: seconds, scale?: 0.1-1.0}
    Slow (tens of seconds) on purpose: it uses the deliverable's encoder settings,
    which is the part a Player preview cannot tell you anything about.
    """
    payload = await request.json() if await request.body() else {}
    job = _job(job_id)
    try:
        clip = preview_clip(
            job,
            start_seconds=float(payload.get("start") or 0.0),
            duration=float(payload.get("duration") or 5.0),
            scale=float(payload.get("scale") or 0.5),
        )
    except (PreviewError, OSError) as exc:
        raise HTTPException(400, f"Render clip duyệt lỗi: {exc}") from exc

    clip["url"] = _preview_url(job_id, clip["rel"])
    return clip


@router.get("/media/{job_id}/preview/{name}")
def get_preview_file(job_id: str, name: str) -> FileResponse:
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(400, "Tên file không hợp lệ")
    job = _job(job_id)
    path = job.dir / "preview" / name
    if not path.exists() or not path.is_file():
        raise HTTPException(404, f"Không có file xem thử {name}")
    return FileResponse(path)
