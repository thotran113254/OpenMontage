"""ElevenLabs Scribe v2 speech-to-text.

One API call instead of minutes of local inference, which is the point: on this
pipeline the transcribe stage went from "a few minutes" to ~10-30 s, and that is
what makes the prompt-iterate loop usable.

Audio is extracted before upload rather than sending the video. Scribe accepts
video, but a 1080p mp4 is a few hundred megabytes where a mono 64 kbps mp3 of
the same speech is a few megabytes — the bandwidth buys nothing, and the words
come from the audio either way.

The tool returns the raw provider shape (words with `type` and `speaker_id`);
turning that into the pipeline's word spine is `lib/talking_head_edit/asr`'s job,
so other pipelines can use this tool with their own conventions.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path
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

MAX_KEYTERMS = 100
# Scribe's own ceiling is 3 GB / 10 hours; talking-head footage never comes
# close, so the guard here is only to fail loudly rather than upload for
# minutes and then get rejected.
MAX_UPLOAD_BYTES = 3 * 1024 * 1024 * 1024


class ElevenLabsScribe(BaseTool):
    name = "elevenlabs_scribe"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "analysis"
    provider = "elevenlabs"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    # Same audio can come back with slightly different token boundaries between
    # model revisions, so this is not a deterministic tool.
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = ["python:elevenlabs", "env:ELEVENLABS_API_KEY", "binary:ffmpeg"]
    install_instructions = (
        "pip install elevenlabs\n"
        "Set ELEVENLABS_API_KEY in .env (https://elevenlabs.io/app/settings/api-keys)"
    )
    agent_skills = ["speech-to-text"]

    capabilities = [
        "transcribe",
        "word_timestamps",
        "diarization",
        "language_detection",
        "keyterm_prompting",
        "audio_event_detection",
    ]

    best_for = [
        "fast word-level transcription (one API call, ~10-30s)",
        "speaker diarization for interviews and multi-speaker footage",
        "brand names and jargon via keyterm prompting",
    ]

    input_schema = {
        "type": "object",
        "required": ["input_path"],
        "properties": {
            "input_path": {"type": "string", "description": "Audio or video file"},
            "model_id": {"type": "string", "default": "scribe_v2",
                         "enum": ["scribe_v2", "scribe_v2_realtime"]},
            "language": {"type": "string",
                         "description": "ISO 639-1/639-3 hint, or null to auto-detect"},
            "diarize": {"type": "boolean", "default": False},
            "keyterms": {"type": "array", "items": {"type": "string"},
                         "description": f"Up to {MAX_KEYTERMS} terms the model should "
                                        "prefer (brand names, jargon)"},
            "send_video": {"type": "boolean", "default": False,
                           "description": "Upload the file as-is instead of extracting "
                                          "audio first. Costs bandwidth; measured no "
                                          "accuracy gain on talking-head footage."},
        },
    }

    output_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "words": {"type": "array"},
            "language": {"type": "string"},
            "language_probability": {"type": "number"},
            "duration_seconds": {"type": "number"},
            "model_id": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=200, network_required=True,
    )
    # Retrying inside the tool would double the wait before the caller can fall
    # back to a local engine; the ASR layer owns that decision.
    retry_policy = RetryPolicy(max_retries=0)
    resume_support = ResumeSupport.NONE
    idempotency_key_fields = ["input_path", "model_id", "language", "diarize"]
    side_effects = ["uploads audio to ElevenLabs"]
    fallback = "transcriber"
    user_visible_verification = [
        "Spot-check transcript against the spoken audio",
        "Check speaker labels on a multi-speaker section",
    ]

    def get_status(self) -> ToolStatus:
        try:
            import elevenlabs  # noqa: F401
        except ImportError:
            return ToolStatus.UNAVAILABLE
        if not os.environ.get("ELEVENLABS_API_KEY"):
            return ToolStatus.UNAVAILABLE
        return ToolStatus.AVAILABLE

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 25.0

    # ---- audio extraction ------------------------------------------------
    @staticmethod
    def _extract_audio(source: Path, target: Path) -> None:
        """Mono 64 kbps mp3 at 16 kHz — everything Scribe needs, nothing more."""
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
             "-b:a", "64k", str(target), "-loglevel", "error"],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or not target.exists():
            raise RuntimeError(
                f"Không tách được audio khỏi {source.name}: "
                f"{result.stderr.strip()[:300]}"
            )

    @staticmethod
    def _word_dict(word: Any) -> dict[str, Any]:
        """SDK objects and plain dicts both appear here (dicts in tests)."""
        if isinstance(word, dict):
            data = word
        else:
            data = {
                "text": getattr(word, "text", None),
                "start": getattr(word, "start", None),
                "end": getattr(word, "end", None),
                "type": getattr(word, "type", None),
                "speaker_id": getattr(word, "speaker_id", None),
            }
        return {
            "text": data.get("text") or data.get("word") or "",
            "start": data.get("start"),
            "end": data.get("end"),
            "type": data.get("type") or "word",
            "speaker_id": data.get("speaker_id") or data.get("speaker"),
        }

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        input_path = Path(inputs["input_path"])
        if not input_path.exists():
            return ToolResult(success=False, error=f"Không tìm thấy file: {input_path}")

        model_id = inputs.get("model_id") or "scribe_v2"
        language = inputs.get("language") or None
        diarize = bool(inputs.get("diarize", False))
        keyterms = [str(t).strip() for t in (inputs.get("keyterms") or []) if str(t).strip()]
        if len(keyterms) > MAX_KEYTERMS:
            return ToolResult(
                success=False,
                error=f"keyterms tối đa {MAX_KEYTERMS} từ, nhận được {len(keyterms)}",
            )

        try:
            from elevenlabs import ElevenLabs
        except ImportError:
            return ToolResult(
                success=False,
                error="Chưa cài SDK. Chạy: pip install elevenlabs",
            )
        if not os.environ.get("ELEVENLABS_API_KEY"):
            return ToolResult(
                success=False,
                error="Thiếu ELEVENLABS_API_KEY trong môi trường (.env)",
            )

        started = time.time()
        temp_dir: tempfile.TemporaryDirectory[str] | None = None
        try:
            if inputs.get("send_video"):
                upload_path = input_path
            else:
                temp_dir = tempfile.TemporaryDirectory(prefix="scribe_")
                upload_path = Path(temp_dir.name) / f"{input_path.stem}.mp3"
                self._extract_audio(input_path, upload_path)

            if upload_path.stat().st_size > MAX_UPLOAD_BYTES:
                return ToolResult(
                    success=False,
                    error=f"File vượt giới hạn 3 GB của Scribe: {upload_path}",
                )

            call: dict[str, Any] = {"model_id": model_id}
            if language:
                call["language_code"] = language
            if diarize:
                call["diarize"] = True
            if keyterms:
                call["keyterms"] = keyterms

            client = ElevenLabs()
            with open(upload_path, "rb") as handle:
                response = client.speech_to_text.convert(file=handle, **call)
        except Exception as exc:  # noqa: BLE001 — SDK raises many unrelated types
            return ToolResult(success=False, error=f"{type(exc).__name__}: {exc}")
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()

        raw_words = getattr(response, "words", None)
        if raw_words is None and isinstance(response, dict):
            raw_words = response.get("words")
        words = [self._word_dict(w) for w in (raw_words or [])]

        def field(name: str) -> Any:
            if isinstance(response, dict):
                return response.get(name)
            return getattr(response, name, None)

        spoken = [w for w in words if w["type"] == "word" and w.get("end") is not None]
        duration = max((float(w["end"]) for w in spoken), default=0.0)

        return ToolResult(
            success=True,
            data={
                "text": field("text") or "",
                "words": words,
                "language": field("language_code") or language or "",
                "language_probability": field("language_probability"),
                "duration_seconds": round(duration, 3),
                "model_id": model_id,
                "diarized": diarize,
                "keyterms_used": len(keyterms),
                "elapsed_seconds": round(time.time() - started, 2),
            },
        )
