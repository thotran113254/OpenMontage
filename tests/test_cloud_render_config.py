"""Tests for lib/cloud_render/config.py -- 3-layer resolve + validate.

No network, no vastai import needed: config.py never touches the account.
"""

from __future__ import annotations

import json

import pytest

from lib.cloud_render.config import (
    BUILTIN_DEFAULTS,
    CloudRenderConfigError,
    global_defaults,
    resolve,
    sources_of,
    validate,
)


# ---------------------------------------------------------------------------
# global_defaults
# ---------------------------------------------------------------------------

def test_global_defaults_falls_back_to_builtins_when_file_missing(tmp_path):
    missing = tmp_path / "does-not-exist.json"
    assert global_defaults(missing) == BUILTIN_DEFAULTS


def test_global_defaults_falls_back_to_builtins_on_malformed_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    assert global_defaults(bad) == BUILTIN_DEFAULTS


def test_global_defaults_merges_committed_config_file(tmp_path):
    custom = tmp_path / "cloud-render.json"
    custom.write_text(json.dumps({"max_dph_usd": 0.20}), encoding="utf-8")
    merged = global_defaults(custom)
    assert merged["max_dph_usd"] == 0.20
    # untouched keys still come from BUILTIN_DEFAULTS
    assert merged["pricing_mode"] == BUILTIN_DEFAULTS["pricing_mode"]


def test_builtin_default_pricing_mode_is_bid():
    """Locked decision: cheaper+risk over safer+costlier -- bid, not on-demand."""
    assert BUILTIN_DEFAULTS["pricing_mode"] == "bid"


def test_builtin_default_disabled_by_default():
    assert BUILTIN_DEFAULTS["enabled"] is False


# ---------------------------------------------------------------------------
# resolve -- global -> project -> job
# ---------------------------------------------------------------------------

def test_resolve_with_no_overrides_returns_builtins(tmp_path):
    missing = tmp_path / "does-not-exist.json"
    resolved = resolve(global_path=missing)
    assert resolved["max_dph_usd"] == BUILTIN_DEFAULTS["max_dph_usd"]
    assert resolved["pricing_mode"] == "bid"


def test_resolve_project_overrides_global(tmp_path):
    missing = tmp_path / "does-not-exist.json"
    resolved = resolve(project={"max_dph_usd": 0.05}, global_path=missing)
    assert resolved["max_dph_usd"] == 0.05


def test_resolve_job_overrides_project(tmp_path):
    missing = tmp_path / "does-not-exist.json"
    resolved = resolve(project={"max_dph_usd": 0.05}, job={"max_dph_usd": 0.10}, global_path=missing)
    assert resolved["max_dph_usd"] == 0.10


def test_resolve_ignores_null_override(tmp_path):
    """A null override must not disable a ceiling -- see config._clean."""
    missing = tmp_path / "does-not-exist.json"
    resolved = resolve(project={"max_dph_usd": None}, global_path=missing)
    assert resolved["max_dph_usd"] == BUILTIN_DEFAULTS["max_dph_usd"]


def test_resolve_ignores_unknown_keys(tmp_path):
    missing = tmp_path / "does-not-exist.json"
    resolved = resolve(project={"totally_unknown_key": "x"}, global_path=missing)
    assert "totally_unknown_key" not in resolved


# ---------------------------------------------------------------------------
# validate -- fail loud, never default silently
# ---------------------------------------------------------------------------

def test_validate_rejects_zero_max_dph_usd():
    config = {**BUILTIN_DEFAULTS, "max_dph_usd": 0}
    with pytest.raises(CloudRenderConfigError):
        validate(config)


def test_validate_rejects_negative_max_dph_usd():
    config = {**BUILTIN_DEFAULTS, "max_dph_usd": -0.1}
    with pytest.raises(CloudRenderConfigError):
        validate(config)


@pytest.mark.parametrize("bad_runtime", [4, 241, -1])
def test_validate_rejects_out_of_range_max_runtime_minutes(bad_runtime):
    config = {**BUILTIN_DEFAULTS, "max_runtime_minutes": bad_runtime}
    with pytest.raises(CloudRenderConfigError):
        validate(config)


@pytest.mark.parametrize("good_runtime", [5, 60, 240])
def test_validate_accepts_boundary_max_runtime_minutes(good_runtime):
    config = {**BUILTIN_DEFAULTS, "max_runtime_minutes": good_runtime}
    validated = validate(config)
    assert validated["max_runtime_minutes"] == good_runtime


@pytest.mark.parametrize("bad_batch_runtime", [4, 481, -1])
def test_validate_rejects_out_of_range_max_batch_runtime_minutes(bad_batch_runtime):
    config = {**BUILTIN_DEFAULTS, "max_batch_runtime_minutes": bad_batch_runtime}
    with pytest.raises(CloudRenderConfigError):
        validate(config)


@pytest.mark.parametrize("good_batch_runtime", [5, 90, 480])
def test_validate_accepts_boundary_max_batch_runtime_minutes(good_batch_runtime):
    config = {**BUILTIN_DEFAULTS, "max_batch_runtime_minutes": good_batch_runtime}
    validated = validate(config)
    assert validated["max_batch_runtime_minutes"] == good_batch_runtime


def test_validate_rejects_unknown_pricing_mode():
    config = {**BUILTIN_DEFAULTS, "pricing_mode": "spot"}
    with pytest.raises(CloudRenderConfigError):
        validate(config)


@pytest.mark.parametrize("mode", ["on-demand", "bid"])
def test_validate_accepts_known_pricing_modes(mode):
    config = {**BUILTIN_DEFAULTS, "pricing_mode": mode}
    validated = validate(config)
    assert validated["pricing_mode"] == mode


def test_validate_rejects_disk_gb_below_10():
    config = {**BUILTIN_DEFAULTS, "disk_gb": 9}
    with pytest.raises(CloudRenderConfigError):
        validate(config)


def test_validate_accepts_disk_gb_at_10():
    config = {**BUILTIN_DEFAULTS, "disk_gb": 10}
    validated = validate(config)
    assert validated["disk_gb"] == 10


def test_resolve_raises_when_project_layer_pushes_bad_ceiling(tmp_path):
    """A bad ceiling from ANY layer must fail loud at resolve() time."""
    missing = tmp_path / "does-not-exist.json"
    with pytest.raises(CloudRenderConfigError):
        resolve(project={"max_dph_usd": -1}, global_path=missing)


# ---------------------------------------------------------------------------
# sources_of -- which layer a value came from
# ---------------------------------------------------------------------------

def test_sources_of_reports_global_when_no_overrides(tmp_path):
    missing = tmp_path / "does-not-exist.json"
    resolved = resolve(global_path=missing)
    origin = sources_of(resolved, global_path=missing)
    assert origin["max_dph_usd"] == "global"


def test_sources_of_reports_project_layer(tmp_path):
    missing = tmp_path / "does-not-exist.json"
    project = {"max_dph_usd": 0.05}
    resolved = resolve(project=project, global_path=missing)
    origin = sources_of(resolved, project=project, global_path=missing)
    assert origin["max_dph_usd"] == "project"


def test_sources_of_reports_job_layer_over_project(tmp_path):
    missing = tmp_path / "does-not-exist.json"
    project = {"max_dph_usd": 0.05}
    job = {"max_dph_usd": 0.10}
    resolved = resolve(project=project, job=job, global_path=missing)
    origin = sources_of(resolved, project=project, job=job, global_path=missing)
    assert origin["max_dph_usd"] == "job"


def test_resolve_matches_committed_config_file():
    """The real config/cloud-render.json must parse and validate cleanly."""
    resolved = resolve()
    assert resolved["enabled"] is False
    assert resolved["pricing_mode"] == "bid"
    assert resolved["max_dph_usd"] == 0.15
    assert resolved["max_total_usd_per_rental"] == 0.50
    assert resolved["max_runtime_minutes"] == 60
