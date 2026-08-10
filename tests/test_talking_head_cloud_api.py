"""Contract tests for /api/cloud/* — UI depends on these shapes.

Free queue ops only (enqueue/list/remove/status). Paid preview/execute call
Vast.ai and are covered by unit tests on the tool layer with fakes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi", reason="server deps chưa cài")
from fastapi.testclient import TestClient  # noqa: E402

from lib.talking_head_edit.job_store import JobStore  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    from lib.cloud_render import queue as cloud_queue
    from server import api_jobs
    from server.cloud_worker import cloud_worker

    store = JobStore(root=tmp_path / "jobs")
    monkeypatch.setattr(api_jobs, "store", store)

    queue_path = tmp_path / "batch-queue.json"
    lock_path = tmp_path / "batch-queue.lock"
    monkeypatch.setattr(cloud_queue, "STATE_DIR", tmp_path)
    monkeypatch.setattr(cloud_queue, "QUEUE_PATH", queue_path)
    monkeypatch.setattr(cloud_queue, "LOCK_PATH", lock_path)

    # Isolate find_job used by the queue prune path and by api_cloud enrichment.
    def fake_find_job(job_id, legacy_root=None, projects_root=None):
        try:
            return store.get(job_id)
        except FileNotFoundError:
            raise

    monkeypatch.setattr(cloud_queue, "find_job", fake_find_job)
    monkeypatch.setattr("lib.talking_head_edit.job_store.find_job", fake_find_job)

    # Reset in-process worker between tests.
    cloud_worker.clear_finished() if cloud_worker.status()["status"] != "running" else None
    # Force idle regardless (tests never start a real rental).
    from server import cloud_worker as cw_mod
    cw_mod.cloud_worker._state = cw_mod.CloudWorker._idle_state()

    from server.app import app

    test_client = TestClient(app)
    test_client.store = store  # type: ignore[attr-defined]
    return test_client


def make_job_with_props(client, title="clip"):
    source = client.store.root.parent / "footage.mp4"
    source.write_bytes(b"video")
    job = client.store.create(source, {}, title=title)
    job.update(current_version=1)
    props = {"durationSeconds": 30, "events": [], "fps": 30, "width": 1080, "height": 1920}
    job.props_path(1).write_text(json.dumps(props), encoding="utf-8")
    return job


class TestCloudStatus:
    def test_status_empty_queue(self, client):
        body = client.get("/api/cloud/status").json()
        assert body["queue"]["count"] == 0
        assert body["queue"]["entries"] == []
        assert "flush_check" in body
        assert "config" in body
        assert body["operation"]["status"] == "idle"
        assert body["ready_to_flush"] is False

    def test_enqueue_list_remove(self, client):
        job = make_job_with_props(client, title="A")
        enq = client.post("/api/cloud/queue", json={"job_id": job.job_id, "note": "cuoi ngay"})
        assert enq.status_code == 200, enq.text
        entry = enq.json()["entry"]
        assert entry["job_id"] == job.job_id
        assert entry["title"] == "A"
        assert entry["note"] == "cuoi ngay"
        assert entry["has_props"] is True
        assert entry["version_at_enqueue"] == 1

        listed = client.get("/api/cloud/queue").json()
        assert listed["flush_check"]["job_count"] == 1
        assert len(listed["entries"]) == 1

        membership = client.get(f"/api/cloud/job/{job.job_id}/queued").json()
        assert membership["queued"] is True

        removed = client.delete(f"/api/cloud/queue/{job.job_id}")
        assert removed.status_code == 200
        assert client.get(f"/api/cloud/job/{job.job_id}/queued").json()["queued"] is False

    def test_enqueue_requires_props(self, client):
        source = client.store.root.parent / "raw.mp4"
        source.write_bytes(b"v")
        job = client.store.create(source, {}, title="empty")
        # current_version defaults to 0 — no props yet
        res = client.post("/api/cloud/queue", json={"job_id": job.job_id})
        assert res.status_code == 400
        assert "props" in res.text.lower() or "bản dựng" in res.text

    def test_enqueue_missing_job_404(self, client):
        res = client.post("/api/cloud/queue", json={"job_id": "khong-ton-tai"})
        assert res.status_code == 404

    def test_clear_needs_confirm(self, client):
        job = make_job_with_props(client)
        client.post("/api/cloud/queue", json={"job_id": job.job_id})
        bad = client.post("/api/cloud/queue/clear", json={})
        assert bad.status_code == 400
        ok = client.post("/api/cloud/queue/clear", json={"confirm": True})
        assert ok.status_code == 200
        assert client.get("/api/cloud/queue").json()["entries"] == []

    def test_execute_requires_confirm(self, client):
        res = client.post("/api/cloud/execute", json={
            "mode": "flush",
            "offer_id": 1,
            "dry_run_ref": "x",
        })
        assert res.status_code == 400
        assert "confirm" in res.text.lower()

    def test_execute_requires_dry_run_ref(self, client):
        res = client.post("/api/cloud/execute", json={
            "mode": "render_now",
            "job_id": "any",
            "offer_id": 1,
            "confirm": True,
        })
        assert res.status_code == 400
