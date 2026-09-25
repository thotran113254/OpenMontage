"""Job server API contract. No real jobs are executed — the queue is stubbed.

Focus is on the two things that would be dangerous to get wrong: which files
the server is willing to read from disk, and whether progress replay works
after a browser reload.
"""

from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi", reason="server deps chưa cài")
from fastapi.testclient import TestClient  # noqa: E402

from lib.talking_head_edit.job_store import JobStore  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from lib.talking_head_edit.project_store import ProjectStore
    from server import api_jobs, api_projects

    store = JobStore(root=tmp_path / "jobs")
    monkeypatch.setattr(api_jobs, "store", store)
    # `/api/jobs` merges the legacy root with every project's jobs dir, so the
    # project store has to be redirected too or the developer's real builds show
    # up in this test.
    monkeypatch.setattr(api_projects, "store", ProjectStore(tmp_path / "autoedit"))

    submitted: list = []
    monkeypatch.setattr(api_jobs.job_queue, "submit", lambda run: (submitted.append(run), 1)[1])
    monkeypatch.setattr(api_jobs.job_queue, "cancel", lambda job_id: True)

    from server.app import app

    test_client = TestClient(app)
    test_client.store = store          # type: ignore[attr-defined]
    test_client.submitted = submitted  # type: ignore[attr-defined]
    return test_client


def make_job(client, **options):
    source = client.store.root.parent / "footage.mp4"
    source.write_bytes(b"video")
    job = client.store.create(source, options or {}, title="thu nghiem")
    return job


class TestHealthAndResources:
    def test_health(self, client):
        assert client.get("/api/health").json() == {"ok": True, "auth": False}

    def test_config_exposes_director_defaults(self, client):
        data = client.get("/api/config").json()
        assert "director_model" in data
        assert isinstance(data["director_model"], str)
        assert data["director_model"]
        assert "gateway_configured" in data

    def test_resources_only_lists_files_that_exist(self, client):
        data = client.get("/api/resources").json()
        assert data["sfx"] and all(entry["name"].endswith(".mp3") for entry in data["sfx"])
        assert data["bgm"]


class TestJobLifecycle:
    def test_list_and_detail(self, client):
        job = make_job(client)
        listed = client.get("/api/jobs").json()
        assert [item["job_id"] for item in listed] == [job.job_id]

        detail = client.get(f"/api/jobs/{job.job_id}").json()
        assert detail["media_base"] == f"/api/media/{job.job_id}/"
        assert detail["has_final"] is False
        assert detail["audit_report"] is None

    def test_unknown_job_is_404(self, client):
        assert client.get("/api/jobs/khong-co-that").status_code == 404

    def test_render_queues_only_the_render_stage(self, client):
        job = make_job(client)
        client.post(f"/api/jobs/{job.job_id}/render?scale=0.5")
        assert client.submitted[-1].stages == ["render"]
        assert job.load()["options"]["render_scale"] == 0.5

    def test_visuals_queues_design_then_render(self, client):
        job = make_job(client)
        response = client.post(f"/api/jobs/{job.job_id}/visuals")
        assert response.status_code == 200
        assert client.submitted[-1].stages == ["visuals", "render"]
        assert client.submitted[-1].use_cache is False

    def test_revise_queues_patch_then_recut(self, client):
        job = make_job(client)
        response = client.post(f"/api/jobs/{job.job_id}/revise",
                               json={"instruction": "bỏ card cuối"})
        assert response.status_code == 200
        assert client.submitted[-1].stages == ["revise", "audit", "resolve"]
        assert job.load()["options"]["revise_instruction"] == "bỏ card cuối"

    def test_revise_rejects_empty_and_oversized_instructions(self, client):
        job = make_job(client)
        assert client.post(f"/api/jobs/{job.job_id}/revise", json={"instruction": "  "}).status_code == 400
        assert client.post(f"/api/jobs/{job.job_id}/revise",
                           json={"instruction": "x" * 2001}).status_code == 400


class TestVersions:
    def test_rollback_copies_forward_instead_of_rewinding(self, client):
        job = make_job(client)
        job.spec_path(1).write_text('{"events": []}', encoding="utf-8")
        job.props_path(1).write_text('{"events": [], "durationSeconds": 1}', encoding="utf-8")
        job.update(current_version=1, versions=[{"version": 1, "kind": "director"}])
        job.spec_path(2).write_text('{"events": [{"type": "card"}]}', encoding="utf-8")
        job.update(current_version=2)

        result = client.post(f"/api/jobs/{job.job_id}/rollback", json={"version": 1}).json()
        assert result["version"] == 3, "rollback tạo phiên bản mới, không xoá lịch sử"
        assert result["reverted_to"] == 1 and result["restored_from"] == 1
        assert job.spec_path(2).exists(), "phiên bản bị rollback vẫn phải còn"
        new_spec = json.loads(job.spec_path(3).read_text(encoding="utf-8"))
        assert new_spec["events"] == []
        assert not job.props_path(3).exists(), (
            "props không được copy -- src.mp4 vẫn theo bản cắt mới nhất, cần resolve lại")
        assert client.submitted[-1].stages == ["resolve"]
        assert client.submitted[-1].job_id == job.job_id

    def test_rollback_restores_options_a_later_version_changed(self, client):
        job = make_job(client)
        job.spec_path(1).write_text('{"events": []}', encoding="utf-8")
        base_options = job.load()["options"]
        job.update(current_version=1, versions=[{"version": 1, "kind": "director"}])
        job.spec_path(2).write_text('{"events": []}', encoding="utf-8")
        job.update(
            current_version=2, options={**base_options, "tempo": 1.2},
            versions=[
                {"version": 1, "kind": "director"},
                {"version": 2, "kind": "revise", "options_previous": {"tempo": base_options["tempo"]}},
            ],
        )

        result = client.post(f"/api/jobs/{job.job_id}/rollback", json={"version": 1}).json()
        assert result["options_changed"] == {"tempo": base_options["tempo"]}
        assert job.load()["options"]["tempo"] == base_options["tempo"]
        assert result["queue_position"] is not None

    def test_rollback_to_missing_version_is_404(self, client):
        job = make_job(client)
        response = client.post(f"/api/jobs/{job.job_id}/rollback", json={"version": 9})
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"


class TestMediaAccess:
    def test_serves_a_job_file(self, client):
        job = make_job(client)
        (job.dir / "src.mp4").write_bytes(b"cut video")
        response = client.get(f"/api/media/{job.job_id}/src.mp4")
        assert response.status_code == 200
        assert response.content == b"cut video"

    def test_falls_back_to_the_shared_audio_library(self, client):
        job = make_job(client)
        response = client.get(f"/api/media/{job.job_id}/sfx_pop.mp3")
        assert response.status_code == 200
        assert len(response.content) > 0

    def test_rejects_names_that_try_to_escape(self, client):
        job = make_job(client)
        for name in ("..%2F..%2F.env", "sub/dir.mp4", ".env"):
            assert client.get(f"/api/media/{job.job_id}/{name}").status_code in (400, 404)

    def test_input_path_outside_allowed_roots_is_refused(self, client):
        response = client.post("/api/jobs", json={"input_path": "C:/Windows/system.ini"})
        assert response.status_code in (400, 403)


class TestProgressStream:
    def test_replays_history_then_ends(self, client):
        job = make_job(client)
        job.emit("log", "probe", "dòng 1")
        job.emit("log", "probe", "dòng 2")
        job.update(status="completed")

        with client.stream("GET", f"/api/jobs/{job.job_id}/events") as response:
            body = "".join(chunk for chunk in response.iter_text())
        assert "dòng 1" in body and "dòng 2" in body
        assert "stream_end" in body

    def test_offset_skips_events_already_seen(self, client):
        job = make_job(client)          # creating a job already emits one event
        job.emit("log", "probe", "cũ")
        job.emit("log", "probe", "mới")
        job.update(status="completed")

        # offset counts every line in events.jsonl, so 2 = job_created + "cũ"
        with client.stream("GET", f"/api/jobs/{job.job_id}/events?offset=2") as response:
            body = "".join(chunk for chunk in response.iter_text())
        assert "cũ" not in body, "dòng đã xem không được phát lại"
        assert "mới" in body, "dòng chưa xem phải được gửi"

    def test_saving_hand_edited_props_creates_a_version(self, client):
        job = make_job(client)
        job.spec_path(1).write_text('{"events": []}', encoding="utf-8")
        job.update(current_version=1)
        result = client.put(f"/api/jobs/{job.job_id}/props",
                            json={"videoSrc": "src.mp4", "events": [], "durationSeconds": 5}).json()
        assert result["version"] == 2
        assert job.props_path(2).exists()
        assert job.load()["versions"][-1]["kind"] == "manual"


def test_version_entries_accept_legacy_and_manual_cut_kinds():
    """A hand-made job with a kind-less version, or a Cắt/Giữ version, must not 500 the job list."""
    from server.schemas import VersionEntry

    assert VersionEntry(version=1).kind is None
    assert VersionEntry(version=2, kind="manual_cuts").kind == "manual_cuts"


def test_queue_status_schema_matches_the_queue():
    """An idle queue reports running=None; the schema once demanded a list and 500'd."""
    from server.schemas import QueueStatusResponse

    assert QueueStatusResponse(running=None, pending=0).running is None
    assert QueueStatusResponse(running="job-1", pending=2).pending == 2


def test_every_shipped_edit_style_fits_the_response_schema():
    """The schema once required `style_id` while the data says `id`: /api/edit-styles 500'd."""
    from lib.talking_head_edit.edit_styles import load_all
    from server.schemas import EditStyleResponse

    styles = load_all()
    assert styles
    for style in styles:
        EditStyleResponse.model_validate(style)


def test_every_saved_look_preset_fits_the_response_schema():
    """created_at is stored as ISO text; a float-typed schema turned /api/look-presets into a 500."""
    from lib.talking_head_edit.look_presets import load_all
    from server.schemas import LookPresetResponse

    for preset in load_all():
        LookPresetResponse.model_validate(preset)
