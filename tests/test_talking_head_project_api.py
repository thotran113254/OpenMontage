"""Project HTTP API contract.

The queue is stubbed — nothing here runs a pipeline. What is exercised is what
would be dangerous or annoying to get wrong: a batch upload where one file is
bad, the path guard, the delete confirmations, and whether config inheritance
survives the round trip through HTTP.
"""

from __future__ import annotations

import io
import json

import pytest

fastapi = pytest.importorskip("fastapi", reason="server deps chưa cài")
from fastapi.testclient import TestClient  # noqa: E402


class FakeProbe:
    def __init__(self, duration=12.0, has_audio=True, volume=-20.0):
        self.payload = {"duration": duration, "width": 1080, "height": 1920,
                        "fps": 30.0, "video_codec": "h264",
                        "audio_codec": "aac" if has_audio else None,
                        "audio_channels": 2 if has_audio else None,
                        "has_audio": has_audio, "mean_volume_db": volume,
                        "size_bytes": 1024}

    def __call__(self, path):
        return dict(self.payload)


@pytest.fixture
def client(tmp_path, monkeypatch):
    from lib.talking_head_edit import project_store as project_module
    from lib.talking_head_edit import sources as sources_mod
    from lib.talking_head_edit.project_store import ProjectStore
    from server import api_jobs, api_projects

    monkeypatch.setattr(sources_mod, "probe_source", FakeProbe())
    monkeypatch.setattr(project_module, "thumbnail",
                        lambda source, out, at_seconds=1.0: (out.write_bytes(b"jpg"), out)[1])
    store = ProjectStore(tmp_path / "autoedit")
    monkeypatch.setattr(api_projects, "store", store)

    submitted: list = []
    monkeypatch.setattr(api_jobs.job_queue, "submit",
                        lambda run: (submitted.append(run), 1)[1])

    from server.app import app

    test_client = TestClient(app)
    test_client.project_store = store        # type: ignore[attr-defined]
    test_client.submitted = submitted        # type: ignore[attr-defined]
    return test_client


def make_project(client, title="Buổi quay 1", **payload):
    response = client.post("/api/projects", json={"title": title, **payload})
    assert response.status_code == 200, response.text
    return response.json()["project_id"]


def upload_files(client, project_id, names=("take-1.mp4",), megabytes=2):
    files = [("files", (name, io.BytesIO(b"\0" * (megabytes << 20)), "video/mp4"))
             for name in names]
    return client.post(f"/api/projects/{project_id}/sources", files=files)


class TestProjectCrud:
    def test_create_and_list(self, client):
        project_id = make_project(client)
        listed = client.get("/api/projects").json()
        assert [row["project_id"] for row in listed] == [project_id]

    def test_create_without_a_title_is_refused(self, client):
        assert client.post("/api/projects", json={}).status_code == 400

    def test_an_invalid_assembly_mode_is_caught_at_creation(self, client):
        """Otherwise it fails at probe time on every single build instead of at
        the moment someone typed it."""
        response = client.post("/api/projects",
                               json={"title": "P", "assembly": {"mode": "magic"}})
        assert response.status_code == 400
        assert "mode" in response.json()["detail"]

    def test_detail_reports_where_each_setting_came_from(self, client):
        project_id = make_project(client, assembly={"mode": "best_take"})
        data = client.get(f"/api/projects/{project_id}").json()
        assert data["assembly_resolved"]["mode"] == "best_take"
        assert data["assembly_origin"]["mode"] == "project"
        assert data["assembly_origin"]["cross_source_cut"] == "global"

    def test_missing_project_is_404(self, client):
        assert client.get("/api/projects/khong-co-that").status_code == 404

    def test_delete_requires_confirmation(self, client):
        project_id = make_project(client)
        assert client.delete(f"/api/projects/{project_id}").status_code == 409
        assert client.delete(
            f"/api/projects/{project_id}?confirm=true").status_code == 200
        assert client.get("/api/projects").json() == []

    def test_settings_can_be_updated(self, client):
        project_id = make_project(client)
        response = client.put(f"/api/projects/{project_id}/settings", json={
            "keyterms": ["Zalo OA", "CRM", "  "],
            "assembly": {"mode": "sequential"},
            "defaults": {"topic": "chatbot"},
        })
        assert response.status_code == 200
        state = response.json()
        assert state["keyterms"] == ["Zalo OA", "CRM"]
        assert state["assembly"]["mode"] == "sequential"
        assert state["defaults"]["topic"] == "chatbot"

    def test_empty_settings_update_is_refused(self, client):
        project_id = make_project(client)
        assert client.put(f"/api/projects/{project_id}/settings",
                          json={}).status_code == 400

    def test_invalid_assembly_in_settings_is_refused(self, client):
        project_id = make_project(client)
        assert client.put(f"/api/projects/{project_id}/settings",
                          json={"assembly": {"speaker_aware": "maybe"}}
                          ).status_code == 400


class TestSourceUpload:
    def test_batch_upload_returns_a_spec_per_file(self, client):
        project_id = make_project(client)
        response = upload_files(client, project_id, ("take-1.mp4", "take-2.mp4"))
        assert response.status_code == 200
        body = response.json()
        assert [s["id"] for s in body["added"]] == ["s0", "s1"]
        assert body["failed"] == []
        assert body["added"][0]["role"] == "aroll"

    def test_one_bad_file_does_not_lose_the_good_ones(self, client):
        """Five 200 MB uploads must not all be discarded because the last one had
        the wrong extension."""
        project_id = make_project(client)
        files = [
            ("files", ("good.mp4", io.BytesIO(b"\0" * 2048), "video/mp4")),
            ("files", ("bad.exe", io.BytesIO(b"\0" * 16), "application/octet-stream")),
        ]
        body = client.post(f"/api/projects/{project_id}/sources", files=files).json()
        assert [s["id"] for s in body["added"]] == ["s0"]
        assert body["failed"][0]["filename"] == "bad.exe"

    def test_source_metadata_can_be_edited(self, client):
        project_id = make_project(client)
        upload_files(client, project_id)
        response = client.patch(f"/api/projects/{project_id}/sources/s0",
                               json={"role": "broll", "take_group": "mo-dau",
                                     "label": "take 1"})
        assert response.status_code == 200
        spec = response.json()
        assert spec["role"] == "broll"
        assert spec["take_group"] == "mo-dau"

    def test_editing_a_measured_field_is_refused(self, client):
        project_id = make_project(client)
        upload_files(client, project_id)
        # `duration` is not in the allow-list, so it is simply not applied
        response = client.patch(f"/api/projects/{project_id}/sources/s0",
                               json={"duration": 999})
        assert response.status_code == 200
        assert response.json()["duration"] == 12.0

    def test_thumbnail_is_served(self, client):
        project_id = make_project(client)
        upload_files(client, project_id)
        response = client.get(f"/api/projects/{project_id}/sources/s0/thumb")
        assert response.status_code == 200
        assert response.content == b"jpg"

    def test_thumbnail_for_a_missing_source_is_404(self, client):
        project_id = make_project(client)
        assert client.get(
            f"/api/projects/{project_id}/sources/s9/thumb").status_code == 404

    def test_media_route_refuses_traversal(self, client):
        project_id = make_project(client)
        upload_files(client, project_id)
        assert client.get(
            f"/api/projects/{project_id}/media/..%2F..%2Fproject.json"
        ).status_code in (400, 404)

    def test_from_path_refuses_a_file_outside_the_allowed_roots(self, client):
        project_id = make_project(client)
        response = client.post(f"/api/projects/{project_id}/sources/from-path",
                               json={"path": "C:/Windows/system.ini"})
        assert response.status_code in (400, 403)

    def test_from_path_labels_with_the_original_filename(self, client, tmp_path,
                                                        monkeypatch):
        """The stored name carries an `s0_` prefix; labelling with it makes take
        detection reduce two takes of one subject to different subjects, and names
        the group after the prefix."""
        monkeypatch.setenv("AUTOEDIT_INPUT_ROOTS", str(tmp_path))
        clip = tmp_path / "intro-take1.mp4"
        clip.write_bytes(b"\0" * 2048)

        project_id = make_project(client)
        response = client.post(f"/api/projects/{project_id}/sources/from-path",
                               json={"path": str(clip)})
        assert response.status_code == 200
        assert response.json()["label"] == "intro-take1"

    def test_take_suggestions_are_reported_not_applied(self, client):
        project_id = make_project(client)
        upload_files(client, project_id, ("intro-take1.mp4", "intro-take2.mp4"))
        suggestions = client.get(
            f"/api/projects/{project_id}/take-suggestions").json()
        assert suggestions and set(suggestions[0]["sources"]) == {"s0", "s1"}
        # Nothing was applied — the human confirms.
        state = client.get(f"/api/projects/{project_id}").json()
        assert all(s["take_group"] == "main" for s in state["sources"])


class TestBuilds:
    def test_creating_a_build_queues_it(self, client):
        project_id = make_project(client, defaults={"topic": "chatbot"})
        upload_files(client, project_id)
        response = client.post(f"/api/projects/{project_id}/jobs",
                               json={"options": {"prompt": "cắt gọn"}})
        assert response.status_code == 200
        body = response.json()
        assert body["project_id"] == project_id
        assert body["run"] is True
        assert client.submitted[-1].job_id == body["job_id"]

    def test_create_build_run_false_does_not_queue(self, client):
        """UI 'chỉ tạo job' path — user runs stages later by hand."""
        project_id = make_project(client)
        upload_files(client, project_id)
        before = len(client.submitted)
        response = client.post(
            f"/api/projects/{project_id}/jobs",
            json={"run": False, "options": {"prompt": "sẽ chạy tay"}},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["run"] is False
        assert body["queue_position"] is None
        assert len(client.submitted) == before
        detail = client.get(f"/api/jobs/{body['job_id']}").json()
        assert detail["status"] == "created"

    def test_create_build_can_stop_before_render(self, client):
        """UI 'dựng + duyệt' passes prepare stages only."""
        project_id = make_project(client)
        upload_files(client, project_id)
        stages = ["probe", "transcribe", "select", "direct", "audit",
                  "calibrate", "resolve"]
        response = client.post(
            f"/api/projects/{project_id}/jobs",
            json={"stages": stages, "options": {"tempo": 1.05}},
        )
        assert response.status_code == 200
        run = client.submitted[-1]
        assert run.stages == stages
        assert "render" not in (run.stages or [])

    def test_a_build_inherits_project_config_over_http(self, client):
        project_id = make_project(client, keyterms=["Zalo OA"],
                                  assembly={"mode": "best_take"},
                                  defaults={"topic": "chatbot"})
        upload_files(client, project_id)
        job_id = client.post(f"/api/projects/{project_id}/jobs", json={}).json()["job_id"]

        detail = client.get(f"/api/jobs/{job_id}").json()
        options = detail["options"]
        assert options["keyterms"] == ["Zalo OA"]
        assert options["assembly"]["mode"] == "best_take"
        assert options["topic"] == "chatbot"

    def test_a_project_with_no_speech_cannot_build(self, client, monkeypatch):
        from lib.talking_head_edit import sources as sources_mod

        project_id = make_project(client)
        monkeypatch.setattr(sources_mod, "probe_source",
                            FakeProbe(has_audio=False, volume=None))
        upload_files(client, project_id, ("broll.mp4",))
        response = client.post(f"/api/projects/{project_id}/jobs", json={})
        assert response.status_code == 400
        assert "lời nói" in response.json()["detail"]

    def test_builds_are_listed(self, client):
        project_id = make_project(client)
        upload_files(client, project_id)
        job_id = client.post(f"/api/projects/{project_id}/jobs", json={}).json()["job_id"]
        builds = client.get(f"/api/projects/{project_id}/jobs").json()
        assert [b["job_id"] for b in builds] == [job_id]
        assert builds[0]["has_final"] is False

    def test_a_project_build_is_reachable_through_the_job_api(self, client):
        """The two-root lookup: /api/jobs/<id> must work for a build that lives
        inside a project, not just for a legacy job."""
        project_id = make_project(client)
        upload_files(client, project_id)
        job_id = client.post(f"/api/projects/{project_id}/jobs", json={}).json()["job_id"]
        detail = client.get(f"/api/jobs/{job_id}").json()
        assert detail["job_id"] == job_id
        assert detail["project_id"] == project_id


class TestSourceDeletion:
    def test_a_source_in_use_returns_409_naming_the_build(self, client):
        project_id = make_project(client)
        upload_files(client, project_id)
        job_id = client.post(f"/api/projects/{project_id}/jobs", json={}).json()["job_id"]

        response = client.delete(f"/api/projects/{project_id}/sources/s0")
        assert response.status_code == 409
        assert job_id in response.json()["detail"]

    def test_force_deletes_and_reports_what_it_broke(self, client):
        project_id = make_project(client)
        upload_files(client, project_id)
        job_id = client.post(f"/api/projects/{project_id}/jobs", json={}).json()["job_id"]

        response = client.delete(
            f"/api/projects/{project_id}/sources/s0?force=true")
        assert response.status_code == 200
        assert response.json()["was_used_by"] == [job_id]

    def test_an_unused_source_deletes_without_force(self, client):
        project_id = make_project(client)
        upload_files(client, project_id)
        assert client.delete(
            f"/api/projects/{project_id}/sources/s0").status_code == 200
        assert client.get(f"/api/projects/{project_id}").json()["sources"] == []
