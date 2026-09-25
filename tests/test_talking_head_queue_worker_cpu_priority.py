"""`low_priority_prefix()` wired into `JobQueue._execute`'s subprocess launch.

A separate file from `test_talking_head_queue_worker_reconcile.py` (owned by
the pipeline-intent phase concurrently) so this addition never collides with
in-flight edits there. Launches a real `python -c` sleeper through the actual
prefixed command path (`nice`/`ionice` exec in place on POSIX) and proves the
pid stays killable through `JobQueue.cancel()` — the concern the VPS's shared
cores raised (see `lib/talking_head_edit/cpu_budget.py`).
"""

from __future__ import annotations

import sys
import time

import pytest

from lib.talking_head_edit.job_store import JobStore
from server.queue_worker import JobQueue, QueuedRun, _pid_alive


@pytest.fixture()
def store(tmp_path):
    return JobStore(root=tmp_path / "jobs")


@pytest.fixture()
def sleeper_queue(store, monkeypatch):
    """A real `JobQueue` whose command is a tiny sleeper instead of the CLI,
    still built through the real `_build_command` -> `low_priority_prefix()`
    path (see `queue_worker.JobQueue._build_command`'s docstring)."""
    queue = JobQueue(store)
    original_build = queue._build_command

    def sleeper_command(run):
        from lib.talking_head_edit.cpu_budget import low_priority_prefix

        return [*low_priority_prefix(), sys.executable, "-c",
                "import time; time.sleep(30)"]

    monkeypatch.setattr(queue, "_build_command", sleeper_command)
    yield queue
    if queue._current:
        queue.cancel(queue._current[0])


def _wait_until(predicate, timeout=5.0, interval=0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class TestLowPriorityPrefixIsKillable:
    def test_prefixed_subprocess_runs_and_is_killable_via_cancel(self, store, sleeper_queue):
        source = store.root.parent / "footage.mp4"
        source.write_bytes(b"video")
        job = store.create(source, {}, title="cpu-priority")

        sleeper_queue.submit(QueuedRun(job.job_id))

        assert _wait_until(lambda: sleeper_queue._current is not None), (
            "the prefixed command must actually start running")
        _, process = sleeper_queue._current
        assert _pid_alive(process.pid), (
            "nice/ionice must exec in place -- the tracked pid must be the real process")

        assert sleeper_queue.cancel(job.job_id) is True
        assert _wait_until(lambda: not _pid_alive(process.pid)), (
            "cancel must still reach the process through the nice/ionice prefix")
        assert job.load()["status"] == "cancelled"
