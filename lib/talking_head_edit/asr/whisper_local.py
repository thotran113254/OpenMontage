"""Local Whisper provider — the offline fallback.

Wraps the existing `tools.analysis.transcriber` without modifying it, so the
tool keeps serving every other pipeline unchanged. Slow (minutes on `medium`)
but needs no API key and never sends audio off the machine, which is exactly
what makes it the right fallback.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.talking_head_edit.asr.base import (
    AsrFatal,
    AsrTransient,
    Spine,
    build_spine,
    normalise_words,
)

PROVIDER = "whisper_local"


def transcribe(path: str | Path, options: dict[str, Any] | None = None,
               log_dir: str | Path | None = None) -> Spine:
    opts = options or {}
    model_size = str(opts.get("asr_model") or opts.get("whisper_model") or "medium")
    language = opts.get("language") or None

    from tools.analysis.transcriber import Transcriber

    payload: dict[str, Any] = {
        "input_path": str(path),
        "model_size": model_size,
        "language": language,
    }
    if log_dir:
        payload["output_dir"] = str(log_dir)
    # Whisper diarization needs whisperx; the tool degrades on its own when it
    # is missing, so asking for it costs nothing when it is not installed.
    if opts.get("diarize"):
        payload["diarize"] = True

    result = Transcriber().execute(payload)
    if not result.success:
        error = str(result.error or "")
        # A missing local engine is not something another provider can fix, but
        # it is also not a bug in the call — say which it is.
        if "not installed" in error or "pip install" in error:
            raise AsrFatal(f"Whisper local không dùng được: {error}")
        raise AsrTransient(f"Whisper local thất bại: {error}")

    words = normalise_words(result.data.get("word_timestamps") or [])
    return build_spine(
        words,
        provider=PROVIDER,
        model=model_size,
        language=result.data.get("language"),
        duration_seconds=result.data.get("duration_seconds"),
        audio_events=[],
    )
