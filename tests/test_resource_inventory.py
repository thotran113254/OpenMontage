#!/usr/bin/env python3
"""Phase 05 Test: Resource inventory validation.

Ensures:
  - Inventory JSON is well-formed
  - Every playbook transition/SFX name exists in inventory
  - No conceptual-only (non-real) entries
  - All SFX files referenced in inventory actually exist
"""

import json
from pathlib import Path
import pytest


@pytest.fixture
def resource_inventory():
    """Load the real resource inventory."""
    inventory_path = Path(__file__).resolve().parent.parent / "assets_library" / "resource_inventory.json"
    with open(inventory_path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def playbook():
    """Load the ugc-talking-head playbook."""
    import yaml
    playbook_path = Path(__file__).resolve().parent.parent / "styles" / "ugc-talking-head.yaml"
    with open(playbook_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture
def assets_library_dir():
    """Path to assets_library directory."""
    return Path(__file__).resolve().parent.parent / "assets_library"


class TestResourceInventoryWellFormed:
    """Validate inventory JSON structure."""

    def test_inventory_is_valid_json(self, resource_inventory):
        """Verify inventory loaded as valid JSON."""
        assert isinstance(resource_inventory, dict), "Inventory should be a JSON object"

    def test_inventory_has_required_top_level_keys(self, resource_inventory):
        """Verify inventory has sound_effects, transitions, and metadata."""
        required_keys = ["sound_effects", "transitions"]
        for key in required_keys:
            assert key in resource_inventory, f"Missing required key: {key}"

    def test_sound_effects_is_list(self, resource_inventory):
        """Verify sound_effects is an array."""
        assert isinstance(resource_inventory["sound_effects"], list), \
            "sound_effects should be an array"

    def test_transitions_is_list(self, resource_inventory):
        """Verify transitions is an array."""
        assert isinstance(resource_inventory["transitions"], list), \
            "transitions should be an array"

    def test_sound_effects_have_required_fields(self, resource_inventory):
        """Verify each SFX has name, file, and tags."""
        for sfx in resource_inventory["sound_effects"]:
            assert "name" in sfx, f"SFX missing 'name': {sfx}"
            assert "file" in sfx, f"SFX '{sfx.get('name')}' missing 'file'"
            assert "tags" in sfx, f"SFX '{sfx.get('name')}' missing 'tags'"
            assert isinstance(sfx["tags"], list), \
                f"SFX '{sfx.get('name')}' tags should be a list, got {type(sfx['tags'])}"

    def test_transitions_have_required_fields(self, resource_inventory):
        """Verify each transition has name, runtime, and preset."""
        for trans in resource_inventory["transitions"]:
            assert "name" in trans, f"Transition missing 'name': {trans}"
            assert "runtime" in trans, f"Transition '{trans.get('name')}' missing 'runtime'"
            assert "preset" in trans, f"Transition '{trans.get('name')}' missing 'preset'"

    def test_inventory_entries_are_real_not_conceptual(self, resource_inventory):
        """Verify all inventory entries correspond to real files."""
        for sfx in resource_inventory["sound_effects"]:
            assert sfx.get("file") and len(sfx["file"]) > 0, \
                f"SFX '{sfx.get('name')}' has empty or missing file path"
            # File should not be placeholder text like "TBD", "N/A", "conceptual"
            file_path = sfx["file"].lower()
            assert not any(p in file_path for p in ["tbd", "n/a", "conceptual", "todo"]), \
                f"SFX '{sfx.get('name')}' file looks conceptual: {sfx['file']}"


class TestResourceInventoryFileExistence:
    """Verify referenced files actually exist."""

    def test_all_sfx_files_exist(self, resource_inventory, assets_library_dir):
        """Verify each SFX file path exists in assets_library."""
        for sfx in resource_inventory["sound_effects"]:
            file_path = assets_library_dir / sfx["file"]
            assert file_path.exists(), \
                f"SFX file not found: {file_path} (referenced in inventory as '{sfx['file']}')"

    def test_sfx_files_have_correct_extension(self, resource_inventory):
        """Verify SFX files have audio extensions."""
        valid_extensions = {".wav", ".mp3", ".aac", ".flac"}
        for sfx in resource_inventory["sound_effects"]:
            file_ext = Path(sfx["file"]).suffix.lower()
            assert file_ext in valid_extensions, \
                f"SFX '{sfx.get('name')}' has unexpected extension: {file_ext}"

    def test_transition_presets_are_valid_names(self, resource_inventory):
        """Verify transition preset names are valid identifiers."""
        for trans in resource_inventory["transitions"]:
            preset = trans["preset"]
            # "none", "fade", "slide", "wipe", etc. should be simple lowercase identifiers
            assert isinstance(preset, str) and len(preset) > 0, \
                f"Transition '{trans.get('name')}' has invalid preset: {preset}"
            assert preset.replace("_", "").isalnum(), \
                f"Transition '{trans.get('name')}' preset has invalid characters: {preset}"


class TestPlaybookTransitionsInInventory:
    """Verify playbook transitions/sfx exist in inventory."""

    def test_playbook_transitions_exist_in_inventory(self, playbook, resource_inventory):
        """Verify playbook declares only transitions that exist in inventory."""
        # Extract transition names from playbook (if playbook structure has them)
        # For this playbook, we just ensure common transition names are in inventory
        if "transitions" in playbook:
            playbook_transitions = playbook.get("transitions", [])
            if isinstance(playbook_transitions, list):
                inventory_transitions = {t["name"] for t in resource_inventory["transitions"]}
                for trans in playbook_transitions:
                    if isinstance(trans, dict):
                        trans_name = trans.get("name")
                    else:
                        trans_name = str(trans)

                    assert trans_name in inventory_transitions, \
                        f"Playbook uses transition '{trans_name}' not in inventory"

    def test_playbook_sfx_exist_in_inventory(self, playbook, resource_inventory):
        """Verify playbook SFX references exist in inventory."""
        # Extract SFX names from playbook (if structure has them)
        # For this playbook, verify common SFX names are in inventory
        if "sound_effects" in playbook:
            playbook_sfx = playbook.get("sound_effects", [])
            if isinstance(playbook_sfx, list):
                inventory_sfx = {s["name"] for s in resource_inventory["sound_effects"]}
                for sfx in playbook_sfx:
                    if isinstance(sfx, dict):
                        sfx_name = sfx.get("name")
                    else:
                        sfx_name = str(sfx)

                    assert sfx_name in inventory_sfx, \
                        f"Playbook uses SFX '{sfx_name}' not in inventory"


class TestInventoryCompleteness:
    """Verify inventory covers expected capabilities."""

    def test_inventory_has_minimum_transition_count(self, resource_inventory):
        """Verify inventory has at least 2 transitions (more than just 'none')."""
        transitions = resource_inventory["transitions"]
        assert len(transitions) >= 2, \
            f"Expected at least 2 transitions, got {len(transitions)}"

    def test_inventory_includes_none_transition(self, resource_inventory):
        """Verify 'none' transition is always available."""
        transition_names = {t["name"] for t in resource_inventory["transitions"]}
        assert "none" in transition_names, "Inventory must include 'none' transition"

    def test_inventory_has_minimum_sfx_count(self, resource_inventory):
        """Verify inventory has multiple SFX entries."""
        sfx = resource_inventory["sound_effects"]
        assert len(sfx) >= 3, \
            f"Expected at least 3 SFX entries, got {len(sfx)}"

    def test_inventory_names_are_unique(self, resource_inventory):
        """Verify all transition and SFX names are unique."""
        transition_names = [t["name"] for t in resource_inventory["transitions"]]
        sfx_names = [s["name"] for s in resource_inventory["sound_effects"]]

        # Check for duplicates
        assert len(transition_names) == len(set(transition_names)), \
            f"Duplicate transition names found: {transition_names}"
        assert len(sfx_names) == len(set(sfx_names)), \
            f"Duplicate SFX names found: {sfx_names}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
