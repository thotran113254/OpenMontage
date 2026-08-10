"""Tests for lib/cloud_render/remote.py -- render_batch, render_now, and the
video_compose refusal.

Zero network, zero spend: `transfer.ssh` / `push_kit` / `fetch_to_remote` /
`push_from_remote`, the R2 presign/manifest calls, and
`vast_client.search` / `rent` / `wait_running` / `destroy` are monkeypatched
on the phase-01 modules directly (attribute lookup at call time means
patching the module attribute is enough -- no sys.modules faking needed,
unlike the vastai SDK itself). `_stream_command` (this module's own
subprocess.Popen wrapper) is monkeypatched too so no real ssh subprocess
ever spawns.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.cloud_render import kit, ledger, remote, setup_key, transfer, vast_client
from lib.r2_storage import config as r2_config


def _completed(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def make_composer_manifest(tmp_path, name="composer") -> kit.ComposerKitManifest:
    kit_dir = tmp_path / name
    kit_dir.mkdir(exist_ok=True)
    (kit_dir / "package.json").write_text("{}", encoding="utf-8")
    return kit.ComposerKitManifest(kit_dir=kit_dir, kit_hash="composerhash", size_bytes=10,
                                   file_count=1)


def make_job_manifest(tmp_path, job_id="job-1", name="job-kit") -> kit.JobKitManifest:
    kit_dir = tmp_path / name
    kit_dir.mkdir(exist_ok=True)
    (kit_dir / "props.json").write_text("{}", encoding="utf-8")
    return kit.JobKitManifest(
        job_id=job_id, kit_dir=kit_dir, kit_hash="deadbeef", size_bytes=10, file_count=1,
        composition_id="MonaTimeline", props_path="props.json", public_dir="public",
        expected_output_name="out/final.mp4", estimated_render_seconds=57.0)


# ---------------------------------------------------------------------------
# Fidelity guard: remote.py must never hardcode a second Remotion argv list
# ---------------------------------------------------------------------------

class TestRemoteNeverDuplicatesTheFlagList:
    def test_remote_module_source_has_no_second_hardcoded_render_argv(self):
        """The one real defense against cloud/local visual drift is that only
        `build_remotion_command` builds the argv. If this literal ever shows
        up in remote.py again, someone re-introduced a second flag list."""
        source = Path(remote.__file__).read_text(encoding="utf-8")
        assert '"remotion", "render"' not in source
        assert "build_remotion_command" in source  # the shared builder IS used


# ---------------------------------------------------------------------------
# render_pipeline -- structured refusal, never a partial render
# ---------------------------------------------------------------------------

class TestRenderPipelineRefusal:
    def test_raises_unsupported_with_reason_and_options(self):
        with pytest.raises(remote.CloudRenderUnsupported) as exc_info:
            remote.render_pipeline()
        assert "file://" in exc_info.value.reason
        assert "video_compose.py" in exc_info.value.reason
        assert exc_info.value.options
        assert isinstance(exc_info.value.options, list)


# ---------------------------------------------------------------------------
# _wait_ready
# ---------------------------------------------------------------------------

class TestWaitReady:
    def test_returns_once_ssh_reports_ready(self, monkeypatch):
        monkeypatch.setattr(transfer, "ssh", lambda *a, **k: _completed(returncode=0))
        remote._wait_ready("1.2.3.4", 22, "/key", timeout_s=1.0, poll_interval_s=0.01)  # no raise

    def test_raises_after_timeout_when_never_ready(self, monkeypatch):
        monkeypatch.setattr(transfer, "ssh", lambda *a, **k: _completed(returncode=1))
        with pytest.raises(remote.CloudRenderError, match="openmontage-ready"):
            remote._wait_ready("1.2.3.4", 22, "/key", timeout_s=0.05, poll_interval_s=0.01)

    def test_polls_multiple_times_before_success(self, monkeypatch):
        calls = {"n": 0}

        def fake_ssh(*a, **k):
            calls["n"] += 1
            return _completed(returncode=0 if calls["n"] >= 3 else 1)

        monkeypatch.setattr(transfer, "ssh", fake_ssh)
        remote._wait_ready("1.2.3.4", 22, "/key", timeout_s=5.0, poll_interval_s=0.01)
        assert calls["n"] == 3


# ---------------------------------------------------------------------------
# _stream_command
# ---------------------------------------------------------------------------

class FakeProcess:
    def __init__(self, lines, exit_code=0):
        self.stdout = iter(lines)
        self._exit_code = exit_code
        self.killed = False

    def wait(self, timeout=None):
        return self._exit_code

    def kill(self):
        self.killed = True


class TestStreamCommand:
    def test_feeds_every_line_to_callback_and_returns_exit_code(self, monkeypatch):
        fake = FakeProcess(["Rendered 10/100, x\n", "Rendered 50/100, x\n"], exit_code=0)
        monkeypatch.setattr(remote.subprocess, "Popen", lambda *a, **k: fake)

        seen = []
        code = remote._stream_command(["ssh", "..."], timeout_s=30.0, on_line=seen.append)

        assert code == 0
        assert seen == ["Rendered 10/100, x\n", "Rendered 50/100, x\n"]

    def test_nonzero_exit_code_is_returned_not_raised(self, monkeypatch):
        fake = FakeProcess(["boom\n"], exit_code=1)
        monkeypatch.setattr(remote.subprocess, "Popen", lambda *a, **k: fake)

        code = remote._stream_command(["ssh", "..."], timeout_s=30.0, on_line=lambda line: None)
        assert code == 1

    def test_deadline_exceeded_kills_process_and_raises(self, monkeypatch):
        fake = FakeProcess(["one\n", "two\n", "three\n"], exit_code=0)
        monkeypatch.setattr(remote.subprocess, "Popen", lambda *a, **k: fake)

        with pytest.raises(remote.CloudRenderError, match="timeout"):
            remote._stream_command(["ssh", "..."], timeout_s=-1.0, on_line=lambda line: None)
        assert fake.killed


# ---------------------------------------------------------------------------
# render_batch -- shared setup once, per-item isolation, deadline guard
# ---------------------------------------------------------------------------

@pytest.fixture()
def happy_batch_env(monkeypatch, tmp_path):
    """Every network call succeeds; a test overrides one to force a failure."""
    fake_settings = SimpleNamespace(bucket="test-bucket", prefix="projects",
                                    endpoint_url="https://fake.example")
    monkeypatch.setattr(r2_config, "is_configured", lambda: (True, []))
    monkeypatch.setattr(r2_config, "resolve", lambda *a, **k: fake_settings)

    monkeypatch.setattr(transfer, "ssh", lambda *a, **k: _completed(returncode=0))
    monkeypatch.setattr(transfer, "push_kit", lambda *a, **k: ("key", 10, False))
    monkeypatch.setattr(transfer, "fetch_to_remote", lambda *a, **k: _completed(returncode=0))
    monkeypatch.setattr(transfer, "push_from_remote", lambda *a, **k: _completed(returncode=0))

    def fake_download(key, local_path, *, settings=None):
        Path(local_path).write_bytes(b"v" * 200_000)

    monkeypatch.setattr(remote.r2_presign, "download", fake_download)
    monkeypatch.setattr(remote.r2_presign, "copy_object", lambda *a, **k: None)
    monkeypatch.setattr(remote.r2_presign, "delete_object", lambda *a, **k: None)
    monkeypatch.setattr(remote.r2_manifest, "record_external_upload", lambda *a, **k: None)

    monkeypatch.setattr(remote, "_stream_command",
                        lambda args, *, timeout_s, on_line: (on_line("Rendered 50/100, x") or 0))
    monkeypatch.setattr(remote, "probe_duration", lambda path: 30.0)
    return SimpleNamespace(tmp_path=tmp_path)


def _rental():
    return SimpleNamespace(id=42, ssh_host="1.2.3.4", ssh_port=22)


class TestRenderBatchHappyPath:
    def test_single_item_returns_done_and_emits_progress(self, happy_batch_env):
        composer = make_composer_manifest(happy_batch_env.tmp_path)
        job_kit = make_job_manifest(happy_batch_env.tmp_path)
        emitted = []
        job = SimpleNamespace(emit=lambda *a, **k: emitted.append((a, k)))
        item = remote.BatchItem(job_id="job-1", kit=job_kit,
                                flags={"crf": 17, "jpeg_quality": 100, "scale": 1.0},
                                timeout_s=60.0, expected_duration_seconds=30.0, job=job)

        results = remote.render_batch(_rental(), composer, [item], key_path="/key",
                                       max_concurrency=16)

        assert len(results) == 1
        assert results[0].status == "done"
        assert results[0].result.size_bytes == 200_000
        assert results[0].result.duration_seconds == 30.0
        assert emitted, "progress phải được job.emit ít nhất một lần"
        assert emitted[0][0][:2] == ("progress", "render")

    def test_works_without_a_job_for_progress(self, happy_batch_env):
        composer = make_composer_manifest(happy_batch_env.tmp_path)
        job_kit = make_job_manifest(happy_batch_env.tmp_path)
        item = remote.BatchItem(job_id="job-1", kit=job_kit, timeout_s=60.0)

        results = remote.render_batch(_rental(), composer, [item], key_path="/key",
                                       max_concurrency=16)
        assert results[0].status == "done"
        assert results[0].result.size_bytes == 200_000

    def test_final_mp4_exists_before_record_external_upload_reads_it(self, monkeypatch, tmp_path):
        """Regression test for a real bug: `record_external_upload` stats
        `job.dir/"final.mp4"` immediately -- if that file has not been
        written yet (it used to be copied by the CALLER, after render_batch
        returned), this raises FileNotFoundError and the whole item is
        wrongly marked "failed" even though the render/verify/archive-copy
        all succeeded. Deliberately does NOT monkeypatch
        `record_external_upload` -- it must run against a real job dir with
        no pre-existing final.mp4, exactly the first-ever-render case."""
        fake_settings = SimpleNamespace(bucket="test-bucket", prefix="projects",
                                        endpoint_url="https://fake.example")
        monkeypatch.setattr(r2_config, "is_configured", lambda: (True, []))
        monkeypatch.setattr(r2_config, "resolve", lambda *a, **k: fake_settings)
        monkeypatch.setattr(transfer, "ssh", lambda *a, **k: _completed(returncode=0))
        monkeypatch.setattr(transfer, "push_kit", lambda *a, **k: ("key", 10, False))
        monkeypatch.setattr(transfer, "fetch_to_remote", lambda *a, **k: _completed(returncode=0))
        monkeypatch.setattr(transfer, "push_from_remote", lambda *a, **k: _completed(returncode=0))

        def fake_download(key, local_path, *, settings=None):
            Path(local_path).write_bytes(b"v" * 200_000)

        monkeypatch.setattr(remote.r2_presign, "download", fake_download)
        monkeypatch.setattr(remote.r2_presign, "copy_object", lambda *a, **k: None)
        monkeypatch.setattr(remote.r2_presign, "delete_object", lambda *a, **k: None)
        monkeypatch.setattr(remote, "_stream_command",
                            lambda args, *, timeout_s, on_line: 0)
        monkeypatch.setattr(remote, "probe_duration", lambda path: 30.0)
        # record_external_upload is intentionally left REAL.

        composer = make_composer_manifest(tmp_path)
        job_kit = make_job_manifest(tmp_path)
        job_dir = tmp_path / "job-1"
        job_dir.mkdir()
        assert not (job_dir / "final.mp4").exists()  # first-ever render for this job
        job = SimpleNamespace(dir=job_dir, emit=lambda *a, **k: None)
        item = remote.BatchItem(job_id="job-1", kit=job_kit, timeout_s=60.0,
                                expected_duration_seconds=30.0, job=job)

        results = remote.render_batch(_rental(), composer, [item], key_path="/key",
                                       max_concurrency=16)

        assert results[0].status == "done", results[0].error
        assert (job_dir / "final.mp4").exists()
        assert (job_dir / "final.mp4").read_bytes() == b"v" * 200_000
        manifest = json.loads((job_dir / ".r2sync.json").read_text(encoding="utf-8"))
        assert manifest["files"]["final.mp4"]["key"] == "projects/autoedit-jobs/job-1/final.mp4"

    def test_npm_ci_invoked_exactly_once_for_multiple_items(self, monkeypatch, happy_batch_env):
        composer = make_composer_manifest(happy_batch_env.tmp_path)
        ssh_calls = []

        def recording_ssh(host, port, key_path, command, *, timeout=60.0):
            ssh_calls.append(command)
            return _completed(returncode=0)

        monkeypatch.setattr(transfer, "ssh", recording_ssh)
        items = [
            remote.BatchItem(job_id=f"job-{i}",
                             kit=make_job_manifest(happy_batch_env.tmp_path, job_id=f"job-{i}",
                                                   name=f"job-kit-{i}"),
                             timeout_s=60.0)
            for i in range(3)
        ]
        results = remote.render_batch(_rental(), composer, items, key_path="/key",
                                       max_concurrency=16)

        assert all(r.status == "done" for r in results)
        npm_ci_calls = [c for c in ssh_calls if "npm ci" in c]
        assert len(npm_ci_calls) == 1

    def test_on_output_called_incrementally_per_item(self, happy_batch_env):
        composer = make_composer_manifest(happy_batch_env.tmp_path)
        items = [
            remote.BatchItem(job_id=f"job-{i}",
                             kit=make_job_manifest(happy_batch_env.tmp_path, job_id=f"job-{i}",
                                                   name=f"job-kit-{i}"),
                             timeout_s=60.0)
            for i in range(2)
        ]
        seen = []
        remote.render_batch(_rental(), composer, items, key_path="/key", max_concurrency=16,
                            on_output=lambda item, result: seen.append(item.job_id))
        assert seen == ["job-0", "job-1"]


class TestRenderBatchSharedSetupFailures:
    def test_missing_ssh_host_raises_before_any_network_call(self, happy_batch_env):
        composer = make_composer_manifest(happy_batch_env.tmp_path)
        job_kit = make_job_manifest(happy_batch_env.tmp_path)
        rental = SimpleNamespace(id=1, ssh_host=None, ssh_port=None)
        with pytest.raises(remote.CloudRenderError, match="ssh_host"):
            remote.render_batch(rental, composer, [remote.BatchItem(job_id="job-1", kit=job_kit)],
                                key_path="/key", max_concurrency=8)

    def test_ready_file_timeout_raises(self, monkeypatch, happy_batch_env):
        monkeypatch.setattr(transfer, "ssh", lambda *a, **k: _completed(returncode=1))
        composer = make_composer_manifest(happy_batch_env.tmp_path)
        job_kit = make_job_manifest(happy_batch_env.tmp_path)
        with pytest.raises(remote.CloudRenderError, match="openmontage-ready"):
            remote.render_batch(_rental(), composer, [remote.BatchItem(job_id="job-1", kit=job_kit)],
                                key_path="/key", max_concurrency=8, ready_timeout_s=0.02)

    def test_composer_upload_failure_raises(self, monkeypatch, happy_batch_env):
        def failing_push_kit(*a, **k):
            raise RuntimeError("no space")

        monkeypatch.setattr(transfer, "push_kit", failing_push_kit)
        composer = make_composer_manifest(happy_batch_env.tmp_path)
        job_kit = make_job_manifest(happy_batch_env.tmp_path)
        with pytest.raises(remote.CloudRenderError, match="push kit composer"):
            remote.render_batch(_rental(), composer, [remote.BatchItem(job_id="job-1", kit=job_kit)],
                                key_path="/key", max_concurrency=8)

    def test_npm_ci_failure_raises(self, monkeypatch, happy_batch_env):
        def fake_ssh(host, port, key_path, command, *, timeout=60.0):
            if "npm ci" in command:
                return _completed(returncode=1, stdout="registry error")
            return _completed(returncode=0)

        monkeypatch.setattr(transfer, "ssh", fake_ssh)
        composer = make_composer_manifest(happy_batch_env.tmp_path)
        job_kit = make_job_manifest(happy_batch_env.tmp_path)
        with pytest.raises(remote.CloudRenderError, match="npm ci"):
            remote.render_batch(_rental(), composer, [remote.BatchItem(job_id="job-1", kit=job_kit)],
                                key_path="/key", max_concurrency=8)


class TestRenderBatchPerItemFailuresDoNotAbortBatch:
    def test_one_failing_item_does_not_stop_the_rest(self, monkeypatch, happy_batch_env):
        composer = make_composer_manifest(happy_batch_env.tmp_path)
        items = [
            remote.BatchItem(job_id=f"job-{i}",
                             kit=make_job_manifest(happy_batch_env.tmp_path, job_id=f"job-{i}",
                                                   name=f"job-kit-{i}"),
                             timeout_s=60.0)
            for i in range(4)
        ]

        calls = {"n": 0}

        def flaky_stream(args, *, timeout_s, on_line):
            calls["n"] += 1
            if calls["n"] == 2:
                return 1  # job-1 fails its render step
            return on_line("Rendered 50/100, x") or 0

        monkeypatch.setattr(remote, "_stream_command", flaky_stream)
        results = remote.render_batch(_rental(), composer, items, key_path="/key",
                                       max_concurrency=16)

        by_id = {r.job_id: r for r in results}
        assert by_id["job-0"].status == "done"
        assert by_id["job-1"].status == "failed"
        assert "exit 1" in by_id["job-1"].error
        assert by_id["job-2"].status == "done"
        assert by_id["job-3"].status == "done"

    def test_push_from_remote_failure_marks_only_that_item_failed(self, monkeypatch, happy_batch_env):
        composer = make_composer_manifest(happy_batch_env.tmp_path)
        job_kit = make_job_manifest(happy_batch_env.tmp_path)
        monkeypatch.setattr(transfer, "push_from_remote",
                            lambda *a, **k: _completed(returncode=1, stderr="conn reset"))
        results = remote.render_batch(_rental(), composer,
                                       [remote.BatchItem(job_id="job-1", kit=job_kit)],
                                       key_path="/key", max_concurrency=8)
        assert results[0].status == "failed"
        assert "đẩy kết quả render" in results[0].error

    def test_output_too_small_marks_item_failed_not_raised(self, monkeypatch, happy_batch_env):
        def fake_download(key, local_path, *, settings=None):
            Path(local_path).write_bytes(b"x" * 10)  # far below MIN_OUTPUT_BYTES

        monkeypatch.setattr(remote.r2_presign, "download", fake_download)
        composer = make_composer_manifest(happy_batch_env.tmp_path)
        job_kit = make_job_manifest(happy_batch_env.tmp_path)
        results = remote.render_batch(_rental(), composer,
                                       [remote.BatchItem(job_id="job-1", kit=job_kit)],
                                       key_path="/key", max_concurrency=8)
        assert results[0].status == "failed"
        assert "quá nhỏ" in results[0].error

    def test_duration_drift_beyond_tolerance_marks_item_failed(self, monkeypatch, happy_batch_env):
        monkeypatch.setattr(remote, "probe_duration", lambda path: 45.0)  # expected 30.0
        composer = make_composer_manifest(happy_batch_env.tmp_path)
        job_kit = make_job_manifest(happy_batch_env.tmp_path)
        item = remote.BatchItem(job_id="job-1", kit=job_kit, timeout_s=30.0,
                                expected_duration_seconds=30.0)
        results = remote.render_batch(_rental(), composer, [item], key_path="/key",
                                       max_concurrency=8)
        assert results[0].status == "failed"
        assert "lệch" in results[0].error

    def test_duration_within_one_percent_tolerance_passes(self, monkeypatch, happy_batch_env):
        monkeypatch.setattr(remote, "probe_duration", lambda path: 30.2)  # within 1% of 30.0
        composer = make_composer_manifest(happy_batch_env.tmp_path)
        job_kit = make_job_manifest(happy_batch_env.tmp_path)
        item = remote.BatchItem(job_id="job-1", kit=job_kit, timeout_s=30.0,
                                expected_duration_seconds=30.0)
        results = remote.render_batch(_rental(), composer, [item], key_path="/key",
                                       max_concurrency=8)
        assert results[0].status == "done"
        assert results[0].result.duration_seconds == 30.2


class TestRenderBatchDeadlineGuard:
    def test_deadline_exceeded_leaves_remainder_pending_without_raising(self, happy_batch_env):
        """job-0/job-1 have a small `timeout_s` that fits before the
        deadline; job-2/job-3 claim a `timeout_s` that alone would run past
        it -- the guard must stop at job-2 and leave everything after it
        `pending`, with no exception escaping."""
        import time as time_module
        now = int(time_module.time())
        composer = make_composer_manifest(happy_batch_env.tmp_path)
        timeouts = [10.0, 10.0, 200.0, 200.0]
        items = [
            remote.BatchItem(job_id=f"job-{i}",
                             kit=make_job_manifest(happy_batch_env.tmp_path, job_id=f"job-{i}",
                                                   name=f"job-kit-{i}"),
                             timeout_s=timeouts[i])
            for i in range(4)
        ]

        results = remote.render_batch(
            _rental(), composer, items, key_path="/key", max_concurrency=16,
            deadline_epoch=now + 100, safety_margin_s=0.0)

        by_id = {r.job_id: r for r in results}
        assert by_id["job-0"].status == "done"
        assert by_id["job-1"].status == "done"
        assert by_id["job-2"].status == "pending"
        assert by_id["job-3"].status == "pending"


# ---------------------------------------------------------------------------
# render_now -- guaranteed destroy in every failure mode
# ---------------------------------------------------------------------------

BASE_CONFIG = {
    "enabled": True,
    "offer_query": "reliability>0.95 cpu_cores_effective>=32",
    "pricing_mode": "bid",
    "max_dph_usd": 0.15,
    "image": "node:22-bookworm",
    "disk_gb": 12,
    "apt_packages": ["ffmpeg"],
    "max_runtime_minutes": 30,
    "ssh_key_path": "~/.ssh/openmontage_cloud_render",
    "render_seconds_per_video_second": 1.9,
}


class FakeJob:
    def __init__(self, tmp_path, duration=30.0):
        self.job_id = "job-260806"
        self.dir = tmp_path / "job"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.final_path = self.dir / "final.mp4"
        self.emitted: list[tuple] = []
        self._duration = duration

    def props_path(self, version):
        path = self.dir / f"props_v{version}.json"
        if not path.exists():
            path.write_text(json.dumps({"durationSeconds": self._duration}), encoding="utf-8")
        return path

    def load(self):
        return {"options": {"render_crf": 17, "render_jpeg_quality": 100}}

    def emit(self, *args, **kwargs):
        self.emitted.append((args, kwargs))


@dataclass
class FakeOffer:
    id: int = 501
    dph: float = 0.05
    cpu_cores_effective: float | None = 32.0
    ram_gb: float | None = 64.0
    disk_avail_gb: float | None = 100.0
    reliability: float | None = 0.99
    geolocation: str | None = "US"
    gpu_name: str | None = None


@dataclass
class FakeRental:
    id: int = 999
    label: str | None = "openmontage-x"
    actual_status: str | None = "running"
    ssh_host: str | None = "1.2.3.4"
    ssh_port: int | None = 22
    dph_total: float | None = 0.05


@pytest.fixture()
def render_now_env(monkeypatch, tmp_path):
    """Fully faked rent/render/destroy stack. Each test overrides exactly one
    step to force a failure and asserts `destroy` still ran exactly once."""
    recorder = SimpleNamespace(
        destroy_calls=[], close_calls=[], cleanup_calls=[], rent_calls=[], reap_calls=[])

    monkeypatch.setattr(ledger, "reap", lambda *a, **k: recorder.reap_calls.append(1))

    def fake_close(intent_id, **kwargs):
        recorder.close_calls.append((intent_id, kwargs))
        return {"intent_id": intent_id, **kwargs}

    monkeypatch.setattr(ledger, "close", fake_close)

    composer_manifest = make_composer_manifest(tmp_path)
    job_manifest = make_job_manifest(tmp_path)

    def fake_build_composer_kit(**kwargs):
        return composer_manifest

    def fake_build_job_kit(job, version, **kwargs):
        return job_manifest

    def fake_cleanup_kit(built_manifest):
        recorder.cleanup_calls.append(built_manifest)

    monkeypatch.setattr(kit, "build_composer_kit", fake_build_composer_kit)
    monkeypatch.setattr(kit, "build_job_kit", fake_build_job_kit)
    monkeypatch.setattr(kit, "cleanup_kit", fake_cleanup_kit)

    monkeypatch.setattr(vast_client, "search", lambda *a, **k: [FakeOffer()])

    def fake_rent(offer_id, offer_dph, **kwargs):
        recorder.rent_calls.append((offer_id, offer_dph, kwargs))
        return FakeRental()

    monkeypatch.setattr(vast_client, "rent", fake_rent)
    monkeypatch.setattr(vast_client, "wait_running", lambda instance_id, **k: FakeRental())

    def fake_destroy(instance_id, **kwargs):
        recorder.destroy_calls.append(instance_id)

    monkeypatch.setattr(vast_client, "destroy", fake_destroy)
    monkeypatch.setattr(setup_key, "public_key_text", lambda *a, **k: "ssh-ed25519 AAAA test")

    # render_batch success by default; tests override via
    # monkeypatch.setattr(remote, "render_batch", ...)
    def fake_render_batch(rental, composer, items, **kwargs):
        local_output = tmp_path / "downloaded.mp4"
        local_output.write_bytes(b"v" * 200_000)
        # Mirrors the real `_render_one_item`, which is now the sole writer
        # of `job.final_path` (render_now/queue.py no longer copy it).
        job = items[0].job
        if job is not None:
            job.final_path.parent.mkdir(parents=True, exist_ok=True)
            job.final_path.write_bytes(b"v" * 200_000)
        result = remote.RemoteRenderResult(
            local_output_path=local_output, size_bytes=200_000,
            duration_seconds=30.0, wall_seconds=12.0)
        return [remote.BatchItemResult(job_id=items[0].job_id, status="done", result=result)]

    monkeypatch.setattr(remote, "render_batch", fake_render_batch)

    return recorder


class TestRenderNowHappyPath:
    def test_returns_result_and_destroys_once(self, render_now_env, tmp_path):
        job = FakeJob(tmp_path)
        result = remote.render_now(job, 1, config=BASE_CONFIG)

        assert result.instance_id == 999
        assert result.offer_id == 501
        assert job.final_path.read_bytes() == b"v" * 200_000
        assert render_now_env.destroy_calls == [999]
        assert len(render_now_env.close_calls) == 1
        assert len(render_now_env.cleanup_calls) == 2  # composer kit + job kit
        assert len(render_now_env.reap_calls) == 1

    def test_cost_hook_receives_selected_offer(self, render_now_env, tmp_path):
        job = FakeJob(tmp_path)
        selected = []
        remote.render_now(job, 1, config=BASE_CONFIG,
                          cost_hooks={"on_offer_selected": lambda offer: selected.append(offer)})
        assert selected and selected[0].id == 501


class TestRenderNowCeilingEnforcement:
    """`max_total_usd` must bound the actual rental duration, not just be
    validated once and discarded (real gap found in code review: the tool
    layer checked it against the config ceiling but never forwarded it)."""

    def test_max_total_usd_clamps_deadline_below_config_default(self, render_now_env, tmp_path):
        job = FakeJob(tmp_path)
        # FakeOffer.dph=0.05; config max_runtime_minutes=30 -> unclamped
        # deadline would be ~30 min out. max_total_usd=0.01 only affords
        # 0.01/0.05*60 = 12 minutes.
        before = int(time.time())
        remote.render_now(job, 1, config=BASE_CONFIG, max_total_usd=0.01)
        after = int(time.time())

        assert len(render_now_env.rent_calls) == 1
        deadline_epoch = render_now_env.rent_calls[0][2]["deadline_epoch"]
        clamped_minutes = (deadline_epoch - before) / 60.0
        assert 10.0 <= clamped_minutes <= 13.0, (
            f"expected ~12 min (ceiling-bound), got {clamped_minutes:.1f} min "
            f"-- max_total_usd did not clamp the deadline")
        # sanity: definitely not the unclamped 30-minute default
        assert deadline_epoch < before + 20 * 60
        assert deadline_epoch <= after + 13 * 60

    def test_max_total_usd_too_low_for_offer_price_refuses_before_renting(
            self, render_now_env, tmp_path):
        job = FakeJob(tmp_path)
        # 0.001/0.05*60 = 1.2 min, below MIN_VIABLE_RUNTIME_MINUTES (5) -- must
        # refuse rather than rent a box that cannot possibly finish booting.
        with pytest.raises(remote.CloudRenderError, match="qua thap"):
            remote.render_now(job, 1, config=BASE_CONFIG, max_total_usd=0.001)

        assert render_now_env.rent_calls == []
        assert render_now_env.destroy_calls == []
        assert len(render_now_env.cleanup_calls) == 2  # composer + job kit still cleaned up

    def test_no_max_total_usd_keeps_config_default_unclamped(self, render_now_env, tmp_path):
        job = FakeJob(tmp_path)
        before = int(time.time())
        remote.render_now(job, 1, config=BASE_CONFIG)  # no max_total_usd -> back-compat default

        deadline_epoch = render_now_env.rent_calls[0][2]["deadline_epoch"]
        assert 29 * 60 <= deadline_epoch - before <= 31 * 60

    def test_disabled_config_refuses_before_touching_the_ledger(self, render_now_env, tmp_path):
        job = FakeJob(tmp_path)
        disabled_config = {**BASE_CONFIG, "enabled": False}

        with pytest.raises(remote.CloudRenderError, match="enabled: false"):
            remote.render_now(job, 1, config=disabled_config)

        # defense-in-depth check fires before even reap() -- proves this is a
        # real precondition, not something bolted on after the fact
        assert render_now_env.reap_calls == []
        assert render_now_env.rent_calls == []


class TestRenderNowFailureModesAlwaysDestroyOnce:
    def test_wait_running_timeout_destroys_once(self, render_now_env, tmp_path, monkeypatch):
        monkeypatch.setattr(vast_client, "wait_running",
                            lambda instance_id, **k: (_ for _ in ()).throw(
                                vast_client.CloudRenderError("timed out")))
        job = FakeJob(tmp_path)
        with pytest.raises(vast_client.CloudRenderError):
            remote.render_now(job, 1, config=BASE_CONFIG)
        assert render_now_env.destroy_calls == [999]
        assert len(render_now_env.close_calls) == 1
        assert len(render_now_env.cleanup_calls) == 2

    def test_render_batch_item_failure_destroys_once(self, render_now_env, tmp_path, monkeypatch):
        def failing_render_batch(rental, composer, items, **kwargs):
            return [remote.BatchItemResult(job_id=items[0].job_id, status="failed",
                                           error="npm ci thất bại (exit 1)")]

        monkeypatch.setattr(remote, "render_batch", failing_render_batch)
        job = FakeJob(tmp_path)
        with pytest.raises(remote.CloudRenderError, match="npm ci"):
            remote.render_now(job, 1, config=BASE_CONFIG)
        assert render_now_env.destroy_calls == [999]
        assert len(render_now_env.close_calls) == 1
        assert len(render_now_env.cleanup_calls) == 2

    def test_render_batch_raising_destroys_once(self, render_now_env, tmp_path, monkeypatch):
        def raising_render_batch(rental, composer, items, **kwargs):
            raise remote.CloudRenderError("npm ci thất bại (exit 1)")

        monkeypatch.setattr(remote, "render_batch", raising_render_batch)
        job = FakeJob(tmp_path)
        with pytest.raises(remote.CloudRenderError, match="npm ci"):
            remote.render_now(job, 1, config=BASE_CONFIG)
        assert render_now_env.destroy_calls == [999]
        assert len(render_now_env.close_calls) == 1
        assert len(render_now_env.cleanup_calls) == 2

    def test_keyboard_interrupt_mid_render_destroys_once(self, render_now_env, tmp_path, monkeypatch):
        def interrupted_render_batch(rental, composer, items, **kwargs):
            raise KeyboardInterrupt()

        monkeypatch.setattr(remote, "render_batch", interrupted_render_batch)
        job = FakeJob(tmp_path)
        with pytest.raises(KeyboardInterrupt):
            remote.render_now(job, 1, config=BASE_CONFIG)
        assert render_now_env.destroy_calls == [999]
        assert len(render_now_env.close_calls) == 1
        assert len(render_now_env.cleanup_calls) == 2

    def test_no_eligible_offer_never_rents_or_destroys(self, render_now_env, tmp_path, monkeypatch):
        monkeypatch.setattr(vast_client, "search", lambda *a, **k: [])
        job = FakeJob(tmp_path)
        with pytest.raises(remote.CloudRenderError, match="Không có offer"):
            remote.render_now(job, 1, config=BASE_CONFIG)
        assert render_now_env.destroy_calls == []
        assert render_now_env.rent_calls == []
        assert len(render_now_env.cleanup_calls) == 2  # both kits still cleaned up

    def test_destroy_failure_leaves_ledger_active_not_closed(self, render_now_env, tmp_path, monkeypatch):
        """Per the phase's step 7: a failed destroy must log loudly and leave
        the ledger record `active` (for the reaper to retry), not silently
        close it as if the rental were gone."""
        def failing_destroy(instance_id, **kwargs):
            raise RuntimeError("vast API 500")

        monkeypatch.setattr(vast_client, "destroy", failing_destroy)
        job = FakeJob(tmp_path)

        result = remote.render_now(job, 1, config=BASE_CONFIG)  # render itself still succeeds

        assert result.instance_id == 999
        assert render_now_env.close_calls == []  # never closed -- reaper must retry
        assert any("Không destroy được" in e[0][2] for e in job.emitted if e[0][0] == "warning")


# ---------------------------------------------------------------------------
# Destroy guarantee, parameterized over every failure point in the coverage
# matrix's "remote (P2)" row: destroy must be called exactly once for each of
# success, wait timeout, ready timeout, npm ci fail, render fail, R2 transfer
# fail, KeyboardInterrupt, SystemExit. This is a single meta-test on purpose:
# adding a 9th failure mode later must add a case to DESTROY_GUARANTEE_CASES,
# not be silently forgotten the way it would be if each case lived in its own
# unrelated test.
# ---------------------------------------------------------------------------

def _apply_success(monkeypatch):
    pass  # render_now_env's defaults already succeed end to end


def _apply_wait_running_timeout(monkeypatch):
    def raise_timeout(instance_id, **k):
        raise vast_client.CloudRenderError("wait_running timed out")
    monkeypatch.setattr(vast_client, "wait_running", raise_timeout)


def _apply_ready_timeout(monkeypatch):
    def raise_ready_timeout(rental, composer, items, **kwargs):
        raise remote.CloudRenderError("openmontage-ready timeout")
    monkeypatch.setattr(remote, "render_batch", raise_ready_timeout)


def _apply_npm_ci_fail(monkeypatch):
    def raise_npm_ci_fail(rental, composer, items, **kwargs):
        raise remote.CloudRenderError("npm ci thất bại (exit 1)")
    monkeypatch.setattr(remote, "render_batch", raise_npm_ci_fail)


def _apply_render_fail(monkeypatch):
    def failing_render(rental, composer, items, **kwargs):
        return [remote.BatchItemResult(job_id=items[0].job_id, status="failed",
                                       error="remote render lỗi (exit 1)")]
    monkeypatch.setattr(remote, "render_batch", failing_render)


def _apply_r2_transfer_fail(monkeypatch):
    def failing_transfer(rental, composer, items, **kwargs):
        return [remote.BatchItemResult(job_id=items[0].job_id, status="failed",
                                       error="đẩy kết quả render lên R2 thất bại: conn reset")]
    monkeypatch.setattr(remote, "render_batch", failing_transfer)


def _apply_keyboard_interrupt(monkeypatch):
    def raise_keyboard_interrupt(rental, composer, items, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(remote, "render_batch", raise_keyboard_interrupt)


def _apply_system_exit(monkeypatch):
    def raise_system_exit(rental, composer, items, **kwargs):
        raise SystemExit(1)
    monkeypatch.setattr(remote, "render_batch", raise_system_exit)


DESTROY_GUARANTEE_CASES = [
    # The 3rd element is a zero-arg callable, not a class, resolved inside
    # the test body at RUN time rather than captured here at collection
    # time: `tests/test_cloud_render_ledger_reaper.py`'s `cloud_render_modules`
    # fixture does `importlib.reload(vast_client)` for its own (unrelated)
    # tests, which rebinds a brand-new `CloudRenderError` class object onto
    # the module. Since that fixture's tests collate alphabetically before
    # this file's, a class reference captured here at import time would be
    # the *pre-reload* class -- a `pytest.raises()` against it would then
    # never match the *post-reload* class the code under test actually
    # raises, purely because of module import ordering. A callable
    # re-reads `vast_client.CloudRenderError` fresh every time.
    ("success", _apply_success, None),
    ("wait_running_timeout", _apply_wait_running_timeout, lambda: vast_client.CloudRenderError),
    ("ready_timeout", _apply_ready_timeout, lambda: remote.CloudRenderError),
    ("npm_ci_fail", _apply_npm_ci_fail, lambda: remote.CloudRenderError),
    ("render_fail", _apply_render_fail, lambda: remote.CloudRenderError),
    ("r2_transfer_fail", _apply_r2_transfer_fail, lambda: remote.CloudRenderError),
    ("keyboard_interrupt", _apply_keyboard_interrupt, lambda: KeyboardInterrupt),
    ("system_exit", _apply_system_exit, lambda: SystemExit),
]


class TestDestroyGuaranteeAcrossAllEightFailurePoints:
    @pytest.mark.parametrize(
        "label,apply_patch,expected_exc_factory", DESTROY_GUARANTEE_CASES,
        ids=[case[0] for case in DESTROY_GUARANTEE_CASES])
    def test_destroy_called_exactly_once(self, render_now_env, tmp_path, monkeypatch,
                                         label, apply_patch, expected_exc_factory):
        apply_patch(monkeypatch)
        job = FakeJob(tmp_path)

        if expected_exc_factory is None:
            remote.render_now(job, 1, config=BASE_CONFIG)
        else:
            with pytest.raises(expected_exc_factory()):
                remote.render_now(job, 1, config=BASE_CONFIG)

        assert render_now_env.destroy_calls == [999], (
            f"destroy not called exactly once for failure point {label!r}")

    def test_all_eight_coverage_matrix_failure_points_are_present(self):
        """Guards the matrix itself: a case silently dropped from
        DESTROY_GUARANTEE_CASES would make the parametrized test above pass
        trivially on fewer cases without anyone noticing."""
        labels = {case[0] for case in DESTROY_GUARANTEE_CASES}
        assert labels == {
            "success", "wait_running_timeout", "ready_timeout", "npm_ci_fail",
            "render_fail", "r2_transfer_fail", "keyboard_interrupt", "system_exit",
        }
