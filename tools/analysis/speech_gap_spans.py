"""Span math + lexicon helpers for `SpeechGapDetector`.

Kept separate from `speech_gap_detector.py` so the pure span-computation
logic stays unit-testable in isolation from the BaseTool/execute plumbing,
and to respect this repo's ~200-line-per-file convention.
"""

from __future__ import annotations

from typing import Any, Optional

# Conservative Vietnamese filler lexicon (normalized: lowercased, punctuation
# stripped; diacritics kept since they carry meaning). Deliberately excludes
# words with real grammatical function even when used as hesitation markers
# (e.g. "thi" as a conjunction) to avoid cutting meaningful speech.
DEFAULT_FILLER_LEXICON: list[str] = ["à", "ừm", "ờ", "ừ", "kiểu", "kiểu như"]

# Natural silence kept around a dead-air cut so the trim isn't abrupt.
DEFAULT_PAD_SECONDS = 0.15

# Spans whose gap is smaller than this are merged into one contiguous
# removal (mirrors the "near-contiguous silence -> single block" rule used
# for silencedetect-based trimming: gap < 0.2s => single block).
MERGE_GAP_EPSILON = 0.2


def normalize_word(text: str) -> str:
    """Lowercase + strip whitespace/punctuation for lexicon matching."""
    text = text.strip().lower()
    return text.strip(".,!?;:\"'()[]…-")


def extract_words(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a transcriber-shaped payload into a single word list.

    Prefers the flat top-level `word_timestamps`; falls back to flattening
    `segments[].words[]` for transcripts only carrying the nested shape.
    """
    words = transcript.get("word_timestamps")
    if words:
        return words
    flattened: list[dict[str, Any]] = []
    for seg in transcript.get("segments", []):
        flattened.extend(seg.get("words", []))
    return flattened


def compute_pause_tighten_spans(
    silence_ranges: list[tuple[float, float]],
    min_pause_seconds: float,
    keep_seconds: float = DEFAULT_PAD_SECONDS,
) -> list[dict[str, Any]]:
    """Compress (not fully remove) natural inter-sentence hesitation pauses.

    Built from raw ffmpeg `silencedetect` (start, end) ranges rather than
    Whisper word-gaps: a low-quality transcript's word timings can smooth
    over genuine short pauses entirely (verified on a real sample this
    session), while silencedetect measures the waveform directly and finds
    them reliably regardless of transcription accuracy.

    Keeps `keep_seconds` of natural silence (split evenly at both edges of
    the pause) instead of cutting the whole gap — a hard 0.15s-total pause
    reads as tightened pacing, not robotic jump-cuts.
    """
    spans: list[dict[str, Any]] = []
    half_keep = keep_seconds / 2.0
    for start, end in silence_ranges:
        duration = end - start
        if duration < min_pause_seconds:
            continue
        cut_start = start + half_keep
        cut_end = end - half_keep
        if cut_end > cut_start:
            spans.append(_span(cut_start, cut_end, "pause_tighten", confidence=0.95))
    return spans


def compute_dead_air_spans(
    words: list[dict[str, Any]],
    min_dead_air_seconds: float,
    pad_seconds: float = DEFAULT_PAD_SECONDS,
    total_duration: Optional[float] = None,
) -> list[dict[str, Any]]:
    """Find removable silence: leading, inter-word, and trailing gaps."""
    spans: list[dict[str, Any]] = []
    if not words:
        return spans

    first_start = float(words[0]["start"])
    if first_start >= min_dead_air_seconds:
        end = max(0.0, first_start - pad_seconds)
        if end > 0.0:
            spans.append(_span(0.0, end, "dead_air", confidence=0.9))

    for i in range(len(words) - 1):
        gap_start = float(words[i]["end"])
        gap_end = float(words[i + 1]["start"])
        if gap_end - gap_start >= min_dead_air_seconds:
            start, end = gap_start + pad_seconds, gap_end - pad_seconds
            if end > start:
                spans.append(_span(start, end, "dead_air", confidence=0.9))

    if total_duration is not None:
        last_end = float(words[-1]["end"])
        if total_duration - last_end >= min_dead_air_seconds:
            start = last_end + pad_seconds
            if total_duration > start:
                spans.append(_span(start, total_duration, "dead_air", confidence=0.9))

    return spans


def compute_filler_spans(
    words: list[dict[str, Any]],
    lexicon: list[str],
    min_filler_confidence: float,
) -> list[dict[str, Any]]:
    """Find filler-word spans via normalized lexicon match.

    Checks multi-word entries (e.g. "kiểu như") as a bigram first, so a
    two-token filler isn't split into a false single-word match plus a
    stray leftover token.
    """
    single_word = {w for w in lexicon if " " not in w}
    multi_word = {w for w in lexicon if " " in w}

    spans: list[dict[str, Any]] = []
    i, n = 0, len(words)
    while i < n:
        text_i = normalize_word(words[i].get("word", ""))
        prob_i = float(words[i].get("probability", 1.0))

        if i + 1 < n and multi_word:
            text_j = normalize_word(words[i + 1].get("word", ""))
            prob_j = float(words[i + 1].get("probability", 1.0))
            bigram = f"{text_i} {text_j}".strip()
            if bigram in multi_word and min(prob_i, prob_j) >= min_filler_confidence:
                spans.append(_span(
                    float(words[i]["start"]), float(words[i + 1]["end"]),
                    "filler", text=bigram, confidence=round(min(prob_i, prob_j), 3),
                ))
                i += 2
                continue

        if text_i in single_word and prob_i >= min_filler_confidence:
            spans.append(_span(
                float(words[i]["start"]), float(words[i]["end"]),
                "filler", text=text_i, confidence=round(prob_i, 3),
            ))
        i += 1

    return spans


def corroborate_dead_air(
    spans: list[dict[str, Any]],
    energy_profile: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop dead-air candidates where audio_energy shows active loudness.

    `audio_energy` is 1s-granular so this is a coarse guard only: if any
    second overlapping the candidate is "active", the transcript's own VAD
    likely swallowed real speech there -- don't remove it.
    """
    if not energy_profile:
        return spans
    active_seconds = {p["time_seconds"] for p in energy_profile if p.get("active")}
    kept: list[dict[str, Any]] = []
    for span in spans:
        if span["kind"] != "dead_air":
            kept.append(span)
            continue
        lo, hi = int(span["start_seconds"]), int(span["end_seconds"]) + 1
        if any(sec in active_seconds for sec in range(lo, hi)):
            continue
        kept.append(span)
    return kept


def merge_spans(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort, merge overlapping/near-adjacent spans, dedupe.

    Never emits overlapping removal ranges: a span starting within
    `MERGE_GAP_EPSILON` of the previous span's end is folded into it.
    """
    if not spans:
        return []
    ordered = sorted(spans, key=lambda s: s["start_seconds"])
    merged: list[dict[str, Any]] = [dict(ordered[0])]

    for span in ordered[1:]:
        last = merged[-1]
        if span["start_seconds"] <= last["end_seconds"] + MERGE_GAP_EPSILON:
            last["end_seconds"] = max(last["end_seconds"], span["end_seconds"])
            last["confidence"] = round(min(last["confidence"], span["confidence"]), 3)
            if last["kind"] != span["kind"]:
                last["kind"] = "filler" if "filler" in (last["kind"], span["kind"]) else last["kind"]
            texts = [t for t in (last.get("text"), span.get("text")) if t]
            last["text"] = " ".join(texts) if texts else None
        else:
            merged.append(dict(span))

    for span in merged:
        span["start_seconds"] = round(span["start_seconds"], 3)
        span["end_seconds"] = round(span["end_seconds"], 3)
    return merged


def _span(
    start: float, end: float, kind: str,
    text: Optional[str] = None, confidence: float = 1.0,
) -> dict[str, Any]:
    return {
        "start_seconds": round(start, 3),
        "end_seconds": round(end, 3),
        "kind": kind,
        "text": text,
        "confidence": round(confidence, 3),
    }
