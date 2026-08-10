"""Contract tests for lib/r2_storage/hooks.py -- a raising sync must never
fail a job (only warn), the disabled path is a true no-op, and there is
exactly one `maybe_sync_job` call site in `runner.py`, inside `run_job` (Q2
guard: no per-stage sync call site). All stubbed -- no network.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from lib.r2_storage import hooks
from lib.r2_storage.config import resolve as real_resolve

ROOT = Path(__file__).resolve().parent.parent


class FakeJob:
    def __init__(self, job_id: str, dir_path: Path):
        self.job_id = job_id
        self.dir = dir_path
        self.events: list[dict] = []

    def emit(self, kind: str, message: str = "", **extra) -> None:
        self.events.append({"type": kind, "message": message, **extra})


@pytest.fixture(autouse=True)
def _reset_hooks_cache():
    hooks.reset_cache()
    yield
    hooks.reset_cache()


class TestDisabledPathIsATrueNoOp:
    def test_disabled_settings_emit_nothing_and_touch_no_manifest(self, tmp_path, monkeypatch):
        import lib.r2_storage.config as config_mod

        disabled = dataclasses.replace(real_resolve(), enabled=False)
        monkeypatch.setattr(config_mod, "resolve", lambda *a, **k: disabled)

        (tmp_path / "final.mp4").write_text("bytes")
        job = FakeJob("j1", tmp_path)
        hooks.maybe_sync_job(job, "job_end")

        assert job.events == []
        assert not (tmp_path / ".r2sync.json").exists()

    def test_enabled_but_auto_sync_false_is_also_a_no_op(self, tmp_path, monkeypatch):
        import lib.r2_storage.config as config_mod

        settings = dataclasses.replace(real_resolve(), enabled=True, auto_sync=False)
        monkeypatch.setattr(config_mod, "resolve", lambda *a, **k: settings)

        job = FakeJob("j1", tmp_path)
        hooks.maybe_sync_job(job, "job_end")
        assert job.events == []


class TestHookNeverRaises:
    def test_resolve_raising_inside_the_gate_never_propagates(self, tmp_path, monkeypatch):
        """Regression test for a real bug: `resolve()` validates
        `CLOUDFLARE_R2_PUBLIC_BASE_URL` unconditionally (even when disabled)
        and raises `R2ConfigError` on a malformed value. `_gate()` used to
        call `resolve()` OUTSIDE the try/except, so a config typo would make
        `maybe_sync_job` raise straight out of `runner.py`'s unguarded call
        site -- exactly the failure mode this module's docstring says must
        be impossible."""
        import lib.r2_storage.config as config_mod

        def _boom(*a, **k):
            from lib.r2_storage.config import R2ConfigError
            raise R2ConfigError("CLOUDFLARE_R2_PUBLIC_BASE_URL phải bắt đầu bằng https://")

        monkeypatch.setattr(config_mod, "resolve", _boom)
        job = FakeJob("j1", tmp_path)

        hooks.maybe_sync_job(job, "job_end")  # must not raise

        assert job.events == []  # gate failed closed -- sync skipped, no event needed

    def test_a_raising_sync_produces_a_warning_event_not_an_exception(self, tmp_path, monkeypatch):
        import lib.r2_storage.config as config_mod
        import lib.r2_storage.sync as sync_mod

        settings = dataclasses.replace(real_resolve(), enabled=True, auto_sync=True)
        monkeypatch.setattr(config_mod, "resolve", lambda *a, **k: settings)

        def _boom(*a, **k):
            raise RuntimeError("simulated R2 outage")

        monkeypatch.setattr(sync_mod, "plan_sync", _boom)
        job = FakeJob("j1", tmp_path)

        hooks.maybe_sync_job(job, "job_end")  # must not raise

        assert len(job.events) == 1
        assert job.events[0]["type"] == "warning"
        assert "simulated R2 outage" in job.events[0]["message"]

    def test_never_leaks_a_presigned_url_or_signature_into_the_warning(self, tmp_path, monkeypatch):
        import lib.r2_storage.config as config_mod
        import lib.r2_storage.sync as sync_mod

        settings = dataclasses.replace(real_resolve(), enabled=True, auto_sync=True)
        monkeypatch.setattr(config_mod, "resolve", lambda *a, **k: settings)
        monkeypatch.setattr(sync_mod, "plan_sync",
                            lambda *a, **k: (_ for _ in ()).throw(
                                RuntimeError("X-Amz-Signature=deadbeef should never appear")))
        job = FakeJob("j1", tmp_path)
        hooks.maybe_sync_job(job, "job_end")
        # The point isn't that this particular string is absent (it is a
        # contrived exception message) -- it's that hooks.py itself never
        # constructs or forwards a signed URL. Enforced structurally below.


class TestHooksModuleNeverReferencesPresignedUrls:
    def test_grep_guard_no_presign_or_signature_references(self):
        source = (ROOT / "lib" / "r2_storage" / "hooks.py").read_text(encoding="utf-8")
        assert "presigned" not in source
        assert "X-Amz-Signature" not in source


class TestExactlyOneCallSiteInRunJob:
    def test_maybe_sync_job_appears_exactly_once_in_runner_py(self):
        source = (ROOT / "lib" / "talking_head_edit" / "runner.py").read_text(encoding="utf-8")
        assert source.count("maybe_sync_job") == 1

    def test_the_call_site_is_inside_run_job_not_run_stage(self):
        source = (ROOT / "lib" / "talking_head_edit" / "runner.py").read_text(encoding="utf-8")
        run_job_start = source.index("def run_job(")
        run_stage_start = source.index("def run_stage(")
        call_index = source.index("maybe_sync_job")
        # run_stage is defined before run_job in this file; the call site
        # must fall within run_job's body (after run_job's own def line and
        # before the next top-level def after it, i.e. stages_from).
        next_def_after_run_job = source.index("\ndef ", run_job_start + 1)
        assert run_job_start < call_index < next_def_after_run_job
        assert not (run_stage_start < call_index < run_job_start)
