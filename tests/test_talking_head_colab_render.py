"""Colab render orchestration, with the browser and R2 replaced by fakes.

What must hold whatever happens on Colab: the runtime is always released
(an idle TPU burns compute units), temp R2 keys are always deleted, a failure
says which step failed, and nothing but a verified MP4 becomes final.mp4.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from lib.cloud_render import colab


class FakeBrowser:
    def __init__(self, fail_at: str | None = None):
        self.calls: list[str] = []
        self.fail_at = fail_at
        self.cell = ""

    def _step(self, name):
        self.calls.append(name)
        if name == self.fail_at:
            raise colab.ColabBrowserError(f"{name} hỏng")

    def ensure_chrome(self):
        self._step("chrome")

    def ensure_logged_in(self):
        self._step("login")

    def open_notebook(self, url):
        self._step("notebook")
        return "https://colab.research.google.com/drive/abc"

    def set_runtime(self, label):
        self._step(f"runtime:{label}")

    def connect(self):
        self._step("connect")
        return "Connected (TPU) RAM 172 GB"

    def run_cell(self, code):
        self._step("run")
        self.cell = code

    def unassign(self):
        self.calls.append("unassign")
        return True


class FakeR2:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.deleted: list[str] = []
        self.texts: dict[str, str] = {}

    def get_url(self, key, ttl, settings=None):
        return f"https://r2.test/{key}?sig=get"

    def put_url(self, key, ttl, settings=None):
        return f"https://r2.test/{key}?sig=put"

    def put_text(self, key, text, settings=None):
        self.texts[key] = text

    def read_text(self, key, settings=None):
        return json.dumps(self.statuses.pop(0)) if self.statuses else None

    def delete_object(self, key, settings=None):
        self.deleted.append(key)


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    """A job and every heavy dependency replaced; returns (job, r2, finalized)."""
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    props = job_dir / "props_v1.json"
    props.write_text(json.dumps({"durationSeconds": 12.0}), encoding="utf-8")
    events: list[tuple] = []
    job = SimpleNamespace(job_id="j1", dir=job_dir, final_path=job_dir / "final.mp4",
                          props_path=lambda v: props, emit=lambda *a, **k: events.append(a))
    job.events = events

    kit_dir = tmp_path / "kit"
    kit_dir.mkdir()
    manifest = SimpleNamespace(kit_dir=kit_dir, kit_hash="h1")
    monkeypatch.setattr(colab.kit, "build_composer_kit", lambda: manifest)
    monkeypatch.setattr(colab.kit, "build_job_kit", lambda job, version: manifest)
    monkeypatch.setattr(colab.kit, "cleanup_kit", lambda m: None)
    monkeypatch.setattr(colab.transfer, "push_kit", lambda *a, **k: ("k", 0, False))
    monkeypatch.setattr(colab, "stage_assets", lambda job, props, video=None: None)
    monkeypatch.setattr(colab, "deliverable_video", lambda job, version, options: None)
    monkeypatch.setattr(colab, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(colab, "LOCK_PATH", tmp_path / "lock")
    monkeypatch.setattr(colab, "POLL_SECONDS", 0)
    finalized: list[str] = []

    def fake_finalize(job, job_id, staging_key, work_dir, expected, *, settings, started):
        finalized.append(staging_key)
        return SimpleNamespace(size_bytes=5_000_000, duration_seconds=12.0, wall_seconds=90.0)

    monkeypatch.setattr(colab, "finalize_output", fake_finalize)
    return job, finalized, monkeypatch


def _patch_r2(monkeypatch, r2):
    import lib.r2_storage.presign as presign
    for name in ("get_url", "put_url", "put_text", "read_text", "delete_object"):
        monkeypatch.setattr(presign, name, getattr(r2, name))


CONFIG = {"enabled": True, "runtime": "v6e-1 TPU", "max_runtime_minutes": 60,
          "stall_minutes": 6, "compute_units_per_hour": 4.08}


def test_a_render_runs_on_the_tpu_and_releases_the_runtime(wired):
    job, finalized, monkeypatch = wired
    r2 = FakeR2([{"state": "running", "stage": "render", "percent": 50, "ts": 1},
                 {"state": "done", "stage": "done", "percent": 100, "ts": 2}])
    _patch_r2(monkeypatch, r2)
    browser = FakeBrowser()

    result = colab.render_job(job, 1, {}, browser=browser, settings=object(), config=CONFIG)

    assert result["location"] == "colab" and finalized
    assert "runtime:v6e-1 TPU" in browser.calls and browser.calls[-1] == "unassign"
    assert browser.cell.startswith("!curl -fsSL 'https://r2.test/") and "| bash" in browser.cell
    assert len(r2.deleted) == 5, "run.sh, status, log, job kit and staging MP4 are removed"
    assert any("Render (Colab) 50%" in str(e) for e in job.events)


@pytest.mark.parametrize("step", ["login", "connect", "run"])
def test_a_browser_failure_still_releases_and_cleans_up(wired, step):
    job, finalized, monkeypatch = wired
    r2 = FakeR2([])
    _patch_r2(monkeypatch, r2)
    browser = FakeBrowser(fail_at=step)

    with pytest.raises(colab.ColabRenderError, match=f"{step} hỏng"):
        colab.render_job(job, 1, {}, browser=browser, settings=object(), config=CONFIG)

    assert browser.calls[-1] == "unassign" and not finalized
    assert len(r2.deleted) == 5


def test_a_failed_render_reports_its_step(wired):
    job, finalized, monkeypatch = wired
    _patch_r2(monkeypatch, FakeR2([{"state": "failed", "stage": "npm_ci", "message": "E404",
                                    "percent": 0, "ts": 1}]))
    with pytest.raises(colab.ColabRenderError, match="npm_ci: E404"):
        colab.render_job(job, 1, {}, browser=FakeBrowser(), settings=object(), config=CONFIG)
    assert not finalized


def test_a_silent_runtime_is_a_stall_not_a_hang(wired):
    job, _, monkeypatch = wired
    _patch_r2(monkeypatch, FakeR2([]))
    with pytest.raises(colab.ColabRenderError, match="không báo tiến độ"):
        colab.render_job(job, 1, {}, browser=FakeBrowser(), settings=object(),
                         config={**CONFIG, "stall_minutes": 0})


def test_a_second_render_is_refused_while_one_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(colab, "LOCK_PATH", tmp_path / "lock")
    with colab.single_flight():
        with pytest.raises(colab.ColabRenderError, match="render Colab khác"):
            with colab.single_flight():
                pass


def test_disabled_config_refuses_before_touching_anything(wired):
    job, _, _ = wired
    browser = FakeBrowser()
    with pytest.raises(colab.ColabRenderError, match="đang tắt"):
        colab.render_job(job, 1, {}, browser=browser, settings=object(), config={"enabled": False})
    assert browser.calls == []


def test_the_script_uses_the_local_flags_and_quotes_every_url():
    script = colab.build_script(
        composer_url="https://r2/c?a=1&b=2", composer_hash="abc", job_url="https://r2/j",
        status_url="https://r2/s", log_url="https://r2/l", output_url="https://r2/o",
        command=colab.remotion_command(crf=17, jpeg_quality=100), timeout_s=3600)
    assert "'https://r2/c?a=1&b=2'" in script
    assert "--concurrency=$WORKERS --crf=17 --jpeg-quality=100" in script
    assert subprocess.run(["bash", "-n"], input=script, text=True).returncode == 0


def test_status_parsing_ignores_garbage():
    assert colab.parse_status(None) is None
    assert colab.parse_status("not json") is None
    assert colab.parse_status('{"state":"done"}') == {"state": "done"}


def test_the_render_stage_sends_full_size_renders_to_colab(monkeypatch, tmp_path):
    from lib.talking_head_edit.stages import render

    job = SimpleNamespace(load=lambda: {"current_version": 1},
                          props_path=lambda v: tmp_path / "props.json", emit=lambda *a, **k: None)
    (tmp_path / "props.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(colab, "render_job", lambda job, version, options: {"location": "colab"})
    assert render.run(job, {"render_location": "colab"}) == {"location": "colab"}

    def broken(job, version, options):
        raise colab.ColabRenderError("TPU không sẵn")

    monkeypatch.setattr(colab, "render_job", broken)
    monkeypatch.setattr(colab, "load_config", lambda: {"fallback_to_local": False})
    with pytest.raises(render.RenderError, match="TPU không sẵn"):
        render.run(job, {"render_location": "colab"})


class _SlowMenuBrowser:
    """A ColabBrowser whose connection menu shows its items only after a delay."""

    def __init__(self, item_appears_after: int | None):
        from lib.cloud_render.colab_browser import ColabBrowser

        self.browser = ColabBrowser(session="test", cdp_port=0)
        self.polls = 0
        self.menu_opens = 0

        def fake_run(*args, timeout=None):
            if args[:4] == ("find", "role", "button", "click"):
                self.menu_opens += 1
                return True, "✓ Done"
            if args[:3] == ("find", "role", "menuitem"):
                self.polls += 1
                if item_appears_after is not None and self.polls >= item_appears_after:
                    return True, "✓ Done"
                return False, "✗ No element found"
            return True, ""

        self.browser._run = fake_run


def test_a_slow_connection_menu_is_waited_for(monkeypatch):
    """A loaded VPS opened the menu after the old fixed 2s wait and the click missed."""
    import lib.cloud_render.colab_browser as colab_browser

    monkeypatch.setattr(colab_browser.time, "sleep", lambda s: None)
    fake = _SlowMenuBrowser(item_appears_after=3)
    fake.browser._connection_menu("Change runtime type")
    assert fake.polls == 3 and fake.menu_opens == 1


def test_a_menu_that_never_shows_the_item_fails_after_reopening(monkeypatch):
    import lib.cloud_render.colab_browser as colab_browser

    monkeypatch.setattr(colab_browser.time, "sleep", lambda s: None)
    fake = _SlowMenuBrowser(item_appears_after=None)
    with pytest.raises(colab_browser.ColabBrowserError, match="3 lần mở menu"):
        fake.browser._connection_menu("Change runtime type")
    assert fake.menu_opens == 3
