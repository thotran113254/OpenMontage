"""Surviving a server restart mid-render.

`_execute` starts each run as its own process group so `kill_tree` can signal
the whole tree — a side effect of that isolation is that the subprocess also
survives the SERVER's own death: it is nobody's child, so nothing reaps it,
nothing notices it, and (before this file's changes) nothing could cancel it.

These tests use a real long-lived child process standing in for the CLI
subprocess, so "the queue notices/kills it" is proven against the OS, not
against a mock.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from lib.talking_head_edit.job_store import JobStore
from server.queue_worker import (
    AlreadyRunningError, JobQueue, QueuedRun, _pid_alive,
)


def _spawn_sleeper(seconds: float = 30.0) -> subprocess.Popen[str]:
    isolation = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                 if sys.platform == "win32" else {"start_new_session": True})
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({seconds})"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **isolation,
    )


@pytest.fixture()
def store(tmp_path):
    return JobStore(root=tmp_path / "jobs")


@pytest.fixture()
def orphaned_job(store):
    """A job whose job.json claims a still-alive `worker_pid` this queue never started."""
    source = store.root.parent / "footage.mp4"
    source.write_bytes(b"video")
    job = store.create(source, {}, title="orphan")
    process = _spawn_sleeper()
    job.update(worker_pid=process.pid, status="running")
    yield job, process
    if process.poll() is None:
        process.kill()
        process.wait(timeout=5)


class TestReconcileOnStartup:
    def test_adopts_a_live_orphan(self, store, orphaned_job):
        job, process = orphaned_job
        try:
            q = JobQueue(store)
            assert q.status()["running"] == job.job_id
        finally:
            process.kill()

    def test_ignores_a_dead_pid(self, store):
        """A crash, not a restart mid-render: the recorded pid never comes back."""
        source = store.root.parent / "footage.mp4"
        source.write_bytes(b"video")
        job = store.create(source, {}, title="stale")
        job.update(worker_pid=999_999_999, status="running")  # not a real pid

        q = JobQueue(store)
        assert q.status()["running"] is None

    def test_no_jobs_at_all_does_not_crash(self, tmp_path):
        JobQueue(JobStore(root=tmp_path / "empty-jobs"))


class TestAdoptedStatusSelfHeals:
    def test_running_flips_to_none_once_the_pid_exits(self, store, orphaned_job):
        job, process = orphaned_job
        q = JobQueue(store)
        assert q.status()["running"] == job.job_id

        process.kill()
        process.wait(timeout=5)
        deadline = time.time() + 5
        while _pid_alive(process.pid) and time.time() < deadline:
            time.sleep(0.1)

        assert q.status()["running"] is None


class TestCancelAdopted:
    def test_kills_the_orphan_and_marks_the_job_cancelled(self, store, orphaned_job):
        job, process = orphaned_job
        q = JobQueue(store)

        assert q.cancel(job.job_id) is True

        deadline = time.time() + 10
        while _pid_alive(process.pid) and time.time() < deadline:
            time.sleep(0.2)
        assert not _pid_alive(process.pid), "orphan vẫn sống sau khi cancel"
        assert job.load()["status"] == "cancelled"

    def test_a_second_cancel_is_a_harmless_no_op(self, store, orphaned_job):
        job, process = orphaned_job
        q = JobQueue(store)
        q.cancel(job.job_id)
        assert q.cancel(job.job_id) is False


class TestSubmitRefusesADuplicateRun:
    def test_raises_instead_of_queueing_a_second_process(self, store, orphaned_job):
        job, process = orphaned_job
        q = JobQueue(store)
        with pytest.raises(AlreadyRunningError):
            q.submit(QueuedRun(job.job_id))

    def test_submitting_a_different_job_is_unaffected(self, store, orphaned_job):
        job, process = orphaned_job
        other = store.create(store.root.parent / "footage.mp4", {}, title="other")
        try:
            q = JobQueue(store)
            position = q.submit(QueuedRun(other.job_id))
            assert position >= 1
        finally:
            # drain so the worker thread does not try to actually run the CLI
            q.cancel(other.job_id)

    def test_submit_recovers_once_the_orphan_is_gone(self, store, orphaned_job):
        job, process = orphaned_job
        q = JobQueue(store)
        process.kill()
        process.wait(timeout=5)
        deadline = time.time() + 5
        while _pid_alive(process.pid) and time.time() < deadline:
            time.sleep(0.1)

        position = q.submit(QueuedRun(job.job_id))
        assert position >= 1
        q.cancel(job.job_id)  # drain before the worker thread launches the real CLI


class TestPidAlive:
    def test_true_for_a_live_process(self):
        process = _spawn_sleeper(5)
        try:
            assert _pid_alive(process.pid) is True
        finally:
            process.kill()
            process.wait(timeout=5)

    def test_false_after_it_exits(self):
        process = subprocess.Popen([sys.executable, "-c", "pass"])
        process.wait()
        assert _pid_alive(process.pid) is False
