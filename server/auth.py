"""Everything that decides whether a request may reach `/api/*`.

Three ways in, once `AUTOEDIT_API_TOKEN` is set:

1. `Authorization: Bearer <t>` / `X-API-Key: <t>` — automation callers.
2. The `autoedit_session` cookie — the browser, after `POST /api/auth/login`
   exchanges the master token for it once. The raw token never sits in the
   browser (no query string, no JS-readable storage) after that.
3. A signed URL (`?exp=...&sig=...`, GET only) — for `<video src>`/`<img src>`
   and webhook `mp4_url`s, where nothing can attach a header. The signature is
   scoped to one exact path and expires; unlike a token in the query string,
   leaking it (access logs, Referer, a shared link) does not hand over the
   whole API, and only for a bounded time.

Off entirely when `AUTOEDIT_API_TOKEN` is unset -- the historical behaviour.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any

SESSION_COOKIE_NAME = "autoedit_session"
SESSION_VERSION = "v1"
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
DEFAULT_SIGNED_URL_TTL_SECONDS = 24 * 60 * 60


def api_token() -> str:
    """Read fresh from the environment on every call -- never cached, so a
    test (or a `.env` reload) that changes the var takes effect immediately."""
    return (os.environ.get("AUTOEDIT_API_TOKEN") or "").strip()


def auth_enabled() -> bool:
    return bool(api_token())


def _constant_time_eq(a: str, b: str) -> bool:
    """`hmac.compare_digest` raises `TypeError` on a `str` with a non-ASCII
    byte (a header a client controls) -- comparing UTF-8 bytes instead makes a
    malformed/foreign-charset token a clean 401 rather than a 500."""
    try:
        return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
    except (UnicodeEncodeError, AttributeError):
        return False


def token_matches(candidate: str | None) -> bool:
    """False for an unset candidate even when auth is off, so a caller that
    forgets to check `auth_enabled()` first fails closed, not open."""
    if not candidate:
        return False
    expected = api_token()
    if not expected:
        return False
    return _constant_time_eq(candidate, expected)


def extract_token(headers: Any) -> str | None:
    """`Authorization: Bearer <t>` or `X-API-Key: <t>` -- header only. A token
    in a query string leaks into proxy/access logs and the browser's own
    history, so it is never accepted here; the browser instead gets a session
    cookie (see `create_session`) and a media URL gets a scoped signature
    (see `sign_path`)."""
    auth_header = headers.get("authorization") or headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header[len("bearer "):].strip()
        if token:
            return token
    api_key = headers.get("x-api-key") or headers.get("X-API-Key")
    if api_key and api_key.strip():
        return api_key.strip()
    return None


def _hmac_hex(message: str) -> str | None:
    token = api_token()
    if not token:
        return None
    return hmac.new(token.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Browser session cookie
# ---------------------------------------------------------------------------

def create_session(ttl_seconds: int = SESSION_TTL_SECONDS) -> str:
    """`v1.<expiry>.<hmac>` -- never the raw token, so a stolen cookie cannot
    be replayed as a bearer token against `POST /api/auth/login` or any other
    header-based caller, and it expires on its own."""
    expiry = int(time.time()) + ttl_seconds
    payload = f"{SESSION_VERSION}.{expiry}"
    digest = _hmac_hex(payload)
    return f"{payload}.{digest}" if digest else ""


def verify_session(value: str | None) -> bool:
    if not value:
        return False
    parts = value.split(".")
    if len(parts) != 3 or parts[0] != SESSION_VERSION:
        return False
    _, expiry_raw, sig = parts
    try:
        expiry = int(expiry_raw)
    except ValueError:
        return False
    if expiry < int(time.time()):
        return False
    expected = _hmac_hex(f"{SESSION_VERSION}.{expiry_raw}")
    if not expected:
        return False
    return _constant_time_eq(expected, sig)


# ---------------------------------------------------------------------------
# Signed URLs (media links handed to a <video>/<img> tag or a webhook)
# ---------------------------------------------------------------------------

def signed_url_ttl_seconds() -> int:
    raw = (os.environ.get("AUTOEDIT_SIGNED_URL_TTL") or "").strip()
    try:
        return int(raw) if raw else DEFAULT_SIGNED_URL_TTL_SECONDS
    except ValueError:
        return DEFAULT_SIGNED_URL_TTL_SECONDS


def sign_path(path: str, ttl_seconds: int | None = None) -> tuple[int, str] | tuple[None, None]:
    """`(expiry, signature)` for a GET to exactly `path` (no query string --
    the signature covers the path only, so appending it back on is what a
    caller checks against). `(None, None)` when no token is configured (the
    caller decides what an unsigned URL means in that case)."""
    token = api_token()
    if not token:
        return None, None
    ttl = signed_url_ttl_seconds() if ttl_seconds is None else ttl_seconds
    expiry = int(time.time()) + ttl
    digest = _hmac_hex(f"{path}|{expiry}")
    return expiry, digest


def verify_signed_path(path: str, exp: str | None, sig: str | None) -> bool:
    if not exp or not sig:
        return False
    try:
        expiry = int(exp)
    except ValueError:
        return False
    if expiry < int(time.time()):
        return False
    expected = _hmac_hex(f"{path}|{expiry}")
    if not expected:
        return False
    return _constant_time_eq(expected, sig)


# ---------------------------------------------------------------------------
# Combined check, used by the app-level middleware
# ---------------------------------------------------------------------------

def is_authorized(headers: Any, cookies: Any, query_params: Any, method: str, path: str) -> bool:
    if not auth_enabled():
        return True
    if token_matches(extract_token(headers)):
        return True
    if verify_session(cookies.get(SESSION_COOKIE_NAME)):
        return True
    if method.upper() == "GET" and verify_signed_path(
        path, query_params.get("exp"), query_params.get("sig"),
    ):
        return True
    return False


def is_loopback_bind(bind_host: str) -> bool:
    return bind_host.strip() in ("127.0.0.1", "localhost", "::1", "")
