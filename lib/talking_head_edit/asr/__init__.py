"""The one entry point for building a word spine.

Callers ask for a spine and get one; which engine produced it is a detail they
should not encode. `options["asr_provider"]` picks the engine, and a transient
failure of the preferred one falls back to the other with a warning loud enough
to be noticed — a silent fallback would turn a missing API key into "why is
transcribe suddenly slow" three weeks later.

Fatal errors (bad parameters, no speech in the file) never fall back: hiding
those behind a second engine hides the bug.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from lib.talking_head_edit.asr import elevenlabs_scribe, whisper_local
from lib.talking_head_edit.asr.base import (
    SPINE_SCHEMA,
    AsrError,
    AsrFatal,
    AsrTransient,
    Spine,
    build_spine,
    extract_audio_events,
    normalise_words,
    speakers_of,
)

DEFAULT_PROVIDER = elevenlabs_scribe.PROVIDER

PROVIDERS: dict[str, Callable[..., Spine]] = {
    elevenlabs_scribe.PROVIDER: elevenlabs_scribe.transcribe,
    whisper_local.PROVIDER: whisper_local.transcribe,
}

# Where each provider falls back to when it fails transiently. Whisper is the
# end of the chain: it needs no network, so if it fails there is nowhere better.
FALLBACK: dict[str, str] = {
    elevenlabs_scribe.PROVIDER: whisper_local.PROVIDER,
}


def resolve_provider(options: dict[str, Any] | None) -> str:
    name = str((options or {}).get("asr_provider") or DEFAULT_PROVIDER)
    if name not in PROVIDERS:
        raise AsrFatal(
            f"ASR provider không hợp lệ: {name}. Hợp lệ: {', '.join(PROVIDERS)}"
        )
    return name


def transcribe(path: str | Path, options: dict[str, Any] | None = None,
               log_dir: str | Path | None = None,
               on_warning: Callable[[str], None] | None = None) -> Spine:
    """Build a spine for one media file.

    `on_warning` receives human-readable Vietnamese notes (the job's `emit`
    hooks in here) — the fallback path always sends one.
    """
    opts = dict(options or {})
    provider = resolve_provider(opts)

    def warn(message: str) -> None:
        if on_warning:
            on_warning(message)

    try:
        spine = PROVIDERS[provider](path, opts, log_dir)
        spine["asr_fallback"] = False
        return spine
    except AsrFatal:
        raise
    except AsrTransient as exc:
        alternative = FALLBACK.get(provider)
        if not alternative:
            raise
        model = opts.get("whisper_model") or "medium"
        warn(f"{provider} lỗi ({str(exc)[:160]}) — đã dùng {alternative} "
             f"'{model}' thay thế. Kiểm tra cấu hình nếu không cố ý.")
        fallback_opts = {**opts, "asr_provider": alternative, "asr_model": model}
        spine = PROVIDERS[alternative](path, fallback_opts, log_dir)
        spine["asr_fallback"] = True
        spine["asr_fallback_from"] = provider
        spine["asr_fallback_reason"] = str(exc)[:300]
        return spine


__all__ = [
    "SPINE_SCHEMA",
    "AsrError",
    "AsrFatal",
    "AsrTransient",
    "DEFAULT_PROVIDER",
    "FALLBACK",
    "PROVIDERS",
    "Spine",
    "build_spine",
    "extract_audio_events",
    "normalise_words",
    "resolve_provider",
    "speakers_of",
    "transcribe",
]
