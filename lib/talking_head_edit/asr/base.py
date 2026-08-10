"""Shared spine shape and word normalisation for every ASR provider.

The word spine is the ONE clock of this pipeline, so its shape must not depend
on which engine produced it. Everything a provider returns passes through
`normalise_words` here, which enforces three invariants downstream code relies
on without checking:

* only real spoken words are in the list — a provider that also reports
  whitespace or non-speech sounds would shift every index the director sees,
  and every caption with it
* `end > start` always, because a zero-length word makes the caption pill blink
* no per-word confidence field, because only one of the two engines has one and
  a field that exists half the time invites code that silently assumes it

Errors are split by whether retrying with another engine is the right answer:
a network blip or a rate limit is `AsrTransient` (fall back), a bad request is
`AsrFatal` (a bug in our call — surface it instead of hiding it behind Whisper).
"""

from __future__ import annotations

from typing import Any, TypedDict

SPINE_SCHEMA = 2

# Providers vary on whether they classify each token. When they do, only this
# type belongs in the spine.
WORD_TYPE = "word"
AUDIO_EVENT_TYPE = "audio_event"

MIN_WORD_SECONDS = 0.05


class AsrError(RuntimeError):
    """Base class — never raised directly."""


class AsrTransient(AsrError):
    """Temporary: network, auth, quota, 5xx. Falling back to another engine is
    the right response."""


class AsrFatal(AsrError):
    """Permanent: bad parameters, unsupported input, empty audio. Falling back
    would hide the bug, so it must propagate."""


class SpineWord(TypedDict, total=False):
    word: str
    start: float
    end: float
    speaker: str
    src: str          # source id — filled in by spine_build when multi-source


class AudioEvent(TypedDict, total=False):
    kind: str
    start: float
    end: float
    src: str


class Spine(TypedDict, total=False):
    schema: int
    provider: str
    model: str
    language: str
    duration_seconds: float
    word_timestamps: list[SpineWord]
    audio_events: list[AudioEvent]
    speakers: list[str]


def normalise_words(words: list[dict[str, Any]]) -> list[SpineWord]:
    """Provider word list → spine words.

    Accepts both key conventions in use: `word` (Whisper) and `text` (Scribe).
    Entries whose `type` says they are not speech are dropped — when `type` is
    absent the entry is assumed to be a word, which is what Whisper gives us.
    """
    out: list[SpineWord] = []
    for raw in words:
        kind = raw.get("type")
        if kind is not None and kind != WORD_TYPE:
            continue
        text = str(raw.get("word") or raw.get("text") or "").strip()
        if not text:
            continue
        start = float(raw.get("start") or 0.0)
        end = float(raw.get("end") if raw.get("end") is not None else start)
        if end <= start:
            end = start + MIN_WORD_SECONDS
        entry: SpineWord = {"word": text, "start": round(start, 3), "end": round(end, 3)}
        speaker = raw.get("speaker") or raw.get("speaker_id")
        if speaker:
            entry["speaker"] = str(speaker)
        out.append(entry)
    return out


def extract_audio_events(words: list[dict[str, Any]]) -> list[AudioEvent]:
    """Non-speech sounds a provider flagged (laughter, applause, music).

    Editing signal, not timeline: these never enter the word spine, because a
    laugh is not a word the director can anchor a caption to.
    """
    events: list[AudioEvent] = []
    for raw in words:
        if raw.get("type") != AUDIO_EVENT_TYPE:
            continue
        label = str(raw.get("word") or raw.get("text") or "").strip().strip("()").lower()
        start = float(raw.get("start") or 0.0)
        end = float(raw.get("end") if raw.get("end") is not None else start)
        events.append({"kind": label or "unknown",
                       "start": round(start, 3),
                       "end": round(max(end, start), 3)})
    return events


def speakers_of(words: list[SpineWord]) -> list[str]:
    """Unique speaker labels in first-heard order — stable output beats sorted."""
    seen: list[str] = []
    for word in words:
        speaker = word.get("speaker")
        if speaker and speaker not in seen:
            seen.append(speaker)
    return seen


def build_spine(words: list[SpineWord], *, provider: str, model: str,
                language: str | None, duration_seconds: float | None,
                audio_events: list[AudioEvent] | None = None) -> Spine:
    return {
        "schema": SPINE_SCHEMA,
        "provider": provider,
        "model": model,
        "language": language or "",
        "duration_seconds": round(float(duration_seconds or 0.0), 3),
        "word_timestamps": words,
        "audio_events": audio_events or [],
        "speakers": speakers_of(words),
    }
