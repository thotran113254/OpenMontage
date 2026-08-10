"""Local job server for the talking-head auto-editor.

Bound to 127.0.0.1 and unauthenticated by design — it exposes the filesystem
paths of a single developer machine and must not be reachable from the LAN.

Run:  python -m server.app     (or: make ui-server)
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.api_cloud import router as cloud_router
from server.api_jobs import router
from server.api_presets import router as presets_router
from server.api_previews import router as previews_router
from server.api_prompts import router as prompts_router
from server.api_projects import router as projects_router

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
    yield


app = FastAPI(title="OpenMontage — Talking-head auto-edit", version="0.1.0",
              lifespan=lifespan)

# The UI runs on the Vite dev server during development; same-origin in prod.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")
app.include_router(previews_router, prefix="/api")
app.include_router(projects_router, prefix="/api")
app.include_router(prompts_router, prefix="/api")
app.include_router(presets_router, prefix="/api")
app.include_router(cloud_router, prefix="/api")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def main() -> None:
    import uvicorn

    uvicorn.run(
        "server.app:app",
        host="127.0.0.1",
        port=int(os.environ.get("AUTOEDIT_PORT", "8756")),
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
