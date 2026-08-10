"""Tests for lib/cloud_render/queue.py's `flush()` -- rent once, render N
jobs, destroy exactly once, dequeue only what actually rendered.

Two layers of test double:
- `TestFlushOrchestration` fakes `remote.render_batch` itself (already fully
  covered by tests/test_cloud_render_remote.py) so these tests focus purely
  on flush()'s own job: rent/destroy-once, incremental dequeue, retry/
  `blocked` accounting, and the no-eligible-offer/FlushError paths.
- `TestFlushEndToEndSharedComposerUpload` uses the REAL `kit.build_job_kit`/
  `build_composer_kit`/`remote.render_batch`, faking only the network edges
  (`transfer.ssh`/`push_kit`/`fetch_to_remote`/`push_from_remote`, the R2
  presign/manifest calls, `vast_client.*`), to prove the actual point of
  batching: one `npm ci` for N jobs.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.cloud_render import kit, ledger, queue, remote, setup_key, transfer, vast_client
from lib.r2_storage import config as r2_config


def _completed(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class FakeFlushJob:
    """Minimal job double: only the surface `flush()` reads."""

    def __init__(self, tmp_path, job_id, *, duration=30.0, current_version=1):
        self.job_id = job_id
        self.dir = tmp_path / job_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.final_path = self.dir / "final.mp4"
        self.render_public_dir = self.dir / "render_public"
        self.render_public_dir.mkdir(exist_ok=True)
        self._duration = duration
        self._current_version = current_version

    def props_path(self, version):
        path = self.dir / f"props_v{version}.json"
        if not path.exists():
            path.write_text(json.dumps({"durationSeconds": self._duration}), encoding="utf-8")
        return path

    def load(self):
        return {"current_version": self._current_version, "options": {}}


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


BATCH_CONFIG = {
    "enabled": True,
    "offer_query": "reliability>0.95 cpu_cores_effective>=32",
    "pricing_mode": "bid",
    "max_dph_usd": 0.15,
    "image": "node:22-bookworm",
    "disk_gb": 12,
    "apt_packages": ["ffmpeg"],
    "max_runtime_minutes": 60,
    "max_batch_runtime_minutes": 90,
    "ssh_key_path": "~/.ssh/openmontage_cloud_render",
    "render_seconds_per_video_second": 1.9,
}


@pytest.fixture()
def mocked_flush_env(tmp_path, monkeypatch):
    """Every network/rental call faked; `remote.render_batch` itself is
    faked too via `set_render_batch_outcomes` (called per-test) so these
    tests can script exactly which job lands `done`/`failed`/`pending`."""
    monkeypatch.setattr(queue, "STATE_DIR", tmp_path)
    monkeypatch.setattr(queue, "QUEUE_PATH", tmp_path / "batch-queue.json")
    monkeypatch.setattr(queue, "LOCK_PATH", tmp_path / "batch-queue.lock")

    jobs: dict[str, FakeFlushJob] = {}

    def fake_find_job(job_id):
        if job_id in jobs:
            return jobs[job_id]
        raise FileNotFoundError(job_id)

    monkeypatch.setattr(queue, "find_job", fake_find_job)

    def fake_build_job_kit(job, version, **kwargs):
        kit_dir = tmp_path / f"job-kit-{job.job_id}"
        kit_dir.mkdir(exist_ok=True)
        return kit.JobKitManifest(
            job_id=job.job_id, kit_dir=kit_dir, kit_hash=f"hash-{job.job_id}", size_bytes=10,
            file_count=1, composition_id="MonaTimeline", props_path="props.json",
            public_dir="public", expected_output_name="out/final.mp4",
            estimated_render_seconds=60.0)

    def fake_build_composer_kit(**kwargs):
        kit_dir = tmp_path / "composer-kit"
        kit_dir.mkdir(exist_ok=True)
        return kit.ComposerKitManifest(kit_dir=kit_dir, kit_hash="composerhash", size_bytes=10,
                                       file_count=1)

    monkeypatch.setattr(kit, "build_job_kit", fake_build_job_kit)
    monkeypatch.setattr(kit, "build_composer_kit", fake_build_composer_kit)

    monkeypatch.setattr(ledger, "reap", lambda *a, **k: None)
    close_calls = []
    monkeypatch.setattr(ledger, "close", lambda *a, **k: close_calls.append((a, k)))

    monkeypatch.setattr(vast_client, "search", lambda *a, **k: [FakeOffer()])
    rent_calls = []

    def fake_rent(offer_id, offer_dph, **kwargs):
        rent_calls.append((offer_id, offer_dph, kwargs))
        return FakeRental()

    monkeypatch.setattr(vast_client, "rent", fake_rent)
    monkeypatch.setattr(vast_client, "wait_running", lambda instance_id, **k: FakeRental())

    destroy_calls = []
    monkeypatch.setattr(vast_client, "destroy", lambda instance_id, **k: destroy_calls.append(instance_id))
    monkeypatch.setattr(setup_key, "public_key_text", lambda *a, **k: "ssh-ed25519 AAAA test")

    def add_job(job_id, **kwargs):
        job = FakeFlushJob(tmp_path, job_id, **kwargs)
        jobs[job_id] = job
        return job

    def set_render_batch_outcomes(outcomes: dict[str, str], errors: dict[str, str] | None = None):
        """outcomes: job_id -> "done" | "failed" | "pending". `on_output` is
        invoked for every "done" item, matching the real render_batch's
        incremental-download contract."""
        errors = errors or {}

        def fake_render_batch(rental, composer, items, *, on_output=None, **kwargs):
            results = []
            for item in items:
                status = outcomes.get(item.job_id, "done")
                if status == "done":
                    local_output = tmp_path / f"downloaded-{item.job_id}.mp4"
                    local_output.write_bytes(b"v" * 200_000)
                    # Mirrors the real `_render_one_item`, which is now the
                    # sole writer of `job.final_path` (queue.py's `_on_output`
                    # no longer copies it).
                    if item.job is not None:
                        item.job.final_path.parent.mkdir(parents=True, exist_ok=True)
                        item.job.final_path.write_bytes(b"v" * 200_000)
                    result = remote.RemoteRenderResult(
                        local_output_path=local_output, size_bytes=200_000,
                        duration_seconds=30.0, wall_seconds=5.0)
                    if on_output is not None:
                        on_output(item, result)
                    results.append(remote.BatchItemResult(job_id=item.job_id, status="done",
                                                           result=result))
                elif status == "failed":
                    results.append(remote.BatchItemResult(
                        job_id=item.job_id, status="failed",
                        error=errors.get(item.job_id, "render lỗi giả lập")))
                else:
                    results.append(remote.BatchItemResult(job_id=item.job_id, status="pending"))
            return results

        monkeypatch.setattr(remote, "render_batch", fake_render_batch)

    return SimpleNamespace(
        tmp_path=tmp_path, jobs=jobs, add_job=add_job,
        set_render_batch_outcomes=set_render_batch_outcomes,
        rent_calls=rent_calls, destroy_calls=destroy_calls, close_calls=close_calls)


class TestFlushOrchestration:
    def test_all_jobs_done_dequeues_all_and_destroys_once(self, mocked_flush_env):
        env = mocked_flush_env
        for job_id in ("job-a", "job-b"):
            env.add_job(job_id)
            queue.enqueue(job_id, version=1, estimated_render_seconds=60.0)
        env.set_render_batch_outcomes({"job-a": "done", "job-b": "done"})

        result = queue.flush(["job-a", "job-b"], config=BATCH_CONFIG)

        assert sorted(result.rendered) == ["job-a", "job-b"]
        assert result.failed == {}
        assert result.pending == []
        assert result.blocked == []
        assert env.destroy_calls == [999]
        assert queue.list_entries() == []
        assert env.jobs["job-a"].final_path.read_bytes() == b"v" * 200_000

    def test_one_failing_job_leaves_only_it_in_queue(self, mocked_flush_env):
        """Success criterion: batch of 4 where job #2 fails -- 1, 3, 4 land
        locally; #2 is `failed` with an error string; destroy called exactly
        once; queue retains only #2."""
        env = mocked_flush_env
        job_ids = ["job-1", "job-2", "job-3", "job-4"]
        for job_id in job_ids:
            env.add_job(job_id)
            queue.enqueue(job_id, version=1, estimated_render_seconds=60.0)
        env.set_render_batch_outcomes(
            {"job-1": "done", "job-2": "failed", "job-3": "done", "job-4": "done"},
            errors={"job-2": "props file lỗi"})

        result = queue.flush(job_ids, config=BATCH_CONFIG)

        assert sorted(result.rendered) == ["job-1", "job-3", "job-4"]
        assert result.failed == {"job-2": "props file lỗi"}
        assert env.destroy_calls == [999]

        remaining = queue.list_entries()
        assert [e.job_id for e in remaining] == ["job-2"]
        assert remaining[0].status == queue.STATUS_FAILED
        assert remaining[0].attempts == 1
        assert remaining[0].last_error == "props file lỗi"

    def test_deadline_stop_leaves_remainder_pending_in_queue(self, mocked_flush_env):
        """Success criterion: deadline expires after job #2 -- jobs 1-2
        done, 3-4 still `pending`, instance destroyed, no exception escapes."""
        env = mocked_flush_env
        job_ids = ["job-1", "job-2", "job-3", "job-4"]
        for job_id in job_ids:
            env.add_job(job_id)
            queue.enqueue(job_id, version=1, estimated_render_seconds=60.0)
        env.set_render_batch_outcomes(
            {"job-1": "done", "job-2": "done", "job-3": "pending", "job-4": "pending"})

        result = queue.flush(job_ids, config=BATCH_CONFIG)  # must not raise

        assert sorted(result.rendered) == ["job-1", "job-2"]
        assert sorted(result.pending) == ["job-3", "job-4"]
        assert env.destroy_calls == [999]

        remaining = {e.job_id: e for e in queue.list_entries()}
        assert set(remaining) == {"job-3", "job-4"}
        assert remaining["job-3"].status == queue.STATUS_PENDING
        assert remaining["job-3"].attempts == 0  # never touched -- not a failure

    def test_third_failure_blocks_the_job(self, mocked_flush_env):
        env = mocked_flush_env
        env.add_job("job-flaky")
        queue.enqueue("job-flaky", version=1, estimated_render_seconds=60.0)
        env.set_render_batch_outcomes({"job-flaky": "failed"}, errors={"job-flaky": "boom"})

        queue.flush(["job-flaky"], config=BATCH_CONFIG)
        queue.flush(["job-flaky"], config=BATCH_CONFIG)
        result = queue.flush(["job-flaky"], config=BATCH_CONFIG)

        assert result.blocked == ["job-flaky"]
        entry = queue.list_entries(prune_missing=False)[0]
        assert entry.attempts == 3
        assert entry.status == queue.STATUS_BLOCKED

    def test_force_flush_is_the_same_function_with_explicit_job_ids(self, mocked_flush_env):
        """Force flush = flush() with an explicit job list, including a job
        that was never enqueued -- no separate code path, no crash."""
        env = mocked_flush_env
        env.add_job("job-unqueued")
        env.set_render_batch_outcomes({"job-unqueued": "done"})

        result = queue.flush(["job-unqueued"], config=BATCH_CONFIG)

        assert result.rendered == ["job-unqueued"]
        assert queue.list_entries() == []  # was never queued, nothing to remove -- no error

    def test_no_eligible_offer_never_rents_and_still_cleans_up_kits(self, mocked_flush_env, monkeypatch):
        env = mocked_flush_env
        env.add_job("job-a")
        queue.enqueue("job-a", version=1, estimated_render_seconds=60.0)
        monkeypatch.setattr(vast_client, "search", lambda *a, **k: [])

        with pytest.raises(remote.CloudRenderError, match="Không có offer"):
            queue.flush(["job-a"], config=BATCH_CONFIG)

        assert env.rent_calls == []
        assert env.destroy_calls == []
        # job-a's queue entry is untouched (still pending) -- renting never happened
        entry = queue.list_entries()[0]
        assert entry.status == queue.STATUS_PENDING

    def test_all_kits_fail_to_build_raises_flush_error_without_renting(self, mocked_flush_env, monkeypatch):
        env = mocked_flush_env
        env.add_job("job-bad")
        queue.enqueue("job-bad", version=1, estimated_render_seconds=60.0)
        monkeypatch.setattr(kit, "build_job_kit",
                            lambda job, version, **k: (_ for _ in ()).throw(
                                kit.KitError("thiếu staging dir")))

        with pytest.raises(queue.FlushError):
            queue.flush(["job-bad"], config=BATCH_CONFIG)

        assert env.rent_calls == []
        entry = queue.list_entries(prune_missing=False)[0]
        assert entry.status == queue.STATUS_FAILED
        assert entry.attempts == 1

    def test_destroy_failure_does_not_raise_and_leaves_ledger_open(self, mocked_flush_env, monkeypatch):
        env = mocked_flush_env
        env.add_job("job-a")
        queue.enqueue("job-a", version=1, estimated_render_seconds=60.0)
        env.set_render_batch_outcomes({"job-a": "done"})
        monkeypatch.setattr(vast_client, "destroy",
                            lambda instance_id, **k: (_ for _ in ()).throw(RuntimeError("API 500")))

        result = queue.flush(["job-a"], config=BATCH_CONFIG)  # must not raise

        assert result.rendered == ["job-a"]
        assert env.close_calls == []  # ledger left active for the reaper to retry


class TestFlushCeilingEnforcement:
    """Same real gap as `render_now` (found in code review): `max_total_usd`
    must bound the batch rental's own deadline, not just be validated once
    and discarded by the tool layer."""

    def test_max_total_usd_clamps_batch_deadline(self, mocked_flush_env):
        env = mocked_flush_env
        env.add_job("job-a")
        queue.enqueue("job-a", version=1, estimated_render_seconds=60.0)
        env.set_render_batch_outcomes({"job-a": "done"})

        # FakeOffer.dph=0.05; BATCH_CONFIG max_batch_runtime_minutes=90 would
        # normally win the min() against OVERHEAD_MINUTES+render estimate.
        # max_total_usd=0.02 only affords 0.02/0.05*60 = 24 minutes.
        before = int(time.time())
        queue.flush(["job-a"], config=BATCH_CONFIG, max_total_usd=0.02)

        assert len(env.rent_calls) == 1
        deadline_epoch = env.rent_calls[0][2]["deadline_epoch"]
        clamped_minutes = (deadline_epoch - before) / 60.0
        assert clamped_minutes <= 25.0, (
            f"expected the ~24-minute ceiling to win, got {clamped_minutes:.1f} min")

    def test_max_total_usd_too_low_for_offer_price_refuses_before_renting(self, mocked_flush_env):
        env = mocked_flush_env
        env.add_job("job-a")
        queue.enqueue("job-a", version=1, estimated_render_seconds=60.0)
        env.set_render_batch_outcomes({"job-a": "done"})

        with pytest.raises(remote.CloudRenderError, match="qua thap"):
            queue.flush(["job-a"], config=BATCH_CONFIG, max_total_usd=0.001)

        assert env.rent_calls == []
        assert env.destroy_calls == []
        # the job must not be penalized with a retry -- the batch never
        # attempted it, the ceiling refused before any per-job work started
        entry = queue.list_entries(prune_missing=False)[0]
        assert entry.status == queue.STATUS_PENDING
        assert entry.attempts == 0

    def test_disabled_config_refuses_before_renting(self, mocked_flush_env):
        env = mocked_flush_env
        env.add_job("job-a")
        queue.enqueue("job-a", version=1, estimated_render_seconds=60.0)
        disabled_config = {**BATCH_CONFIG, "enabled": False}

        with pytest.raises(remote.CloudRenderError, match="enabled: false"):
            queue.flush(["job-a"], config=disabled_config)

        assert env.rent_calls == []


# ---------------------------------------------------------------------------
# End-to-end: real kit-building + real render_batch, faked only at the
# ssh/scp/vast_client edge -- proves the actual point of batching.
# ---------------------------------------------------------------------------

class FakeE2EJob:
    def __init__(self, tmp_path, job_id, *, duration=10.0):
        self.job_id = job_id
        self.dir = tmp_path / job_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.final_path = self.dir / "final.mp4"
        self.render_public_dir = self.dir / "render_public"
        self.render_public_dir.mkdir(exist_ok=True)
        (self.render_public_dir / "src.mp4").write_bytes(b"video-bytes")
        self._duration = duration

    def props_path(self, version):
        path = self.dir / f"props_v{version}.json"
        if not path.exists():
            path.write_text(json.dumps({"durationSeconds": self._duration}), encoding="utf-8")
        return path

    def load(self):
        return {"current_version": 1, "options": {}}


@pytest.fixture()
def fake_composer_dir(tmp_path):
    composer = tmp_path / "composer"
    composer.mkdir()
    (composer / "package.json").write_text("{}", encoding="utf-8")
    (composer / "package-lock.json").write_text("{}", encoding="utf-8")
    (composer / "tsconfig.json").write_text("{}", encoding="utf-8")
    src = composer / "src"
    src.mkdir()
    (src / "index.tsx").write_text("export default {}", encoding="utf-8")
    return composer


class TestFlushEndToEndSharedComposerUpload:
    def test_npm_ci_invoked_exactly_once_regardless_of_job_count(
            self, tmp_path, monkeypatch, fake_composer_dir):
        monkeypatch.setattr(queue, "STATE_DIR", tmp_path)
        monkeypatch.setattr(queue, "QUEUE_PATH", tmp_path / "batch-queue.json")
        monkeypatch.setattr(queue, "LOCK_PATH", tmp_path / "batch-queue.lock")

        jobs = {f"job-{i}": FakeE2EJob(tmp_path, f"job-{i}") for i in range(3)}
        monkeypatch.setattr(queue, "find_job", lambda job_id: jobs[job_id])
        for job_id in jobs:
            queue.enqueue(job_id, version=1, estimated_render_seconds=20.0)

        # Real build_composer_kit, redirected at the real repo's composer dir.
        real_build_composer_kit = kit.build_composer_kit
        monkeypatch.setattr(
            kit, "build_composer_kit",
            lambda **kwargs: real_build_composer_kit(composer_dir=fake_composer_dir))
        # kit.build_job_kit stays REAL (uses each FakeE2EJob's real staged dir).

        monkeypatch.setattr(ledger, "reap", lambda *a, **k: None)
        monkeypatch.setattr(ledger, "close", lambda *a, **k: None)
        monkeypatch.setattr(vast_client, "search", lambda *a, **k: [FakeOffer()])
        monkeypatch.setattr(vast_client, "rent", lambda *a, **k: FakeRental())
        monkeypatch.setattr(vast_client, "wait_running", lambda instance_id, **k: FakeRental())
        destroy_calls = []
        monkeypatch.setattr(vast_client, "destroy", lambda instance_id, **k: destroy_calls.append(instance_id))
        monkeypatch.setattr(setup_key, "public_key_text", lambda *a, **k: "ssh-ed25519 AAAA test")

        ssh_calls = []

        def recording_ssh(host, port, key_path, command, *, timeout=60.0):
            ssh_calls.append(command)
            return _completed(returncode=0)

        monkeypatch.setattr(transfer, "ssh", recording_ssh)
        monkeypatch.setattr(transfer, "push_kit", lambda *a, **k: ("key", 10, False))
        monkeypatch.setattr(transfer, "fetch_to_remote", lambda *a, **k: _completed(returncode=0))
        monkeypatch.setattr(transfer, "push_from_remote", lambda *a, **k: _completed(returncode=0))

        fake_r2_settings = SimpleNamespace(bucket="test-bucket", prefix="projects",
                                           endpoint_url="https://fake.example")
        monkeypatch.setattr(r2_config, "is_configured", lambda: (True, []))
        monkeypatch.setattr(r2_config, "resolve", lambda *a, **k: fake_r2_settings)

        def fake_download(key, local_path, *, settings=None):
            Path(local_path).write_bytes(b"v" * 200_000)

        monkeypatch.setattr(remote.r2_presign, "download", fake_download)
        monkeypatch.setattr(remote.r2_presign, "copy_object", lambda *a, **k: None)
        monkeypatch.setattr(remote.r2_presign, "delete_object", lambda *a, **k: None)
        monkeypatch.setattr(remote.r2_manifest, "record_external_upload", lambda *a, **k: None)
        monkeypatch.setattr(remote, "_stream_command",
                            lambda args, *, timeout_s, on_line: 0)
        monkeypatch.setattr(remote, "probe_duration", lambda path: 10.0)

        result = queue.flush(list(jobs), config=BATCH_CONFIG)

        assert sorted(result.rendered) == sorted(jobs)
        assert result.failed == {}
        npm_ci_calls = [c for c in ssh_calls if "npm ci" in c]
        assert len(npm_ci_calls) == 1
        assert destroy_calls == [999]
        for job in jobs.values():
            assert job.final_path.read_bytes() == b"v" * 200_000
