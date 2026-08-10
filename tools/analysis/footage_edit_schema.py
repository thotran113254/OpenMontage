"""Input schema contract for FootageEditAnalyzer.

Split out of footage_edit_analyzer.py to keep it under the project's
~200-line convention (CLAUDE.md "Consider Modularization").
"""

from __future__ import annotations

from tools.analysis.footage_edit_disfluency_prompt import MIN_DISFLUENCY_CONFIDENCE_DEFAULT
from tools.analysis.footage_edit_prompt import (
    DEFAULT_MODEL,
    DEFAULT_THINKING_LEVEL,
    SUPPORTED_MODELS,
    SUPPORTED_THINKING_LEVELS,
)

INPUT_SCHEMA = {
    "type": "object",
    "required": ["input_path"],
    "properties": {
        "input_path": {"type": "string", "description": "Path to the raw talking-head video"},
        "playbook_path": {
            "type": "string",
            "default": "styles/ugc-talking-head.yaml",
            "description": "Path to the style playbook YAML",
        },
        "inventory_path": {
            "type": "string",
            "default": "assets_library/resource_inventory.json",
            "description": "Path to Phase 01b's resource inventory manifest",
        },
        "model": {
            "type": "string",
            "enum": SUPPORTED_MODELS,
            "default": DEFAULT_MODEL,
            "description": (
                "gemini-3.1-flash-lite is the default (fast/cheap, sufficient "
                "for simple edit grammar); gemini-3.5-flash is an optional "
                "upgrade for more nuanced analysis."
            ),
        },
        "thinking_level": {
            "type": "string",
            "enum": SUPPORTED_THINKING_LEVELS,
            "default": DEFAULT_THINKING_LEVEL,
        },
        "output_dir": {"type": "string", "default": "output/footage_edit_plan"},
        "removal_spans": {
            "type": "array",
            "description": (
                "Optional pre-computed removal_spans[] (speech_gap_detector "
                "output — dead_air/filler/pause_tighten). Pass its output "
                "here or via removal_spans_path."
            ),
        },
        "removal_spans_path": {
            "type": "string",
            "description": "Path to a JSON file with removal_spans[] (or {\"removal_spans\": [...]})",
        },
        "broll_sources": {
            "type": "array",
            "items": {"type": "string"},
            "default": [],
            "description": (
                "Optional paths to real b-roll candidate clips the project actually has. "
                "Each is described via one extra Gemini call and carried into "
                "footage_edit_plan.broll_candidates[] for the asset stage to match against "
                "b_roll.needed beats (see footage_edit_broll_matcher.match_broll_to_beats). "
                "Leave empty when no real b-roll footage exists for this project — "
                "b_roll.needed beats then fall back to no-insert, never a fabricated visual, "
                "unless the user separately approves AI-generated inserts."
            ),
        },
        "min_disfluency_confidence": {
            "type": "number",
            "default": MIN_DISFLUENCY_CONFIDENCE_DEFAULT,
            "description": (
                "Minimum confidence for a Gemini-flagged speech disfluency "
                "(stumble/repeat/false-start) to be merged into removal_spans[]. "
                "Lower-confidence flags still appear in disfluency_spans[] for "
                "human review but are excluded from automatic removal."
            ),
        },
    },
}
