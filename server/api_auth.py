"""Browser session login: exchange the master token for an HttpOnly cookie.

Kept out of `api_jobs.py`: this is the one router the auth middleware must let
through unauthenticated (see `server/app.py`'s `_AUTH_EXEMPT_PATHS`), so giving
it its own module makes that exemption list easy to audit at a glance.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response

from server import auth
from server.errors import CodedHTTPException
from server.schemas import (
    AuthStatusResponse, LoginRequest, LoginResponse, LogoutResponse,
)

router = APIRouter()


@router.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, response: Response) -> dict[str, Any]:
    if not auth.auth_enabled():
        # Nothing to log into: every request is already open.
        return {"ok": True}
    if not auth.token_matches(payload.token):
        raise CodedHTTPException(401, "Sai token", code="unauthorized")
    response.set_cookie(
        key=auth.SESSION_COOKIE_NAME,
        value=auth.create_session(),
        httponly=True,
        samesite="lax",
        path="/",
        max_age=auth.SESSION_TTL_SECONDS,
        # No `domain=`: a cookie scoped to whatever host served it is exactly
        # what makes it survive the Vite dev proxy (same-origin from the
        # browser's point of view) without extra configuration.
    )
    return {"ok": True}


@router.post("/auth/logout", response_model=LogoutResponse)
def logout(response: Response) -> dict[str, Any]:
    response.delete_cookie(key=auth.SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/auth/status", response_model=AuthStatusResponse)
def status(request: Request) -> dict[str, Any]:
    required = auth.auth_enabled()
    authenticated = (not required) or auth.is_authorized(
        request.headers, request.cookies, request.query_params, request.method,
        request.url.path,
    )
    return {"auth_required": required, "authenticated": authenticated}
