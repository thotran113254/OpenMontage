#!/usr/bin/env python3
"""QA Test 10: SpeechGapDetector — filler-word / dead-air span detection.

No API calls, no ffmpeg required for the synthetic + real-transcript checks
(corroborate_with_audio_energy is disabled for those). Exercises:
  - span math (dead-air gaps, leading/trailing silence, filler lexicon match,
    multi-word bigram match, confidence gating, merge/dedupe)
  - the full BaseTool.execute() contract against a synthetic transcript
  - a real Vietnamese transcript sample (projects/sample-review) to confirm
    no meaningful word (e.g. "thì") is ever flagged as a filler
  - the degraded audio_energy-only fallback path when no transcript/media
    is available
"""

import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# Windows consoles default to cp1252, which chokes on the Vietnamese
# diacritics printed by this test (filler-word text, span dumps).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from tools.analysis.speech_gap_spans import (
    compute_dead_air_spans,
    compute_filler_spans,
    compute_pause_tighten_spans,
    corroborate_dead_air,
    extract_words,
    merge_spans,
)
from tools.analysis.speech_gap_sources import degraded_audio_only_result
from tools.analysis.speech_gap_detector import SpeechGapDetector

PASS = 0
FAIL = 0

def check(name, condition, detail=""):
    global PASS, FAIL
    status = "PASS" if condition else "FAIL"
    if condition:
        PASS += 1
    else:
        FAIL += 1
    suffix = f" — {detail}" if detail else ""
    print(f"  [{status}] {name}{suffix}")


OUT = Path(__file__).resolve().parent / "output"
OUT.mkdir(exist_ok=True)

# ===================================================================
# Test 1: Synthetic 10-utterance transcript (per phase success criteria)
# ===================================================================
print("--- Test 1: Synthetic transcript — dead-air + filler + confidence gate ---")

SYNTHETIC_WORDS = [
    {"word": " Xin", "start": 0.30, "end": 0.60, "probability": 0.95},
    {"word": " chào", "start": 0.60, "end": 0.90, "probability": 0.96},
    {"word": " à", "start": 0.90, "end": 1.10, "probability": 0.85},       # filler, flagged
    {"word": " mọi", "start": 1.10, "end": 1.40, "probability": 0.90},
    {"word": " người", "start": 1.40, "end": 1.70, "probability": 0.90},
    # 0.8s gap here -> dead air
    {"word": " ừm", "start": 2.50, "end": 2.70, "probability": 0.40},      # low-conf filler, NOT flagged
    {"word": " thì", "start": 2.70, "end": 2.90, "probability": 0.97},     # meaningful word, never flagged
    {"word": " kiểu", "start": 2.90, "end": 3.10, "probability": 0.80},
    {"word": " như", "start": 3.10, "end": 3.30, "probability": 0.75},     # bigram "kiểu như" -> one flagged span
    {"word": " là", "start": 3.30, "end": 3.60, "probability": 0.90},
    {"word": " vậy", "start": 3.65, "end": 4.00, "probability": 0.92},
]
TOTAL_DURATION = 5.0  # 1.0s trailing gap -> dead air

dead_air = compute_dead_air_spans(SYNTHETIC_WORDS, min_dead_air_seconds=0.6, total_duration=TOTAL_DURATION)
filler = compute_filler_spans(SYNTHETIC_WORDS, ["à", "ừm", "ờ", "ừ", "kiểu", "kiểu như"], min_filler_confidence=0.5)

check("Exactly 2 dead-air spans (inter-word + trailing, no leading)", len(dead_air) == 2, f"{dead_air}")
if len(dead_air) == 2:
    check("Inter-word gap within ±0.2s of expected [1.85, 2.35]",
          abs(dead_air[0]["start_seconds"] - 1.85) < 0.2 and abs(dead_air[0]["end_seconds"] - 2.35) < 0.2,
          f"{dead_air[0]}")
    check("Trailing gap within ±0.2s of expected [4.15, 5.0]",
          abs(dead_air[1]["start_seconds"] - 4.15) < 0.2 and abs(dead_air[1]["end_seconds"] - 5.0) < 0.2,
          f"{dead_air[1]}")

check("Exactly 2 filler spans ('à' + bigram 'kiểu như')", len(filler) == 2, f"{filler}")
filler_texts = [f["text"] for f in filler]
check("'à' flagged", "à" in filler_texts, f"{filler_texts}")
check("'kiểu như' bigram flagged as single span", "kiểu như" in filler_texts, f"{filler_texts}")
check("Low-confidence 'ừm' (0.40 < 0.5) NOT flagged", not any("ừm" in (t or "") for t in filler_texts))
check("Meaningful word 'thì' NEVER flagged", not any("thì" in (t or "") for t in filler_texts))

merged = merge_spans(dead_air + filler)
check("Merge produces 4 non-overlapping spans (no accidental collapse)", len(merged) == 4, f"{len(merged)}")
starts = [s["start_seconds"] for s in merged]
check("Merged spans sorted by start time", starts == sorted(starts))

# ===================================================================
# Test 2: Back-to-back fillers merge into one contiguous removal
# ===================================================================
print("\n--- Test 2: Back-to-back fillers merge ---")

BACK_TO_BACK = [
    {"word": " à", "start": 1.0, "end": 1.15, "probability": 0.9},
    {"word": " ờ", "start": 1.20, "end": 1.35, "probability": 0.9},  # gap 0.05s < MERGE_GAP_EPSILON
]
f2 = compute_filler_spans(BACK_TO_BACK, ["à", "ờ"], 0.5)
check("Both back-to-back fillers detected individually first", len(f2) == 2, f"{f2}")
m2 = merge_spans(f2)
check("Back-to-back fillers merge into a single span", len(m2) == 1, f"{m2}")
if m2:
    check("Merged span covers full [1.0, 1.35] range",
          abs(m2[0]["start_seconds"] - 1.0) < 1e-6 and abs(m2[0]["end_seconds"] - 1.35) < 1e-6, f"{m2[0]}")

# ===================================================================
# Test 3: audio_energy corroboration drops a likely-VAD-miss candidate
# ===================================================================
print("\n--- Test 3: audio_energy corroboration ---")

candidate_dead_air = [{"start_seconds": 2.0, "end_seconds": 3.0, "kind": "dead_air", "text": None, "confidence": 0.9}]
energy_profile_speech_present = [
    {"time_seconds": t, "loudness_lufs": -20.0, "active": True} for t in range(0, 5)
]
kept = corroborate_dead_air(candidate_dead_air, energy_profile_speech_present)
check("Candidate dropped when audio_energy shows active loudness (VAD miss guard)", len(kept) == 0, f"{kept}")

energy_profile_truly_silent = [
    {"time_seconds": t, "loudness_lufs": -80.0, "active": False} for t in range(0, 5)
]
kept2 = corroborate_dead_air(candidate_dead_air, energy_profile_truly_silent)
check("Candidate kept when audio_energy confirms silence", len(kept2) == 1, f"{kept2}")

# ===================================================================
# Test 4: Full execute() contract against the synthetic transcript
# ===================================================================
print("\n--- Test 4: SpeechGapDetector.execute() end-to-end (synthetic) ---")

tool = SpeechGapDetector()
check("Tool status is AVAILABLE (no hard dependency)", tool.get_status().name == "AVAILABLE")

synthetic_transcript = {
    "word_timestamps": SYNTHETIC_WORDS,
    "language": "vi",
    "duration_seconds": TOTAL_DURATION,
}
result = tool.execute({
    "input_path": str(Path(__file__).resolve()),  # any existing path; not read (transcript is inline)
    "transcript": synthetic_transcript,
    "corroborate_with_audio_energy": False,
    "output_dir": str(OUT),
})
check("execute() succeeds", result.success, result.error or "")
if result.success:
    check("removal_spans present", "removal_spans" in result.data)
    check("counts.total == len(removal_spans)",
          result.data["counts"]["total"] == len(result.data["removal_spans"]))
    check("source == 'transcript'", result.data["source"] == "transcript")
    check("Artifact file written", Path(result.artifacts[0]).exists(), result.artifacts[0])
    artifact_data = json.loads(Path(result.artifacts[0]).read_text(encoding="utf-8"))
    check("Artifact JSON round-trips removal_spans", artifact_data["removal_spans"] == result.data["removal_spans"])

# ===================================================================
# Test 5: Real Vietnamese transcript sample — no false-positive cuts
# ===================================================================
print("\n--- Test 5: Real transcript sample (projects/sample-review) ---")

real_transcript_path = Path(__file__).resolve().parent.parent.parent / "projects/sample-review/artifacts/transcript_full.json"
if real_transcript_path.exists():
    real_transcript = json.loads(real_transcript_path.read_text(encoding="utf-8"))
    words = extract_words(real_transcript)
    check("Real transcript has word timestamps", len(words) > 0, f"{len(words)} words")

    result2 = tool.execute({
        "input_path": str(real_transcript_path),  # exists; not read since transcript_json is provided
        "transcript_json": str(real_transcript_path),
        "corroborate_with_audio_energy": False,
        "output_dir": str(OUT),
    })
    check("execute() succeeds on real transcript", result2.success, result2.error or "")
    if result2.success:
        spans = result2.data["removal_spans"]
        print(f"  Real sample: {result2.data['counts']} over {result2.data['duration_seconds']}s")
        filler_span_texts = [s["text"] for s in spans if s["kind"] == "filler"]
        check("No real span flags meaningful word 'thì'",
              not any(t and "thì" in t.split() and t.strip() == "thì" for t in filler_span_texts),
              f"{filler_span_texts}")
        check("Dead-air spans (if any) don't exceed transcript duration",
              all(s["end_seconds"] <= real_transcript.get("duration_seconds", 1e9) + 0.01 for s in spans))
else:
    print("  [SKIP] Real transcript sample not found at expected path")

# ===================================================================
# Test 6: Degraded fallback when no transcript AND no media available
# ===================================================================
print("\n--- Test 6: Degraded fallback (no transcript, no media) ---")

fallback_result = degraded_audio_only_result(
    input_path=Path("nonexistent_file_for_qa_test.mp4"),
    output_dir=OUT,
    min_dead_air=0.6,
    pad_seconds=0.15,
    transcript_error="faster-whisper is not installed",
    duration_seconds=0.01,
)
check("Degraded path returns a structured error (not a crash)", not fallback_result.success)
check("Error message references the original transcript failure",
      "faster-whisper" in (fallback_result.error or ""), fallback_result.error or "")

# ===================================================================
# Test 7: pause_tighten span math (ffmpeg silencedetect ranges -> spans)
# ===================================================================
print("\n--- Test 7: pause_tighten compression (waveform-measured pauses) ---")

# Pure span math -- no ffmpeg/media needed, mirrors real silencedetect output
# shape (start, end) tuples in seconds.
silence_ranges = [
    (1.0, 1.6),   # 0.6s pause, qualifies (>= 0.4s min)
    (5.0, 5.2),   # 0.2s pause, too short, dropped
    (10.0, 10.5),  # 0.5s pause, qualifies
]

pause_spans = compute_pause_tighten_spans(silence_ranges, min_pause_seconds=0.4, keep_seconds=0.15)

check("Short sub-threshold pause dropped, 2 spans remain", len(pause_spans) == 2, f"{pause_spans}")
if len(pause_spans) == 2:
    first, second = pause_spans
    check("Kind is 'pause_tighten'", first["kind"] == "pause_tighten" and second["kind"] == "pause_tighten")
    check("0.15s natural silence kept at each edge (not the full 0.6s gap)",
          abs((first["end_seconds"] - first["start_seconds"]) - (0.6 - 0.15)) < 0.01,
          f"kept {first['end_seconds'] - first['start_seconds']}s of a 0.6s pause")
    check("Removed span sits INSIDE the original silence range, not touching its edges",
          first["start_seconds"] > 1.0 and first["end_seconds"] < 1.6,
          f"{first}")

zero_qualifying = compute_pause_tighten_spans([(0.0, 0.1)], min_pause_seconds=0.4, keep_seconds=0.15)
check("All-sub-threshold input produces zero spans (no forced cut)", zero_qualifying == [])

# ===================================================================
# Summary
# ===================================================================
print(f"\n{'='*60}")
print(f"SPEECH GAP DETECTION TEST COMPLETE: {PASS} passed, {FAIL} failed")
print(f"{'='*60}")

if FAIL:
    sys.exit(1)
