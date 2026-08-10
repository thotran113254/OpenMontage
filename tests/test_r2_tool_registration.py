"""Registry-contract tests for the `cloudflare_r2` tool -- discovery,
capability/provider, dependency-prefix guard, UNAVAILABLE without boto3, and
the locked "shipped config is enabled:false" decision guard.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tools.base_tool import ToolStatus
from tools.storage.cloudflare_r2 import CloudflareR2
from tools.tool_registry import registry

ROOT = Path(__file__).resolve().parent.parent


class TestRegistryDiscovery:
    def test_registry_discovers_cloudflare_r2(self):
        registry.discover()
        assert registry.get("cloudflare_r2") is not None

    def test_get_by_capability_returns_exactly_this_tool(self):
        registry.discover()
        tools = registry.get_by_capability("object_storage")
        assert [t.name for t in tools] == ["cloudflare_r2"]

    def test_capability_catalog_includes_object_storage(self, monkeypatch):
        # An isolated registry with only this tool registered -- confirms the
        # new capability family renders correctly in the catalog/menu-building
        # code without paying the global registry's full get_status() sweep
        # across every OTHER tool (~25s on this repo; unrelated to R2 -- a
        # handful of unrelated tools' own get_status() checks are what's slow).
        from tools.tool_registry import ToolRegistry

        fresh = ToolRegistry()
        fresh.register(CloudflareR2())
        monkeypatch.setattr(fresh, "ensure_discovered", lambda *a, **k: None)

        catalog = fresh.capability_catalog()
        assert "object_storage" in catalog
        assert {entry["name"] for entry in catalog["object_storage"]} == {"cloudflare_r2"}

        summary = fresh.provider_menu_summary()
        entry = next((c for c in summary["capabilities"] if c["capability"] == "object_storage"), None)
        assert entry is not None


class TestDependencyPrefixes:
    def test_every_dependency_uses_a_prefix_check_dependencies_understands(self):
        tool = CloudflareR2()
        for dep in tool.dependencies:
            assert dep.split(":", 1)[0] in ("cmd", "env", "python"), (
                f"dependency {dep!r} uses an unrecognized prefix -- "
                f"check_dependencies() would silently ignore it")

    def test_python_boto3_and_credential_env_vars_are_declared(self):
        tool = CloudflareR2()
        assert "python:boto3" in tool.dependencies
        assert "env:CLOUDFLARE_R2_ACCESS_KEY_ID" in tool.dependencies
        assert "env:CLOUDFLARE_R2_SECRET_ACCESS_KEY" in tool.dependencies

    def test_bucket_is_deliberately_not_a_dependency(self):
        """Bucket can come from config/r2-storage.json instead of env --
        making it a hard env dependency would falsely report UNAVAILABLE for
        a user who only set it in the JSON."""
        tool = CloudflareR2()
        assert not any("BUCKET" in dep for dep in tool.dependencies)


class TestAvailabilityWithoutBoto3:
    def test_unavailable_when_boto3_genuinely_missing(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "boto3", None)
        tool = CloudflareR2()
        assert tool.get_status() == ToolStatus.UNAVAILABLE

    def test_discovery_survives_boto3_absent(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "boto3", None)
        registry.discover()  # must not raise
        tool = registry.get("cloudflare_r2")
        assert tool is not None
        assert tool.get_status() == ToolStatus.UNAVAILABLE

    def test_missing_secret_key_names_it_in_the_dependency_error_text(self, monkeypatch):
        monkeypatch.delenv("CLOUDFLARE_R2_SECRET_ACCESS_KEY", raising=False)
        tool = CloudflareR2()
        assert tool.get_status() == ToolStatus.UNAVAILABLE


class TestNoTopLevelBoto3Import:
    def test_importing_the_tool_module_does_not_import_boto3(self, monkeypatch):
        import importlib

        was_present = "boto3" in sys.modules
        importlib.reload(sys.modules["tools.storage.cloudflare_r2"])
        assert ("boto3" in sys.modules) == was_present


class TestExecuteEnabledGate:
    def test_upload_with_enabled_false_and_no_force_names_the_config_file(self):
        tool = CloudflareR2()
        result = tool.execute({"action": "upload", "local_path": "x", "remote_key": "y"})
        assert result.success is False
        assert "config/r2-storage.json" in result.error


class TestShippedConfigLockedDecision:
    def test_committed_config_ships_enabled_false(self):
        """Locked decision (plan Q-series): R2 storage is opt-in. A future
        contributor flipping this "for convenience" must fail a test, not
        silently ship auto-uploading everyone's footage."""
        config = json.loads((ROOT / "config" / "r2-storage.json").read_text(encoding="utf-8"))
        assert config["enabled"] is False
        assert config["auto_sync"] is False
        assert config["sync_after_stage"] is False

    def test_committed_config_has_no_credential_shaped_keys(self):
        import re

        config = json.loads((ROOT / "config" / "r2-storage.json").read_text(encoding="utf-8"))
        pattern = re.compile(r"(secret|access[_-]?key|token|password)", re.IGNORECASE)
        offending = [k for k in config if pattern.search(k)]
        assert offending == []


class TestNoSecretLeakage:
    def test_no_hardcoded_secret_value_in_the_tool_module(self):
        """The env var NAME `CLOUDFLARE_R2_SECRET_ACCESS_KEY` legitimately
        appears in `dependencies` (required by `check_dependencies()`) --
        that is a declaration, not a leak. What must never appear is an
        actual credential VALUE, e.g. a hardcoded AWS-style access key id."""
        source = (ROOT / "tools" / "storage" / "cloudflare_r2.py").read_text(encoding="utf-8")
        assert "AKIA" not in source
