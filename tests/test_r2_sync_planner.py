"""Tests for lib/r2_storage/sync.py's `plan_sync` -- pure, no network. Exclude
globs, POSIX keys, mtime-only change, content change, stale-manifest discard,
`record_external_upload` interaction.
"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest

from lib.r2_storage import manifest as manifest_mod
from lib.r2_storage import sync as sync_mod


def _settings(**overrides):
    base = dict(bucket="test-bucket", exclude=("*.log", "*.tmp", "__pycache__/**", ".r2sync.json"),
               max_upload_mb_per_sync=5000)
    base.update(overrides)
    return SimpleNamespace(**base)


class TestPlanSyncFreshDirectory:
    def test_every_file_is_new_with_no_manifest(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.txt").write_text("world")
        plan = sync_mod.plan_sync(tmp_path, "prefix/x", _settings())
        assert {i.relpath for i in plan.upload} == {"a.txt", "b.txt"}
        assert plan.skip == []
        assert plan.object_count == 2

    def test_keys_are_posix_even_on_windows(self, tmp_path):
        sub = tmp_path / "nested"
        sub.mkdir()
        (sub / "c.txt").write_text("x")
        plan = sync_mod.plan_sync(tmp_path, "prefix/x", _settings())
        assert plan.upload[0].key == "prefix/x/nested/c.txt"
        assert "\\" not in plan.upload[0].key

    def test_exclude_globs_skip_matching_files(self, tmp_path):
        (tmp_path / "keep.txt").write_text("x")
        (tmp_path / "debug.log").write_text("x")
        (tmp_path / ".r2sync.json").write_text("{}")
        plan = sync_mod.plan_sync(tmp_path, "prefix/x", _settings())
        assert {i.relpath for i in plan.upload} == {"keep.txt"}


class TestPlanSyncAgainstAnExistingManifest:
    def test_unchanged_file_is_skipped_with_no_hashing(self, tmp_path, monkeypatch):
        (tmp_path / "a.txt").write_text("hello")
        settings = _settings()
        plan1 = sync_mod.plan_sync(tmp_path, "prefix/x", settings)
        _apply_fake(tmp_path, plan1, settings)

        def _boom(*a, **k):
            raise AssertionError("file_md5 called on an unchanged file")

        monkeypatch.setattr(manifest_mod, "file_md5", _boom)
        plan2 = sync_mod.plan_sync(tmp_path, "prefix/x", settings)
        assert plan2.upload == []
        assert {i.relpath for i in plan2.skip} == {"a.txt"}
        assert plan2.skip[0].reason == "unchanged"

    def test_touching_only_mtime_hashes_but_does_not_upload(self, tmp_path):
        path = tmp_path / "a.txt"
        path.write_text("hello")
        settings = _settings()
        plan1 = sync_mod.plan_sync(tmp_path, "prefix/x", settings)
        _apply_fake(tmp_path, plan1, settings)

        future = time.time() + 10
        os.utime(path, (future, future))
        plan2 = sync_mod.plan_sync(tmp_path, "prefix/x", settings)
        assert plan2.upload == []
        assert plan2.skip[0].reason == "touched"

    def test_content_change_triggers_upload_of_only_that_file(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.txt").write_text("world")
        settings = _settings()
        plan1 = sync_mod.plan_sync(tmp_path, "prefix/x", settings)
        _apply_fake(tmp_path, plan1, settings)

        (tmp_path / "a.txt").write_text("CHANGED")
        plan2 = sync_mod.plan_sync(tmp_path, "prefix/x", settings)
        assert {i.relpath for i in plan2.upload} == {"a.txt"}
        assert {i.relpath for i in plan2.skip} == {"b.txt"}

    def test_stale_manifest_from_a_different_bucket_is_discarded(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        settings = _settings(bucket="bucket-a")
        plan1 = sync_mod.plan_sync(tmp_path, "prefix/x", settings)
        _apply_fake(tmp_path, plan1, settings)

        other_settings = _settings(bucket="bucket-b")
        plan2 = sync_mod.plan_sync(tmp_path, "prefix/x", other_settings)
        assert {i.relpath for i in plan2.upload} == {"a.txt"}  # full re-plan, not skipped


class TestRecordExternalUpload:
    def test_recorded_file_shows_up_as_skip_not_upload(self, tmp_path):
        (tmp_path / "final.mp4").write_text("rendered bytes")
        settings = _settings()
        manifest_mod.record_external_upload(
            tmp_path, "final.mp4", "prefix/x/final.mp4", settings)
        plan = sync_mod.plan_sync(tmp_path, "prefix/x", settings)
        assert plan.upload == []
        assert {i.relpath for i in plan.skip} == {"final.mp4"}

    def test_key_must_end_with_relpath(self, tmp_path):
        (tmp_path / "final.mp4").write_text("x")
        with pytest.raises(ValueError):
            manifest_mod.record_external_upload(
                tmp_path, "final.mp4", "prefix/x/other.mp4", _settings())


class TestMaxUploadGuard:
    def test_apply_sync_aborts_before_any_upload_when_plan_exceeds_the_cap(self, tmp_path, monkeypatch):
        (tmp_path / "big.bin").write_bytes(b"x" * 2000)
        settings = _settings(max_upload_mb_per_sync=0.001)  # ~1 KB cap, file is ~2 KB
        plan = sync_mod.plan_sync(tmp_path, "prefix/x", settings)
        assert plan.upload  # sanity: something would have been uploaded

        def _boom(*a, **k):
            raise AssertionError("build_client called -- the size guard must fire first")

        monkeypatch.setattr("lib.r2_storage.client.build_client", _boom)
        with pytest.raises(Exception, match="max_upload_mb_per_sync"):
            sync_mod.apply_sync(plan, settings)


def _apply_fake(local_dir, plan, settings):
    """Write manifest entries for `plan.upload` without any network call --
    mirrors what `apply_sync` does after a real upload, for tests that only
    care about the diff logic."""
    data = manifest_mod.load(local_dir, settings.bucket, plan.prefix)
    for item in plan.upload:
        data["files"][item.relpath] = {
            "size": item.size, "mtime_ns": item.mtime_ns, "md5": item.md5,
            "key": item.key, "uploaded_at": 0.0,
        }
    manifest_mod.save(local_dir, data)
