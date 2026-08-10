"""Filler-word / dead-air detection tool for raw talking-head footage.

Produces `removal_spans[]` marking Vietnamese filler words ("à", "ừm", "ờ",
"kiểu", ...) and dead-air pauses so a later edit stage can tighten a single
continuous take. Reuses `Transcriber`'s word-level timestamps as the primary
signal (dead-air = inter-word gap >= threshold; filler = word text in a
curated lexicon) instead of re-implementing VAD/ASR. `AudioEnergy` is used
only as an optional corroboration signal (1s-granular, too coarse to be the
cut-point source itself). See `speech_gap_spans.py` for span math and
`speech_gap_sources.py` for transcript acquisition + degraded fallback.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ResumeSupport,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)
from tools.analysis.speech_gap_spans import (
    DEFAULT_FILLER_LEXICON,
    DEFAULT_PAD_SECONDS,
    compute_dead_air_spans,
    compute_filler_spans,
    compute_pause_tighten_spans,
    corroborate_dead_air,
    extract_words,
    merge_spans,
)
from tools.analysis.speech_gap_sources import (
    audio_energy_profile,
    degraded_audio_only_result,
    resolve_transcript,
    silence_ranges_via_ffmpeg,
    write_removal_spans_artifact,
)


class SpeechGapDetector(BaseTool):
    name = "speech_gap_detector"
    version = "0.1.0"
    tier = ToolTier.ANALYZE
    capability = "analysis"
    provider = "openmontage"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    # No hard dependency here: transcription is delegated to `Transcriber`
    # (which declares its own faster-whisper dependency) and is entirely
    # skippable when a precomputed transcript_json/transcript is supplied.
    dependencies: list[str] = []
    install_instructions = (
        "For automatic transcription install faster-whisper: "
        "pip install faster-whisper. Or pass transcript_json / transcript "
        "to skip transcription entirely."
    )
    agent_skills = ["ffmpeg"]

    capabilities = ["dead_air_detection", "filler_word_detection", "removal_spans"]
    best_for = ["cutting dead-air pauses and filler words from a single continuous take"]
    not_good_for = [
        "frame-accurate music-beat detection (use audio_energy)",
        "semantic filler detection beyond the curated lexicon",
    ]

    input_schema = {
        "type": "object",
        "required": ["input_path"],
        "properties": {
            "input_path": {"type": "string", "description": "Path to audio or video file"},
            "transcript_json": {
                "type": "string",
                "description": "Path to a precomputed transcriber-shaped JSON file "
                "(avoids re-transcribing when a prior stage already ran Transcriber)",
            },
            "transcript": {
                "type": "object",
                "description": "Precomputed transcriber-shaped payload passed inline "
                "(alternative to transcript_json)",
            },
            "filler_lexicon": {
                "type": "array", "items": {"type": "string"}, "default": DEFAULT_FILLER_LEXICON,
            },
            "min_dead_air_seconds": {"type": "number", "default": 0.6},
            "min_filler_confidence": {"type": "number", "default": 0.5},
            "pad_seconds": {"type": "number", "default": DEFAULT_PAD_SECONDS},
            "corroborate_with_audio_energy": {"type": "boolean", "default": True},
            "tighten_pauses": {
                "type": "boolean", "default": True,
                "description": "Compress (not remove) short inter-sentence hesitation "
                "pauses via direct waveform silence detection, independent of transcript "
                "quality. Separate from min_dead_air_seconds (which fully removes long gaps).",
            },
            "min_pause_seconds": {
                "type": "number", "default": 0.4,
                "description": "Minimum silence duration to tighten (shorter than "
                "min_dead_air_seconds by design — catches brief hesitation, not just long dead air).",
            },
            "pause_keep_seconds": {
                "type": "number", "default": DEFAULT_PAD_SECONDS,
                "description": "Total natural silence kept per tightened pause (split at both edges).",
            },
            "model_size": {
                "type": "string",
                "enum": ["tiny", "base", "small", "medium", "large-v2", "large-v3"],
                "default": "base",
                "description": "Passed through to Transcriber when no transcript is provided",
            },
            "output_dir": {"type": "string", "description": "Directory for removal_spans.json"},
        },
    }

    output_schema = {
        "type": "object",
        "properties": {
            "removal_spans": {"type": "array"},
            "counts": {"type": "object"},
            "source": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=10)
    retry_policy = RetryPolicy(max_retries=0, retryable_errors=[])
    resume_support = ResumeSupport.FROM_START
    idempotency_key_fields = [
        "input_path", "transcript_json", "min_dead_air_seconds", "min_filler_confidence",
        "tighten_pauses", "min_pause_seconds", "pause_keep_seconds",
    ]
    side_effects = ["writes removal_spans.json to output_dir"]
    fallback_tools = ["audio_energy"]
    user_visible_verification = [
        "Spot-check flagged filler/dead-air spans against the source audio",
        "Confirm no meaningful words were cut (e.g. real use of 'thì')",
    ]

    def get_status(self) -> ToolStatus:
        # Always available: worst case degrades to audio_energy-only
        # dead-air detection (see execute()) when faster-whisper is missing.
        return ToolStatus.AVAILABLE

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        input_path = Path(inputs["input_path"])
        lexicon = inputs.get("filler_lexicon") or DEFAULT_FILLER_LEXICON
        min_dead_air = float(inputs.get("min_dead_air_seconds", 0.6))
        min_filler_conf = float(inputs.get("min_filler_confidence", 0.5))
        pad_seconds = float(inputs.get("pad_seconds", DEFAULT_PAD_SECONDS))
        corroborate = inputs.get("corroborate_with_audio_energy", True)
        tighten_pauses = inputs.get("tighten_pauses", True)
        min_pause_seconds = float(inputs.get("min_pause_seconds", 0.4))
        pause_keep_seconds = float(inputs.get("pause_keep_seconds", DEFAULT_PAD_SECONDS))
        output_dir = Path(inputs.get("output_dir", input_path.parent))

        start = time.time()
        transcript, transcript_error = resolve_transcript(inputs)

        if transcript is None:
            return degraded_audio_only_result(
                input_path, output_dir, min_dead_air, pad_seconds,
                transcript_error, time.time() - start,
            )

        words = extract_words(transcript)
        total_duration = transcript.get("duration_seconds")

        dead_air_spans = compute_dead_air_spans(words, min_dead_air, pad_seconds, total_duration)
        filler_spans = compute_filler_spans(words, lexicon, min_filler_conf)

        if corroborate and dead_air_spans and input_path.exists():
            profile = audio_energy_profile(input_path)
            if profile is not None:
                dead_air_spans = corroborate_dead_air(dead_air_spans, profile)

        pause_spans: list[dict[str, Any]] = []
        if tighten_pauses and input_path.exists():
            silence_ranges = silence_ranges_via_ffmpeg(input_path, min_duration=min_pause_seconds)
            if silence_ranges:
                pause_spans = compute_pause_tighten_spans(
                    silence_ranges, min_pause_seconds, pause_keep_seconds
                )

        removal_spans = merge_spans(dead_air_spans + filler_spans + pause_spans)
        artifact_path = write_removal_spans_artifact(output_dir, input_path, removal_spans)

        return ToolResult(
            success=True,
            data={
                "removal_spans": removal_spans,
                "counts": {
                    "dead_air": sum(1 for s in removal_spans if s["kind"] == "dead_air"),
                    "filler": sum(1 for s in removal_spans if s["kind"] == "filler"),
                    "pause_tighten": sum(1 for s in removal_spans if s["kind"] == "pause_tighten"),
                    "total": len(removal_spans),
                },
                "source": "transcript",
                "language": transcript.get("language"),
                "duration_seconds": total_duration,
            },
            artifacts=[str(artifact_path)],
            duration_seconds=round(time.time() - start, 2),
        )
