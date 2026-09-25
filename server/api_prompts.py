"""HTTP API for prompt templates and A/B runs.

`preview` is the endpoint that matters: viewing a template with `{{spine}}` in it
tells nobody anything, while seeing the actual 4000 words that will be sent makes
the problem obvious. So every read of a prompt can be rendered against a real
job's spine.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from server.schemas import (
    ABTestRequest, PutOverrideRequest, SetCurrentVersionRequest,
)
from lib.talking_head_edit import prompt_ab, prompt_registry
from lib.talking_head_edit.prompt_registry import PromptError

router = APIRouter()

MAX_BODY_CHARS = 60_000


def _bad(exc: PromptError) -> HTTPException:
    return HTTPException(400, str(exc))


def _job(job_id: str):
    from server.api_jobs import _job as resolve

    return resolve(job_id)


@router.get("/prompts")
def list_prompts() -> list[dict[str, Any]]:
    return prompt_registry.catalog()


@router.get("/prompts/{prompt_id}")
def get_prompt(prompt_id: str, version: str | None = Query(None)) -> dict[str, Any]:
    try:
        resolved = version or prompt_registry.current_version(prompt_id)
        return {
            "id": prompt_id,
            "version": resolved,
            "current": prompt_registry.current_version(prompt_id),
            "body": prompt_registry.load(prompt_id, resolved),
            "placeholders": prompt_registry.placeholders(prompt_id, resolved),
            "versions": prompt_registry.versions(prompt_id),
        }
    except PromptError as exc:
        raise _bad(exc) from exc


@router.get("/prompts/{prompt_id}/preview", response_class=PlainTextResponse)
def preview_prompt(prompt_id: str, job_id: str = Query(...),
                   version: str | None = Query(None)) -> str:
    """The prompt as it would actually be sent, using this job's real spine."""
    job = _job(job_id)
    try:
        return render_for_job(job, prompt_id, version)
    except PromptError as exc:
        raise _bad(exc) from exc


def render_for_job(job, prompt_id: str, version: str | None = None) -> str:
    """Render one prompt against a job's current spine and options.

    Each prompt needs different inputs, so this maps prompt id to the builder that
    knows how to assemble them — rather than asking the client to supply
    variables it has no way to know.
    """
    from lib.talking_head_edit.prompt_captions import build_caption_prompt, chunk_ranges
    from lib.talking_head_edit.prompt_structure import build_structure_prompt
    from lib.talking_head_edit.stages.direct import director_words, load_style_profile

    state = job.load()
    options = state.get("options", {})
    if not job.spine_path.exists():
        raise PromptError("Job chưa có spine — chạy transcribe trước khi xem prompt thật.")
    words, boundaries = director_words(job)

    if prompt_id == "structure":
        return build_structure_prompt(
            words, options, load_style_profile(options.get("style_profile")),
            source_boundaries=boundaries,
            cross_source_cut=bool((state.get("assembly_resolved") or {})
                                  .get("cross_source_cut", False)),
            version=version)

    if prompt_id == "captions":
        chunks = chunk_ranges(words)
        if not chunks:
            raise PromptError("Spine rỗng, không có đoạn caption nào để xem.")
        start, end = chunks[0]
        return build_caption_prompt(words, start, end, version=version)

    if prompt_id == "select_take":
        from lib.talking_head_edit.stages.select import build_select_prompt

        return build_select_prompt(words, boundaries, version=version)

    if prompt_id == "revise":
        from lib.talking_head_edit.stages.revise import build_revise_prompt

        current = int(state.get("current_version", 0))
        spec = (json.loads(job.spec_path(current).read_text(encoding="utf-8"))
                if current and job.spec_path(current).exists() else {"events": []})
        return build_revise_prompt(
            spec, words, options.get("revise_instruction") or "(chưa có yêu cầu)",
            version=version)

    if prompt_id == "cut_verify":
        from lib.talking_head_edit.cut_verifier import build_prompt

        return build_prompt([{"id": 0, "w": [0, 1],
                              "before": " ".join(w["word"] for w in words[:6]),
                              "cut": " ".join(w["word"] for w in words[6:8]),
                              "after": " ".join(w["word"] for w in words[8:14])}],
                            version=version)

    raise PromptError(f"Chưa biết cách render prompt '{prompt_id}' với spine thật")


@router.put("/prompts/{prompt_id}")
def put_override(prompt_id: str, payload: PutOverrideRequest) -> dict[str, Any]:
    body = str(payload.body or "")
    if len(body) > MAX_BODY_CHARS:
        raise HTTPException(400, f"Prompt quá dài (tối đa {MAX_BODY_CHARS} ký tự)")
    try:
        return prompt_registry.save_override(
            prompt_id, body, version=payload.version,
            note=str(payload.note or ""),
            author=str(payload.author or "admin"),
            make_current=bool(payload.make_current))
    except PromptError as exc:
        raise _bad(exc) from exc


@router.delete("/prompts/{prompt_id}/{version}")
def delete_override(prompt_id: str, version: str) -> dict[str, Any]:
    try:
        return prompt_registry.delete_override(prompt_id, version)
    except PromptError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/prompts/{prompt_id}/current")
def set_current(prompt_id: str, payload: SetCurrentVersionRequest) -> dict[str, Any]:
    try:
        return prompt_registry.set_current(prompt_id, str(payload.version or ""))
    except PromptError as exc:
        raise _bad(exc) from exc

@router.get("/prompts/{prompt_id}/diff", response_class=PlainTextResponse)
def diff_prompt(prompt_id: str, version: str = Query(...),
                against: str = Query("v1")) -> str:
    try:
        return prompt_registry.diff(prompt_id, version, against) or "(không khác gì)"
    except PromptError as exc:
        raise _bad(exc) from exc


@router.get("/prompts/{prompt_id}/ab/estimate")
def estimate_ab(prompt_id: str, job_id: str = Query(...),
                version_a: str = Query(...), version_b: str = Query(...)
                ) -> dict[str, Any]:
    """Cost preview. Shown BEFORE the run, because doubling a director call is
    exactly the objection to A/B and the number has to be visible."""
    if prompt_id != "structure":
        raise HTTPException(400, "Hiện chỉ A/B được prompt 'structure'")
    job = _job(job_id)
    try:
        return prompt_ab.preview_cost(job, job.load().get("options", {}),
                                     (version_a, version_b))
    except (PromptError, prompt_ab.AbError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/prompts/{prompt_id}/ab")
def run_ab(prompt_id: str, payload: ABTestRequest) -> dict[str, Any]:
    """Run both versions on the same spine, in the background."""
    if prompt_id != "structure":
        raise HTTPException(400, "Hiện chỉ A/B được prompt 'structure'")
    job = _job(str(payload.job_id or ""))
    version_a = str(payload.version_a or "v1")
    version_b = str(payload.version_b or "")
    if not version_b:
        raise HTTPException(400, "Thiếu version_b")
    import threading

    def work() -> None:
        try:
            prompt_ab.run(job, version_a, version_b,
                         on_log=lambda message: job.emit("log", "prompt_ab", message))
        except Exception as exc:  # noqa: BLE001 — surfaced through the job's log
            job.emit("warning", "prompt_ab", f"A/B thất bại: {exc}")

    threading.Thread(target=work, daemon=True).start()
    job.emit("log", "prompt_ab",
             f"Bắt đầu A/B structure.{version_a} vs structure.{version_b}")
    return {"started": True, "job_id": job.job_id,
            "versions": [version_a, version_b],
            "follow": f"/api/jobs/{job.job_id}/events"}


@router.get("/prompts/{prompt_id}/ab/results")
def ab_results(prompt_id: str, job_id: str = Query(...)) -> list[dict[str, Any]]:
    """Past A/B runs for this job, newest first."""
    job = _job(job_id)
    out: list[dict[str, Any]] = []
    for path in sorted((job.dir / "ab").glob("*/comparison.json"), reverse=True):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out
