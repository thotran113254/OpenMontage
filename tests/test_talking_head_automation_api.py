"""The automation API: auth gate, machine-readable error codes, one-call
`POST /api/runs`, normalized status, webhook signing, SSRF rejection.

No real pipeline runs here -- the queue's `submit` is stubbed exactly like the
other server test modules, and R2 is force-disabled so a dev machine with real
Cloudflare credentials in `.env` never makes a network call from a test.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import time

import pytest

fastapi = pytest.importorskip("fastapi", reason="server deps chưa cài")
from fastapi.testclient import TestClient  # noqa: E402

from lib.talking_head_edit.job_store import JobStore  # noqa: E402


class _FakeProbe:
    """Stands in for ffprobe — a `POST /api/runs` with `project_id` registers
    each source through `Project.register_source`, which shells out to probe
    a real file. Same fixture shape as `test_talking_head_project_api.py`."""

    def __call__(self, path):
        return {"duration": 12.0, "width": 1080, "height": 1920, "fps": 30.0,
                "video_codec": "h264", "audio_codec": "aac", "audio_channels": 2,
                "has_audio": True, "mean_volume_db": -20.0, "size_bytes": 1024}


@pytest.fixture(autouse=True)
def _disable_r2(monkeypatch):
    """`_mp4_url` calls into `lib.r2_storage`; this machine's `.env` carries
    real Cloudflare credentials and `config/r2-storage.json` has `enabled:
    true`, so without this every status/run test would attempt a real network
    call."""
    from lib.r2_storage import config as r2_config

    class _Disabled:
        enabled = False

    monkeypatch.setattr(r2_config, "resolve", lambda *a, **k: _Disabled())


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from lib.talking_head_edit import project_store as project_module
    from lib.talking_head_edit import sources as sources_mod
    from lib.talking_head_edit.project_store import ProjectStore
    from server import api_jobs, api_projects, api_runs
    from server.idempotency import IdempotencyStore

    monkeypatch.setattr(sources_mod, "probe_source", _FakeProbe())
    monkeypatch.setattr(project_module, "thumbnail",
                        lambda source, out, at_seconds=1.0: (out.write_bytes(b"jpg"), out)[1])

    store = JobStore(root=tmp_path / "jobs")
    monkeypatch.setattr(api_jobs, "store", store)
    monkeypatch.setattr(api_projects, "store", ProjectStore(tmp_path / "autoedit"))
    monkeypatch.setattr(api_runs, "store", store)
    monkeypatch.setattr(api_runs, "_idempotency_store",
                        IdempotencyStore(tmp_path / "idempotency.json"))
    monkeypatch.setattr(api_runs, "_download_staging", tmp_path / "downloads")

    submitted: list = []
    monkeypatch.setattr(api_jobs.job_queue, "submit", lambda run: (submitted.append(run), 1)[1])
    monkeypatch.setattr(api_jobs.job_queue, "cancel", lambda job_id: True)

    from server.app import app

    test_client = TestClient(app)
    test_client.store = store          # type: ignore[attr-defined]
    test_client.submitted = submitted  # type: ignore[attr-defined]
    return test_client


def make_source(client, name="footage.mp4") -> str:
    path = client.store.root.parent / name
    path.write_bytes(b"video")
    return str(path)


@contextlib.contextmanager
def _noop_pin(url):
    """Stands in for `source_fetch.resolve_and_pin` in webhook tests: those
    tests exercise retry/signature behavior, not the SSRF guard (covered by
    TestSSRFGuard), and `hook.test` is not a real, resolvable host."""
    yield


class TestAuthGate:
    def test_health_is_open_and_reports_auth_off(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"ok": True, "auth": False}

    def test_without_token_configured_everything_stays_open(self, client):
        assert client.get("/api/jobs").status_code == 200

    def test_with_token_configured_bare_request_is_401(self, client, monkeypatch):
        monkeypatch.setenv("AUTOEDIT_API_TOKEN", "s3cret")
        response = client.get("/api/jobs")
        assert response.status_code == 401
        assert response.json()["code"] == "unauthorized"

    def test_health_stays_open_even_with_token_configured(self, client, monkeypatch):
        monkeypatch.setenv("AUTOEDIT_API_TOKEN", "s3cret")
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"ok": True, "auth": True}

    def test_bearer_token_is_accepted(self, client, monkeypatch):
        monkeypatch.setenv("AUTOEDIT_API_TOKEN", "s3cret")
        response = client.get("/api/jobs", headers={"Authorization": "Bearer s3cret"})
        assert response.status_code == 200

    def test_wrong_bearer_token_is_401(self, client, monkeypatch):
        monkeypatch.setenv("AUTOEDIT_API_TOKEN", "s3cret")
        response = client.get("/api/jobs", headers={"Authorization": "Bearer wrong"})
        assert response.status_code == 401

    def test_x_api_key_header_is_accepted(self, client, monkeypatch):
        monkeypatch.setenv("AUTOEDIT_API_TOKEN", "s3cret")
        response = client.get("/api/jobs", headers={"X-API-Key": "s3cret"})
        assert response.status_code == 200

    def test_raw_query_token_is_no_longer_accepted(self, client, monkeypatch):
        """The master token in a query string leaks into proxy/access logs and
        Referer headers -- only a scoped, expiring signature (`?exp=&sig=`,
        see TestSignedURL) or a session cookie may authorize a GET now."""
        monkeypatch.setenv("AUTOEDIT_API_TOKEN", "s3cret")
        response = client.get("/api/jobs?token=s3cret")
        assert response.status_code == 401

    def test_openapi_and_docs_require_auth_too(self, client, monkeypatch):
        monkeypatch.setenv("AUTOEDIT_API_TOKEN", "s3cret")
        assert client.get("/openapi.json").status_code == 401
        assert client.get(
            "/openapi.json", headers={"Authorization": "Bearer s3cret"},
        ).status_code == 200

    def test_options_preflight_is_never_gated(self, client, monkeypatch):
        monkeypatch.setenv("AUTOEDIT_API_TOKEN", "s3cret")
        response = client.options("/api/jobs")
        assert response.status_code != 401


class TestSessionAuth:
    def test_login_sets_an_httponly_cookie_with_no_domain(self, client, monkeypatch):
        monkeypatch.setenv("AUTOEDIT_API_TOKEN", "s3cret")
        response = client.post("/api/auth/login", json={"token": "s3cret"})
        assert response.status_code == 200 and response.json() == {"ok": True}
        set_cookie = response.headers.get("set-cookie", "")
        assert "autoedit_session=" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "Path=/" in set_cookie
        assert "samesite=lax" in set_cookie.lower()
        assert "domain=" not in set_cookie.lower(), (
            "no Domain attribute -- required for the Vite proxy to pass it through")

    def test_wrong_token_login_is_401(self, client, monkeypatch):
        monkeypatch.setenv("AUTOEDIT_API_TOKEN", "s3cret")
        response = client.post("/api/auth/login", json={"token": "wrong"})
        assert response.status_code == 401
        assert response.json()["code"] == "unauthorized"

    def test_cookie_from_login_authorizes_a_later_request(self, client, monkeypatch):
        """Simulates the Vite proxy passing `Cookie`/`Set-Cookie` through for a
        same-origin `/api` call: the cookie value is replayed on a fresh
        request rather than relying on TestClient's own cookie jar."""
        monkeypatch.setenv("AUTOEDIT_API_TOKEN", "s3cret")
        login = client.post("/api/auth/login", json={"token": "s3cret"})
        cookie_value = login.cookies.get("autoedit_session")
        assert cookie_value

        client.cookies.set("autoedit_session", cookie_value)
        response = client.get("/api/jobs")
        assert response.status_code == 200

    def test_logout_clears_the_cookie(self, client, monkeypatch):
        monkeypatch.setenv("AUTOEDIT_API_TOKEN", "s3cret")
        login = client.post("/api/auth/login", json={"token": "s3cret"})
        cookie_value = login.cookies.get("autoedit_session")
        logout = client.post("/api/auth/logout")
        assert logout.status_code == 200
        # Deleting via a Max-Age=0 Set-Cookie -- the ORIGINAL cookie value
        # itself is not invalidated server-side (this is a stateless signed
        # cookie), so this test only proves the client is told to drop it.
        assert "autoedit_session=" in logout.headers.get("set-cookie", "")

    def test_status_reports_auth_required_and_authenticated(self, client, monkeypatch):
        assert client.get("/api/auth/status").json() == {
            "auth_required": False, "authenticated": True}

        monkeypatch.setenv("AUTOEDIT_API_TOKEN", "s3cret")
        assert client.get("/api/auth/status").json() == {
            "auth_required": True, "authenticated": False}

        login = client.post("/api/auth/login", json={"token": "s3cret"})
        cookie_value = login.cookies.get("autoedit_session")
        client.cookies.set("autoedit_session", cookie_value)
        authed = client.get("/api/auth/status")
        assert authed.json() == {"auth_required": True, "authenticated": True}

    def test_status_is_exempt_from_the_auth_gate(self, client, monkeypatch):
        monkeypatch.setenv("AUTOEDIT_API_TOKEN", "s3cret")
        assert client.get("/api/auth/status").status_code == 200


class TestSignedURL:
    def test_a_freshly_signed_path_authorizes_a_get(self, client, monkeypatch):
        from server import auth

        monkeypatch.setenv("AUTOEDIT_API_TOKEN", "s3cret")
        path = "/api/jobs"
        expiry, sig = auth.sign_path(path)
        response = client.get(f"{path}?exp={expiry}&sig={sig}")
        assert response.status_code == 200

    def test_an_expired_signature_is_401(self, client, monkeypatch):
        from server import auth

        monkeypatch.setenv("AUTOEDIT_API_TOKEN", "s3cret")
        path = "/api/jobs"
        expiry, sig = auth.sign_path(path, ttl_seconds=-10)
        response = client.get(f"{path}?exp={expiry}&sig={sig}")
        assert response.status_code == 401

    def test_a_signature_for_a_different_path_is_401(self, client, monkeypatch):
        from server import auth

        monkeypatch.setenv("AUTOEDIT_API_TOKEN", "s3cret")
        expiry, sig = auth.sign_path("/api/jobs")
        response = client.get(f"/api/queue?exp={expiry}&sig={sig}")
        assert response.status_code == 401

    def test_a_signature_does_not_authorize_a_post(self, client, monkeypatch):
        from server import auth

        monkeypatch.setenv("AUTOEDIT_API_TOKEN", "s3cret")
        path = "/api/jobs/khong-co-that/cancel"
        expiry, sig = auth.sign_path(path)
        response = client.post(f"{path}?exp={expiry}&sig={sig}")
        assert response.status_code == 401


class TestErrorShape:
    def test_not_found_carries_a_code(self, client):
        response = client.get("/api/jobs/khong-co-that")
        assert response.status_code == 404
        body = response.json()
        assert body["code"] == "not_found"
        assert "detail" in body

    def test_pydantic_validation_error_carries_a_code(self, client):
        response = client.post("/api/runs", json={"sources": []})
        assert response.status_code == 422
        assert response.json()["code"] == "validation_error"

    def test_already_running_conflict_carries_a_code(self, client, monkeypatch):
        from server.queue_worker import AlreadyRunningError

        def explode(run):
            raise AlreadyRunningError("đang chạy rồi")

        from server import api_jobs

        monkeypatch.setattr(api_jobs.job_queue, "submit", explode)
        source = make_source(client)
        response = client.post("/api/jobs", json={"input_path": source})
        assert response.status_code == 409
        assert response.json()["code"] == "conflict"


class TestCreateRun:
    def test_create_from_local_path_defaults_to_full_pipeline(self, client):
        source = make_source(client)
        response = client.post("/api/runs", json={"sources": [{"path": source}]})
        assert response.status_code == 202
        body = response.json()
        assert body["run_id"]
        assert body["status_url"] == f"/api/runs/{body['run_id']}"
        assert body["events_url"] == f"/api/jobs/{body['run_id']}/events"
        assert client.submitted[-1].stages is None, "render=true runs the default full stage list"

    def test_render_false_stops_before_render(self, client):
        source = make_source(client)
        response = client.post("/api/runs",
                               json={"sources": [{"path": source}], "render": False})
        assert response.status_code == 202
        stages = client.submitted[-1].stages
        assert "render" not in stages and "verify" not in stages
        assert "resolve" in stages

    def test_rejects_a_source_with_neither_path_nor_url(self, client):
        response = client.post("/api/runs", json={"sources": [{}]})
        assert response.status_code == 400
        assert response.json()["code"] == "bad_request"

    def test_idempotency_key_replays_the_same_run(self, client):
        source = make_source(client)
        body = {"sources": [{"path": source}], "title": "cung mot yeu cau"}
        headers = {"Idempotency-Key": "abc-123"}
        first = client.post("/api/runs", json=body, headers=headers)
        second = client.post("/api/runs", json=body, headers=headers)
        assert first.status_code == 202 and second.status_code == 202
        assert first.json()["run_id"] == second.json()["run_id"]
        assert len(client.submitted) == 1, "the replay must not queue a second job"

    def test_idempotency_key_conflict_on_different_body(self, client):
        source = make_source(client)
        headers = {"Idempotency-Key": "abc-123"}
        client.post("/api/runs", json={"sources": [{"path": source}], "title": "A"},
                    headers=headers)
        response = client.post("/api/runs", json={"sources": [{"path": source}], "title": "B"},
                               headers=headers)
        assert response.status_code == 409
        assert response.json()["code"] == "idempotency_conflict"

    def test_create_under_an_existing_project_reuses_project_create_job(self, client):
        from server import api_projects

        project = api_projects.store.create("Du an tu dong")
        source = make_source(client, "clip.mp4")
        response = client.post("/api/runs", json={
            "project_id": project.project_id, "sources": [{"path": source}],
        })
        assert response.status_code == 202
        state = project.load()
        assert state["jobs"], "the build must be recorded on the project"
        assert len(state["sources"]) == 1

    def test_unknown_project_id_is_404(self, client):
        source = make_source(client)
        response = client.post("/api/runs", json={
            "project_id": "khong-ton-tai", "sources": [{"path": source}],
        })
        assert response.status_code == 404

    def test_webhook_url_must_be_http_or_https(self, client):
        source = make_source(client)
        response = client.post("/api/runs", json={
            "sources": [{"path": source}], "webhook_url": "ftp://example.com/hook",
        })
        assert response.status_code == 400
        assert response.json()["code"] == "bad_request"


class TestRunStatus:
    def test_freshly_queued_run(self, client):
        source = make_source(client)
        run_id = client.post("/api/runs", json={"sources": [{"path": source}]}).json()["run_id"]
        body = client.get(f"/api/runs/{run_id}").json()
        assert body["state"] == "queued"
        assert body["percent"] == 0
        assert body["outputs"]["mp4_url"] is None

    def test_percent_reflects_completed_stages(self, client):
        source = make_source(client)
        run_id = client.post("/api/runs", json={"sources": [{"path": source}]}).json()["run_id"]
        job = client.store.get(run_id)
        state = job.load()
        state["stages"]["probe"]["status"] = "completed"
        state["stages"]["transcribe"]["status"] = "completed"
        job.save(state)
        body = client.get(f"/api/runs/{run_id}").json()
        assert body["percent"] > 0

    def test_prepare_mode_finish_reports_awaiting_render(self, client):
        source = make_source(client)
        run_id = client.post("/api/runs",
                             json={"sources": [{"path": source}], "render": False}).json()["run_id"]
        job = client.store.get(run_id)
        job.update(status="completed")
        body = client.get(f"/api/runs/{run_id}").json()
        assert body["state"] == "awaiting_render"

    def test_finished_render_reports_succeeded_with_mp4_url(self, client):
        source = make_source(client)
        run_id = client.post("/api/runs", json={"sources": [{"path": source}]}).json()["run_id"]
        job = client.store.get(run_id)
        state = job.load()
        state["stages"]["render"]["status"] = "completed"
        state["status"] = "completed"
        job.save(state)
        (job.dir / "final.mp4").write_bytes(b"mp4")
        body = client.get(f"/api/runs/{run_id}").json()
        assert body["state"] == "succeeded"
        assert body["outputs"]["mp4_url"] == f"http://testserver/api/media/{run_id}/final.mp4"

    def test_mp4_url_carries_a_scoped_signature_not_the_master_token(self, client, monkeypatch):
        from server import auth

        monkeypatch.setenv("AUTOEDIT_API_TOKEN", "s3cret")
        source = make_source(client)
        run_id = client.post("/api/runs", json={"sources": [{"path": source}]},
                             headers={"Authorization": "Bearer s3cret"}).json()["run_id"]
        job = client.store.get(run_id)
        state = job.load()
        state["stages"]["render"]["status"] = "completed"
        state["status"] = "completed"
        job.save(state)
        (job.dir / "final.mp4").write_bytes(b"mp4")
        body = client.get(f"/api/runs/{run_id}",
                          headers={"Authorization": "Bearer s3cret"}).json()
        mp4_url = body["outputs"]["mp4_url"]
        assert "s3cret" not in mp4_url, "the master token must never appear in a response"
        assert "&sig=" in mp4_url and "?exp=" in mp4_url

        # The signature must actually work: strip the base and replay it.
        path_and_query = mp4_url.split("/api/media/", 1)[1]
        response = client.get(f"/api/media/{path_and_query}")
        assert response.status_code == 200
        assert auth.verify_signed_path(
            f"/api/media/{run_id}/final.mp4",
            mp4_url.split("exp=")[1].split("&")[0],
            mp4_url.split("sig=")[1],
        )

    def test_failed_stage_reports_a_structured_error(self, client):
        source = make_source(client)
        run_id = client.post("/api/runs", json={"sources": [{"path": source}]}).json()["run_id"]
        job = client.store.get(run_id)
        state = job.load()
        state["stages"]["direct"]["status"] = "failed"
        state["stages"]["direct"]["error"] = "model từ chối"
        state["status"] = "failed"
        job.save(state)
        body = client.get(f"/api/runs/{run_id}").json()
        assert body["state"] == "failed"
        assert body["error"] == {"code": "direct_failed", "message": "model từ chối"}

    def test_unknown_run_is_404(self, client):
        assert client.get("/api/runs/khong-co-that").status_code == 404


class TestRunReviseAndCancel:
    def test_revise_reuses_the_chat_turn_and_reports_status_url(self, client, monkeypatch):
        from lib.talking_head_edit.stages import revise as revise_stage

        def stub_revise(job, options):
            job.update(current_version=1, versions=[
                {"version": 1, "kind": "revise", "instruction": options.get("revise_instruction")},
            ])
            job.spec_path(1).write_text('{"events": []}', encoding="utf-8")
            return {"version": 1, "report": {"added": 0, "removed": 0, "modified": 0},
                    "usage": {"total_tokens": 10}}

        monkeypatch.setattr(revise_stage, "run", stub_revise)
        source = make_source(client)
        run_id = client.post("/api/runs", json={"sources": [{"path": source}]}).json()["run_id"]
        response = client.post(f"/api/runs/{run_id}/revise", json={"message": "bỏ card cuối"})
        assert response.status_code == 200
        body = response.json()
        assert body["applied"] is True
        assert body["status_url"] == f"/api/runs/{run_id}"
        # render=true at creation -> a revise re-queues render too
        assert client.submitted[-1].stages == ["audit", "resolve", "render", "verify"]

    def test_revise_without_render_only_requeues_preview_stages(self, client, monkeypatch):
        from lib.talking_head_edit.stages import revise as revise_stage

        monkeypatch.setattr(revise_stage, "run", lambda job, options: {
            "version": 1, "report": {}, "usage": {},
        })
        source = make_source(client)
        run_id = client.post("/api/runs",
                             json={"sources": [{"path": source}], "render": False}).json()["run_id"]
        client.post(f"/api/runs/{run_id}/revise", json={"message": "sửa nhẹ"})
        assert client.submitted[-1].stages == ["audit", "resolve"]

    def test_cancel_delegates_to_the_queue(self, client):
        source = make_source(client)
        run_id = client.post("/api/runs", json={"sources": [{"path": source}]}).json()["run_id"]
        response = client.post(f"/api/runs/{run_id}/cancel")
        assert response.status_code == 200
        assert response.json() == {"run_id": run_id, "cancelled": True}


class TestSSRFGuard:
    def test_rejects_loopback_target(self):
        from server.source_fetch import BlockedURLError, download_url_to

        with pytest.raises(BlockedURLError):
            download_url_to("http://127.0.0.1/video.mp4", None, max_mb=10)  # type: ignore[arg-type]

    def test_rejects_non_http_scheme(self):
        from server.source_fetch import BlockedURLError, download_url_to

        with pytest.raises(BlockedURLError):
            download_url_to("ftp://example.com/video.mp4", None, max_mb=10)  # type: ignore[arg-type]

    def test_rejects_private_ip_literal(self):
        from server.source_fetch import BlockedURLError, download_url_to

        with pytest.raises(BlockedURLError):
            download_url_to("http://10.1.2.3/video.mp4", None, max_mb=10)  # type: ignore[arg-type]

    def test_rejects_cgnat_range(self):
        """100.64.0.0/10 (RFC 6598) is not `is_private` on this project's
        Python versions but is routed by Tailscale/CGNAT overlays -- must be
        blocked explicitly (M12 in the automation-API review)."""
        from server.source_fetch import BlockedURLError, download_url_to

        with pytest.raises(BlockedURLError):
            download_url_to("http://100.64.0.1/video.mp4", None, max_mb=10)  # type: ignore[arg-type]

    def test_dns_pin_forces_resolution_to_the_vetted_ip(self):
        """Closes the DNS-rebinding TOCTOU: once pinned, a lookup for the
        pinned hostname returns the vetted IP even though the literal address
        given differs -- proving `requests`' own resolution (which goes
        through `socket.getaddrinfo`, patched by this pin) cannot answer
        something else a moment later."""
        import socket

        from server.source_fetch import _pinned_dns

        with _pinned_dns("pinned.example", "203.0.113.5"):
            infos = socket.getaddrinfo("pinned.example", None)
        assert infos[0][4][0] == "203.0.113.5"
        # The pin does not leak past the `with` block.
        with pytest.raises(socket.gaierror):
            socket.getaddrinfo("pinned.example", None)

    def test_webhook_url_is_ssrf_checked_at_run_creation(self, client):
        source = make_source(client)
        response = client.post("/api/runs", json={
            "sources": [{"path": source}], "webhook_url": "http://127.0.0.1:9/hook",
        })
        assert response.status_code == 400
        assert response.json()["code"] == "bad_request"

    def test_webhook_delivery_refuses_a_private_target(self, client, monkeypatch):
        from server import webhooks as webhooks_mod

        job = client.store.create(make_source(client),
                                  {"webhook_url": "http://127.0.0.1:9/hook"}, title="wh")
        job.update(status="completed")

        def fail_post(*a, **k):
            raise AssertionError("must never dial an SSRF-blocked webhook URL")

        monkeypatch.setattr(webhooks_mod.requests, "post", fail_post)
        webhooks_mod._deliver("http://127.0.0.1:9/hook", {"run_id": job.job_id}, job)
        events = list(job.read_events())
        assert any(event.get("type") == "warning" for event in events)


class TestWebhookDelivery:
    def test_fires_with_a_valid_hmac_signature(self, client, monkeypatch, tmp_path):
        from server import webhooks as webhooks_mod

        monkeypatch.setenv("AUTOEDIT_API_TOKEN", "s3cret")
        job = client.store.create(make_source(client), {"webhook_url": "http://hook.test/x"},
                                  title="wh")
        job.update(status="completed")

        calls: list[dict] = []

        class _Resp:
            ok = True
            status_code = 200

        def fake_post(url, data=None, headers=None, timeout=None):
            calls.append({"url": url, "data": data, "headers": headers})
            return _Resp()

        monkeypatch.setattr(webhooks_mod.requests, "post", fake_post)
        monkeypatch.setattr(webhooks_mod, "resolve_and_pin", _noop_pin)
        webhooks_mod.fire_if_terminal(job, lambda j: {"run_id": j.job_id, "state": "succeeded"})

        for _ in range(100):
            if calls:
                break
            time.sleep(0.02)
        assert calls, "the webhook must fire in the background thread"
        assert calls[0]["url"] == "http://hook.test/x"
        body = calls[0]["data"]
        expected = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
        assert calls[0]["headers"]["X-Autoedit-Signature"] == expected

    def test_no_signature_header_when_no_token_configured(self, client, monkeypatch):
        from server import webhooks as webhooks_mod

        job = client.store.create(make_source(client), {"webhook_url": "http://hook.test/x"},
                                  title="wh")
        job.update(status="failed")

        calls: list[dict] = []

        class _Resp:
            ok = True
            status_code = 200

        def fake_post(url, data=None, headers=None, timeout=None):
            calls.append(headers)
            return _Resp()

        monkeypatch.setattr(webhooks_mod.requests, "post", fake_post)
        monkeypatch.setattr(webhooks_mod, "resolve_and_pin", _noop_pin)
        webhooks_mod.fire_if_terminal(job, lambda j: {"run_id": j.job_id})

        for _ in range(100):
            if calls:
                break
            time.sleep(0.02)
        assert calls and "X-Autoedit-Signature" not in calls[0]

    def test_non_terminal_status_never_fires(self, client, monkeypatch):
        from server import webhooks as webhooks_mod

        job = client.store.create(make_source(client), {"webhook_url": "http://hook.test/x"},
                                  title="wh")
        job.update(status="running")

        def fail_thread(*a, **k):
            raise AssertionError("must not start a delivery thread for a non-terminal status")

        monkeypatch.setattr(webhooks_mod.threading, "Thread", fail_thread)
        webhooks_mod.fire_if_terminal(job, lambda j: {"run_id": j.job_id})

    def test_missing_webhook_url_never_fires(self, client, monkeypatch):
        from server import webhooks as webhooks_mod

        job = client.store.create(make_source(client), {}, title="wh")
        job.update(status="completed")

        def fail_thread(*a, **k):
            raise AssertionError("must not start a delivery thread with no webhook_url")

        monkeypatch.setattr(webhooks_mod.threading, "Thread", fail_thread)
        webhooks_mod.fire_if_terminal(job, lambda j: {"run_id": j.job_id})

    def test_retries_then_logs_a_job_warning_on_repeated_failure(self, client, monkeypatch):
        from server import webhooks as webhooks_mod

        monkeypatch.setattr(webhooks_mod, "_BACKOFF_SECONDS", (0.0, 0.0, 0.0))
        job = client.store.create(make_source(client), {"webhook_url": "http://hook.test/x"},
                                  title="wh")
        job.update(status="completed")

        attempts = []

        def always_fails(url, data=None, headers=None, timeout=None):
            attempts.append(1)
            raise webhooks_mod.requests.RequestException("boom")

        monkeypatch.setattr(webhooks_mod.requests, "post", always_fails)
        monkeypatch.setattr(webhooks_mod, "resolve_and_pin", _noop_pin)
        webhooks_mod._deliver("http://hook.test/x", {"run_id": job.job_id}, job)

        assert len(attempts) == 3
        events = list(job.read_events())
        assert any(event.get("type") == "warning" for event in events)


class TestCutsEndpoint:
    def _job_with_spec(self, client):
        job = client.store.create(make_source(client), {}, title="cuts")
        job.spec_path(0).write_text('{"events": [], "cut_proposed": []}', encoding="utf-8")
        return job

    def test_applies_deterministic_cuts_without_an_llm_call(self, client):
        job = self._job_with_spec(client)
        response = client.post(f"/api/jobs/{job.job_id}/cuts",
                               json={"cut": [[5, 6]], "keep": []})
        assert response.status_code == 200
        body = response.json()
        assert body["version"] == 1
        assert "report" in body
        assert client.submitted[-1].stages == ["audit", "resolve"]
        assert client.submitted[-1].use_cache is False

    def test_rejects_a_pair_with_the_wrong_shape(self, client):
        job = self._job_with_spec(client)
        response = client.post(f"/api/jobs/{job.job_id}/cuts", json={"cut": [[5]], "keep": []})
        assert response.status_code == 400
        assert response.json()["code"] == "bad_request"

    def test_rejects_a_reversed_range(self, client):
        job = self._job_with_spec(client)
        response = client.post(f"/api/jobs/{job.job_id}/cuts", json={"cut": [[6, 5]], "keep": []})
        assert response.status_code == 400

    def test_rejects_a_negative_index(self, client):
        job = self._job_with_spec(client)
        response = client.post(f"/api/jobs/{job.job_id}/cuts", json={"cut": [[-1, 2]], "keep": []})
        assert response.status_code == 400

    def test_empty_cut_and_keep_is_rejected(self, client):
        job = self._job_with_spec(client)
        response = client.post(f"/api/jobs/{job.job_id}/cuts", json={"cut": [], "keep": []})
        assert response.status_code == 400

    def test_refuses_while_the_job_is_busy(self, client, monkeypatch):
        from server import api_jobs

        job = self._job_with_spec(client)
        monkeypatch.setattr(api_jobs.job_queue, "is_busy", lambda job_id: True)
        response = client.post(f"/api/jobs/{job.job_id}/cuts",
                               json={"cut": [[1, 2]], "keep": []})
        assert response.status_code == 409
        assert response.json()["code"] == "job_busy"


class TestBusyGating:
    def test_direct_revise_refuses_while_the_job_is_busy(self, client, monkeypatch):
        from server import api_jobs

        job = client.store.create(make_source(client), {}, title="busy")
        monkeypatch.setattr(api_jobs.job_queue, "is_busy", lambda job_id: True)
        response = client.post(f"/api/jobs/{job.job_id}/revise",
                               json={"instruction": "bỏ card cuối"})
        assert response.status_code == 409
        assert response.json()["code"] == "job_busy"

    def test_chat_refuses_while_the_job_is_busy(self, client, monkeypatch):
        from server import api_jobs

        job = client.store.create(make_source(client), {}, title="busy")
        monkeypatch.setattr(api_jobs.job_queue, "is_busy", lambda job_id: True)
        response = client.post(f"/api/jobs/{job.job_id}/chat", json={"message": "xin chào"})
        assert response.status_code == 409
        assert response.json()["code"] == "job_busy"

    def test_runs_revise_refuses_while_the_job_is_busy(self, client, monkeypatch):
        from server import api_jobs

        source = make_source(client)
        run_id = client.post("/api/runs", json={"sources": [{"path": source}]}).json()["run_id"]
        monkeypatch.setattr(api_jobs.job_queue, "is_busy", lambda job_id: True)
        response = client.post(f"/api/runs/{run_id}/revise", json={"message": "sửa"})
        assert response.status_code == 409
        assert response.json()["code"] == "job_busy"

    def test_not_busy_by_default_on_a_fresh_job(self, client):
        """Sanity check for the fixture: `is_busy` is the REAL `JobQueue`
        method here, not stubbed — a freshly created, never-submitted job must
        read as not busy."""
        from server import api_jobs

        job = client.store.create(make_source(client), {}, title="idle")
        assert api_jobs.job_queue.is_busy(job.job_id) is False


class TestIdempotencyPending:
    def test_a_pending_reservation_is_409_with_retry_after(self, client):
        from server import api_runs
        from server.schemas import CreateRunRequest

        source = make_source(client)
        payload = {"sources": [{"path": source}]}
        body_hash = api_runs._canonical_body_hash(CreateRunRequest(**payload))
        api_runs._idempotency_store.reserve("stuck-key", body_hash)

        response = client.post(
            "/api/runs", json=payload, headers={"Idempotency-Key": "stuck-key"})
        assert response.status_code == 409
        assert response.json()["code"] == "idempotency_in_progress"
        assert response.headers.get("retry-after") == "30"

    def test_a_failed_creation_releases_the_reservation(self, client):
        source = make_source(client)
        headers = {"Idempotency-Key": "release-key"}
        # An unknown project_id fails naturally (404) partway through
        # `create_run`, after the key was reserved.
        first = client.post("/api/runs", json={
            "project_id": "khong-ton-tai", "sources": [{"path": source}],
        }, headers=headers)
        assert first.status_code == 404

        # A retry under the SAME key must not see `idempotency_in_progress` or
        # `idempotency_conflict` forever -- the failed attempt's reservation
        # must have been released.
        second = client.post(
            "/api/runs", json={"sources": [{"path": source}]}, headers=headers)
        assert second.status_code == 202


class TestAutopilotDryRun:
    def test_dry_run_is_rejected_since_the_cli_has_no_dry_run_mode(self, client):
        job = client.store.create(make_source(client), {}, title="autopilot")
        response = client.post(f"/api/jobs/{job.job_id}/autopilot", json={"dry_run": True})
        assert response.status_code == 400
        assert response.json()["code"] == "unsupported"
