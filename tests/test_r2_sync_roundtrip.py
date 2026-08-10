"""Integration tests for lib/r2_storage/sync.py against a real (moto) S3
endpoint -- push, re-push (0 uploads, 0 API calls), pull, prune (prefix-scoped),
and the multipart threshold path. See `tests/conftest.py::r2_moto_server`.
"""

from __future__ import annotations

import json

import pytest

from lib.r2_storage.client import build_client
from lib.r2_storage.config import resolve
from lib.r2_storage.sync import apply_sync, plan_sync, prune, pull


@pytest.fixture()
def settings(r2_moto_server, tmp_path, monkeypatch):
    config_path = tmp_path / "r2-storage.json"
    config_path.write_text(json.dumps({"enabled": True, "max_upload_mb_per_sync": 5000}))
    return resolve(path=config_path)


class TestPushRePush:
    def test_repush_of_unchanged_dir_is_zero_uploads_zero_api_calls(
            self, settings, tmp_path, counting_client):
        local_dir = tmp_path / "job"
        local_dir.mkdir()
        (local_dir / "a.txt").write_text("hello")
        (local_dir / "b.txt").write_text("world")

        plan1 = plan_sync(local_dir, "projects/x", settings)
        result1 = apply_sync(plan1, settings)
        assert result1.uploaded == 2
        assert result1.skipped == 0

        plan2 = plan_sync(local_dir, "projects/x", settings)
        assert plan2.upload == []
        assert len(plan2.skip) == 2

        client = build_client(settings)
        counter = counting_client(client)
        import lib.r2_storage.client as client_mod
        original = client_mod.build_client
        client_mod.build_client = lambda s: client
        try:
            result2 = apply_sync(plan2, settings)
        finally:
            client_mod.build_client = original
        assert result2.uploaded == 0
        assert counter["n"] == 0

    def test_objects_land_at_expected_keys(self, settings, tmp_path):
        local_dir = tmp_path / "job"
        local_dir.mkdir()
        (local_dir / "final.mp4").write_bytes(b"video bytes")
        plan = plan_sync(local_dir, "projects/autoedit-jobs/j1", settings)
        apply_sync(plan, settings)

        client = build_client(settings)
        listing = client.list_objects_v2(Bucket=settings.bucket, Prefix="projects/autoedit-jobs/j1")
        keys = {o["Key"] for o in listing.get("Contents", [])}
        assert keys == {"projects/autoedit-jobs/j1/final.mp4"}


class TestMultipartThreshold:
    def test_large_file_crosses_multipart_threshold_and_still_round_trips(
            self, settings, tmp_path, monkeypatch):
        import dataclasses

        small_threshold_settings = dataclasses.replace(
            settings, multipart_threshold_mb=1, multipart_chunksize_mb=1)
        local_dir = tmp_path / "job"
        local_dir.mkdir()
        (local_dir / "big.bin").write_bytes(b"x" * (3 * 1024 * 1024))  # 3 MB, threshold is 1 MB

        plan = plan_sync(local_dir, "projects/y", small_threshold_settings)
        assert plan.upload[0].reason == "new"
        result = apply_sync(plan, small_threshold_settings)
        assert result.uploaded == 1

        client = build_client(small_threshold_settings)
        head = client.head_object(Bucket=small_threshold_settings.bucket, Key="projects/y/big.bin")
        assert head["ContentLength"] == 3 * 1024 * 1024


class TestPull:
    def test_pull_downloads_everything_under_prefix(self, settings, tmp_path):
        local_dir = tmp_path / "job"
        local_dir.mkdir()
        (local_dir / "a.txt").write_text("hello")
        apply_sync(plan_sync(local_dir, "projects/z", settings), settings)

        dest = tmp_path / "restored"
        result = pull("projects/z", dest, settings)
        assert result["downloaded"] == 1
        assert (dest / "a.txt").read_text() == "hello"

    def test_pull_skips_a_local_file_that_already_matches_by_size(self, settings, tmp_path):
        local_dir = tmp_path / "job"
        local_dir.mkdir()
        (local_dir / "a.txt").write_text("hello")
        apply_sync(plan_sync(local_dir, "projects/z2", settings), settings)

        dest = tmp_path / "restored2"
        dest.mkdir()
        (dest / "a.txt").write_text("hello")  # same size as remote
        result = pull("projects/z2", dest, settings)
        assert result["skipped"] == 1
        assert result["downloaded"] == 0


class TestPruneScopedToItsPrefix:
    def test_prune_never_touches_a_different_top_level_prefix(self, settings, tmp_path):
        local_dir = tmp_path / "job"
        local_dir.mkdir()
        (local_dir / "keep.txt").write_text("keep")
        apply_sync(plan_sync(local_dir, "projects/w", settings), settings)

        client = build_client(settings)
        client.put_object(Bucket=settings.bucket, Key="render-kits/jobs/other/kit.tar.gz",
                          Body=b"unrelated")

        # Remove the local file so it becomes "orphaned" under projects/w.
        (local_dir / "keep.txt").unlink()
        result = prune("projects/w", local_dir, settings, yes=True)

        assert result["orphaned"] == ["projects/w/keep.txt"]
        listing = client.list_objects_v2(Bucket=settings.bucket, Prefix="render-kits/")
        assert [o["Key"] for o in listing.get("Contents", [])] == ["render-kits/jobs/other/kit.tar.gz"]

    def test_prune_dry_run_deletes_nothing(self, settings, tmp_path):
        local_dir = tmp_path / "job"
        local_dir.mkdir()
        (local_dir / "keep.txt").write_text("keep")
        apply_sync(plan_sync(local_dir, "projects/v", settings), settings)
        (local_dir / "keep.txt").unlink()

        result = prune("projects/v", local_dir, settings, yes=False)
        assert result["orphaned"] == ["projects/v/keep.txt"]
        assert result["deleted"] is False

        client = build_client(settings)
        listing = client.list_objects_v2(Bucket=settings.bucket, Prefix="projects/v")
        assert listing.get("Contents")  # still there -- dry run did not delete
