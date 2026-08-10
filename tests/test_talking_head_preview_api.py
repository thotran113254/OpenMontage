"""Preview API contract. ffmpeg/Remotion are stubbed — nothing is encoded here.

Two things would be actively harmful to get wrong, so they carry most of the
tests:

* the grade a preview shows must be the grade the next resolve would apply
  (spec underneath, `grade_overrides` on top). A preview built from the spec
  alone shows a picture the render never produces once overrides exist, which is
  worse than having no preview.
* the preview file route serves from a directory by name, so it must refuse
  anything that could walk out of it.
"""

from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi", reason="server deps chưa cài")
from fastapi.testclient import TestClient  # noqa: E402

from lib.talking_head_edit.job_store import JobStore  # noqa: E402
from lib.talking_head_edit.project_store import ProjectStore  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from server import api_jobs, api_previews, api_projects

    store = JobStore(root=tmp_path / "jobs")
    monkeypatch.setattr(api_previews, "store", store)
    monkeypatch.setattr(api_jobs, "store", store)
    # `_job()` in both routers falls back to this for a build made inside a
    # project; without it, project jobs would only be reachable through the
    # legacy `store` above, which is the exact bug this file guards against.
    monkeypatch.setattr(api_projects, "store", ProjectStore(root=tmp_path / "projects"))
    # Every handler clamps against the real duration; 100s keeps the maths simple.
    monkeypatch.setattr(api_previews, "probe_duration", lambda path: 100.0)

    calls: dict[str, dict] = {}

    def fake_grades(job, variants, at_seconds=None, options=None):
        calls["grade"] = {"variants": variants, "at": at_seconds}
        return {
            "at_seconds": at_seconds,
            "variants": [
                {"name": name, "image": f"preview/grade_{name}.png",
                 "stats": {"luma": 100.0, "warm_bias": 15.0},
                 "face": {"luma": 105.0, "warm_bias": 16.0}}
                for name in ["raw", *variants]
            ],
            "contact_sheet": "preview/grade_compare.png",
        }

    def fake_audio(job, at_seconds, duration=8.0, presets=None):
        calls["audio"] = {"at": at_seconds, "duration": duration, "presets": presets}
        return {"at_seconds": at_seconds,
                "samples": [{"preset": name, "path": "x", "rel": f"preview/audio_{name}.m4a"}
                            for name in (presets or ["off", "voice", "shotgun"])]}

    def fake_clip(job, start_seconds=0.0, duration=5.0, scale=0.5):
        calls["clip"] = {"start": start_seconds, "duration": duration, "scale": scale}
        return {"path": "x", "rel": "preview/clip_v1_0s.mp4", "version": 1,
                "start_seconds": start_seconds, "duration_seconds": duration,
                "scale": scale, "crf": 17}

    monkeypatch.setattr(api_previews, "preview_grades", fake_grades)
    monkeypatch.setattr(api_previews, "preview_audio", fake_audio)
    monkeypatch.setattr(api_previews, "preview_clip", fake_clip)

    from server.app import app

    test_client = TestClient(app)
    test_client.store = store  # type: ignore[attr-defined]
    test_client.calls = calls  # type: ignore[attr-defined]
    return test_client


def make_job(client, *, spec_grade=None, **options):
    source = client.store.root.parent / "footage.mp4"
    source.write_bytes(b"video")
    job = client.store.create(source, options or {}, title="thu nghiem")
    if spec_grade is not None:
        job.update(current_version=1)
        job.spec_path(1).write_text(json.dumps({"grade": spec_grade}), encoding="utf-8")
    return job


def make_project_job(client):
    """A build living under a project's own `jobs/` dir, not the legacy root.

    Skips `Project.add_source`/`create_job` (both shell out to ffprobe) —
    all this needs is a job whose directory sits where a real project build
    would put it, so `_job()`'s project-root fallback has something to find.
    """
    from server import api_projects

    project = api_projects.store.create(title="du an test")
    source = project.dir / "sources" / "s0_footage.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"video")
    return project.job_store().create(source, {}, title="ban dung", project_id=project.project_id)


class TestJobLookupAcrossLayouts:
    """`_job()` must find a build wherever it lives — this is the whole bug:
    `store.get(job_id)` only ever checked the legacy root."""

    def test_finds_a_job_created_inside_a_project(self, client):
        job = make_project_job(client)
        response = client.post(f"/api/jobs/{job.job_id}/preview/grade", json={})
        assert response.status_code == 200

    def test_finds_a_legacy_job_too(self, client):
        """The fix must not regress the layout that worked before."""
        job = make_job(client)
        response = client.post(f"/api/jobs/{job.job_id}/preview/grade", json={})
        assert response.status_code == 200

    def test_unknown_job_is_still_404_after_checking_both_roots(self, client):
        response = client.post("/api/jobs/khong-ton-tai-o-dau-ca/preview/grade", json={})
        assert response.status_code == 404


class TestGradePreview:
    def test_base_grade_merges_overrides_over_spec(self, client):
        """The formula stages/resolve.py uses — spec first, human overrides last."""
        job = make_job(client, spec_grade={"warmth": 2, "contrast": 1.05},
                       grade_overrides={"warmth": 8})
        body = client.post(f"/api/jobs/{job.job_id}/preview/grade", json={}).json()
        assert body["base_grade"] == {"warmth": 8, "contrast": 1.05}

    def test_trial_grade_layers_on_top_of_base(self, client):
        job = make_job(client, spec_grade={"warmth": 2, "contrast": 1.05})
        client.post(f"/api/jobs/{job.job_id}/preview/grade",
                    json={"grade": {"vibrance": 0.2}})
        variants = client.calls["grade"]["variants"]
        assert set(variants) == {"hien_tai", "thu_nghiem"}
        assert variants["thu_nghiem"] == {"warmth": 2, "contrast": 1.05, "vibrance": 0.2}

    def test_no_trial_means_only_current(self, client):
        job = make_job(client, spec_grade={"warmth": 2})
        client.post(f"/api/jobs/{job.job_id}/preview/grade", json={})
        assert list(client.calls["grade"]["variants"]) == ["hien_tai"]

    def test_missing_spec_is_not_an_error(self, client):
        """A job can be previewed before `direct` has ever run."""
        job = make_job(client)
        body = client.post(f"/api/jobs/{job.job_id}/preview/grade", json={}).json()
        assert body["base_grade"] == {}

    def test_urls_point_at_the_preview_route(self, client):
        job = make_job(client)
        body = client.post(f"/api/jobs/{job.job_id}/preview/grade", json={}).json()
        assert body["variants"][0]["url"] == f"/api/media/{job.job_id}/preview/grade_raw.png"
        assert body["contact_sheet_url"] == (
            f"/api/media/{job.job_id}/preview/grade_compare.png"
        )

    def test_at_defaults_to_the_middle_and_clamps_past_the_end(self, client):
        job = make_job(client)
        client.post(f"/api/jobs/{job.job_id}/preview/grade", json={})
        assert client.calls["grade"]["at"] == 50.0        # duration 100 / 2

        client.post(f"/api/jobs/{job.job_id}/preview/grade", json={"at": 999})
        assert client.calls["grade"]["at"] == 100.0       # never past the source

    def test_grade_must_be_an_object(self, client):
        job = make_job(client)
        response = client.post(f"/api/jobs/{job.job_id}/preview/grade",
                               json={"grade": [1, 2, 3]})
        assert response.status_code == 400

    def test_unknown_job_is_404(self, client):
        assert client.post("/api/jobs/khong-co/preview/grade", json={}).status_code == 404

    def test_missing_source_file_is_400(self, client):
        job = make_job(client)
        (client.store.root.parent / "footage.mp4").unlink()
        assert client.post(f"/api/jobs/{job.job_id}/preview/grade", json={}).status_code == 400


class TestAudioPreview:
    def test_sample_window_stays_inside_the_footage(self, client):
        """`at` plus the sample length must not run off the end."""
        job = make_job(client)
        client.post(f"/api/jobs/{job.job_id}/preview/audio",
                    json={"at": 99, "duration": 8})
        assert client.calls["audio"]["at"] == 92.0       # 100 - 8

    def test_marks_the_preset_in_use(self, client):
        job = make_job(client, audio_preset="shotgun")
        body = client.post(f"/api/jobs/{job.job_id}/preview/audio", json={}).json()
        assert body["current_preset"] == "shotgun"
        current = [s["preset"] for s in body["samples"] if s["is_current"]]
        assert current == ["shotgun"]

    def test_urls_point_at_the_preview_route(self, client):
        job = make_job(client)
        body = client.post(f"/api/jobs/{job.job_id}/preview/audio", json={}).json()
        assert body["samples"][0]["url"].startswith(f"/api/media/{job.job_id}/preview/")

    def test_presets_must_be_a_list(self, client):
        job = make_job(client)
        response = client.post(f"/api/jobs/{job.job_id}/preview/audio",
                               json={"presets": "shotgun"})
        assert response.status_code == 400


class TestClipPreview:
    def test_defaults(self, client):
        job = make_job(client)
        body = client.post(f"/api/jobs/{job.job_id}/preview/clip", json={}).json()
        assert client.calls["clip"] == {"start": 0.0, "duration": 5.0, "scale": 0.5}
        assert body["url"] == f"/api/media/{job.job_id}/preview/clip_v1_0s.mp4"

    def test_passes_through_requested_window(self, client):
        job = make_job(client)
        client.post(f"/api/jobs/{job.job_id}/preview/clip",
                    json={"start": 20, "duration": 8, "scale": 1.0})
        assert client.calls["clip"] == {"start": 20.0, "duration": 8.0, "scale": 1.0}


class TestPreviewFileRoute:
    def test_serves_a_file_from_the_preview_dir(self, client):
        job = make_job(client)
        (job.dir / "preview").mkdir(parents=True)
        (job.dir / "preview" / "grade_raw.png").write_bytes(b"png-bytes")
        response = client.get(f"/api/media/{job.job_id}/preview/grade_raw.png")
        assert response.status_code == 200
        assert response.content == b"png-bytes"

    def test_missing_file_is_404(self, client):
        job = make_job(client)
        assert client.get(f"/api/media/{job.job_id}/preview/khong-co.png").status_code == 404

    @pytest.mark.parametrize("name", ["..%2F..%2Fjob.json", ".hidden", "sub%2Ffile.png"])
    def test_refuses_names_that_could_leave_the_directory(self, client, name):
        job = make_job(client)
        response = client.get(f"/api/media/{job.job_id}/preview/{name}")
        assert response.status_code in (400, 404)

    def test_does_not_serve_job_state(self, client):
        """job.json lives one level up; the route must not reach it."""
        job = make_job(client)
        response = client.get(f"/api/media/{job.job_id}/preview/job.json")
        assert response.status_code == 404
