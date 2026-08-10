#!/usr/bin/env python3
"""Phase 05 Test: SpeechGapDetector unit tests with synthetic word-timestamp lists.

Uses pytest conventions to validate span math, confidence gating, merge/dedupe,
and the execute() contract without API calls or audio processing.

Related: tests/qa/test_10_speech_gap_detection.py covers additional integration
tests (real transcripts, audio energy corroboration, fallback paths).
"""

import json
from pathlib import Path
import pytest

from tools.analysis.speech_gap_detector import SpeechGapDetector
from tools.analysis.speech_gap_spans import (
    compute_dead_air_spans,
    compute_filler_spans,
    merge_spans,
)


SYNTHETIC_WORDS = [
    {"word": " Xin", "start": 0.30, "end": 0.60, "probability": 0.95},
    {"word": " chào", "start": 0.60, "end": 0.90, "probability": 0.96},
    {"word": " à", "start": 0.90, "end": 1.10, "probability": 0.85},       # filler
    {"word": " mọi", "start": 1.10, "end": 1.40, "probability": 0.90},
    {"word": " người", "start": 1.40, "end": 1.70, "probability": 0.90},
    # 0.8s gap here -> dead air
    {"word": " ừm", "start": 2.50, "end": 2.70, "probability": 0.40},      # low-conf filler, NOT flagged
    {"word": " thì", "start": 2.70, "end": 2.90, "probability": 0.97},     # meaningful, never flagged
    {"word": " kiểu", "start": 2.90, "end": 3.10, "probability": 0.80},
    {"word": " như", "start": 3.10, "end": 3.30, "probability": 0.75},     # bigram "kiểu như"
    {"word": " là", "start": 3.30, "end": 3.60, "probability": 0.90},
    {"word": " vậy", "start": 3.65, "end": 4.00, "probability": 0.92},
]
TOTAL_DURATION = 5.0


@pytest.fixture
def speech_gap_detector():
    """Initialize SpeechGapDetector tool."""
    return SpeechGapDetector()


@pytest.fixture
def synthetic_transcript():
    """Fixture: synthetic word-timestamp list with known structure."""
    return {
        "word_timestamps": SYNTHETIC_WORDS,
        "language": "vi",
        "duration_seconds": TOTAL_DURATION,
    }


@pytest.fixture
def temp_output_dir(tmp_path):
    """Create temp directory for test outputs."""
    output_dir = tmp_path / "speech_gap_test_output"
    output_dir.mkdir()
    return output_dir


class TestSpeechGapDetectorSpanMath:
    """Unit tests for span computation (dead-air, filler, merge)."""

    def test_dead_air_detection_exact_count(self):
        """Verify exactly 2 dead-air spans (inter-word + trailing)."""
        dead_air = compute_dead_air_spans(
            SYNTHETIC_WORDS,
            min_dead_air_seconds=0.6,
            total_duration=TOTAL_DURATION
        )

        assert len(dead_air) == 2, f"Expected 2 dead-air spans, got {len(dead_air)}: {dead_air}"

    def test_dead_air_inter_word_gap_timing(self):
        """Verify inter-word gap falls in expected range."""
        dead_air = compute_dead_air_spans(
            SYNTHETIC_WORDS,
            min_dead_air_seconds=0.6,
            total_duration=TOTAL_DURATION
        )

        # Between word "người" (ends 1.70) and "ừm" (starts 2.50)
        inter_word = dead_air[0]
        assert 1.70 <= inter_word["start_seconds"] <= 1.90, \
            f"Inter-word gap start should be ~1.8s, got {inter_word['start_seconds']}"
        assert 2.30 <= inter_word["end_seconds"] <= 2.50, \
            f"Inter-word gap end should be ~2.4s, got {inter_word['end_seconds']}"

    def test_dead_air_trailing_gap_timing(self):
        """Verify trailing gap (after last word to duration)."""
        dead_air = compute_dead_air_spans(
            SYNTHETIC_WORDS,
            min_dead_air_seconds=0.6,
            total_duration=TOTAL_DURATION
        )

        # After word "vậy" (ends 4.00) to TOTAL_DURATION (5.0)
        trailing = dead_air[1]
        assert 3.95 <= trailing["start_seconds"] <= 4.15, \
            f"Trailing gap start should be ~4.0s, got {trailing['start_seconds']}"
        assert 4.95 <= trailing["end_seconds"] <= 5.05, \
            f"Trailing gap end should be ~5.0s, got {trailing['end_seconds']}"

    def test_filler_detection_exact_count(self):
        """Verify 2 filler spans ('à' + 'kiểu như' bigram)."""
        filler = compute_filler_spans(
            SYNTHETIC_WORDS,
            ["à", "ừm", "ờ", "ừ", "kiểu", "kiểu như"],
            min_filler_confidence=0.5
        )

        assert len(filler) == 2, f"Expected 2 filler spans, got {len(filler)}: {filler}"

    def test_filler_contains_expected_words(self):
        """Verify 'à' and 'kiểu như' are flagged, others are not."""
        filler = compute_filler_spans(
            SYNTHETIC_WORDS,
            ["à", "ừm", "ờ", "ừ", "kiểu", "kiểu như"],
            min_filler_confidence=0.5
        )

        filler_texts = [f["text"] for f in filler]
        assert "à" in filler_texts, f"'à' should be flagged, got {filler_texts}"
        assert "kiểu như" in filler_texts, f"'kiểu như' bigram should be flagged, got {filler_texts}"

    def test_low_confidence_filler_not_flagged(self):
        """Verify low-confidence fillers (< min_confidence) are not flagged."""
        filler = compute_filler_spans(
            SYNTHETIC_WORDS,
            ["à", "ừm", "ờ", "ừ", "kiểu", "kiểu như"],
            min_filler_confidence=0.5
        )

        filler_texts = [f["text"] for f in filler]
        # 'ừm' has probability 0.40 < 0.5, should not be flagged
        assert not any("ừm" in (t or "") for t in filler_texts), \
            f"Low-confidence 'ừm' should not be flagged, got {filler_texts}"

    def test_meaningful_word_never_flagged(self):
        """Verify meaningful words like 'thì' are never flagged as fillers."""
        filler = compute_filler_spans(
            SYNTHETIC_WORDS,
            ["à", "ừm", "ờ", "ừ", "kiểu", "kiểu như"],
            min_filler_confidence=0.5
        )

        filler_texts = [f["text"] for f in filler]
        # 'thì' is in SYNTHETIC_WORDS but not in filler_lexicon, so should never be flagged
        assert not any("thì" in (t or "") for t in filler_texts), \
            f"Meaningful word 'thì' should never be flagged, got {filler_texts}"

    def test_merge_dedupe_produces_correct_count(self):
        """Verify merge produces exactly 4 non-overlapping spans."""
        dead_air = compute_dead_air_spans(
            SYNTHETIC_WORDS,
            min_dead_air_seconds=0.6,
            total_duration=TOTAL_DURATION
        )
        filler = compute_filler_spans(
            SYNTHETIC_WORDS,
            ["à", "ừm", "ờ", "ừ", "kiểu", "kiểu như"],
            min_filler_confidence=0.5
        )

        merged = merge_spans(dead_air + filler)
        assert len(merged) == 4, f"Expected 4 merged spans, got {len(merged)}: {merged}"

    def test_merged_spans_are_sorted(self):
        """Verify merged spans are sorted by start_seconds."""
        dead_air = compute_dead_air_spans(
            SYNTHETIC_WORDS,
            min_dead_air_seconds=0.6,
            total_duration=TOTAL_DURATION
        )
        filler = compute_filler_spans(
            SYNTHETIC_WORDS,
            ["à", "ừm", "ờ", "ừ", "kiểu", "kiểu như"],
            min_filler_confidence=0.5
        )

        merged = merge_spans(dead_air + filler)
        starts = [s["start_seconds"] for s in merged]
        assert starts == sorted(starts), \
            f"Merged spans should be sorted, got {starts}"

    def test_back_to_back_fillers_merge_into_one(self):
        """Verify adjacent fillers merge into a single contiguous span."""
        back_to_back = [
            {"word": " à", "start": 1.0, "end": 1.15, "probability": 0.9},
            {"word": " ờ", "start": 1.20, "end": 1.35, "probability": 0.9},  # gap < MERGE_GAP_EPSILON
        ]

        filler = compute_filler_spans(back_to_back, ["à", "ờ"], 0.5)
        assert len(filler) == 2, "Should detect 2 fillers individually"

        merged = merge_spans(filler)
        assert len(merged) == 1, f"Back-to-back fillers should merge to 1 span, got {len(merged)}"
        assert abs(merged[0]["start_seconds"] - 1.0) < 1e-6, "Merged span should start at 1.0"
        assert abs(merged[0]["end_seconds"] - 1.35) < 1e-6, "Merged span should end at 1.35"


class TestSpeechGapDetectorRegistration:
    """Test tool registration and availability."""

    def test_tool_instantiates(self, speech_gap_detector):
        """Verify SpeechGapDetector can be instantiated."""
        assert speech_gap_detector is not None
        assert isinstance(speech_gap_detector, SpeechGapDetector)

    def test_tool_has_correct_name(self, speech_gap_detector):
        """Verify tool name matches expected."""
        assert speech_gap_detector.name == "speech_gap_detector"

    def test_tool_status_available(self, speech_gap_detector):
        """Verify tool reports AVAILABLE status (no hard external dependency)."""
        status = speech_gap_detector.get_status()
        assert status.name == "AVAILABLE", f"Expected AVAILABLE, got {status.name}"


class TestSpeechGapDetectorExecuteContract:
    """Test the execute() contract against synthetic transcript."""

    def test_execute_succeeds_with_synthetic_transcript(
        self, speech_gap_detector, synthetic_transcript, temp_output_dir
    ):
        """Verify execute() succeeds with synthetic word-timestamp transcript."""
        result = speech_gap_detector.execute({
            "input_path": __file__,  # any existing path; not read (transcript is inline)
            "transcript": synthetic_transcript,
            "corroborate_with_audio_energy": False,
            "output_dir": str(temp_output_dir),
        })

        assert result.success, f"execute() failed: {result.error}"

    def test_execute_returns_removal_spans(
        self, speech_gap_detector, synthetic_transcript, temp_output_dir
    ):
        """Verify execute() result contains removal_spans."""
        result = speech_gap_detector.execute({
            "input_path": __file__,
            "transcript": synthetic_transcript,
            "corroborate_with_audio_energy": False,
            "output_dir": str(temp_output_dir),
        })

        assert result.success
        assert "removal_spans" in result.data, f"Missing removal_spans in {result.data.keys()}"
        assert isinstance(result.data["removal_spans"], list)

    def test_execute_removal_spans_have_required_fields(
        self, speech_gap_detector, synthetic_transcript, temp_output_dir
    ):
        """Verify each removal span has required fields."""
        result = speech_gap_detector.execute({
            "input_path": __file__,
            "transcript": synthetic_transcript,
            "corroborate_with_audio_energy": False,
            "output_dir": str(temp_output_dir),
        })

        assert result.success
        for span in result.data["removal_spans"]:
            assert "start_seconds" in span
            assert "end_seconds" in span
            assert "kind" in span
            assert span["kind"] in {"filler", "dead_air"}

    def test_execute_counts_match_span_length(
        self, speech_gap_detector, synthetic_transcript, temp_output_dir
    ):
        """Verify counts.total == len(removal_spans)."""
        result = speech_gap_detector.execute({
            "input_path": __file__,
            "transcript": synthetic_transcript,
            "corroborate_with_audio_energy": False,
            "output_dir": str(temp_output_dir),
        })

        assert result.success
        expected_total = len(result.data["removal_spans"])
        actual_total = result.data["counts"]["total"]
        assert actual_total == expected_total, \
            f"Counts mismatch: {actual_total} != {expected_total}"

    def test_execute_source_is_transcript(
        self, speech_gap_detector, synthetic_transcript, temp_output_dir
    ):
        """Verify source field indicates transcript-based detection."""
        result = speech_gap_detector.execute({
            "input_path": __file__,
            "transcript": synthetic_transcript,
            "corroborate_with_audio_energy": False,
            "output_dir": str(temp_output_dir),
        })

        assert result.success
        assert result.data["source"] == "transcript", \
            f"Expected source='transcript', got {result.data['source']}"

    def test_execute_writes_artifact_file(
        self, speech_gap_detector, synthetic_transcript, temp_output_dir
    ):
        """Verify execute() writes an artifact JSON file."""
        result = speech_gap_detector.execute({
            "input_path": __file__,
            "transcript": synthetic_transcript,
            "corroborate_with_audio_energy": False,
            "output_dir": str(temp_output_dir),
        })

        assert result.success
        assert len(result.artifacts) > 0, "No artifacts produced"
        artifact_path = Path(result.artifacts[0])
        assert artifact_path.exists(), f"Artifact file not created: {artifact_path}"

    def test_execute_artifact_json_round_trips(
        self, speech_gap_detector, synthetic_transcript, temp_output_dir
    ):
        """Verify artifact JSON is valid and matches result data."""
        result = speech_gap_detector.execute({
            "input_path": __file__,
            "transcript": synthetic_transcript,
            "corroborate_with_audio_energy": False,
            "output_dir": str(temp_output_dir),
        })

        assert result.success
        artifact_path = Path(result.artifacts[0])
        artifact_data = json.loads(artifact_path.read_text(encoding="utf-8"))

        # Round-trip check: artifact removal_spans should match result data
        assert artifact_data["removal_spans"] == result.data["removal_spans"], \
            "Artifact JSON doesn't match result data"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
