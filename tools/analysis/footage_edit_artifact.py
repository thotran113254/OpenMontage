"""Assembles the final ``footage_edit_plan`` artifact dict from a raw
Gemini response, plus the optional Phase 03 removal_spans merge.

Split out of footage_edit_analyzer.py to keep both files under the
project's ~200-line convention (CLAUDE.md "Consider Modularization").
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from tools.analysis.footage_edit_disfluency_prompt import MIN_DISFLUENCY_CONFIDENCE_DEFAULT
from tools.analysis.footage_edit_prompt import validate_and_clamp_beats
from tools.analysis.speech_gap_spans import merge_spans

_MMSS_RE = re.compile(r"^(\d+):([0-5]?\d)(?:\.(\d+))?$")


def mmss_to_seconds(value: str) -> float:
    """Convert an "MM:SS" or "MM:SS.ms" timestamp string to seconds.

    Falls back to 0.0 for unparseable input rather than raising — this is
    used to derive convenience fields (start_seconds/end_seconds) and must
    never crash the tool over a cosmetic formatting slip from the model.
    """
    match = _MMSS_RE.match(value.strip()) if isinstance(value, str) else None
    if not match:
        return 0.0
    minutes, seconds, frac = match.groups()
    total = int(minutes) * 60 + int(seconds)
    if frac:
        total += float(f"0.{frac}")
    return float(total)


def disfluency_spans_from_parsed(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert Gemini's raw `disfluency_spans[]` (MM:SS + quoted text) into
    artifact-shaped entries with derived `start_seconds`/`end_seconds`.

    Kept as raw, unfiltered entries for QA visibility (see
    `user_visible_verification` on FootageEditAnalyzer) — confidence
    filtering for the removal_spans[] merge happens separately in
    `disfluency_removal_spans`, so a human reviewer can still see anything
    the model flagged but didn't meet the removal threshold.
    """
    spans = parsed.get("disfluency_spans") or []
    converted: list[dict[str, Any]] = []
    for span in spans:
        converted.append({
            "start_time": span.get("start_time", "0:00"),
            "end_time": span.get("end_time", "0:00"),
            "start_seconds": mmss_to_seconds(span.get("start_time", "0:00")),
            "end_seconds": mmss_to_seconds(span.get("end_time", "0:00")),
            "stumbled_fragment": span.get("stumbled_fragment"),
            "clean_continuation": span.get("clean_continuation"),
            "confidence": span.get("confidence"),
        })
    return converted


def disfluency_removal_spans(
    disfluency_spans: list[dict[str, Any]], min_confidence: float
) -> list[dict[str, Any]]:
    """Filter disfluency_spans by confidence and reshape into the same
    removal_spans[] item shape Phase 03 (speech_gap_detector) already
    produces (start_seconds/end_seconds/kind/text/confidence), so both
    sources compose through the shared `merge_spans()` helper.
    """
    removal: list[dict[str, Any]] = []
    for span in disfluency_spans:
        confidence = span.get("confidence")
        if confidence is None or confidence < min_confidence:
            continue
        if span["end_seconds"] <= span["start_seconds"]:
            continue
        text = " -> ".join(
            t for t in (span.get("stumbled_fragment"), span.get("clean_continuation")) if t
        ) or None
        removal.append({
            "start_seconds": round(span["start_seconds"], 3),
            "end_seconds": round(span["end_seconds"], 3),
            "kind": "disfluency",
            "text": text,
            "confidence": round(float(confidence), 3),
        })
    return removal


def build_artifact(
    *,
    parsed: dict[str, Any],
    input_path: Path,
    playbook_path: Path,
    transitions: list[str],
    sound_effects: list[str],
    model: str,
    thinking_level: str,
    elapsed: float,
    usage: Any,
    repair_attempted: bool,
    removal_spans: list[dict[str, Any]],
    real_duration_seconds: float | None = None,
    min_disfluency_confidence: float = MIN_DISFLUENCY_CONFIDENCE_DEFAULT,
    broll_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the schema-shaped footage_edit_plan dict.

    Converts MM:SS beat timestamps to convenience `start_seconds`/
    `end_seconds` fields (direct mapping onto edit_decisions cuts[].
    in_seconds/out_seconds) and clamps any out-of-inventory transition/sfx
    choice to "none", recording the violation for QA visibility.
    """
    raw_beats = parsed.get("beats") or parsed.get("edit_workflow") or []
    for beat in raw_beats:
        beat["start_seconds"] = mmss_to_seconds(beat.get("start_time", "0:00"))
        beat["end_seconds"] = mmss_to_seconds(beat.get("end_time", "0:00"))
    clean_beats, violations = validate_and_clamp_beats(raw_beats, transitions, sound_effects)

    # Gemini sometimes stops transcribing once the narrative "feels" complete,
    # under-covering trailing content (outro/CTA) even when told the real
    # duration — flag the gap so it's caught in review rather than silently
    # shipping a plan that's missing the tail of the video.
    last_beat_end = clean_beats[-1]["end_seconds"] if clean_beats else 0.0
    coverage_gap = None
    if real_duration_seconds is not None:
        gap = real_duration_seconds - last_beat_end
        if gap > 2.0:
            coverage_gap = round(gap, 1)

    # Disfluency spans come from this same Gemini call (semantic/linguistic
    # judgment on repeated/false-start speech — not detectable by silence or
    # lexicon matching, see speech_gap_detector.py). Kept as their own
    # artifact field (raw, for QA) AND merged into removal_spans[] (filtered
    # to high confidence) so they compose with Phase 03's dead_air/filler/
    # pause_tighten spans through the one shared removal_spans[] mechanism
    # edit-director.md already knows how to consume.
    disfluency_spans = disfluency_spans_from_parsed(parsed)
    merged_removal_spans = merge_spans(
        list(removal_spans) + disfluency_removal_spans(disfluency_spans, min_disfluency_confidence)
    )

    return {
        "version": "1.0",
        "source": {
            "path": str(input_path),
            "duration_seconds": real_duration_seconds
            if real_duration_seconds is not None
            else parsed.get("source_duration_seconds", 0.0),
        },
        "style_playbook": str(playbook_path),
        "transcript": parsed.get("transcript", []),
        "pacing_profile": parsed.get("pacing_profile", {}),
        "beats": clean_beats,
        "inventory_used": {"transitions": transitions, "sound_effects": sound_effects},
        "disfluency_spans": disfluency_spans,
        "removal_spans": merged_removal_spans,
        # Real clips this project actually declared (never fabricated) — see
        # footage_edit_broll_matcher.match_broll_to_beats for how a beat's
        # b_roll.suggested_visual gets matched against these by content,
        # instead of a visual being invented for every needed=true beat.
        "broll_candidates": broll_candidates or [],
        "model": model,
        "_analysis_meta": {
            "thinking_level": thinking_level,
            "duration_seconds": round(elapsed, 2),
            "prompt_token_count": getattr(usage, "prompt_token_count", None) if usage else None,
            "candidates_token_count": getattr(usage, "candidates_token_count", None) if usage else None,
            "thoughts_token_count": getattr(usage, "thoughts_token_count", None) if usage else None,
            "repair_attempted": repair_attempted,
            "inventory_violations": violations,
            "duration_coverage_gap_seconds": coverage_gap,
        },
    }


def load_removal_spans(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    """Merge Phase 03 removal_spans, if provided.

    Phase 03 (SpeechGapDetector, tools/analysis/speech_gap_detector.py) is
    implemented and wired into the hybrid pipeline (pipeline_defs/hybrid.yaml)
    as its own script stage, but this tool does not call it directly — it
    accepts the spans as a param (inline list or a JSON file path) per the
    phase spec's documented hand-off ("accepts its output as an input param
    — see P03"), keeping the two tools decoupled.
    """
    spans = inputs.get("removal_spans")
    if spans:
        return spans
    path = inputs.get("removal_spans_path")
    if not path:
        return []
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data.get("removal_spans", data) if isinstance(data, dict) else data
