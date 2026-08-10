"""Contract tests for cloud-render governance (phase 05).

Covers:
- `decision_log.schema.json` gained `render_location_selection` without breaking anything else.
- `render_report.schema.json` gained an optional `render_location` block, and stays backward
  compatible for reports that omit it entirely (local renders).
- Every fixture under `tests/fixtures/` and every `decision_log`/`render_report` artifact still on
  disk under `projects/*/artifacts/` validates against the updated schemas — an enum widening plus
  an optional field must not invalidate anything that validated before.
- `skills/core/cloud-render.md`'s announce template names every field the Phase 04 `dry_run()`
  contract returns, so the announce block and the tool payload cannot silently drift apart.
- `AGENT_GUIDE.md` names the `render_location_selection` category and links the skill.

See:
- plans/260806-1404-vastai-cloud-render/phase-05-skills-schema-decision-contract.md
- plans/260806-1404-vastai-cloud-render/phase-04-tools-registry-cost-tracker.md (dry_run() payload
  shape used as the source of truth for the announce-template grep test)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMAS_DIR = ROOT / "schemas" / "artifacts"
FIXTURES_DIR = ROOT / "tests" / "fixtures"
PROJECTS_DIR = ROOT / "projects"


def load_schema(name: str) -> dict:
    with open(SCHEMAS_DIR / f"{name}.schema.json", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# decision_log — enum widened
# ---------------------------------------------------------------------------

def test_decision_log_enum_has_render_location_selection():
    schema = load_schema("decision_log")
    category_enum = schema["properties"]["decisions"]["items"]["properties"]["category"]["enum"]
    assert "render_location_selection" in category_enum


def test_decision_log_entry_with_render_location_selection_validates():
    schema = load_schema("decision_log")
    artifact = {
        "version": "1.0",
        "project_id": "test-project",
        "decisions": [
            {
                "decision_id": "d-001",
                "stage": "compose",
                "category": "render_location_selection",
                "subject": "Render location for batch of 4 jobs",
                "options_considered": [
                    {
                        "option_id": "local",
                        "label": "Local machine",
                        "score": 0.4,
                        "reason": "Free, no data leaves the machine.",
                        "rejected_because": "Estimated render time exceeds the cloud crossover.",
                    },
                    {
                        "option_id": "cloud",
                        "label": "Vast.ai bid offer #40179084",
                        "score": 0.9,
                        "reason": "Clears the crossover; within ceilings.",
                    },
                ],
                "selected": "cloud",
                "reason": "User approved the announce block; offer within ceiling.",
                "user_visible": True,
                "user_approved": True,
            }
        ],
    }
    jsonschema.validate(artifact, schema)  # must not raise


def test_decision_log_entry_with_only_one_location_option_still_schema_valid():
    """Schema permits it (a single-item array is legal JSON Schema) — the "both options
    required" rule is a governance/reviewer contract, not a schema constraint. This test
    documents that boundary rather than asserting a false schema-level guarantee."""
    schema = load_schema("decision_log")
    artifact = {
        "version": "1.0",
        "project_id": "test-project",
        "decisions": [
            {
                "decision_id": "d-001",
                "stage": "compose",
                "category": "render_location_selection",
                "subject": "Render location",
                "options_considered": [
                    {"option_id": "cloud", "label": "Vast.ai", "score": 0.9, "reason": "fast"}
                ],
                "selected": "cloud",
                "reason": "test",
            }
        ],
    }
    jsonschema.validate(artifact, schema)  # schema-valid; reviewer.md flags this as CRITICAL


# ---------------------------------------------------------------------------
# render_report — optional render_location block
# ---------------------------------------------------------------------------

_MINIMAL_OUTPUT = {
    "path": "renders/final.mp4",
    "format": "mp4",
    "resolution": "1920x1080",
    "duration_seconds": 60,
}


def test_render_report_with_full_render_location_validates():
    schema = load_schema("render_report")
    artifact = {
        "version": "1.0",
        "outputs": [_MINIMAL_OUTPUT],
        "render_location": {
            "location": "cloud",
            "provider": "vastai",
            "instance_id": "40179084",
            "offer_id": "40179084",
            "pricing_mode": "bid",
            "dph_usd": 0.069,
            "rental_seconds": 1740,
            "actual_cost_usd": 0.033,
            "cpu_cores": 32,
            "batch_size": 4,
            "ledger_ref": "projects/cloud-render/rentals.jsonl",
        },
    }
    jsonschema.validate(artifact, schema)  # must not raise


def test_render_report_without_render_location_still_validates():
    """Backward compat: local renders (the overwhelming majority) never set this field."""
    schema = load_schema("render_report")
    artifact = {"version": "1.0", "outputs": [_MINIMAL_OUTPUT]}
    jsonschema.validate(artifact, schema)  # must not raise


def test_render_report_render_location_rejects_unknown_field():
    schema = load_schema("render_report")
    artifact = {
        "version": "1.0",
        "outputs": [_MINIMAL_OUTPUT],
        "render_location": {"location": "cloud", "provider": "vastai", "made_up_field": 1},
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(artifact, schema)


def test_render_report_render_location_rejects_missing_required():
    schema = load_schema("render_report")
    # "location" is required by the render_location sub-schema
    artifact = {
        "version": "1.0",
        "outputs": [_MINIMAL_OUTPUT],
        "render_location": {"provider": "vastai"},
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(artifact, schema)


# ---------------------------------------------------------------------------
# Every fixture/artifact on disk still validates (prove, don't assume)
# ---------------------------------------------------------------------------

def _find_artifacts(stem_prefix: str, shape_key: str) -> list[Path]:
    """Every JSON file under tests/fixtures/ or projects/*/artifacts/ whose filename starts
    with `stem_prefix` (covers versioned copies like `render_report_v5.json`) AND whose
    top-level shape actually looks like the canonical artifact (has `shape_key`).

    The shape check matters: this repo also has non-canonical debug dumps that happen to share
    the `render_report_*` filename prefix (raw `ToolResult` captures with `success`/`data`/
    `duration_seconds` at the top level, not the artifact itself) — those were never valid
    against this schema and are not this phase's concern. Only files actually shaped like the
    artifact are asserted against the schema here.
    """
    found: list[Path] = []
    for base in (FIXTURES_DIR, PROJECTS_DIR):
        if not base.exists():
            continue
        for path in base.rglob("*.json"):
            if not path.stem.startswith(stem_prefix):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and shape_key in data:
                found.append(path)
    return found


_RENDER_REPORT_PATHS = _find_artifacts("render_report", "outputs")
_DECISION_LOG_PATHS = _find_artifacts("decision_log", "decisions")


@pytest.mark.parametrize("path", _RENDER_REPORT_PATHS, ids=lambda p: str(p))
def test_every_render_report_on_disk_still_validates(path: Path):
    schema = load_schema("render_report")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    jsonschema.validate(data, schema)


@pytest.mark.parametrize("path", _DECISION_LOG_PATHS, ids=lambda p: str(p))
def test_every_decision_log_on_disk_still_validates(path: Path):
    schema = load_schema("decision_log")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    jsonschema.validate(data, schema)


def test_at_least_one_render_report_and_decision_log_were_actually_checked():
    """Guard against the parametrized tests above silently collecting zero cases (e.g. a repo
    layout change) and passing trivially."""
    assert _RENDER_REPORT_PATHS, "no render_report artifacts found on disk to check"
    assert _DECISION_LOG_PATHS, "no decision_log artifacts found on disk to check"


# ---------------------------------------------------------------------------
# skills/core/cloud-render.md announce template vs Phase 04 dry_run() payload
# ---------------------------------------------------------------------------

# Exact shape from phase-04-tools-registry-cost-tracker.md's dry_run() example — the accepted
# contract regardless of implementation order (phase 04 may not exist yet when this runs).
_DRY_RUN_PAYLOAD: dict[str, Any] = {
    "tool": "vast_cloud_render",
    "provider": "vastai",
    "would_execute": True,
    "offers": [
        {
            "offer_id": 40179084,
            "dph_usd": 0.0814,
            "cpu_cores": 32,
            "geolocation": "MX",
            "reliability": 0.98,
            "gpu_name": "RTX 4060 (unused)",
        }
    ],
    "recommended_offer_id": 40179084,
    "pricing_mode": "on-demand",
    "interruptible_alternative_dph_usd": 0.069,
    "kit_size_bytes": 812345,
    "jobs": 4,
    "estimated_render_minutes": 23.5,
    "estimated_overhead_minutes": 6,
    "estimated_cost_usd": 0.048,
    "cost_if_rendered_separately_usd": 0.19,
    "estimated_local_render_minutes": 44,
    "ceilings": {
        "max_dph_usd": 0.15,
        "max_total_usd_per_rental": 0.50,
        "max_runtime_minutes": 60,
    },
    "warnings": ["footage leaves this machine", "GPU in the offer is unused — render is CPU-bound"],
    "dry_run_ref": "dr_9f2a1c",
}


def _flatten_keys(payload: Any, keys: set[str]) -> None:
    """Collect every dict key anywhere in the payload (recursing into dicts and lists)."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            keys.add(key)
            _flatten_keys(value, keys)
    elif isinstance(payload, list):
        for item in payload:
            _flatten_keys(item, keys)


def test_cloud_render_skill_announce_template_names_every_dry_run_field():
    """Ties the announce template mechanically to the dry_run() contract: if a field is added
    or renamed in the tool, this test forces the skill (and its field-reference table) to be
    updated in lockstep, per the phase's risk mitigation."""
    body = (ROOT / "skills" / "core" / "cloud-render.md").read_text(encoding="utf-8")

    keys: set[str] = set()
    _flatten_keys(_DRY_RUN_PAYLOAD, keys)
    assert keys, "sanity: the dry_run payload fixture must not be empty"

    missing = sorted(key for key in keys if key not in body)
    assert not missing, (
        f"skills/core/cloud-render.md is missing dry_run() field name(s) {missing}. "
        f"Every key dry_run() returns must be named in the announce template or its field "
        f"reference table so the two cannot silently drift."
    )


def test_cloud_render_skill_states_no_autopilot():
    """Locked decision: no standing pre-approval, no unattended/overnight auto-flush, ever."""
    body = (ROOT / "skills" / "core" / "cloud-render.md").read_text(encoding="utf-8")
    assert "no autopilot" in body.lower() or "no standing pre-approval" in body.lower()
    assert "every rental" in body.lower()


def test_cloud_render_skill_states_bid_is_default():
    """Locked decision: bid/interruptible is the default pricing mode, not on-demand."""
    body = (ROOT / "skills" / "core" / "cloud-render.md").read_text(encoding="utf-8")
    assert "bid" in body.lower()
    assert "default" in body.lower()
    assert "0.069" in body  # measured bid baseline
    assert "0.0814" in body  # measured on-demand baseline


# ---------------------------------------------------------------------------
# AGENT_GUIDE.md carries the render-location contract
# ---------------------------------------------------------------------------

def test_agent_guide_names_render_location_selection_and_links_skill():
    guide = (ROOT / "AGENT_GUIDE.md").read_text(encoding="utf-8")
    assert "Render Location" in guide
    assert "render_location_selection" in guide
    assert "skills/core/cloud-render.md" in guide


# ---------------------------------------------------------------------------
# Vast.ai Layer 3 skill exists and covers the five verified gotchas
# ---------------------------------------------------------------------------

def test_vastai_layer3_skill_covers_all_five_gotchas():
    body = (ROOT / ".agents" / "skills" / "vastai" / "SKILL.md").read_text(encoding="utf-8")
    for token in (
        "Team API key",
        "no_auto_tmux",
        "ssh_direc ssh_proxy",
        "scp",
        "-y",
    ):
        assert token in body, f"vastai SKILL.md missing expected gotcha token: {token!r}"
