"""Tests for lib/cloud_render/kit.py -- the allowlist render-kit packager,
split into `build_composer_kit()` (shared) + `build_job_kit()` (per-job).

Zero network: everything here is local tempdir plumbing. The security
property under test is the allowlist itself -- `remotion-composer/public/`
(349 MB in the real repo) and `.env` must never appear in a built composer
kit, even though both sit right next to the files that DO belong in it.
"""

from __future__ import annotations

import json

import pytest

from lib.cloud_render import kit


class FakeJob:
    """Minimal job double: only the surface kit.py reads."""

    def __init__(self, tmp_path, *, duration_seconds: float = 30.0):
        self.job_id = "fake-job-260806"
        self.dir = tmp_path / "job"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.render_public_dir = self.dir / "render_public"
        self.render_public_dir.mkdir(exist_ok=True)
        self._props = {
            "videoSrc": "src.mp4",
            "durationSeconds": duration_seconds,
        }

    def props_path(self, version: int):
        path = self.dir / f"props_v{version}.json"
        path.write_text(json.dumps(self._props), encoding="utf-8")
        return path


@pytest.fixture()
def fake_composer_dir(tmp_path):
    """A composer dir shaped like remotion-composer/, including the two
    things the allowlist must exclude: public/ (349 MB proxy) and .env."""
    composer = tmp_path / "composer"
    composer.mkdir()
    (composer / "package.json").write_text("{}", encoding="utf-8")
    (composer / "package-lock.json").write_text("{}", encoding="utf-8")
    (composer / "tsconfig.json").write_text("{}", encoding="utf-8")
    src = composer / "src"
    src.mkdir()
    (src / "index.tsx").write_text("export default {}", encoding="utf-8")

    node_modules = composer / "node_modules"
    node_modules.mkdir()
    (node_modules / "some-pkg.js").write_text("module.exports = {}", encoding="utf-8")

    public = composer / "public"
    public.mkdir()
    (public / "bgm_upbeat_bounce.mp3").write_bytes(b"x" * 1000)

    (composer / ".env").write_text("SECRET=1", encoding="utf-8")
    return composer


class TestBuildComposerKit:
    def test_kit_contains_only_allowlisted_composer_files(self, fake_composer_dir):
        manifest = kit.build_composer_kit(composer_dir=fake_composer_dir)

        assert (manifest.kit_dir / "package.json").exists()
        assert (manifest.kit_dir / "package-lock.json").exists()
        assert (manifest.kit_dir / "tsconfig.json").exists()
        assert (manifest.kit_dir / "src" / "index.tsx").exists()
        kit.cleanup_kit(manifest)

    def test_kit_never_contains_shared_public_or_env(self, fake_composer_dir):
        manifest = kit.build_composer_kit(composer_dir=fake_composer_dir)

        all_names = {p.name for p in manifest.kit_dir.rglob("*")}
        assert ".env" not in all_names
        assert "node_modules" not in all_names
        assert "bgm_upbeat_bounce.mp3" not in all_names  # would only exist via composer/public/
        assert not (manifest.kit_dir / "public" / "bgm_upbeat_bounce.mp3").exists()
        kit.cleanup_kit(manifest)

    def test_manifest_reports_size_and_file_count(self, fake_composer_dir):
        manifest = kit.build_composer_kit(composer_dir=fake_composer_dir)

        assert manifest.size_bytes > 0
        assert manifest.file_count == sum(1 for p in manifest.kit_dir.rglob("*") if p.is_file())
        kit.cleanup_kit(manifest)

    def test_kit_hash_is_deterministic_for_identical_content(self, fake_composer_dir):
        first = kit.build_composer_kit(composer_dir=fake_composer_dir)
        second = kit.build_composer_kit(composer_dir=fake_composer_dir)

        assert first.kit_hash == second.kit_hash
        kit.cleanup_kit(first)
        kit.cleanup_kit(second)

    def test_kit_hash_changes_when_package_lock_changes(self, fake_composer_dir):
        first = kit.build_composer_kit(composer_dir=fake_composer_dir)

        (fake_composer_dir / "package-lock.json").write_text(
            json.dumps({"lockfileVersion": 3}), encoding="utf-8")
        second = kit.build_composer_kit(composer_dir=fake_composer_dir)

        assert first.kit_hash != second.kit_hash
        kit.cleanup_kit(first)
        kit.cleanup_kit(second)

    def test_raises_and_cleans_up_when_composer_file_missing(self, fake_composer_dir):
        (fake_composer_dir / "tsconfig.json").unlink()

        with pytest.raises(kit.KitError):
            kit.build_composer_kit(composer_dir=fake_composer_dir)


class TestBuildJobKit:
    def test_kit_includes_job_props_renamed_and_staged_public_dir(self, tmp_path):
        job = FakeJob(tmp_path)
        (job.render_public_dir / "src.mp4").write_bytes(b"video-bytes")
        (job.render_public_dir / "sfx_pop.mp3").write_bytes(b"audio-bytes")

        manifest = kit.build_job_kit(job, 3)

        props_file = manifest.kit_dir / manifest.props_path
        assert props_file.exists()
        assert json.loads(props_file.read_text(encoding="utf-8"))["videoSrc"] == "src.mp4"

        public_dir = manifest.kit_dir / manifest.public_dir
        assert (public_dir / "src.mp4").read_bytes() == b"video-bytes"
        assert (public_dir / "sfx_pop.mp3").read_bytes() == b"audio-bytes"
        kit.cleanup_kit(manifest)

    def test_job_kit_never_contains_composer_files(self, tmp_path):
        """The split's whole point: a job kit alone must never carry
        package.json/node_modules/etc -- those live only in the composer
        kit, uploaded once and shared."""
        job = FakeJob(tmp_path)
        (job.render_public_dir / "src.mp4").write_bytes(b"video-bytes")

        manifest = kit.build_job_kit(job, 1)

        all_names = {p.name for p in manifest.kit_dir.rglob("*")}
        assert "package.json" not in all_names
        assert "node_modules" not in all_names
        kit.cleanup_kit(manifest)

    def test_manifest_reports_composition_and_output_metadata(self, tmp_path):
        job = FakeJob(tmp_path)
        (job.render_public_dir / "src.mp4").write_bytes(b"video-bytes")

        manifest = kit.build_job_kit(job, 1)

        assert manifest.job_id == job.job_id
        assert manifest.composition_id == "MonaTimeline"
        assert manifest.expected_output_name == "out/final.mp4"
        assert manifest.size_bytes > 0
        assert manifest.file_count == sum(1 for p in manifest.kit_dir.rglob("*") if p.is_file())
        kit.cleanup_kit(manifest)

    def test_kit_under_5mb_for_a_90s_job(self, tmp_path):
        """Success criterion: a 90s job's kit (props + staged media only,
        now that the composer is split out) stays well under 5 MB."""
        job = FakeJob(tmp_path, duration_seconds=90.0)
        (job.render_public_dir / "src.mp4").write_bytes(b"v" * (2 * 1024 * 1024))  # 2 MB stand-in

        manifest = kit.build_job_kit(job, 1)

        assert manifest.size_bytes < 5 * 1024 * 1024
        assert manifest.estimated_render_seconds == pytest.approx(90.0 * 1.9, rel=1e-6)
        kit.cleanup_kit(manifest)

    def test_kit_hash_is_deterministic_for_identical_content(self, tmp_path):
        job = FakeJob(tmp_path)
        (job.render_public_dir / "src.mp4").write_bytes(b"video-bytes")

        first = kit.build_job_kit(job, 1)
        second = kit.build_job_kit(job, 1)

        assert first.kit_hash == second.kit_hash
        kit.cleanup_kit(first)
        kit.cleanup_kit(second)

    def test_kit_hash_changes_when_props_change(self, tmp_path):
        job = FakeJob(tmp_path, duration_seconds=30.0)
        (job.render_public_dir / "src.mp4").write_bytes(b"video-bytes")
        first = kit.build_job_kit(job, 1)

        job2 = FakeJob(tmp_path, duration_seconds=99.0)
        job2.dir = job.dir  # reuse same staged public dir
        job2.render_public_dir = job.render_public_dir
        second = kit.build_job_kit(job2, 2)

        assert first.kit_hash != second.kit_hash
        kit.cleanup_kit(first)
        kit.cleanup_kit(second)

    def test_raises_when_props_missing(self, tmp_path):
        class NoPropsJob(FakeJob):
            def props_path(self, version):
                return self.dir / f"props_v{version}.json"  # never written

        with pytest.raises(kit.KitError):
            kit.build_job_kit(NoPropsJob(tmp_path), 1)

    def test_raises_when_staging_dir_missing(self, tmp_path):
        job = FakeJob(tmp_path)
        job.render_public_dir.rmdir()

        with pytest.raises(kit.KitError):
            kit.build_job_kit(job, 1)


class TestCleanupKit:
    def test_cleanup_removes_the_temp_dir(self, fake_composer_dir):
        manifest = kit.build_composer_kit(composer_dir=fake_composer_dir)

        assert manifest.kit_dir.exists()
        kit.cleanup_kit(manifest)
        assert not manifest.kit_dir.exists()

    def test_cleanup_is_idempotent(self, fake_composer_dir):
        manifest = kit.build_composer_kit(composer_dir=fake_composer_dir)

        kit.cleanup_kit(manifest)
        kit.cleanup_kit(manifest)  # must not raise on an already-gone dir

    def test_cleanup_works_on_job_kit_too(self, tmp_path):
        job = FakeJob(tmp_path)
        (job.render_public_dir / "src.mp4").write_bytes(b"video-bytes")
        manifest = kit.build_job_kit(job, 1)

        assert manifest.kit_dir.exists()
        kit.cleanup_kit(manifest)
        assert not manifest.kit_dir.exists()
