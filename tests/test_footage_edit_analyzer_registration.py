#!/usr/bin/env python3
"""Phase 05 Test: FootageEditAnalyzer registration and availability.

Validates:
  - Tool instantiation and name
  - Availability tied to GEMINI_API_KEY environment variable
  - Inventory membership: analyzer only references transitions/sfx present in resource_inventory.json
  - Optional live test (gated by RUN_LIVE_GEMINI env var)
"""

import json
import os
from pathlib import Path
import pytest

from tools.analysis.footage_edit_analyzer import FootageEditAnalyzer
from tools.analysis.footage_edit_prompt import (
    inventory_names,
    load_inventory,
    validate_and_clamp_beats,
)


@pytest.fixture
def analyzer():
    """Initialize FootageEditAnalyzer."""
    return FootageEditAnalyzer()


@pytest.fixture
def resource_inventory():
    """Load the real resource inventory."""
    inventory_path = Path(__file__).resolve().parent.parent / "assets_library" / "resource_inventory.json"
    with open(inventory_path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def fixture_footage_edit_plan():
    """Load the fixture footage_edit_plan."""
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "footage_edit_plan_sample.json"
    with open(fixture_path, encoding="utf-8") as f:
        return json.load(f)


class TestFootageEditAnalyzerRegistration:
    """Test tool registration and status."""

    def test_analyzer_instantiates(self, analyzer):
        """Verify FootageEditAnalyzer can be instantiated."""
        assert analyzer is not None
        assert isinstance(analyzer, FootageEditAnalyzer)

    def test_analyzer_has_correct_name(self, analyzer):
        """Verify tool name is 'footage_edit_analyzer'."""
        assert analyzer.name == "footage_edit_analyzer"

    def test_analyzer_status_reflects_gemini_key(self, analyzer):
        """Verify tool status: AVAILABLE iff GEMINI_API_KEY is set."""
        has_key = "GEMINI_API_KEY" in os.environ
        status = analyzer.get_status()

        if has_key:
            # If key is set, should be AVAILABLE or higher
            assert status.name in ("AVAILABLE", "CONFIGURED", "READY"), \
                f"Expected AVAILABLE status with key, got {status.name}"
        else:
            # If key is not set, should report unavailable
            assert status.name in ("UNAVAILABLE", "MISSING_DEPENDENCY"), \
                f"Expected UNAVAILABLE status without key, got {status.name}"

    def test_analyzer_declares_gemini_dependency(self, analyzer):
        """Verify analyzer indicates GEMINI_API_KEY as a dependency."""
        support_envelope = analyzer.get_info()
        dependencies = support_envelope.get("dependencies", [])

        # Should list GEMINI_API_KEY as a dependency
        assert any("GEMINI_API_KEY" in str(dep) for dep in dependencies), \
            f"GEMINI_API_KEY not in dependencies: {dependencies}"


class TestInventoryMembership:
    """Validate that inventory-constrained values are real."""

    def test_fixture_transitions_exist_in_inventory(self, fixture_footage_edit_plan, resource_inventory):
        """Verify each transition in fixture is in resource_inventory."""
        fixture_transitions = set(fixture_footage_edit_plan["inventory_used"]["transitions"])
        real_transitions = {t["name"] for t in resource_inventory["transitions"]}

        missing = fixture_transitions - real_transitions
        assert not missing, f"Fixture uses transitions not in inventory: {missing}"

    def test_fixture_sfx_exist_in_inventory(self, fixture_footage_edit_plan, resource_inventory):
        """Verify each SFX in fixture is in resource_inventory."""
        fixture_sfx = set(fixture_footage_edit_plan["inventory_used"]["sound_effects"])
        real_sfx = {s["name"] for s in resource_inventory["sound_effects"]}
        real_sfx.add("none")  # "none" is always valid

        missing = fixture_sfx - real_sfx
        assert not missing, f"Fixture uses SFX not in inventory: {missing}"

    def test_beat_transitions_in_inventory_used(self, fixture_footage_edit_plan):
        """Verify each beat's transition references inventory_used.transitions."""
        inventory_transitions = set(fixture_footage_edit_plan["inventory_used"]["transitions"])

        for beat in fixture_footage_edit_plan["beats"]:
            transition_type = beat["transition"]["type"]
            assert transition_type in inventory_transitions, \
                f"Beat {beat['beat_index']} uses transition '{transition_type}' not in inventory_used"

    def test_beat_sfx_in_inventory_used(self, fixture_footage_edit_plan):
        """Verify each beat's SFX references inventory_used.sound_effects."""
        inventory_sfx = set(fixture_footage_edit_plan["inventory_used"]["sound_effects"]) | {"none"}

        for beat in fixture_footage_edit_plan["beats"]:
            sfx_type = beat["sound_effect"]["type"]
            assert sfx_type in inventory_sfx, \
                f"Beat {beat['beat_index']} uses SFX '{sfx_type}' not in inventory_used"


class TestInventoryMembershipValidation:
    """Unit test: validator rejects out-of-set transition/sfx values."""

    def test_rejects_transition_not_in_inventory(self, resource_inventory):
        """Invoke the real validator with an out-of-inventory transition and
        verify it clamps the value to 'none' and records the violation."""
        transitions, sound_effects = inventory_names(resource_inventory)
        real_transitions = set(transitions)
        assert "invalid_transition_xyz" not in real_transitions, \
            "Test setup error: 'invalid_transition_xyz' shouldn't exist in inventory"

        beats = [{
            "beat_index": 1,
            "transition": {"type": "invalid_transition_xyz", "reason": "test"},
            "sound_effect": {"type": "none", "trigger": "none", "reason": "test"},
        }]

        clean_beats, violations = validate_and_clamp_beats(beats, transitions, sound_effects)

        assert clean_beats[0]["transition"]["type"] == "none", \
            "Validator should clamp out-of-inventory transition to 'none'"
        assert any(
            v["field"] == "transition.type"
            and v["invented_value"] == "invalid_transition_xyz"
            and v["beat_index"] == 1
            and v["clamped_to"] == "none"
            for v in violations
        ), f"Expected transition.type violation recorded, got: {violations}"

    def test_rejects_sfx_not_in_inventory(self, resource_inventory):
        """Invoke the real validator with an out-of-inventory SFX and verify
        it clamps the value to 'none' and records the violation."""
        transitions, sound_effects = inventory_names(resource_inventory)
        real_sfx = set(sound_effects)
        assert "invalid_sfx_xyz" not in real_sfx, \
            "Test setup error: 'invalid_sfx_xyz' shouldn't exist in inventory"

        beats = [{
            "beat_index": 1,
            "transition": {"type": "none", "reason": "test"},
            "sound_effect": {"type": "invalid_sfx_xyz", "trigger": "none", "reason": "test"},
        }]

        clean_beats, violations = validate_and_clamp_beats(beats, transitions, sound_effects)

        assert clean_beats[0]["sound_effect"]["type"] == "none", \
            "Validator should clamp out-of-inventory SFX to 'none'"
        assert any(
            v["field"] == "sound_effect.type"
            and v["invented_value"] == "invalid_sfx_xyz"
            and v["beat_index"] == 1
            and v["clamped_to"] == "none"
            for v in violations
        ), f"Expected sound_effect.type violation recorded, got: {violations}"

    def test_fixture_has_no_invented_transitions(self, fixture_footage_edit_plan):
        """Verify fixture's _analysis_meta shows zero transition violations."""
        violations = fixture_footage_edit_plan.get("_analysis_meta", {}).get("inventory_violations", [])
        transition_violations = [v for v in violations if v.get("field") == "transition.type"]

        assert len(transition_violations) == 0, \
            f"Fixture should have zero transition violations, got {len(transition_violations)}: {transition_violations}"

    def test_fixture_has_no_invented_sfx(self, fixture_footage_edit_plan):
        """Verify fixture's _analysis_meta shows zero SFX violations."""
        violations = fixture_footage_edit_plan.get("_analysis_meta", {}).get("inventory_violations", [])
        sfx_violations = [v for v in violations if v.get("field") == "sound_effect.type"]

        assert len(sfx_violations) == 0, \
            f"Fixture should have zero SFX violations, got {len(sfx_violations)}: {sfx_violations}"


class TestFootageEditAnalyzerLiveCall:
    """Optional live test: gated by RUN_LIVE_GEMINI environment variable."""

    @pytest.mark.skipif(
        not os.getenv("RUN_LIVE_GEMINI"),
        reason="RUN_LIVE_GEMINI not set; live Gemini test skipped"
    )
    def test_analyzer_live_call_produces_schema_valid_output(self, analyzer):
        """Live test: run analyzer on a video file if available."""
        video_path = Path.home() / "Downloads" / "tiktok_hd.mp4"

        if not video_path.exists():
            pytest.skip(f"Test video not found at {video_path}")

        # This is a LIVE call — will charge API quota
        result = analyzer.execute({
            "input_path": str(video_path),
            "style_playbook": "styles/ugc-talking-head.yaml",
            "output_dir": str(Path(__file__).resolve().parent / "live_test_output"),
        })

        assert result.success, f"Live analyzer call failed: {result.error}"
        assert "footage_edit_plan" in result.data, "Missing footage_edit_plan in result"

        plan = result.data["footage_edit_plan"]
        assert "beats" in plan
        assert len(plan["beats"]) > 0, "No beats produced"

        # Verify transition variety (not all identical)
        transitions = [b["transition"]["type"] for b in plan["beats"]]
        unique_transitions = set(transitions)
        assert len(unique_transitions) > 1, \
            f"Expected variety in transitions, got only {unique_transitions}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
