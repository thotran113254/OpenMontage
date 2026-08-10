"""Transcript acquisition + degraded-fallback helpers for `SpeechGapDetector`.

Isolates "where does the word-level data come from" (precomputed transcript,
freshly-run `Transcriber`, or a coarse `audio_energy`-only fallback) from the
pure span math in `speech_gap_spans.py` and the BaseTool contract in
`speech_gap_detector.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from tools.base_tool import ToolResult, ToolStatus
from tools.analysis.speech_gap_spans import merge_spans


def resolve_transcript(inputs: dict[str, Any]) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Load an inline/precomputed transcript, or run Transcriber.

    Returns (transcript_or_None, error_message_or_None).
    """
    if inputs.get("transcript"):
        return inputs["transcript"], None

    if inputs.get("transcript_json"):
        path = Path(inputs["transcript_json"])
        if not path.exists():
            return None, f"transcript_json not found: {path}"
        try:
            return json.loads(path.read_text(encoding="utf-8")), None
        except (OSError, json.JSONDecodeError) as e:
            return None, f"Failed to parse transcript_json: {e}"

    input_path = Path(inputs["input_path"])
    if not input_path.exists():
        return None, f"Input file not found: {input_path}"

    from tools.analysis.transcriber import Transcriber

    result = Transcriber().execute({
        "input_path": str(input_path),
        "model_size": inputs.get("model_size", "base"),
        "output_dir": inputs.get("output_dir", str(input_path.parent)),
    })
    if not result.success:
        return None, result.error or "Transcriber failed"
    return result.data, None


def silence_ranges_via_ffmpeg(
    input_path: Path, noise_floor_db: float = -25.0, min_duration: float = 0.3,
) -> Optional[list[tuple[float, float]]]:
    """Measure real silence gaps directly from the waveform via ffmpeg silencedetect.

    Transcript word-gap timing (compute_dead_air_spans) depends on ASR
    accuracy — verified this session that a low-quality transcript smooths
    over genuine short hesitation pauses (0.3-0.6s) that silencedetect still
    catches reliably, since it never touches transcription at all. Returns
    None (not a hard failure) if ffmpeg is missing or produces no output.
    """
    import re
    import subprocess
    import shutil

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    try:
        proc = subprocess.run(
            [
                ffmpeg, "-i", str(input_path),
                "-af", f"silencedetect=noise={noise_floor_db}dB:d={min_duration}",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    starts = [float(m) for m in re.findall(r"silence_start: ([\d.]+)", proc.stderr)]
    ends = [float(m) for m in re.findall(r"silence_end: ([\d.]+)", proc.stderr)]
    return list(zip(starts, ends))


def audio_energy_profile(input_path: Path) -> Optional[list[dict[str, Any]]]:
    """Best-effort audio_energy sampling for dead-air corroboration/fallback."""
    try:
        from tools.analysis.audio_energy import AudioEnergy

        energy_tool = AudioEnergy()
        if energy_tool.get_status() != ToolStatus.AVAILABLE:
            return None
        result = energy_tool.execute({"input_path": str(input_path)})
        if not result.success:
            return None
        return result.data.get("energy_profile")
    except Exception:
        # Corroboration/fallback is best-effort, never a hard failure.
        return None


def write_removal_spans_artifact(
    output_dir: Path, input_path: Path, removal_spans: list[dict[str, Any]]
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / f"{input_path.stem}_removal_spans.json"
    artifact_path.write_text(
        json.dumps({"removal_spans": removal_spans}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return artifact_path


def degraded_audio_only_result(
    input_path: Path,
    output_dir: Path,
    min_dead_air: float,
    pad_seconds: float,
    transcript_error: Optional[str],
    duration_seconds: float,
) -> ToolResult:
    """Fallback when transcription is unavailable/fails.

    Uses audio_energy's 1s-granular loudness profile to find long stretches
    of inactive audio. Coarser and lower-confidence than the transcript
    path, and cannot detect filler words at all (no ASR text available).
    """
    profile = audio_energy_profile(input_path) if input_path.exists() else None
    if profile is None:
        return ToolResult(
            success=False,
            error=(
                f"Transcription unavailable ({transcript_error}) and "
                "audio_energy fallback also unavailable (ffmpeg not found "
                "or analysis failed)."
            ),
        )

    spans: list[dict[str, Any]] = []
    run_start: Optional[int] = None
    for point in profile:
        if not point.get("active"):
            if run_start is None:
                run_start = point["time_seconds"]
        elif run_start is not None:
            _append_coarse_gap(spans, run_start, point["time_seconds"], min_dead_air, pad_seconds)
            run_start = None
    if run_start is not None:
        _append_coarse_gap(spans, run_start, profile[-1]["time_seconds"] + 1, min_dead_air, pad_seconds)

    removal_spans = merge_spans(spans)
    artifact_path = write_removal_spans_artifact(output_dir, input_path, removal_spans)

    return ToolResult(
        success=True,
        data={
            "removal_spans": removal_spans,
            "counts": {"dead_air": len(removal_spans), "filler": 0, "total": len(removal_spans)},
            "source": "degraded_audio_energy_only",
            "degraded_reason": transcript_error,
        },
        artifacts=[str(artifact_path)],
        duration_seconds=round(duration_seconds, 2),
    )


def _append_coarse_gap(
    spans: list[dict[str, Any]], gap_start: float, gap_end: float,
    min_dead_air: float, pad_seconds: float,
) -> None:
    if gap_end - gap_start < min_dead_air:
        return
    start, end = gap_start + pad_seconds, gap_end - pad_seconds
    if end > start:
        spans.append({
            "start_seconds": round(start, 3),
            "end_seconds": round(end, 3),
            "kind": "dead_air",
            "text": None,
            "confidence": 0.3,  # coarse 1s-granular signal, low confidence
        })
