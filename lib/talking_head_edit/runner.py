"""Stage orchestration: run stages in order, cache what hasn't changed.

Cache keys are declared per stage from the things that stage actually reads.
That is what makes the prompt-iterate loop affordable: editing the prompt
invalidates `direct` onward, while the (slow) transcript and the (expensive)
render of an unchanged timeline are reused.
"""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from lib.r2_storage import hooks as r2_hooks
from lib.talking_head_edit.cache import StageCache, hash_inputs
from lib.talking_head_edit.job_store import STAGES, Job
from lib.talking_head_edit.stages import (
    audit, calibrate, direct, probe, render, resolve, revise, select, transcribe,
    verify,
)

STAGE_RUNNERS: dict[str, Callable[..., dict[str, Any]]] = {
    "probe": probe.run,
    "transcribe": transcribe.run,
    "select": select.run,
    "direct": direct.run,
    "audit": audit.run,
    "calibrate": calibrate.run,
    "resolve": resolve.run,
    "render": render.run,
    "verify": verify.run,
    # Not part of the linear pipeline: run explicitly as ["revise","audit","resolve"]
    # when the user asks for a change in words rather than a re-direct.
    "revise": revise.run,
}


def _file_hash(path: Path) -> str:
    return hash_inputs(path.read_text(encoding="utf-8")) if path.exists() else ""


def cache_signature(stage: str, job: Job, options: dict[str, Any]) -> tuple[str, list[Path]]:
    """(input hash, expected outputs) for one stage."""
    state = job.load()
    version = int(state.get("current_version", 0))
    sha = state.get("input_sha256", "")

    if stage == "transcribe":
        from lib.talking_head_edit.asr import resolve_provider
        from lib.talking_head_edit.stages.transcribe import asr_model_for, keyterms_for

        provider = resolve_provider(options)
        # Hash the LIST of source hashes: adding a fourth take must invalidate
        # the joined spine even though the first three are unchanged (their own
        # per-source transcripts still hit their own cache, so this is cheap).
        shas = [str(s.get("sha256") or "") for s in (state.get("sources") or [])] or [sha]
        return hash_inputs({
            "shas": shas,
            "provider": provider,
            "model": asr_model_for(options, provider),
            "language": options.get("language"),
            # Keyterms change what the engine hears, so they change the spine.
            "keyterms": keyterms_for(options),
        }), [job.spine_path]

    if stage == "select":
        # Which take wins depends on the spine and on the assembly mode, and on
        # nothing else — the director's prompt has no say here.
        return hash_inputs({
            "spine": _file_hash(job.spine_path),
            "assembly": (state.get("assembly_resolved")
                         or (options.get("assembly") or {})),
            "model": options.get("model"),
        }), [job.dir / "selection_v1.json"]

    if stage == "direct":
        from lib.talking_head_edit import prompt_registry
        from lib.talking_head_edit.resources import bgm_table, sfx_table

        return hash_inputs({
            "spine": _file_hash(job.spine_path),
            # The director sees the spine AFTER select filtered it, so a
            # different take winning must re-run direct even on the same spine.
            "selection": _file_hash(job.dir / "selection_v1.json"),
            # Editing a prompt must re-run the director. Without this the edit
            # changes nothing observable, and the natural conclusion is that the
            # prompt does not matter.
            # "card_guidance" is nested INTO "structure" (prompt_structure.py
            # renders it and splices the result in as {{card_rule}}), so its own
            # version/edits do not show up in structure's own bytes — it needs
            # to be named here too or reverting/editing it silently no-ops.
            "prompts": prompt_registry.fingerprint(
                ["structure", "captions", "card_guidance"]),
            # The prompt embeds the sfx/bgm inventory, so a new music file
            # genuinely changes what the director is choosing from.
            "resources": hash_inputs([sfx_table(), bgm_table()]),
            "prompt": options.get("prompt", ""),
            "model": options.get("model"),
            "topic": options.get("topic"),
            "card_plan": options.get("card_plan"),
            "bgm": options.get("bgm"),
            "cold_open": options.get("cold_open"),
            "style_profile": _file_hash(Path(options.get("style_profile", "") or "nonexistent")),
        }), [job.spec_path(max(version, 1))]

    if stage == "audit":
        return hash_inputs({"spec": _file_hash(job.spec_path(version))}), [
            job.dir / f"audit_report_v{version}.json"
        ]

    if stage == "calibrate":
        return hash_inputs({
            "spec": _file_hash(job.spec_path(version)),
            "grade": options.get("calibrate_grade"),
            "audio": options.get("calibrate_audio"),
            "at": options.get("calibrate_at"),
        }), [job.dir / "calibrate_report.json"]

    if stage == "resolve":
        return hash_inputs({
            "spec": _file_hash(job.spec_path(version)),
            "tempo": options.get("tempo"),
            "fps": options.get("fps"),
            "size": [options.get("width"), options.get("height")],
            "cold_open": options.get("cold_open"),
            "bgm": options.get("bgm"),
            "brand_pill": options.get("brand_pill"),
            "frame": [options.get("frame_preset"), options.get("frame_overrides")],
            "grade_overrides": options.get("grade_overrides"),
            "encode": [options.get("intermediate_preset"), options.get("intermediate_crf")],
            "audio_preset": options.get("audio_preset"),
        }), [job.src_path, job.props_path(version)]

    if stage == "render":
        return hash_inputs({
            "props": _file_hash(job.props_path(version)),
            "scale": options.get("render_scale", 1.0),
            "crf": options.get("render_crf"),
        }), [job.final_path]

    # probe, verify and revise never cache: the first two are cheap and must
    # reflect the current file, and a revise instruction is an explicit request
    # to change something even if it was issued before.
    return "", []


def run_stage(job: Job, stage: str, options: dict[str, Any], use_cache: bool = True) -> dict[str, Any]:
    cache = StageCache(job)
    signature, outputs = cache_signature(stage, job, options)

    if use_cache and signature and cache.is_fresh(stage, signature, outputs):
        job.set_stage(stage, status="completed", cached=True, ended_at=time.time())
        job.emit("stage_end", stage, f"{stage}: dùng lại kết quả đã cache", cached=True)
        return {"cached": True}

    job.set_stage(stage, status="running", cached=False, started_at=time.time(), error=None)
    job.emit("stage_start", stage, f"{stage}: bắt đầu")
    started = time.time()
    try:
        result = STAGE_RUNNERS[stage](job, options) or {}
    except Exception as exc:  # noqa: BLE001 — surfaced to the user, not swallowed
        detail = traceback.format_exc(limit=3)
        job.set_stage(stage, status="failed", ended_at=time.time(), error=str(exc))
        job.update(status="failed")
        job.emit("stage_failed", stage, str(exc), detail=detail)
        raise

    elapsed = round(time.time() - started, 2)
    job.set_stage(stage, status="completed", ended_at=time.time(),
                  duration_seconds=elapsed, result=result)
    if signature:
        # recompute: a stage may have produced the very files the key points at
        new_signature, _ = cache_signature(stage, job, options)
        cache.mark(stage, new_signature if stage in ("audit", "render") else signature)
    job.emit("stage_end", stage, f"{stage}: xong sau {elapsed}s", duration_seconds=elapsed)
    return result


def run_job(job: Job, options: dict[str, Any] | None = None, stages: list[str] | None = None,
            use_cache: bool = True) -> dict[str, Any]:
    state = job.load()
    opts = {**state.get("options", {}), **(options or {})}
    if options:
        state["options"] = opts
        job.save(state)

    plan = stages or STAGES
    job.update(status="running")
    job.emit("job_start", message=f"Chạy các stage: {', '.join(plan)}")

    results: dict[str, Any] = {}
    for stage in plan:
        results[stage] = run_stage(job, stage, opts, use_cache=use_cache)

    final_state = job.load()
    warnings = any(
        (final_state.get("stages", {}).get(s, {}).get("result") or {}).get("issues")
        for s in ("verify",)
    )
    job.update(status="completed_with_warnings" if warnings else "completed")
    job.emit("job_end", message="Job hoàn tất")
    r2_hooks.maybe_sync_job(job, "job_end")
    return results


def stages_from(start: str) -> list[str]:
    if start not in STAGES:
        raise ValueError(f"Stage không hợp lệ: {start}. Hợp lệ: {', '.join(STAGES)}")
    return STAGES[STAGES.index(start):]


def load_options_file(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
