"""Stage 2 — build the word spine (the ONE clock).

Everything downstream anchors to word indices from this file. Each source is
cached by its own content hash AND the engine that produced it, so adding one
take to a project transcribes only the new file, and switching engines does not
silently reuse the other one's word boundaries.

Sources are transcribed concurrently. That is worth it because Scribe is an
API call — five sources take about as long as one — while the local Whisper
fallback is CPU-bound and gains little, so the pool is small.

The engine itself lives behind `lib.talking_head_edit.asr`: Scribe by default
(one API call, ~10-30 s), local Whisper as the offline fallback.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from lib.talking_head_edit import sources as sources_mod
from lib.talking_head_edit import spine_build
from lib.talking_head_edit.asr import AsrError, resolve_provider
from lib.talking_head_edit.asr import transcribe as asr_transcribe
from lib.talking_head_edit.cache import (
    file_sha256,
    find_cached_transcript,
    transcript_cache_path,
)
from lib.talking_head_edit.job_store import job_input_paths

MIN_WORDS = 5
MAX_KEYTERMS = 100
# Scribe is I/O so this could be higher, but the same pool serves the Whisper
# fallback, and four concurrent local transcriptions would thrash the machine.
MAX_PARALLEL_SOURCES = 4


class TranscribeError(RuntimeError):
    pass


def asr_model_for(options: dict[str, Any], provider: str) -> str:
    """The model name for the *requested* provider.

    Whisper and Scribe do not share a model namespace, so one option key for
    both would mean an old job's `medium` reaching Scribe as a model id. The
    Whisper-side name stays in `whisper_model` for exactly that reason.
    """
    if provider == "whisper_local":
        return str(options.get("whisper_model") or "medium")
    return str(options.get("asr_model") or "scribe_v2")


def keyterms_for(options: dict[str, Any]) -> list[str]:
    """Terms worth hinting to the ASR engine, capped at the provider limit.

    Explicit `keyterms` win. Otherwise the topic and card plan are mined, since
    those are where a brand name or piece of jargon actually shows up and they
    are already filled in on every job — Vietnamese ASR mishears exactly those
    ("chatbot", "CRM", "Zalo OA") and nothing else in the sentence.
    """
    explicit = [str(t).strip() for t in (options.get("keyterms") or []) if str(t).strip()]
    if explicit:
        return explicit[:MAX_KEYTERMS]

    text = " ".join(str(options.get(key) or "") for key in ("topic", "card_plan"))
    terms: list[str] = []
    for token in re.findall(r"[0-9A-Za-zÀ-ỹ][0-9A-Za-zÀ-ỹ.\-]*", text):
        # An all-lowercase common word is not worth a slot in a 100-term budget;
        # keep what looks like a name, a brand, or an acronym. Two-letter
        # all-caps tokens stay ("OA", "AI") — short acronyms are precisely what
        # gets misheard, so a blanket length floor would drop the best hints.
        if token.islower():
            continue
        if len(token) < 3 and not token.isupper():
            continue
        if token not in terms:
            terms.append(token)
    return terms[:MAX_KEYTERMS]


def transcribe_source(job, spec: dict[str, Any], options: dict[str, Any],
                      keyterms: list[str]) -> tuple[dict[str, Any], bool]:
    """(spine, was_cached) for one source. Caches per source content hash."""
    path = Path(spec["path"])
    provider = resolve_provider(options)
    model = asr_model_for(options, provider)
    language = options.get("language") or None
    sha = str(spec.get("sha256") or "") or file_sha256(path)

    cached = find_cached_transcript(sha, provider, model, language or "auto")
    if cached:
        return json.loads(cached.read_text(encoding="utf-8")), True

    try:
        spine = asr_transcribe(
            path,
            {**options, "asr_provider": provider, "asr_model": model,
             "keyterms": keyterms},
            log_dir=job.dir / "logs" / str(spec["id"]),
            on_warning=lambda message: job.emit(
                "warning", "transcribe", f"{path.name}: {message}"),
        )
    except AsrError as exc:
        raise TranscribeError(f"Transcribe {path.name} thất bại: {exc}") from exc

    # Cache under the engine that actually ran: after a fallback that is Whisper,
    # not the Scribe slot the request asked for.
    used_provider = str(spine.get("provider") or provider)
    used_model = str(spine.get("model") or model)
    transcript_cache_path(sha, used_model, language or "auto",
                          used_provider).write_text(
        json.dumps(spine, indent=2, ensure_ascii=False), encoding="utf-8")
    return spine, False


def _sources_of(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Source specs from probe, or a single implied source for a legacy job.

    A job whose probe predates multi-source has no `sources`; synthesising one
    here is what lets it be resumed at `transcribe` without re-probing.
    """
    stored = state.get("sources")
    if stored:
        return stored
    paths = job_input_paths(state)
    probe = state.get("probe") or {}
    return [{"id": "s0", "path": str(paths[0]), "role": "aroll", "order": 0,
             "take_group": "main", "speech": True,
             "sha256": state.get("input_sha256") or "",
             "duration": probe.get("duration_seconds") or 0.0,
             "label": paths[0].stem if paths else "s0"}]


def run(job, options: dict[str, Any]) -> dict[str, Any]:
    state = job.load()
    specs = _sources_of(state)
    speaking = sources_mod.aroll(specs)
    if not speaking:
        raise TranscribeError("Không nguồn nào có lời nói để transcribe.")

    keyterms = keyterms_for(options)
    provider = resolve_provider(options)
    job.emit("log", "transcribe",
             f"Chạy ASR '{provider}' cho {len(speaking)} nguồn"
             + (f", {len(keyterms)} từ khoá gợi ý" if keyterms else ""))

    workers = min(MAX_PARALLEL_SOURCES, max(1, len(speaking)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(
            lambda spec: transcribe_source(job, spec, options, keyterms), speaking))

    per_source: dict[str, dict[str, Any]] = {}
    cached_count = 0
    for spec, (spine, was_cached) in zip(speaking, results):
        per_source[str(spec["id"])] = spine
        cached_count += int(was_cached)
        count = len(spine.get("word_timestamps") or [])
        job.emit("log", "transcribe",
                 f"{Path(spec['path']).name}: {count} từ"
                 + (" (cache)" if was_cached else
                    f" ({spine.get('provider')}/{spine.get('model')})"))

    spine = spine_build.build(per_source, specs)
    words = spine["word_timestamps"]
    if len(words) < MIN_WORDS:
        raise TranscribeError(
            f"Chỉ nhận diện được {len(words)} từ trên toàn bộ nguồn — kiểm tra "
            "lại audio (quá nhỏ, nhiễu, hoặc sai ngôn ngữ)."
        )

    job.spine_path.write_text(
        json.dumps(spine, indent=2, ensure_ascii=False), encoding="utf-8")
    if spine.get("asr_fallback"):
        job.update(asr_fallback=True)

    audio_events = len(spine.get("audio_events") or [])
    speakers = spine.get("speakers") or []
    job.emit("log", "transcribe",
             f"Tổng {len(words)} từ trên {len(speaking)} nguồn, "
             f"ngôn ngữ={spine.get('language')}, {spine.get('provider')}"
             + (f", {len(speakers)} người nói" if len(speakers) > 1 else "")
             + (f", {audio_events} tiếng phi lời nói" if audio_events else ""))
    return {"words": len(words), "cached": cached_count == len(speaking),
            "cached_sources": cached_count, "sources": len(speaking),
            "provider": spine.get("provider"), "speakers": len(speakers),
            "audio_events": audio_events, "overlay_pool": len(spine["overlay_pool"]),
            "fallback": bool(spine.get("asr_fallback"))}
