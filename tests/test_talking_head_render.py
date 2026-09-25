"""Tests for the render.py refactor that phase 02 of the cloud-render plan
needed before any cloud code could exist: `build_remotion_command` and
`parse_progress_line` extracted to module level, `_concurrency` parameterized.

The argv-parity test pins the exact command the pre-refactor closure produced
(literally re-derived from the pre-refactor source) and asserts the new
module-level builder reproduces it element for element -- a fidelity
regression here would make the cloud deliverable visibly different from the
local one (see lib/cloud_render/remote.py's docstring).
"""

from __future__ import annotations

from pathlib import Path


import pytest

from lib.talking_head_edit.stages import render


# ---------------------------------------------------------------------------
# build_remotion_command -- argv parity with the pre-refactor closure
# ---------------------------------------------------------------------------

def _pre_refactor_build_command(out_path, props_path, staging, workers, crf,
                                jpeg_quality, scale) -> list[str]:
    """Verbatim copy of the closure `run()` built inline before the refactor
    (render.py:150-159 pre-change). This is the golden snapshot the new
    module-level builder must reproduce exactly."""
    command = [
        "npx", "remotion", "render", "src/index.tsx", render.COMPOSITION_ID, str(out_path),
        f"--props={props_path}", f"--public-dir={staging}",
        f"--concurrency={workers}", f"--crf={crf}",
        f"--jpeg-quality={jpeg_quality}", "--log=info",
        # added later: bounded frame cache, see render.OFFTHREAD_CACHE_BYTES
        f"--offthreadvideo-cache-size-in-bytes={render.OFFTHREAD_CACHE_BYTES}",
    ]
    if scale < 1.0:
        command.append(f"--scale={scale}")
    return command


class TestBuildRemotionCommandParity:
    def test_argv_identical_to_pre_refactor_closure_full_scale(self):
        out_path = Path("/job/final.mp4")
        props_path = Path("/job/props_v2.json")
        staging = Path("/job/render_public")

        expected = _pre_refactor_build_command(
            out_path, props_path, staging, workers=8, crf=17, jpeg_quality=100, scale=1.0)
        actual = render.build_remotion_command(
            entry="src/index.tsx", composition_id=render.COMPOSITION_ID, out_path=out_path,
            props_path=props_path, public_dir=staging, workers=8, crf=17,
            jpeg_quality=100, scale=1.0)

        assert actual == expected

    def test_argv_identical_to_pre_refactor_closure_preview_scale(self):
        """scale < 1.0 appends --scale=; this is the branch most likely to
        silently diverge in a careless refactor."""
        out_path = Path("/job/preview_50.mp4")
        props_path = Path("/job/props_v1.json")
        staging = Path("/job/render_public")

        expected = _pre_refactor_build_command(
            out_path, props_path, staging, workers=4, crf=20, jpeg_quality=90, scale=0.5)
        actual = render.build_remotion_command(
            entry="src/index.tsx", composition_id=render.COMPOSITION_ID, out_path=out_path,
            props_path=props_path, public_dir=staging, workers=4, crf=20,
            jpeg_quality=90, scale=0.5)

        assert actual == expected
        assert actual[-1] == "--scale=0.5"

    def test_local_run_still_produces_identical_argv_via_the_closure(self, tmp_path, monkeypatch):
        """End-to-end: run()'s internal build_command(workers) -- now delegating
        to the module-level builder -- must still emit the exact same argv it
        did before the refactor, for a fixed options dict."""
        captured: dict[str, list[str]] = {}

        def fake_run_remotion(job, command, log_path):
            captured["command"] = command
            (tmp_path / "final.mp4").write_bytes(b"x" * 10)
            return 0, ""

        monkeypatch.setattr(render, "_run_remotion", fake_run_remotion)
        monkeypatch.setattr(render, "stage_assets",
                            lambda job, props, video=None: tmp_path / "render_public")
        monkeypatch.setattr(render, "deliverable_video", lambda job, version, options: None)
        # free RAM moves between the two _concurrency() calls; pin it
        monkeypatch.setattr(render, "_memory_worker_cap", lambda: None)
        (tmp_path / "render_public").mkdir(exist_ok=True)
        monkeypatch.setattr(render, "COMPOSER_DIR", tmp_path)
        (tmp_path / "node_modules").mkdir(exist_ok=True)

        props_path = tmp_path / "props_v1.json"
        props_path.write_text('{"videoSrc": "src.mp4"}', encoding="utf-8")

        class FakeJob:
            dir = tmp_path
            final_path = tmp_path / "final.mp4"

            def load(self):
                return {"current_version": 1}

            def props_path(self, version):
                return props_path

            def log_path(self, stage):
                return tmp_path / "render.log"

            def emit(self, *a, **k):
                pass

            def rel(self, path):
                return path.name

        options = {"render_crf": 17, "render_jpeg_quality": 100}
        render.run(FakeJob(), options)

        expected = _pre_refactor_build_command(
            tmp_path / "final.mp4", props_path, tmp_path / "render_public",
            workers=render._concurrency(options), crf=17, jpeg_quality=100, scale=1.0)
        assert captured["command"] == expected


# ---------------------------------------------------------------------------
# parse_progress_line
# ---------------------------------------------------------------------------

class TestParseProgressLine:
    def test_extracts_percent_from_rendered_line(self):
        assert render.parse_progress_line("Rendered 45/90, elapsed 3s") == 50

    def test_returns_none_for_unrelated_line(self):
        assert render.parse_progress_line("Bundling...") is None

    def test_returns_none_when_missing_slash(self):
        assert render.parse_progress_line("Rendered something") is None

    def test_returns_none_on_non_numeric_counts(self):
        assert render.parse_progress_line("Rendered a/b, elapsed 1s") is None

    def test_returns_none_on_zero_total(self):
        assert render.parse_progress_line("Rendered 0/0, elapsed 1s") is None


# ---------------------------------------------------------------------------
# _concurrency -- parameterized max_concurrency
# ---------------------------------------------------------------------------

class TestConcurrencyParameterization:
    @pytest.fixture(autouse=True)
    def _plenty_of_memory(self, monkeypatch):
        """These pin the CPU rules; the memory rule has its own tests below."""
        monkeypatch.setattr(render, "_memory_worker_cap", lambda: None)

    def test_default_max_concurrency_matches_local_cap(self, monkeypatch):
        monkeypatch.setattr(render, "available_cores", lambda: 32)
        monkeypatch.setattr(render, "cpu_budget", lambda: 19)
        assert render._concurrency({}) == render.MAX_CONCURRENCY

    def test_the_env_override_is_held_to_the_budget_too(self, monkeypatch):
        monkeypatch.setenv("OPENMONTAGE_RENDER_MAX_CONCURRENCY", "16")
        monkeypatch.setattr(render, "available_cores", lambda: 10)
        monkeypatch.setattr(render, "cpu_budget", lambda: 6)
        assert render._concurrency({}) == 6

    def test_a_shared_small_box_is_held_to_its_cpu_budget(self, monkeypatch):
        """10 cores shared with other projects: render must not take all of them."""
        monkeypatch.setattr(render, "available_cores", lambda: 10)
        monkeypatch.setattr(render, "cpu_budget", lambda: 6)
        assert render._concurrency({}) == 6

    def test_custom_max_concurrency_raises_the_cap_for_cloud(self, monkeypatch):
        monkeypatch.setattr(render.os, "cpu_count", lambda: 64)
        assert render._concurrency({}, max_concurrency=32) == 32

    def test_explicit_request_still_bounded_by_custom_cap(self, monkeypatch):
        monkeypatch.setattr(render.os, "cpu_count", lambda: 64)
        assert render._concurrency({"render_concurrency": 999}, max_concurrency=32) == 32

    def test_half_string_still_works_with_custom_cap(self, monkeypatch):
        monkeypatch.setattr(render.os, "cpu_count", lambda: 64)
        default = max(1, min(32, 64 - 2))
        assert render._concurrency({"render_concurrency": "half"}, max_concurrency=32) == max(
            1, default // 2)

    def test_unparseable_string_falls_back_to_default_with_custom_cap(self, monkeypatch):
        monkeypatch.setattr(render.os, "cpu_count", lambda: 64)
        default = max(1, min(32, 64 - 2))
        assert render._concurrency({"render_concurrency": "lots"}, max_concurrency=32) == default

    def test_env_raises_the_local_cap(self, monkeypatch):
        monkeypatch.setattr(render, "available_cores", lambda: 32)
        monkeypatch.setattr(render, "cpu_budget", lambda: 19)
        monkeypatch.setenv("OPENMONTAGE_RENDER_MAX_CONCURRENCY", "16")
        assert render._concurrency({}) == 16


class TestMemoryAwareConcurrency:
    def _cpu_allows(self, monkeypatch, workers: int) -> None:
        monkeypatch.setattr(render, "available_cores", lambda: 10)
        monkeypatch.setattr(render, "cpu_budget", lambda: workers)

    def test_low_free_memory_lowers_the_local_worker_count(self, monkeypatch):
        """~7 GB free let 5 workers start and every one of them time out."""
        self._cpu_allows(monkeypatch, 6)
        monkeypatch.setattr(render, "_available_memory_mb", lambda: 7000)
        assert render._concurrency({}) == 2

    def test_plenty_of_memory_leaves_the_cpu_budget_in_charge(self, monkeypatch):
        self._cpu_allows(monkeypatch, 6)
        monkeypatch.setattr(render, "_available_memory_mb", lambda: 30000)
        assert render._concurrency({}) == 6

    def test_almost_no_memory_still_renders_with_one_worker(self, monkeypatch):
        self._cpu_allows(monkeypatch, 6)
        monkeypatch.setattr(render, "_available_memory_mb", lambda: 800)
        assert render._concurrency({}) == 1

    def test_unreadable_memory_changes_nothing(self, monkeypatch):
        self._cpu_allows(monkeypatch, 6)
        monkeypatch.setattr(render, "_available_memory_mb", lambda: None)
        assert render._concurrency({}) == 6

    def test_a_rented_box_is_not_held_to_this_machines_memory(self, monkeypatch):
        monkeypatch.setattr(render, "_available_memory_mb", lambda: 800)
        monkeypatch.setattr(render.os, "cpu_count", lambda: 64)
        assert render._concurrency({}, max_concurrency=32) == 32
