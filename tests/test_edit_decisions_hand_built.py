#!/usr/bin/env python3
"""Phase 05 Test: Hand-built edit_decisions validation.

Demonstrates that the Phase 04 field-mapping (footage_edit_plan → edit_decisions)
is realizable in practice. Builds one edit_decisions artifact by hand from the
fixture footage_edit_plan, following the field-translation table in
skills/pipelines/hybrid/edit-director.md, and validates it against the schema.

This proves that Phase 04's mapping is not just documented but actually works.
"""

import json
import sys
from pathlib import Path
import jsonschema
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.analysis.footage_edit_broll_matcher import match_broll_to_beats
from tools.analysis.footage_edit_cuts_builder import build_cuts_from_plan


@pytest.fixture
def footage_edit_plan_fixture():
    """Load the fixture footage_edit_plan."""
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "footage_edit_plan_sample.json"
    with open(fixture_path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def resource_inventory():
    """Load the real resource inventory."""
    inventory_path = Path(__file__).resolve().parent.parent / "assets_library" / "resource_inventory.json"
    with open(inventory_path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def edit_decisions_schema():
    """Load the edit_decisions schema."""
    schema_path = Path(__file__).resolve().parent.parent / "schemas" / "artifacts" / "edit_decisions.schema.json"
    with open(schema_path, encoding="utf-8") as f:
        return json.load(f)


def build_edit_decisions_from_fixture(footage_edit_plan: dict, resource_inventory: dict) -> dict:
    """
    Build an edit_decisions artifact from a footage_edit_plan, following
    skills/pipelines/hybrid/edit-director.md:
    - beats[] + removal_spans[] -> cuts[] via the real build_cuts_from_plan()
      (ripple-shift, source_in_seconds, narration-safe transition clamp) --
      not hand-mapped, per the director's own instruction not to re-derive
      this mapping in prose.
    - beat zoom -> cut transform.animation/scale (still a plain field
      mapping; recommended_scale_range is Gemini free text, not structured)
    - beat b_roll -> matched real clip becomes cuts[].backgroundVideo; no
      match/no candidates falls back to no-insert, never overlays[] and
      never a fabricated visual (see edit-director.md §6b)
    - beat sfx -> audio.sfx[]

    Returns a schema-valid edit_decisions dict.
    """
    sfx_map = {sfx["name"]: sfx["file"] for sfx in resource_inventory["sound_effects"]}
    sfx_map["none"] = None

    beats = footage_edit_plan["beats"]
    source = footage_edit_plan["source"]["path"]
    removal_spans = footage_edit_plan.get("removal_spans", [])
    broll_candidates = footage_edit_plan.get("broll_candidates", [])

    cuts = build_cuts_from_plan(beats, removal_spans, source)

    cuts_by_beat_index: dict[int, list[dict]] = {}
    for cut in cuts:
        beat_num = int(cut["id"].split("_")[0].removeprefix("beat"))
        cuts_by_beat_index.setdefault(beat_num, []).append(cut)

    broll_matches = match_broll_to_beats(beats, broll_candidates)
    sfx_entries = []

    for beat in beats:
        beat_idx = beat["beat_index"]
        beat_cuts = cuts_by_beat_index.get(beat_idx)
        if not beat_cuts:
            continue  # entire beat consumed by a removal span

        # Zoom and the beat's own "reason" apply to the beat's entry cut --
        # a splice sub-cut already carries its own punch-in transform.scale
        # from build_cuts_from_plan, don't clobber it.
        entry_cut = beat_cuts[0]
        zoom_action = beat["zoom"]["action"]
        if "transform" not in entry_cut:
            if zoom_action == "zoom_in":
                entry_cut["transform"] = {"animation": "ken-burns-slow-zoom", "scale": 1.1}
            elif zoom_action == "zoom_out":
                entry_cut["transform"] = {"animation": "ken-burns-slow-zoom", "scale": 0.95}
            elif zoom_action == "static":
                entry_cut["transform"] = {"animation": "static", "scale": 1.0}
        entry_cut["reason"] = beat.get("spoken_text", "")[:50] + "..."

        match = broll_matches.get(beat_idx)
        if match:
            entry_cut["type"] = "text_card"
            entry_cut["backgroundVideo"] = match["path"]
            entry_cut["backgroundVideoStart"] = 0

        if beat["sound_effect"]["type"] != "none":
            sfx_file = sfx_map.get(beat["sound_effect"]["type"])
            if sfx_file:
                sfx_entries.append({
                    "asset_id": sfx_file,
                    "start_seconds": entry_cut["in_seconds"],
                    "volume": 0.8,
                })

    return {
        "version": "1.0",
        "cuts": cuts,
        # b-roll no longer routes through overlays[] -- see edit-director.md
        # §6b (its schema shape doesn't render through this composition).
        "overlays": [],
        "audio": {"sfx": sfx_entries},
        "render_runtime": "remotion",
        "composition_mode": "templated",
    }


class TestEditDecisionsHandBuilt:
    """Validate hand-built edit_decisions from fixture."""

    def test_hand_built_edit_decisions_validates_against_schema(
        self, footage_edit_plan_fixture, resource_inventory, edit_decisions_schema
    ):
        """Build edit_decisions from fixture and validate against schema."""
        edit_decisions = build_edit_decisions_from_fixture(
            footage_edit_plan_fixture, resource_inventory
        )

        # Should not raise jsonschema.ValidationError
        jsonschema.validate(edit_decisions, edit_decisions_schema)

    def test_hand_built_has_required_fields(
        self, footage_edit_plan_fixture, resource_inventory
    ):
        """Verify hand-built artifact has all required top-level fields."""
        edit_decisions = build_edit_decisions_from_fixture(
            footage_edit_plan_fixture, resource_inventory
        )

        required_fields = ["version", "cuts", "render_runtime"]
        for field in required_fields:
            assert field in edit_decisions, f"Missing required field: {field}"

    def test_hand_built_cuts_match_beats_count(
        self, footage_edit_plan_fixture, resource_inventory
    ):
        """Verify one cut per beat."""
        edit_decisions = build_edit_decisions_from_fixture(
            footage_edit_plan_fixture, resource_inventory
        )

        num_beats = len(footage_edit_plan_fixture["beats"])
        num_cuts = len(edit_decisions["cuts"])
        assert num_cuts == num_beats, \
            f"Expected {num_beats} cuts (one per beat), got {num_cuts}"

    def test_hand_built_cuts_have_time_boundaries(
        self, footage_edit_plan_fixture, resource_inventory
    ):
        """Verify each cut has valid in/out seconds."""
        edit_decisions = build_edit_decisions_from_fixture(
            footage_edit_plan_fixture, resource_inventory
        )

        for i, cut in enumerate(edit_decisions["cuts"]):
            assert "in_seconds" in cut, f"Cut {i} missing in_seconds"
            assert "out_seconds" in cut, f"Cut {i} missing out_seconds"
            assert cut["in_seconds"] >= 0, f"Cut {i} in_seconds must be >= 0"
            assert cut["out_seconds"] >= cut["in_seconds"], \
                f"Cut {i} out_seconds ({cut['out_seconds']}) must be >= in_seconds ({cut['in_seconds']})"

    def test_hand_built_never_uses_overlays_for_broll(
        self, footage_edit_plan_fixture, resource_inventory
    ):
        """overlays[]'s schema shape doesn't render through this composition
        (see edit-director.md §6b) -- b-roll must never land there."""
        edit_decisions = build_edit_decisions_from_fixture(
            footage_edit_plan_fixture, resource_inventory
        )
        assert edit_decisions["overlays"] == []

    def test_hand_built_broll_needed_beats_fall_back_cleanly_with_no_candidates(
        self, footage_edit_plan_fixture, resource_inventory
    ):
        """The fixture has no broll_candidates[] (predates this feature) --
        every b_roll.needed beat must fall back to a plain anchor cut, never
        a fabricated backgroundVideo."""
        assert "broll_candidates" not in footage_edit_plan_fixture  # confirms the scenario this test covers
        edit_decisions = build_edit_decisions_from_fixture(
            footage_edit_plan_fixture, resource_inventory
        )
        assert not any("backgroundVideo" in cut for cut in edit_decisions["cuts"])

    def test_hand_built_sfx_entries_only_for_non_none_types(
        self, footage_edit_plan_fixture, resource_inventory
    ):
        """Verify SFX entries only created for sound_effect.type != 'none'."""
        edit_decisions = build_edit_decisions_from_fixture(
            footage_edit_plan_fixture, resource_inventory
        )

        num_sfx_non_none = sum(
            1 for beat in footage_edit_plan_fixture["beats"]
            if beat["sound_effect"]["type"] != "none"
        )
        num_sfx_entries = len(edit_decisions["audio"]["sfx"])

        assert num_sfx_entries == num_sfx_non_none, \
            f"Expected {num_sfx_non_none} SFX entries (for sound_effect.type != 'none'), got {num_sfx_entries}"

    def test_hand_built_transitions_from_inventory(
        self, footage_edit_plan_fixture, resource_inventory
    ):
        """Verify transition_in values are from inventory."""
        edit_decisions = build_edit_decisions_from_fixture(
            footage_edit_plan_fixture, resource_inventory
        )

        inventory_transitions = {t["name"] for t in resource_inventory["transitions"]}

        for cut in edit_decisions["cuts"]:
            if "transition_in" in cut:
                trans = cut["transition_in"]
                assert trans in inventory_transitions, \
                    f"Cut {cut['id']} uses transition '{trans}' not in inventory"

    def test_hand_built_sfx_asset_ids_from_inventory(
        self, footage_edit_plan_fixture, resource_inventory
    ):
        """Verify SFX asset_ids are actual inventory file paths."""
        edit_decisions = build_edit_decisions_from_fixture(
            footage_edit_plan_fixture, resource_inventory
        )

        inventory_sfx_files = {sfx["file"] for sfx in resource_inventory["sound_effects"]}

        for sfx_entry in edit_decisions["audio"]["sfx"]:
            asset_id = sfx_entry["asset_id"]
            assert asset_id in inventory_sfx_files, \
                f"SFX asset_id '{asset_id}' not in inventory files: {inventory_sfx_files}"

    def test_hand_built_zoom_actions_translate_to_animations(
        self, footage_edit_plan_fixture, resource_inventory
    ):
        """Verify zoom_in/zoom_out are translated to animation values."""
        edit_decisions = build_edit_decisions_from_fixture(
            footage_edit_plan_fixture, resource_inventory
        )

        valid_animations = {"ken-burns-slow-zoom", "static"}

        for cut in edit_decisions["cuts"]:
            if "transform" in cut and "animation" in cut["transform"]:
                anim = cut["transform"]["animation"]
                assert anim in valid_animations, \
                    f"Cut {cut['id']} has invalid animation: {anim}"

    def test_hand_built_cuts_stay_output_contiguous(
        self, footage_edit_plan_fixture, resource_inventory
    ):
        """Every consecutive cut pair stays contiguous on the output
        timeline -- required for build-render-groups.ts transitions to ever
        have a chance to activate, and the property removal_spans ripple-
        shift must preserve."""
        edit_decisions = build_edit_decisions_from_fixture(
            footage_edit_plan_fixture, resource_inventory
        )
        cuts = edit_decisions["cuts"]
        for prev, cur in zip(cuts, cuts[1:]):
            assert prev["out_seconds"] == cur["in_seconds"], \
                f"Gap between {prev['id']} and {cur['id']}"

    def test_hand_built_proves_phase04_mapping_realizable(
        self, footage_edit_plan_fixture, resource_inventory, edit_decisions_schema
    ):
        """
        Final proof: hand-built edit_decisions validates against schema.

        This demonstrates that the Phase 04 field-translation table
        (footage_edit_plan → edit_decisions) is not just documented,
        but actually produces valid artifacts.
        """
        edit_decisions = build_edit_decisions_from_fixture(
            footage_edit_plan_fixture, resource_inventory
        )

        # This is the ultimate success criterion: validation passes
        try:
            jsonschema.validate(edit_decisions, edit_decisions_schema)
        except jsonschema.ValidationError as e:
            pytest.fail(
                f"Hand-built edit_decisions failed schema validation. "
                f"This proves Phase 04 mapping is NOT realizable. "
                f"Error: {e.message}"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
