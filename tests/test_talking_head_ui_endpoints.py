"""Endpoints the web UI depends on: spine, prompts, chat, autopilot, media.

The UI types were written from these responses, so a shape change here breaks the
UI silently — TypeScript cannot see a Python dict. These tests are the contract.
"""

from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi", reason="server deps chưa cài")
from fastapi.testclient import TestClient  # noqa: E402

from lib.talking_head_edit.job_store import JobStore  # noqa: E402


def words(count=30):
    return [{"word": f"tu{i}", "start": round(i * 0.5, 3),
             "end": round(i * 0.5 + 0.4, 3), "src": "s0"}
            for i in range(count)]


@pytest.fixture
def client(tmp_path, monkeypatch):
    from server import api_jobs

    store = JobStore(root=tmp_path / "jobs")
    monkeypatch.setattr(api_jobs, "store", store)
    submitted: list = []
    monkeypatch.setattr(api_jobs.job_queue, "submit",
                        lambda run: (submitted.append(run), 1)[1])

    from server.app import app

    test_client = TestClient(app)
    test_client.store = store          # type: ignore[attr-defined]
    test_client.submitted = submitted  # type: ignore[attr-defined]
    return test_client


def make_job(client, with_spine=True, cut_remove=None):
    source = client.store.root.parent / "footage.mp4"
    source.write_bytes(b"video")
    job = client.store.create(source, {}, title="thu nghiem")
    if with_spine:
        job.spine_path.write_text(json.dumps({
            "schema": 3, "word_timestamps": words(),
            "takes": [{"src": "s0", "w0": 0, "w1": 29, "take_group": "main",
                       "order": 0}],
            "speakers": ["speaker_0"],
        }, ensure_ascii=False), encoding="utf-8")
        job.update(current_version=1)
        job.spec_path(1).write_text(json.dumps(
            {"events": [], "cut_remove": cut_remove or []}), encoding="utf-8")
    return job


class TestSpineEndpoint:
    def test_returns_the_words_the_director_saw(self, client):
        job = make_job(client)
        body = client.get(f"/api/jobs/{job.job_id}/spine").json()
        assert len(body["words"]) == 30
        assert body["words"][0]["word"] == "tu0"
        assert body["boundaries"] == []
        assert body["speakers"] == ["speaker_0"]

    def test_internal_index_field_is_not_leaked(self, client):
        """`_orig_index` is a pipeline detail; the UI addresses words by position."""
        job = make_job(client)
        body = client.get(f"/api/jobs/{job.job_id}/spine").json()
        assert all("_orig_index" not in word for word in body["words"])

    def test_cut_ranges_come_from_the_current_spec(self, client):
        job = make_job(client, cut_remove=[[4, 6], [10, 11]])
        body = client.get(f"/api/jobs/{job.job_id}/spine").json()
        assert body["cut_ranges"] == [[4, 6], [10, 11]]

    def test_multi_source_boundaries_are_reported(self, client):
        job = make_job(client)
        job.spine_path.write_text(json.dumps({
            "schema": 3,
            "word_timestamps": [{**w, "src": "s0" if i < 15 else "s1"}
                                for i, w in enumerate(words())],
            "takes": [{"src": "s0", "w0": 0, "w1": 14, "take_group": "main", "order": 0},
                      {"src": "s1", "w0": 15, "w1": 29, "take_group": "main", "order": 1}],
        }, ensure_ascii=False), encoding="utf-8")
        assert client.get(f"/api/jobs/{job.job_id}/spine").json()["boundaries"] == [15]

    def test_a_job_without_a_spine_returns_empty_not_an_error(self, client):
        job = make_job(client, with_spine=False)
        body = client.get(f"/api/jobs/{job.job_id}/spine").json()
        assert body == {"words": [], "boundaries": [], "cut_ranges": []}

    def test_a_missing_job_is_404(self, client):
        assert client.get("/api/jobs/khong-co-that/spine").status_code == 404


class TestPromptEndpoints:
    def test_catalog_lists_the_shipped_prompts(self, client):
        ids = {row["id"] for row in client.get("/api/prompts").json()}
        for expected in ("structure", "captions", "cut_verify", "revise", "select_take"):
            assert expected in ids

    def test_detail_carries_body_versions_and_placeholders(self, client):
        body = client.get("/api/prompts/structure").json()
        assert body["body"].startswith("Bạn là video editor")
        assert "spine" in body["placeholders"]
        assert any(row["is_current"] for row in body["versions"])

    def test_preview_renders_against_a_real_spine(self, client):
        """A template full of `{{spine}}` tells nobody anything; the rendered
        prompt is what makes a problem visible."""
        job = make_job(client)
        response = client.get(
            f"/api/prompts/structure/preview?job_id={job.job_id}")
        assert response.status_code == 200
        assert "{{spine}}" not in response.text
        assert "0:tu0" in response.text

    def test_preview_without_a_spine_explains_itself(self, client):
        job = make_job(client, with_spine=False)
        response = client.get(f"/api/prompts/structure/preview?job_id={job.job_id}")
        assert response.status_code == 400
        assert "transcribe" in response.json()["detail"]

    @pytest.mark.parametrize("bad_id", ["Structure", "bad-id", "a b", "струк"])
    def test_a_bad_prompt_id_is_refused(self, client, bad_id):
        """Ids are restricted to `[a-z0-9_]` because they become a filename."""
        assert client.get(f"/api/prompts/{bad_id}").status_code in (400, 404)

    def test_traversal_never_reaches_the_handler(self, client):
        """`..%2F` is normalised into a different route, so it 4xx's before the
        handler runs — either way it must not read a file."""
        assert client.get("/api/prompts/..%2Fsecrets").status_code >= 400

    def test_diff_between_versions(self, client):
        response = client.get("/api/prompts/structure/diff?version=v2&against=v1")
        assert response.status_code == 200
        assert "broll_rule" in response.text

    def test_ab_is_only_offered_for_structure(self, client):
        job = make_job(client)
        response = client.post("/api/prompts/captions/ab",
                               json={"job_id": job.job_id, "version_b": "v2"})
        assert response.status_code == 400

    def test_ab_estimate_reports_cost_before_running(self, client):
        job = make_job(client)
        response = client.get(
            f"/api/prompts/structure/ab/estimate?job_id={job.job_id}"
            "&version_a=v1&version_b=v2")
        assert response.status_code == 200
        body = response.json()
        assert body["words"] == 30
        assert body["estimated_tokens_in"] > 0

    def test_ab_results_is_empty_before_any_run(self, client):
        job = make_job(client)
        assert client.get(
            f"/api/prompts/structure/ab/results?job_id={job.job_id}").json() == []


class TestChatEndpoints:
    def test_history_starts_empty(self, client):
        job = make_job(client)
        assert client.get(f"/api/jobs/{job.job_id}/chat").json()["turns"] == []

    def test_an_empty_message_is_a_400(self, client):
        job = make_job(client)
        assert client.post(f"/api/jobs/{job.job_id}/chat",
                           json={"message": "   "}).status_code == 400

    def test_an_overlong_message_is_a_400(self, client):
        job = make_job(client)
        assert client.post(f"/api/jobs/{job.job_id}/chat",
                           json={"message": "x" * 5000}).status_code == 400

    def test_a_model_failure_is_a_400_not_a_500(self, client, monkeypatch):
        """A refused revise is a bad request from the caller's point of view; a
        500 would make the UI show "server error" for a normal outcome."""
        from lib.talking_head_edit.stages import revise as revise_stage

        def explode(job, options):
            raise RuntimeError("model từ chối")

        monkeypatch.setattr(revise_stage, "run", explode)
        job = make_job(client)
        response = client.post(f"/api/jobs/{job.job_id}/chat",
                               json={"message": "làm điều không thể"})
        assert response.status_code == 400
        assert "từ chối" in response.json()["detail"]

    def test_a_failed_turn_still_appears_in_history(self, client, monkeypatch):
        from lib.talking_head_edit.stages import revise as revise_stage

        monkeypatch.setattr(revise_stage, "run",
                            lambda job, options: (_ for _ in ()).throw(
                                RuntimeError("hỏng")))
        job = make_job(client)
        client.post(f"/api/jobs/{job.job_id}/chat", json={"message": "thử xem"})
        turns = client.get(f"/api/jobs/{job.job_id}/chat").json()["turns"]
        assert turns and turns[0]["applied"] is False


class TestAutopilotEndpoint:
    def test_it_queues_rather_than_blocking(self, client):
        """Holding an HTTP connection open for a render is how a UI ends up with a
        spinner that never resolves."""
        job = make_job(client)
        response = client.post(f"/api/jobs/{job.job_id}/autopilot", json={})
        assert response.status_code == 200
        assert response.json()["follow"].endswith("/events")
        assert "--autopilot" in client.submitted[-1].extra_args

    def test_stages_are_forwarded(self, client):
        job = make_job(client)
        client.post(f"/api/jobs/{job.job_id}/autopilot",
                    json={"stages": ["render", "verify"]})
        args = client.submitted[-1].extra_args
        assert "--stages" in args and "render,verify" in args

    def test_options_are_merged_into_the_job(self, client):
        job = make_job(client)
        client.post(f"/api/jobs/{job.job_id}/autopilot",
                    json={"options": {"tempo": 1.1}})
        assert job.load()["options"]["tempo"] == 1.1


class TestMediaRouting:
    def test_seam_images_are_served_by_bare_filename(self, client):
        """The UI refers to every medium by filename; seam images live in a
        subdirectory and must resolve the same way."""
        job = make_job(client)
        target = job.dir / "preview" / "timeline" / "seam_0010.50.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"png")
        response = client.get(f"/api/media/{job.job_id}/seam_0010.50.png")
        assert response.status_code == 200
        assert response.content == b"png"

    def test_traversal_is_refused(self, client):
        job = make_job(client)
        assert client.get(
            f"/api/media/{job.job_id}/..%2F..%2Fjob.json").status_code in (400, 404)
