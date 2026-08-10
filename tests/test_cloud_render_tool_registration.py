"""Phase 04 tests: `vast_cloud_render` / `cloud_render_queue` registration,
no-eager-import safety, execution refusals, and the cost-governance gate.

Zero network, zero real rentals: every test that would otherwise touch the
Vast.ai account either (a) exercises only the local validation path that
runs *before* any SDK call inside `VastCloudRender.execute()`, or (b)
monkeypatches the exact lazy-imported symbol that would call the SDK, so a
regression that starts calling it earlier fails loudly instead of silently
spending money.

See:
- plans/260806-1404-vastai-cloud-render/phase-04-tools-registry-cost-tracker.md
- tests/test_footage_edit_analyzer_registration.py (registration-test pattern)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from lib.cloud_render import dry_run_store
from lib.config_model import BudgetMode
from tools.base_tool import ToolStatus
from tools.cost_tracker import ApprovalRequiredError, CostTracker
from tools.tool_registry import registry
from tools.video.cloud_render_queue import CloudRenderQueue
from tools.video.vast_cloud_render import VastCloudRender

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def render_tool() -> VastCloudRender:
    return VastCloudRender()


@pytest.fixture
def queue_tool() -> CloudRenderQueue:
    return CloudRenderQueue()


@pytest.fixture
def isolated_dry_run_refs(tmp_path, monkeypatch):
    """Redirect `dry_run_store`'s state file into `tmp_path` -- never touch
    the real `projects/cloud-render/dry_runs.json`."""
    state_dir = tmp_path / "cloud-render"
    monkeypatch.setattr(dry_run_store, "STATE_DIR", state_dir)
    monkeypatch.setattr(dry_run_store, "REFS_PATH", state_dir / "dry_runs.json")
    return state_dir


@pytest.fixture
def no_sdk_calls(monkeypatch):
    """Make any attempt to actually touch the Vast.ai SDK/account raise
    loudly instead of silently spending money or hitting the network."""
    def _boom(*_args, **_kwargs):
        raise AssertionError("SDK/network call reached that a refusal should have prevented")

    from lib.cloud_render import remote, vast_client
    from lib.cloud_render import queue as cloud_queue

    monkeypatch.setattr(vast_client, "search", _boom)
    monkeypatch.setattr(vast_client, "rent", _boom)
    monkeypatch.setattr(remote, "render_now", _boom)
    monkeypatch.setattr(cloud_queue, "flush", _boom)


# ---------------------------------------------------------------------------
# Basic identity / contract fields
# ---------------------------------------------------------------------------

class TestToolIdentity:
    def test_vast_cloud_render_identity(self, render_tool):
        assert render_tool.name == "vast_cloud_render"
        assert render_tool.capability == "cloud_render"
        assert render_tool.provider == "vastai"

    def test_cloud_render_queue_identity(self, queue_tool):
        assert queue_tool.name == "cloud_render_queue"
        assert queue_tool.capability == "cloud_render"
        assert queue_tool.provider == "openmontage"

    def test_vast_cloud_render_side_effects_disclose_spend_and_upload(self, render_tool):
        # Reviewers/agents must be able to discover these two facts from the
        # registry alone (see phase doc's Security section).
        assert "spends_money" in render_tool.side_effects
        assert "uploads_footage_offmachine" in render_tool.side_effects

    def test_vast_cloud_render_dependencies_use_understood_prefixes(self, render_tool):
        # The talking_head_autoedit.py:41 bug: a "binary:" prefix falls
        # through check_dependencies() silently. Every dep here must use a
        # prefix check_dependencies() actually understands.
        for dep in render_tool.dependencies:
            assert dep.split(":", 1)[0] in ("cmd", "env", "python"), (
                f"dependency {dep!r} uses an unrecognized prefix -- "
                f"check_dependencies() would silently ignore it")
        assert "python:vastai" in render_tool.dependencies
        assert "cmd:ssh" in render_tool.dependencies
        assert "cmd:scp" in render_tool.dependencies

    def test_vast_cloud_render_install_instructions_never_leak_a_key(self, render_tool):
        info = render_tool.get_info()
        # get_info() may show ceilings/config, never key material.
        assert "cloud_render_config" in info
        blob = str(info)
        assert "VAST_API_KEY=" not in blob


# ---------------------------------------------------------------------------
# Registry discovery + capability catalog / provider menu
# ---------------------------------------------------------------------------

class TestRegistryDiscovery:
    def test_registry_discovers_both_tools(self):
        registry.discover()
        names = {t.name for t in registry.get_by_capability("cloud_render")}
        assert {"vast_cloud_render", "cloud_render_queue"} <= names

    def test_capability_catalog_has_cloud_render_family(self):
        catalog = registry.capability_catalog()
        assert "cloud_render" in catalog
        names = {entry["name"] for entry in catalog["cloud_render"]}
        assert {"vast_cloud_render", "cloud_render_queue"} <= names

    def test_provider_menu_summary_has_cloud_render_entry(self):
        summary = registry.provider_menu_summary()
        entry = next((c for c in summary["capabilities"] if c["capability"] == "cloud_render"), None)
        assert entry is not None, "cloud_render missing from provider_menu_summary()['capabilities']"
        assert entry["total"] == 2
        # cloud_render_queue (LOCAL, no deps) is always AVAILABLE; vast_cloud_render
        # is DEGRADED while config ships enabled:false -- so configured counts the
        # queue tool only, and vastai shows up as an unavailable provider.
        assert entry["configured"] >= 1
        assert "openmontage" in entry["available_providers"]

    def test_registry_discover_leaves_vastai_unimported_in_a_fresh_process(self):
        """Authoritative version of the success criterion's literal
        `python -c "...registry.discover()..."; "vastai" not in sys.modules"`
        check -- run in a brand-new process so no other test's import state
        can leak in and mask a regression."""
        script = (
            "import sys\n"
            "from tools.tool_registry import registry\n"
            "registry.discover()\n"
            "print('vastai' not in sys.modules)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT,
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip().splitlines()[-1] == "True", (
            f"stdout={result.stdout!r} stderr={result.stderr!r}")

    def test_neither_tool_module_imports_vastai_at_module_scope(self):
        """Belt-and-suspenders in-process check: importing either tool
        module must not add `vastai` to `sys.modules` as a side effect."""
        import importlib

        for module_name in ("tools.video.vast_cloud_render", "tools.video.cloud_render_queue"):
            was_present = "vastai" in sys.modules
            importlib.reload(sys.modules[module_name])
            assert ("vastai" in sys.modules) == was_present, (
                f"importing {module_name} changed whether 'vastai' is in sys.modules")


# ---------------------------------------------------------------------------
# get_status() -- UNAVAILABLE without the SDK, UNAVAILABLE without ssh/scp,
# DEGRADED when the SDK is present but config.enabled is False.
# ---------------------------------------------------------------------------

class TestAvailability:
    def test_unavailable_when_vastai_genuinely_missing(self, render_tool, monkeypatch):
        # sys.modules[name] = None forces the next `import name` to raise
        # ImportError, simulating "genuinely uninstalled" without actually
        # uninstalling the package from this dev environment.
        monkeypatch.setitem(sys.modules, "vastai", None)
        assert render_tool.get_status() == ToolStatus.UNAVAILABLE

    def test_unavailable_install_instructions_are_actionable(self, render_tool, monkeypatch):
        monkeypatch.setitem(sys.modules, "vastai", None)
        assert render_tool.get_status() == ToolStatus.UNAVAILABLE
        assert "pip install vastai" in render_tool.install_instructions

    def test_unavailable_when_ssh_missing_from_path(self, render_tool, monkeypatch):
        """The exact bug this phase must not repeat (talking_head_autoedit.py:41's
        'binary:ffmpeg' silently ignored): confirm a *real* missing `cmd:` dep
        actually flips status to UNAVAILABLE."""
        monkeypatch.setattr("shutil.which", lambda _name: None)
        assert render_tool.get_status() == ToolStatus.UNAVAILABLE

    def test_degraded_when_sdk_present_but_config_disabled(self, render_tool):
        # config/cloud-render.json ships enabled:false (locked decision) --
        # this is the real, unmocked config on disk.
        status = render_tool.get_status()
        assert status in (ToolStatus.DEGRADED, ToolStatus.UNAVAILABLE)
        info = render_tool.get_info()
        if status == ToolStatus.DEGRADED:
            assert info["cloud_render_config"]["enabled"] is False
            assert info["cloud_render_config"]["reason"]

    def test_cloud_render_queue_available_without_vastai(self, queue_tool, monkeypatch):
        monkeypatch.setitem(sys.modules, "vastai", None)
        assert queue_tool.get_status() == ToolStatus.AVAILABLE


# ---------------------------------------------------------------------------
# execute() refusals -- every one of these must cost zero SDK calls.
# ---------------------------------------------------------------------------

class TestExecuteRefusals:
    def test_refuses_without_offer_id(self, render_tool, no_sdk_calls):
        result = render_tool.execute({"mode": "render_now", "job_id": "x", "max_total_usd": 0.1})
        assert result.success is False
        assert "offer_id" in result.error

    def test_refuses_without_max_total_usd(self, render_tool, no_sdk_calls):
        result = render_tool.execute({"mode": "render_now", "job_id": "x", "offer_id": 1})
        assert result.success is False
        assert "max_total_usd" in result.error

    def test_refuses_render_now_without_job_id(self, render_tool, no_sdk_calls):
        result = render_tool.execute({"mode": "render_now", "offer_id": 1, "max_total_usd": 0.1})
        assert result.success is False
        assert "job_id" in result.error

    def test_refuses_flush_without_job_ids(self, render_tool, no_sdk_calls):
        result = render_tool.execute({"mode": "flush", "offer_id": 1, "max_total_usd": 0.1})
        assert result.success is False
        assert "job_ids" in result.error

    def test_refuses_when_config_disabled(self, render_tool, no_sdk_calls):
        # Real config/cloud-render.json ships enabled:false.
        result = render_tool.execute({
            "mode": "render_now", "job_id": "x", "offer_id": 1, "max_total_usd": 0.1,
        })
        assert result.success is False
        assert "enabled: false" in result.error or "tat" in result.error.lower()

    def test_refuses_offer_id_absent_from_dry_run_ref(
        self, render_tool, no_sdk_calls, isolated_dry_run_refs, monkeypatch,
    ):
        # Force enabled:true via a job-config override so the ref check is
        # the thing actually under test, not the enabled gate above it.
        monkeypatch.setattr(
            render_tool, "_resolve_config",
            lambda inputs: _disabled_config_with_enabled_true())
        ref = dry_run_store.create([111, 222], {"max_dph_usd": 0.15,
                                                "max_total_usd_per_rental": 0.5,
                                                "max_runtime_minutes": 60})
        result = render_tool.execute({
            "mode": "render_now", "job_id": "x", "offer_id": 999,
            "max_total_usd": 0.1, "dry_run_ref": ref,
        })
        assert result.success is False
        assert "999" in result.error

    def test_refuses_missing_dry_run_ref(self, render_tool, no_sdk_calls, monkeypatch):
        monkeypatch.setattr(
            render_tool, "_resolve_config",
            lambda inputs: _disabled_config_with_enabled_true())
        result = render_tool.execute({
            "mode": "render_now", "job_id": "x", "offer_id": 1, "max_total_usd": 0.1,
        })
        assert result.success is False
        assert "dry_run_ref" in result.error

    def test_refuses_max_total_usd_above_ceiling(self, render_tool, no_sdk_calls, monkeypatch):
        monkeypatch.setattr(
            render_tool, "_resolve_config",
            lambda inputs: _disabled_config_with_enabled_true())
        result = render_tool.execute({
            "mode": "render_now", "job_id": "x", "offer_id": 1,
            "max_total_usd": 999.0, "dry_run_ref": "irrelevant",
        })
        assert result.success is False
        assert "ceiling" in result.error.lower() or "vuot" in result.error.lower()

    def test_invalid_mode_refuses(self, render_tool, no_sdk_calls):
        result = render_tool.execute({"mode": "nonsense"})
        assert result.success is False


def _disabled_config_with_enabled_true() -> dict:
    from lib.cloud_render.config import BUILTIN_DEFAULTS
    return {**BUILTIN_DEFAULTS, "enabled": True}


# ---------------------------------------------------------------------------
# cloud_render_queue: works fully offline, without vastai, without network.
# ---------------------------------------------------------------------------

class TestCloudRenderQueueTool:
    @pytest.fixture
    def isolated_queue(self, tmp_path, monkeypatch):
        from lib.cloud_render import queue as cloud_queue

        queue_path = tmp_path / "batch-queue.json"
        lock_path = tmp_path / "batch-queue.lock"
        monkeypatch.setattr(cloud_queue, "STATE_DIR", tmp_path)
        monkeypatch.setattr(cloud_queue, "QUEUE_PATH", queue_path)
        monkeypatch.setattr(cloud_queue, "LOCK_PATH", lock_path)

        def fake_find_job(job_id):
            if job_id.startswith("existing-"):
                return object()
            raise FileNotFoundError(job_id)

        monkeypatch.setattr(cloud_queue, "find_job", fake_find_job)
        return queue_path

    def test_enqueue_list_remove_clear_work_without_vastai(
        self, queue_tool, isolated_queue, monkeypatch,
    ):
        monkeypatch.setitem(sys.modules, "vastai", None)

        enq = queue_tool.execute({
            "operation": "enqueue", "job_id": "existing-1", "version": 1,
            "estimated_render_seconds": 90.0, "duration_seconds": 47.0, "note": "take 2",
        })
        assert enq.success is True
        assert enq.data["entry"]["job_id"] == "existing-1"

        listed = queue_tool.execute({"operation": "list"})
        assert listed.success is True
        assert [e["job_id"] for e in listed.data["entries"]] == ["existing-1"]

        removed = queue_tool.execute({"operation": "remove", "job_id": "existing-1"})
        assert removed.success is True
        assert removed.data["removed"] is True

        cleared = queue_tool.execute({"operation": "clear"})
        assert cleared.success is True

    def test_flush_check_works_without_vastai(self, queue_tool, isolated_queue, monkeypatch):
        monkeypatch.setitem(sys.modules, "vastai", None)
        result = queue_tool.execute({"operation": "flush_check"})
        assert result.success is True
        assert "job_count" in result.data
        assert result.data["job_count"] == 0

    def test_reap_delegates_to_ledger_reap(self, queue_tool, monkeypatch):
        """`reap` is the one operation that legitimately needs the account
        (it reconciles local vs remote state) -- proven here by mocking
        `ledger.reap` itself rather than hitting the network."""
        from lib.cloud_render import ledger

        calls: list[bool] = []

        def fake_reap(*, dry_run=False):
            calls.append(dry_run)
            return ledger.ReapReport()

        monkeypatch.setattr(ledger, "reap", fake_reap)
        result = queue_tool.execute({"operation": "reap", "dry_run": True})
        assert result.success is True
        assert calls == [True]

    def test_unknown_operation_refuses(self, queue_tool):
        result = queue_tool.execute({"operation": "rent_directly"})
        assert result.success is False


# ---------------------------------------------------------------------------
# dry_run_store: the mechanical "no unilateral substitution" primitive.
# ---------------------------------------------------------------------------

class TestDryRunStore:
    def test_create_then_resolve_round_trips(self, isolated_dry_run_refs):
        ref = dry_run_store.create([111, 222], {"max_dph_usd": 0.15})
        record = dry_run_store.resolve(ref, 111)
        assert record["offer_ids"] == [111, 222]

    def test_resolve_rejects_unseen_offer_id(self, isolated_dry_run_refs):
        ref = dry_run_store.create([111], {"max_dph_usd": 0.15})
        with pytest.raises(dry_run_store.DryRunRefError):
            dry_run_store.resolve(ref, 999)

    def test_resolve_rejects_missing_ref(self, isolated_dry_run_refs):
        with pytest.raises(dry_run_store.DryRunRefError):
            dry_run_store.resolve(None, 111)
        with pytest.raises(dry_run_store.DryRunRefError):
            dry_run_store.resolve("dr_doesnotexist", 111)

    def test_resolve_rejects_expired_ref(self, isolated_dry_run_refs):
        ref = dry_run_store.create([111], {"max_dph_usd": 0.15}, ttl_seconds=1.0, now=1_000.0)
        with pytest.raises(dry_run_store.DryRunRefError):
            dry_run_store.resolve(ref, 111, now=1_002.0)


# ---------------------------------------------------------------------------
# Cost governance: the new-paid-tool approval gate (cost_tracker.py, unmodified)
# ---------------------------------------------------------------------------

class TestCostGovernance:
    def test_reserve_raises_approval_required_using_real_config_yaml_defaults(self):
        raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        budget_cfg = raw["budget"]
        assert budget_cfg["require_approval_for_new_paid_tool"] is True  # the gate this phase relies on

        tracker = CostTracker(
            budget_total_usd=budget_cfg["total_usd"],
            reserve_pct=budget_cfg["reserve_pct"],
            single_action_approval_usd=budget_cfg["single_action_approval_usd"],
            require_approval_for_new_paid_tool=budget_cfg["require_approval_for_new_paid_tool"],
            mode=BudgetMode(budget_cfg["mode"]),
        )
        entry_id = tracker.estimate("vast_cloud_render", "rental", 0.05)
        with pytest.raises(ApprovalRequiredError):
            tracker.reserve(entry_id)

        tracker.approve_tool("vast_cloud_render")
        entry_id2 = tracker.estimate("vast_cloud_render", "rental", 0.05)
        tracker.reserve(entry_id2)  # no longer raises once approved
        assert tracker.budget_reserved_usd == pytest.approx(0.05)

    def test_estimate_cost_and_runtime_are_non_negative_with_no_job(self, render_tool):
        assert render_tool.estimate_cost({}) >= 0.0
        assert render_tool.estimate_runtime({}) >= 0.0

    def test_cloud_render_queue_estimate_cost_is_zero(self, queue_tool):
        assert queue_tool.estimate_cost({}) == 0.0
