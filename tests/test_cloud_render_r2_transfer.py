"""Contract tests for the R2-mediated Vast.ai transfer path
(lib/cloud_render/transfer.py + remote.py's staging/verify/copy flow).

Zero network: `subprocess.run` is monkeypatched to a recorder, and R2 calls
are monkeypatched to local no-ops/fakes -- the point is to prove the *shape*
of the transfer (stdin not argv, no credential env names, staging-before-
archive) is correct, not to exercise real R2 or a real SSH connection.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.cloud_render import kit, remote, transfer
from lib.r2_storage import config as r2_config

ROOT = Path(__file__).resolve().parent.parent


def _fake_settings():
    return SimpleNamespace(bucket="test-bucket", prefix="projects",
                           endpoint_url="https://fake.example")


def _completed(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class TestPresignedUrlNeverInArgv:
    def test_fetch_to_remote_puts_the_url_on_stdin_not_argv(self, monkeypatch):
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["input"] = kwargs.get("input")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(r2_config, "resolve", lambda *a, **k: _fake_settings())
        monkeypatch.setattr("lib.r2_storage.presign.get_url", lambda *a, **k: "https://x/?X-Amz-Signature=abc")

        transfer.fetch_to_remote("1.2.3.4", 22, "/key", "render-kits/jobs/x/kit.tar.gz",
                                 "/root/kit/jobs/x")

        argv_blob = " ".join(captured["args"])
        assert "X-Amz-Signature" not in argv_blob
        assert "X-Amz-Signature" in (captured["input"] or "")

    def test_push_from_remote_puts_the_url_on_stdin_not_argv(self, monkeypatch):
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["input"] = kwargs.get("input")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr("lib.r2_storage.presign.put_url", lambda *a, **k: "https://x/?X-Amz-Signature=abc")

        transfer.push_from_remote("1.2.3.4", 22, "/key", "/root/kit/out/final.mp4",
                                  "render-kits/jobs/x/out/final.mp4", ttl=600)

        argv_blob = " ".join(captured["args"])
        assert "X-Amz-Signature" not in argv_blob
        assert "X-Amz-Signature" in (captured["input"] or "")


class TestNoCredentialEnvNamesInCloudRender:
    def test_grep_guard_no_access_key_or_secret_names(self):
        for path in (ROOT / "lib" / "cloud_render").glob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert "SECRET_ACCESS_KEY" not in source, f"found in {path}"
            assert "ACCESS_KEY_ID" not in source, f"found in {path}"


class TestCurlErrorMapping:
    @pytest.mark.parametrize("code,expected_substring", [
        (22, "presign hết hạn"),
        (28, "timeout"),
        (56, "mất kết nối"),
    ])
    def test_known_exit_codes_map_to_a_vietnamese_cause(self, code, expected_substring):
        message = transfer._curl_error(code, "some stderr tail")
        assert expected_substring in message

    def test_unknown_exit_code_still_names_the_code(self):
        message = transfer._curl_error(99, "")
        assert "99" in message


class TestVerificationFailureNeverPromotesToArchive:
    def test_duration_drift_deletes_staging_without_copying_to_archive(self, monkeypatch, tmp_path):
        """Drives the REAL `remote.render_batch` -> `_render_one_item` path
        with a duration that drifts past tolerance: `copy_object` (staging ->
        archive promotion) must never be called, and `delete_object` (staging
        cleanup) must still run exactly once, via the item's own `finally`."""
        calls = {"copy_object": 0, "delete_object": 0}

        monkeypatch.setattr(r2_config, "is_configured", lambda: (True, []))
        monkeypatch.setattr(r2_config, "resolve", lambda *a, **k: _fake_settings())
        monkeypatch.setattr(transfer, "ssh", lambda *a, **k: _completed(returncode=0))
        monkeypatch.setattr(transfer, "push_kit", lambda *a, **k: ("key", 10, False))
        monkeypatch.setattr(transfer, "fetch_to_remote", lambda *a, **k: _completed(returncode=0))
        monkeypatch.setattr(transfer, "push_from_remote", lambda *a, **k: _completed(returncode=0))

        def fake_download(key, path, *, settings=None):
            Path(path).write_bytes(b"v" * 200_000)  # above MIN_OUTPUT_BYTES

        monkeypatch.setattr(remote.r2_presign, "download", fake_download)
        monkeypatch.setattr(remote.r2_presign, "copy_object",
                            lambda *a, **k: calls.__setitem__("copy_object", calls["copy_object"] + 1))
        monkeypatch.setattr(remote.r2_presign, "delete_object",
                            lambda *a, **k: calls.__setitem__("delete_object", calls["delete_object"] + 1))
        monkeypatch.setattr(remote.r2_manifest, "record_external_upload", lambda *a, **k: None)
        monkeypatch.setattr(remote, "_stream_command",
                            lambda args, *, timeout_s, on_line: 0)
        monkeypatch.setattr(remote, "probe_duration", lambda path: 999.0)  # wildly off vs expected 30.0

        composer = kit.ComposerKitManifest(kit_dir=tmp_path, kit_hash="h", size_bytes=1, file_count=1)
        job_kit = kit.JobKitManifest(
            job_id="job-1", kit_dir=tmp_path, kit_hash="jh", size_bytes=1, file_count=1,
            composition_id="MonaTimeline", props_path="props.json", public_dir="public",
            expected_output_name="out/final.mp4", estimated_render_seconds=10.0)
        item = remote.BatchItem(job_id="job-1", kit=job_kit, timeout_s=30.0,
                                expected_duration_seconds=30.0)
        rental = SimpleNamespace(id=1, ssh_host="1.2.3.4", ssh_port=22)

        results = remote.render_batch(rental, composer, [item], key_path="/key", max_concurrency=8)

        assert results[0].status == "failed"
        assert "lệch" in results[0].error
        assert calls["copy_object"] == 0
        assert calls["delete_object"] == 1
