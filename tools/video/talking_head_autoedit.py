"""Registry-visible entry point for the talking-head auto-edit pipeline.

Intentionally thin. `registry.discover()` imports every module under `tools/`,
so the pipeline itself lives in `lib/talking_head_edit/` and is imported lazily
inside `execute()` — preflight must not pay for ffmpeg/Whisper/HTTP machinery
just to list what exists.
"""

from __future__ import annotations

import os
import shutil
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ResumeSupport,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)


class TalkingHeadAutoEdit(BaseTool):
    name = "talking_head_autoedit"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "video_post"
    provider = "openmontage"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC     # the director stage is a model call
    runtime = ToolRuntime.HYBRID             # local ffmpeg/Remotion + hosted LLM

    dependencies = [
        "binary:ffmpeg", "binary:npx",
        "env:NINE_ROUTER_API_KEY", "env:NINE_ROUTER_BASE_URL",
        "python:faster_whisper",
    ]
    install_instructions = (
        "1. ffmpeg + Node.js đã cài sẵn theo hướng dẫn repo\n"
        "2. cd remotion-composer && npm install\n"
        "3. pip install faster-whisper\n"
        "4. Đặt NINE_ROUTER_BASE_URL + NINE_ROUTER_API_KEY trong .env "
        "(hoặc AUTOEDIT_DIRECTOR_BASE_URL/AUTOEDIT_DIRECTOR_API_KEY)"
    )
    agent_skills = ["remotion-best-practices", "ffmpeg"]

    capabilities = ["talking_head_autoedit", "word_anchored_timeline", "auto_caption_burn"]
    best_for = [
        "footage talking-head một mạch → bản dựng ngắn có caption, card, cold-open",
        "lặp nhanh bằng prompt: sửa yêu cầu rồi chỉ chạy lại từ stage bị ảnh hưởng",
    ]
    not_good_for = [
        "video nhiều người nói / nhiều cảnh (pipeline giả định một người, một take)",
        "footage không có tiếng nói (mọi mốc thời gian neo theo lời nói)",
    ]

    input_schema = {
        "type": "object",
        "required": ["input_path"],
        "properties": {
            "input_path": {"type": "string", "description": "Footage gốc (mp4)"},
            "job_id": {"type": "string", "description": "Chạy tiếp một job đã có"},
            "title": {"type": "string"},
            "prompt": {"type": "string", "description": "Yêu cầu riêng cho video này"},
            "topic": {"type": "string"},
            "card_plan": {"type": "string"},
            "brand_pill": {"type": "string"},
            "model": {"type": "string"},
            "whisper_model": {"type": "string", "default": "medium"},
            "language": {"type": "string", "default": "vi"},
            "tempo": {"type": "number", "default": 1.06},
            "bgm": {"type": "boolean", "default": True},
            "cold_open": {"type": "boolean", "default": True},
            "render_scale": {"type": "number", "default": 1.0},
            "stages": {"type": "array", "items": {"type": "string"}},
            "use_cache": {"type": "boolean", "default": True},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=4, ram_mb=4096, vram_mb=0, disk_mb=4000, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=0)
    resume_support = ResumeSupport.FROM_CHECKPOINT   # --stage-from / job_id
    idempotency_key_fields = ["input_path", "prompt", "model"]
    side_effects = [
        "ghi job workspace vào projects/autoedit-jobs/<job_id>/",
        "gọi LLM director qua gateway (tốn token)",
        "chạy ffmpeg và Remotion (tốn CPU, vài phút)",
    ]
    user_visible_verification = [
        "Đọc audit_report: mọi cut bị từ chối có lý do hợp lý không",
        "Xem verify_report + 8 frame mẫu: overlay có vỡ, chữ có đọc được không",
        "Nghe thử điểm nối cold-open: có nuốt âm đầu câu không",
    ]

    def get_status(self) -> ToolStatus:
        if not shutil.which("ffmpeg"):
            return ToolStatus.UNAVAILABLE
        has_gateway = (
            (os.environ.get("NINE_ROUTER_API_KEY") and os.environ.get("NINE_ROUTER_BASE_URL"))
            or (os.environ.get("AUTOEDIT_DIRECTOR_API_KEY")
                and os.environ.get("AUTOEDIT_DIRECTOR_BASE_URL"))
        )
        return ToolStatus.AVAILABLE if has_gateway else ToolStatus.UNAVAILABLE

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        # Lazy: keeps registry.discover() cheap and independent of this stack.
        from lib.talking_head_edit.job_store import DEFAULT_OPTIONS, JobStore
        from lib.talking_head_edit.runner import run_job

        store = JobStore()
        options = {**DEFAULT_OPTIONS, **{
            key: inputs[key] for key in (
                "prompt", "topic", "card_plan", "brand_pill", "model", "whisper_model",
                "language", "tempo", "bgm", "cold_open", "render_scale",
            ) if key in inputs
        }}

        try:
            if inputs.get("job_id"):
                job = store.get(inputs["job_id"])
            else:
                job = store.create(inputs["input_path"], options, title=inputs.get("title", ""))

            run_job(job, options, stages=inputs.get("stages"),
                    use_cache=inputs.get("use_cache", True))
        except Exception as exc:  # noqa: BLE001 — reported as a tool failure
            return ToolResult(success=False, error=str(exc))

        state = job.load()
        artifacts = [str(job.props_path(state["current_version"]))]
        if job.final_path.exists():
            artifacts.append(str(job.final_path))

        return ToolResult(
            success=True,
            data={
                "job_id": job.job_id,
                "job_dir": str(job.dir),
                "status": state["status"],
                "version": state["current_version"],
                "stages": state["stages"],
            },
            artifacts=artifacts,
            cost_usd=float(state.get("cost_usd", 0.0)),
        )
