"""Scribe provider — the default.

Two provider-specific shapes are handled here and nowhere else:

* `spacing` tokens are dropped. Leaving them in would shift every word index
  the director sees away from the spoken words, so every caption and every cut
  would land on the wrong syllable. This is the single most damaging thing that
  could go wrong in this file.
* `audio_event` tokens (laughter, applause) move to `audio_events[]`. They are
  editing signal, not clock ticks.

Error classification decides whether the caller may fall back to Whisper, so it
is deliberate rather than best-effort: anything that looks like a bad request
(HTTP 4xx that is not auth/quota) is fatal, because falling back would bury a
bug in our own call under a slow local transcription that happens to work.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from lib.talking_head_edit.asr.base import (
    AsrFatal,
    AsrTransient,
    Spine,
    build_spine,
    extract_audio_events,
    normalise_words,
)

PROVIDER = "elevenlabs_scribe"
DEFAULT_MODEL = "scribe_v2"

# Substrings that mean "retrying elsewhere is reasonable": auth, quota, network,
# server-side failure.
_TRANSIENT_MARKERS = (
    "401", "403", "429", "500", "502", "503", "504",
    "timeout", "timed out", "connection", "temporarily", "rate limit",
    "unauthorized", "quota", "api_key", "elevenlabs_api_key", "chưa cài sdk",
    "thiếu elevenlabs_api_key",
)
# A 4xx that is not in the list above is our mistake, not theirs.
_FATAL_STATUS = re.compile(r"\b4\d\d\b")


def classify(error: str) -> AsrTransient | AsrFatal:
    """Provider error string → which exception the caller should see."""
    lowered = error.lower()
    if any(marker in lowered for marker in _TRANSIENT_MARKERS):
        return AsrTransient(error)
    if _FATAL_STATUS.search(lowered) or "invalid" in lowered or "unsupported" in lowered:
        return AsrFatal(error)
    # Unknown failures are treated as transient: a fallback that produces a
    # video beats a hard stop, and the warning still names the real error.
    return AsrTransient(error)


def transcribe(path: str | Path, options: dict[str, Any] | None = None,
               log_dir: str | Path | None = None) -> Spine:
    opts = options or {}
    model = str(opts.get("asr_model") or DEFAULT_MODEL)
    language = opts.get("language") or None
    keyterms = list(opts.get("keyterms") or [])

    from tools.analysis.elevenlabs_scribe import ElevenLabsScribe

    result = ElevenLabsScribe().execute({
        "input_path": str(path),
        "model_id": model,
        "language": language,
        "diarize": bool(opts.get("diarize")),
        "keyterms": keyterms,
        "send_video": bool(opts.get("asr_send_video")),
    })
    if not result.success:
        raise classify(str(result.error or "Scribe thất bại không rõ lý do"))

    raw = result.data.get("words") or []
    words = normalise_words(raw)
    if not words:
        # An empty result is not a transport problem — the request went through
        # and came back with no speech, which fallback cannot improve on.
        raise AsrFatal(
            f"Scribe không nhận diện được từ nào trong {Path(path).name} — "
            "kiểm tra audio nguồn (quá nhỏ, nhiễu, hoặc sai ngôn ngữ)."
        )

    spine = build_spine(
        words,
        provider=PROVIDER,
        model=model,
        language=result.data.get("language") or language,
        duration_seconds=result.data.get("duration_seconds"),
        audio_events=extract_audio_events(raw),
    )
    if log_dir:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        (log_path / "scribe_text.txt").write_text(
            str(result.data.get("text") or ""), encoding="utf-8")
    return spine
