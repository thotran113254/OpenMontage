"""Local job server for the talking-head auto-editor.

Unauthenticated by design — it exposes filesystem paths of this developer
machine. Default bind is 127.0.0.1. Set AUTOEDIT_BIND=0.0.0.0 and
AUTOEDIT_PUBLIC_HOST to publish on a VPS IP (see CLAUDE.md).

Run:  python -m server.app     (or: make autoedit-server)
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from lib.env_loader import load_env
from server import auth, errors
from server.api_auth import router as auth_router
from server.api_cloud import router as cloud_router
from server.api_edit_styles import router as edit_styles_router
from server.api_job_versions import router as job_versions_router
from server.api_jobs import router
from server.api_presets import router as presets_router
from server.api_previews import router as previews_router
from server.api_projects import router as projects_router
from server.api_prompts import router as prompts_router
from server.api_runs import router as runs_router
from server.schemas import HealthResponse

load_env()

DEFAULT_API_PORT = "8861"
DEFAULT_UI_PORT = "5617"
DEFAULT_BIND = "127.0.0.1"


def _ui_origins() -> list[str]:
    port = os.environ.get("AUTOEDIT_UI_PORT", DEFAULT_UI_PORT)
    public_host = os.environ.get("AUTOEDIT_PUBLIC_HOST", "").strip()
    origins = [
        f"http://localhost:{port}",
        f"http://127.0.0.1:{port}",
    ]
    if public_host:
        origins.append(f"http://{public_host}:{port}")
    extra = os.environ.get("AUTOEDIT_CORS_ORIGINS", "")
    origins.extend(item.strip() for item in extra.split(",") if item.strip())
    # Preserve order, drop duplicates.
    return list(dict.fromkeys(origins))


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Drop `.part` files left by uploads that died mid-flight.

    They are large (video), invisible in the UI, and nothing will ever finish
    them — so server start is the only place they can be cleaned.
    """
    from server.api_projects import store as project_store

    removed = project_store.clean_partial_uploads()
    if removed:
        print(f"Đã dọn {len(removed)} file upload dở dang")

    # Best-effort orphan sweep for cloud-render rentals (phase 01's design
    # requires a reap() at every entry point, including server startup --
    # a restarted server must not leave a past-deadline rental unswept
    # until the next render call). Never blocks startup: missing `vastai`,
    # no API key, or a network hiccup all degrade to a printed warning.
    try:
        from lib.cloud_render import ledger as cloud_ledger
        report = cloud_ledger.reap()
        if report.total:
            print(f"cloud-render reap: destroyed={len(report.destroyed)} "
                  f"closed={len(report.closed)} left_alone={len(report.left_alone)}")
    except Exception as exc:  # noqa: BLE001 -- startup must never fail over this
        print(f"cloud-render reap bị bỏ qua lúc khởi động server: {exc}")

    if not auth.auth_enabled():
        bind = os.environ.get("AUTOEDIT_BIND", DEFAULT_BIND)
        if not auth.is_loopback_bind(bind):
            print(
                f"CẢNH BÁO: server bind ra {bind} (không phải loopback) nhưng "
                "AUTOEDIT_API_TOKEN chưa đặt -- API đang mở cho bất kỳ ai gõ được "
                "IP này. Đặt AUTOEDIT_API_TOKEN trong .env để bật xác thực."
            )
    yield


app = FastAPI(title="OpenMontage — Talking-head auto-edit", version="0.1.0",
              lifespan=lifespan)
errors.install(app)

# The UI runs on the Vite dev server during development; same-origin in prod.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ui_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Paths reachable without a token even when AUTOEDIT_API_TOKEN is set: health
# checks must stay reachable for uptime monitoring (indistinguishable from "is
# the token wrong" otherwise), and the auth endpoints themselves obviously
# cannot require what they exist to grant.
_AUTH_EXEMPT_PATHS = {"/api/health", "/api/auth/login", "/api/auth/logout", "/api/auth/status"}
# Everything under /api/* plus the interactive docs and the schema they read
# from -- exposing filesystem-adjacent job data through /docs "Try it out"
# would otherwise bypass the same gate the JSON endpoints enforce.
_AUTH_PROTECTED_PREFIXES = ("/api/",)
_AUTH_PROTECTED_EXACT = {"/openapi.json", "/docs", "/redoc"}


@app.middleware("http")
async def _auth_gate(request: Request, call_next):
    # A CORS preflight carries no credentials and expects no body; gating it
    # would make the browser see a 401 with no CORS headers instead of the
    # preflight response, breaking every cross-origin call before it starts.
    if request.method == "OPTIONS":
        return await call_next(request)
    path = request.url.path
    protected = path in _AUTH_PROTECTED_EXACT or path.startswith(_AUTH_PROTECTED_PREFIXES)
    if protected and path not in _AUTH_EXEMPT_PATHS and auth.auth_enabled():
        if not auth.is_authorized(
            request.headers, request.cookies, request.query_params, request.method, path,
        ):
            return JSONResponse(
                status_code=401,
                content={"detail": "Thiếu hoặc sai thông tin xác thực", "code": "unauthorized"},
            )
    return await call_next(request)


app.include_router(router, prefix="/api")
app.include_router(previews_router, prefix="/api")
app.include_router(projects_router, prefix="/api")
app.include_router(prompts_router, prefix="/api")
app.include_router(presets_router, prefix="/api")
app.include_router(edit_styles_router, prefix="/api")
app.include_router(cloud_router, prefix="/api")
app.include_router(runs_router, prefix="/api")
app.include_router(job_versions_router, prefix="/api")
app.include_router(auth_router, prefix="/api")


@app.get("/api/health", response_model=HealthResponse)
def health() -> dict[str, Any]:
    return {"ok": True, "auth": auth.auth_enabled()}


@app.get("/api/config")
def autoedit_config() -> dict[str, str | bool]:
    """Runtime defaults the UI shows when a job does not override them."""
    from lib.talking_head_edit.director_client import default_model, gateway_config

    try:
        gateway_config()
        gateway_ok = True
    except Exception:  # noqa: BLE001 — surface as a boolean, not a 500
        gateway_ok = False

    from lib.cloud_render.colab import load_config as colab_config

    return {
        "director_model": default_model(),
        "gateway_configured": gateway_ok,
        "colab_render": bool(colab_config().get("enabled")),
    }


def main() -> None:
    import uvicorn

    uvicorn.run(
        "server.app:app",
        host=os.environ.get("AUTOEDIT_BIND", DEFAULT_BIND),
        port=int(os.environ.get("AUTOEDIT_PORT", DEFAULT_API_PORT)),
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
