#!/usr/bin/env python3
"""Unit tests for disfluency-span handling in FootageEditAnalyzer's artifact
builder (tools/analysis/footage_edit_artifact.py).

Covers: MM:SS -> seconds conversion of raw Gemini disfluency_spans[],
confidence-gated conversion into removal_spans[] shape, and composition with
Phase 03 (speech_gap_detector) removal_spans via the shared merge_spans().
Pure unit tests against synthetic parsed payloads — no Gemini API calls.
"""

from pathlib import Path

import jsonschema
import pytest

from tools.analysis.footage_edit_artifact import (
    build_artifact,
    disfluency_removal_spans,
    disfluency_spans_from_parsed,
)


SAMPLE_DISFLUENCY_RAW = [
    {
        "start_time": "00:05",
        "end_time": "00:07",
        "stumbled_fragment": "cái... cái việc",
        "clean_continuation": "cái việc mà khách hàng cần",
        "confidence": 0.9,
    },
    {
        "start_time": "00:40",
        "end_time": "00:41",
        "stumbled_fragment": "thì là",
        "clean_continuation": "thì mình cần",
        "confidence": 0.4,  # below default threshold -> visible but not removed
    },
]


class TestDisfluencySpansFromParsed:
    """Conversion of raw Gemini disfluency_spans[] into artifact shape."""

    def test_converts_mmss_to_seconds(self):
        converted = disfluency_spans_from_parsed({"disfluency_spans": SAMPLE_DISFLUENCY_RAW})
        assert converted[0]["start_seconds"] == 5.0
        assert converted[0]["end_seconds"] == 7.0

    def test_preserves_quoted_text_fields(self):
        converted = disfluency_spans_from_parsed({"disfluency_spans": SAMPLE_DISFLUENCY_RAW})
        assert converted[0]["stumbled_fragment"] == "cái... cái việc"
        assert converted[0]["clean_continuation"] == "cái việc mà khách hàng cần"

    def test_missing_field_returns_empty_list(self):
        assert disfluency_spans_from_parsed({}) == []

    def test_empty_list_is_expected_clean_take_output(self):
        """An empty disfluency_spans[] (clean take, no stumbles) must not error."""
        assert disfluency_spans_from_parsed({"disfluency_spans": []}) == []


class TestDisfluencyRemovalSpans:
    """Confidence-gated conversion into the shared removal_spans[] shape."""

    def test_high_confidence_span_included(self):
        converted = disfluency_spans_from_parsed({"disfluency_spans": SAMPLE_DISFLUENCY_RAW})
        removal = disfluency_removal_spans(converted, min_confidence=0.75)
        assert len(removal) == 1
        assert removal[0]["kind"] == "disfluency"
        assert removal[0]["start_seconds"] == 5.0
        assert removal[0]["end_seconds"] == 7.0

    def test_low_confidence_span_excluded(self):
        converted = disfluency_spans_from_parsed({"disfluency_spans": SAMPLE_DISFLUENCY_RAW})
        removal = disfluency_removal_spans(converted, min_confidence=0.75)
        starts = [r["start_seconds"] for r in removal]
        assert 40.0 not in starts, "Below-threshold disfluency must not enter removal_spans"

    def test_removal_span_text_shows_both_fragments(self):
        converted = disfluency_spans_from_parsed({"disfluency_spans": SAMPLE_DISFLUENCY_RAW})
        removal = disfluency_removal_spans(converted, min_confidence=0.75)
        assert "cái... cái việc" in removal[0]["text"]
        assert "cái việc mà khách hàng cần" in removal[0]["text"]

    def test_missing_confidence_excluded_not_crashed(self):
        no_conf = [{"start_seconds": 1.0, "end_seconds": 2.0, "stumbled_fragment": "x", "clean_continuation": "y", "confidence": None}]
        assert disfluency_removal_spans(no_conf, min_confidence=0.75) == []

    def test_zero_length_span_excluded(self):
        zero_len = [{"start_seconds": 5.0, "end_seconds": 5.0, "stumbled_fragment": "x", "clean_continuation": "y", "confidence": 0.9}]
        assert disfluency_removal_spans(zero_len, min_confidence=0.75) == []


class TestBuildArtifactDisfluencyIntegration:
    """Verify build_artifact() merges disfluency detections into removal_spans[]."""

    class _FakeUsage:
        """Minimal usage_metadata stand-in so `_analysis_meta` token counts
        are real integers (matches footage_edit_plan.schema.json's
        `_analysis_meta.*_token_count` `integer` type) instead of None,
        which is a real Gemini response but not what this test is about."""

        prompt_token_count = 100
        candidates_token_count = 200
        thoughts_token_count = 50

    def _base_kwargs(self, parsed, removal_spans=None, min_disfluency_confidence=0.75):
        return dict(
            parsed=parsed,
            input_path=Path("dummy.mp4"),
            playbook_path=Path("styles/ugc-talking-head.yaml"),
            transitions=["none", "fade"],
            sound_effects=["none"],
            model="gemini-3.1-flash-lite",
            thinking_level="HIGH",
            elapsed=1.0,
            usage=self._FakeUsage(),
            repair_attempted=False,
            removal_spans=removal_spans or [],
            real_duration_seconds=90.0,
            min_disfluency_confidence=min_disfluency_confidence,
        )

    def test_artifact_includes_disfluency_spans_field(self):
        parsed = {"beats": [], "disfluency_spans": SAMPLE_DISFLUENCY_RAW}
        artifact = build_artifact(**self._base_kwargs(parsed))
        assert "disfluency_spans" in artifact
        assert len(artifact["disfluency_spans"]) == 2

    def test_high_confidence_disfluency_reaches_removal_spans(self):
        parsed = {"beats": [], "disfluency_spans": SAMPLE_DISFLUENCY_RAW}
        artifact = build_artifact(**self._base_kwargs(parsed))
        kinds = [s["kind"] for s in artifact["removal_spans"]]
        assert "disfluency" in kinds

    def test_composes_with_phase03_removal_spans(self):
        """Disfluency spans merge alongside pre-existing Phase 03 spans
        (dead_air/filler/pause_tighten) rather than replacing them."""
        parsed = {"beats": [], "disfluency_spans": SAMPLE_DISFLUENCY_RAW}
        phase03_spans = [
            {"start_seconds": 20.0, "end_seconds": 20.5, "kind": "dead_air", "text": None, "confidence": 0.9}
        ]
        artifact = build_artifact(**self._base_kwargs(parsed, removal_spans=phase03_spans))
        kinds = {s["kind"] for s in artifact["removal_spans"]}
        assert kinds == {"dead_air", "disfluency"}

    def test_clean_take_produces_empty_removal_additions(self):
        parsed = {"beats": [], "disfluency_spans": []}
        artifact = build_artifact(**self._base_kwargs(parsed))
        assert artifact["disfluency_spans"] == []
        assert artifact["removal_spans"] == []

    def test_artifact_validates_against_schema(self):
        schema_path = Path(__file__).resolve().parent.parent / "schemas" / "artifacts" / "footage_edit_plan.schema.json"
        import json
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        parsed = {
            "beats": [],
            "transcript": [],
            "pacing_profile": {"total_beats": 0, "avg_beat_length_seconds": 0, "editing_philosophy": "test"},
            "disfluency_spans": SAMPLE_DISFLUENCY_RAW,
        }
        artifact = build_artifact(**self._base_kwargs(parsed))
        jsonschema.validate(artifact, schema)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
