#!/usr/bin/env python3
"""Phase 05 Test: footage_edit_plan schema validation.

Validates that:
  - A known-good sample (fixture from real Gemini output) validates against the schema
  - Schema enforcement catches invalid values (inventory violations, missing fields)
"""

import json
from pathlib import Path
import jsonschema
import pytest


TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
SCHEMAS_DIR = PROJECT_ROOT / "schemas" / "artifacts"
STYLES_SCHEMAS_DIR = PROJECT_ROOT / "schemas" / "styles"


def load_schema(name: str) -> dict:
    """Load a JSON schema from schemas/artifacts/."""
    schema_path = SCHEMAS_DIR / f"{name}.schema.json"
    with open(schema_path, encoding="utf-8") as f:
        return json.load(f)


def load_fixture(name: str) -> dict:
    """Load a fixture from tests/fixtures/."""
    fixture_path = FIXTURES_DIR / f"{name}.json"
    with open(fixture_path, encoding="utf-8") as f:
        return json.load(f)


class TestFootageEditPlanSchema:
    """Validate the footage_edit_plan artifact against its schema."""

    def test_sample_validates_against_schema(self):
        """Load the fixture and validate it against footage_edit_plan.schema.json."""
        schema = load_schema("footage_edit_plan")
        fixture = load_fixture("footage_edit_plan_sample")

        # Should not raise
        jsonschema.validate(fixture, schema)

    def test_fixture_has_all_required_top_level_fields(self):
        """Check that the fixture includes all required fields."""
        fixture = load_fixture("footage_edit_plan_sample")
        required = ["version", "source", "style_playbook", "transcript",
                    "pacing_profile", "beats", "inventory_used", "model"]

        for field in required:
            assert field in fixture, f"Missing required field: {field}"

    def test_fixture_beats_have_required_structure(self):
        """Verify each beat has required keys."""
        fixture = load_fixture("footage_edit_plan_sample")
        required_beat_fields = [
            "beat_index", "start_time", "end_time", "spoken_text",
            "zoom", "transition", "b_roll", "sound_effect"
        ]

        for beat in fixture["beats"]:
            for field in required_beat_fields:
                assert field in beat, f"Beat {beat.get('beat_index')} missing {field}"

    def test_fixture_transitions_in_inventory_used(self):
        """Verify that beat transitions reference inventory_used.transitions."""
        fixture = load_fixture("footage_edit_plan_sample")
        inventory_transitions = set(fixture["inventory_used"]["transitions"])

        for beat in fixture["beats"]:
            transition_type = beat["transition"]["type"]
            assert transition_type in inventory_transitions, \
                f"Beat {beat['beat_index']} uses transition '{transition_type}' not in inventory"

    def test_fixture_sfx_in_inventory_used(self):
        """Verify that beat sound effects reference inventory_used.sound_effects."""
        fixture = load_fixture("footage_edit_plan_sample")
        inventory_sfx = set(fixture["inventory_used"]["sound_effects"]) | {"none"}

        for beat in fixture["beats"]:
            sfx_type = beat["sound_effect"]["type"]
            assert sfx_type in inventory_sfx, \
                f"Beat {beat['beat_index']} uses sfx '{sfx_type}' not in inventory"

    def test_removal_spans_have_valid_kind(self):
        """Check removal_spans (if present) have valid kind enum."""
        fixture = load_fixture("footage_edit_plan_sample")
        removal_spans = fixture.get("removal_spans", [])

        valid_kinds = {"filler", "dead_air"}
        for span in removal_spans:
            assert span["kind"] in valid_kinds, \
                f"Invalid removal span kind: {span['kind']}"

    def test_invalid_fixture_fails_validation(self):
        """Ensure invalid data is rejected."""
        schema = load_schema("footage_edit_plan")

        # Missing required field
        invalid_fixture = {"version": "1.0"}  # missing most fields

        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(invalid_fixture, schema)

    def test_beat_with_invalid_zoom_action_fails(self):
        """Verify zoom action enum is enforced."""
        schema = load_schema("footage_edit_plan")

        invalid_fixture = {
            "version": "1.0",
            "source": {"path": "test.mp4", "duration_seconds": 10},
            "style_playbook": "styles/test.yaml",
            "transcript": [],
            "pacing_profile": {
                "total_beats": 1,
                "avg_beat_length_seconds": 10,
                "editing_philosophy": "test"
            },
            "beats": [
                {
                    "beat_index": 1,
                    "start_time": "00:00",
                    "end_time": "00:10",
                    "spoken_text": "test",
                    "zoom": {
                        "action": "invalid_action",  # not in enum
                        "reason": "test"
                    },
                    "transition": {"type": "none", "reason": "test"},
                    "b_roll": {"needed": False, "reason": "test"},
                    "sound_effect": {"type": "none", "reason": "test"}
                }
            ],
            "inventory_used": {"transitions": ["none"], "sound_effects": []},
            "model": "test"
        }

        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(invalid_fixture, schema)


class TestPlaybookSchema:
    """Validate the playbook schema (Phase 01 success criterion)."""

    def test_playbook_exists_and_is_well_formed(self):
        """Check that the ugc-talking-head playbook exists and is valid YAML."""
        import yaml

        playbook_path = PROJECT_ROOT / "styles" / "ugc-talking-head.yaml"
        assert playbook_path.exists(), f"Playbook not found at {playbook_path}"

        with open(playbook_path, encoding="utf-8") as f:
            playbook = yaml.safe_load(f)

        assert isinstance(playbook, dict), "Playbook should be a YAML dict"
        # Playbook structure has identity.name, not top-level name
        assert "identity" in playbook, "Playbook missing 'identity' section"
        assert "name" in playbook.get("identity", {}), "Playbook identity missing 'name' field"

    def test_playbook_schema_exists(self):
        """Verify the playbook schema file exists."""
        schema_path = STYLES_SCHEMAS_DIR / "playbook.schema.json"
        assert schema_path.exists(), f"Playbook schema not found at {schema_path}"

        with open(schema_path, encoding="utf-8") as f:
            schema = json.load(f)

        assert "$schema" in schema or "type" in schema, "Valid JSON schema"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
