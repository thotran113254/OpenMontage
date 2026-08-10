"""Tests for lib/cloud_render/queue.py -- the durable upsert queue,
`flush_check()`'s pure cost/threshold computation, and the lock file.

Zero network: `queue.find_job` is monkeypatched to a fake lookup so these
tests never touch the real `projects/autoedit-jobs/` tree, and
`queue.STATE_DIR`/`QUEUE_PATH`/`LOCK_PATH` are redirected into `tmp_path` so
they never touch the real `projects/cloud-render/`.
"""

from __future__ import annotations

import json
import time

import pytest

from lib.cloud_render import queue


@pytest.fixture()
def isolated_queue(tmp_path, monkeypatch):
    """Redirect the queue file + lock into tmp_path, and make `find_job`
    resolve any job_id starting with `existing-` as present, everything else
    as missing -- the exact prune-on-list signal `list_entries` reacts to."""
    queue_path = tmp_path / "batch-queue.json"
    lock_path = tmp_path / "batch-queue.lock"
    monkeypatch.setattr(queue, "STATE_DIR", tmp_path)
    monkeypatch.setattr(queue, "QUEUE_PATH", queue_path)
    monkeypatch.setattr(queue, "LOCK_PATH", lock_path)

    def fake_find_job(job_id):
        if job_id.startswith("existing-"):
            return object()
        raise FileNotFoundError(job_id)

    monkeypatch.setattr(queue, "find_job", fake_find_job)
    return queue_path


class TestEnqueue:
    def test_enqueue_returns_a_pending_entry(self, isolated_queue):
        entry = queue.enqueue("existing-1", version=1, estimated_render_seconds=120.0,
                              duration_seconds=63.0, note="shoot 1 take 2")
        assert entry.job_id == "existing-1"
        assert entry.status == queue.STATUS_PENDING
        assert entry.attempts == 0
        assert entry.version_at_enqueue == 1

    def test_enqueue_is_idempotent_on_job_id(self, isolated_queue):
        queue.enqueue("existing-1", version=1, estimated_render_seconds=100.0)
        queue.enqueue("existing-1", version=2, estimated_render_seconds=150.0, note="revised")

        entries = queue.list_entries()
        assert len(entries) == 1
        assert entries[0].version_at_enqueue == 2
        assert entries[0].note == "revised"

    def test_reenqueue_does_not_reset_enqueued_at(self, isolated_queue):
        first = queue.enqueue("existing-1", version=1, estimated_render_seconds=100.0)
        second = queue.enqueue("existing-1", version=2, estimated_render_seconds=100.0)
        assert first.enqueued_at == second.enqueued_at

    def test_enqueue_survives_a_restart(self, isolated_queue):
        """Enqueue 3 jobs, drop the in-memory module state (simulated by
        re-reading straight off disk via a fresh `list_entries()` call),
        and confirm all 3 are still present with identical fields."""
        queue.enqueue("existing-1", version=1, estimated_render_seconds=100.0, note="a")
        queue.enqueue("existing-2", version=1, estimated_render_seconds=200.0, note="b")
        queue.enqueue("existing-3", version=1, estimated_render_seconds=300.0, note="c")

        raw = json.loads(isolated_queue.read_text(encoding="utf-8"))
        assert len(raw["entries"]) == 3

        entries = {e.job_id: e for e in queue.list_entries()}
        assert set(entries) == {"existing-1", "existing-2", "existing-3"}
        assert entries["existing-2"].note == "b"
        assert entries["existing-3"].estimated_render_seconds == 300.0


class TestListEntries:
    def test_list_prunes_entry_whose_job_dir_is_gone(self, isolated_queue):
        queue.enqueue("existing-1", version=1, estimated_render_seconds=100.0)
        queue.enqueue("gone-1", version=1, estimated_render_seconds=100.0)

        with pytest.warns(UserWarning, match="gone-1"):
            entries = queue.list_entries()

        assert [e.job_id for e in entries] == ["existing-1"]
        # the prune persists -- a second list() finds it already gone, no warning
        assert [e.job_id for e in queue.list_entries()] == ["existing-1"]

    def test_list_with_prune_missing_false_keeps_everything(self, isolated_queue):
        queue.enqueue("existing-1", version=1, estimated_render_seconds=100.0)
        queue.enqueue("gone-1", version=1, estimated_render_seconds=100.0)

        entries = queue.list_entries(prune_missing=False)
        assert {e.job_id for e in entries} == {"existing-1", "gone-1"}


class TestRemoveAndClear:
    def test_remove_returns_true_and_drops_the_entry(self, isolated_queue):
        queue.enqueue("existing-1", version=1, estimated_render_seconds=100.0)
        assert queue.remove("existing-1") is True
        assert queue.list_entries() == []

    def test_remove_returns_false_for_unknown_job_id(self, isolated_queue):
        assert queue.remove("existing-nope") is False

    def test_clear_empties_the_queue(self, isolated_queue):
        queue.enqueue("existing-1", version=1, estimated_render_seconds=100.0)
        queue.enqueue("existing-2", version=1, estimated_render_seconds=100.0)
        queue.clear()
        assert queue.list_entries() == []


# ---------------------------------------------------------------------------
# flush_check -- pure data, zero network calls, zero instances
# ---------------------------------------------------------------------------

BATCH_CONFIG = {
    "max_dph_usd": 0.10,
    "batch": {"min_jobs": 3, "min_total_render_minutes": 20, "max_wait_minutes": 240},
}


class TestFlushCheck:
    def test_empty_queue_reports_zero_and_no_thresholds_met(self, isolated_queue):
        report = queue.flush_check(BATCH_CONFIG)
        assert report["job_count"] == 0
        assert report["thresholds_met"] == []
        assert report["estimated_cost_usd"] == 0.0

    def test_reports_job_count_and_render_minutes(self, isolated_queue):
        queue.enqueue("existing-1", version=1, estimated_render_seconds=600.0)  # 10 min
        queue.enqueue("existing-2", version=1, estimated_render_seconds=900.0)  # 15 min

        report = queue.flush_check(BATCH_CONFIG)
        assert report["job_count"] == 2
        assert report["estimated_render_minutes"] == pytest.approx(25.0)

    def test_min_jobs_threshold_met_at_exactly_three(self, isolated_queue):
        for i in range(3):
            queue.enqueue(f"existing-{i}", version=1, estimated_render_seconds=60.0)

        report = queue.flush_check(BATCH_CONFIG)
        assert "min_jobs" in report["thresholds_met"]

    def test_min_total_render_minutes_threshold(self, isolated_queue):
        queue.enqueue("existing-1", version=1, estimated_render_seconds=20 * 60.0)
        report = queue.flush_check(BATCH_CONFIG)
        assert "min_total_render_minutes" in report["thresholds_met"]

    def test_max_wait_minutes_threshold(self, isolated_queue, monkeypatch):
        queue.enqueue("existing-1", version=1, estimated_render_seconds=60.0)
        # Rewrite enqueued_at directly to simulate a job that has waited long
        # enough to trip max_wait_minutes (240 min in BATCH_CONFIG).
        data = json.loads(isolated_queue.read_text(encoding="utf-8"))
        stale_epoch = time.time() - 300 * 60
        stale_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stale_epoch))
        data["entries"][0]["enqueued_at"] = stale_iso
        isolated_queue.write_text(json.dumps(data), encoding="utf-8")

        report = queue.flush_check(BATCH_CONFIG)
        assert "max_wait_minutes" in report["thresholds_met"]
        assert report["oldest_age_minutes"] >= 240

    def test_cost_comparison_shows_amortization_saving(self, isolated_queue):
        for i in range(3):
            queue.enqueue(f"existing-{i}", version=1, estimated_render_seconds=300.0)  # 5 min each

        report = queue.flush_check(BATCH_CONFIG)
        # 3 separate rentals each pay overhead once vs. one batch paying it once.
        assert (report["estimated_cost_if_rendered_separately_usd"]
               > report["estimated_cost_usd"])

    def test_flush_check_never_calls_vast_client(self, isolated_queue, monkeypatch):
        """Policy lives in the agent skill, not Python -- flush_check must
        never rent anything, asserted via a call-count guard on vast_client."""
        from lib.cloud_render import vast_client

        call_count = {"n": 0}

        def guard(*a, **k):
            call_count["n"] += 1
            raise AssertionError("flush_check must never call vast_client")

        monkeypatch.setattr(vast_client, "search", guard)
        monkeypatch.setattr(vast_client, "rent", guard)

        for i in range(4):
            queue.enqueue(f"existing-{i}", version=1, estimated_render_seconds=300.0)
        queue.flush_check(BATCH_CONFIG)

        assert call_count["n"] == 0


# ---------------------------------------------------------------------------
# Lock file
# ---------------------------------------------------------------------------

class TestLock:
    def test_stale_lock_is_reclaimed(self, isolated_queue, monkeypatch):
        lock_path = queue.LOCK_PATH
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("99999999", encoding="utf-8")
        # Backdate the lock file past the stale timeout.
        stale_time = time.time() - queue.LOCK_STALE_SECONDS - 5
        import os
        os.utime(lock_path, (stale_time, stale_time))

        # A stale lock must not block a normal operation.
        entry = queue.enqueue("existing-1", version=1, estimated_render_seconds=60.0)
        assert entry.job_id == "existing-1"
        assert not lock_path.exists()  # released after the op completes

    def test_fresh_lock_blocks_until_timeout(self, isolated_queue, monkeypatch):
        lock_path = queue.LOCK_PATH
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("99999999", encoding="utf-8")  # fresh -- just written

        monkeypatch.setattr(queue, "LOCK_ACQUIRE_TIMEOUT_S", 0.05)
        with pytest.raises(queue.QueueLockError):
            queue.enqueue("existing-1", version=1, estimated_render_seconds=60.0)
