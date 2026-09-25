"""Fire `options.webhook_url` when a queued run reaches a terminal state.

Delivery runs in a background thread so a slow or dead endpoint on the
caller's side can never block the next item in the FIFO queue (see
`queue_worker.py`'s module docstring on why only one job runs at a time).
Every failure is logged as a job warning event and otherwise swallowed: the
job's own state on disk is the source of truth, the webhook is a courtesy.

The URL is vetted at send time (not just when the run was created): a job can
run for a long time, and re-checking here closes both a DNS-rebinding window
and any "the URL pointed somewhere safe an hour ago" drift (see M12 in the
automation-API review).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from typing import Any, Callable

import requests

from server.auth import api_token
from server.run_status import TERMINAL_STATUSES
from server.source_fetch import BlockedURLError, is_http_url, resolve_and_pin

_logger = logging.getLogger(__name__)

_ATTEMPTS = 3
_BACKOFF_SECONDS = (1.0, 4.0, 9.0)
_TIMEOUT_SECONDS = 10.0


def _signature(body: bytes) -> str | None:
    token = api_token()
    if not token:
        return None
    digest = hmac.new(token.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _deliver(url: str, payload: dict[str, Any], job: Any) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    signature = _signature(body)
    if signature:
        headers["X-Autoedit-Signature"] = signature

    last_error = ""
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            with resolve_and_pin(url):
                response = requests.post(url, data=body, headers=headers, timeout=_TIMEOUT_SECONDS)
            if response.ok:
                return
            last_error = f"HTTP {response.status_code}"
        except BlockedURLError as exc:
            # Not transient -- retrying an SSRF-blocked target wastes the
            # remaining attempts and would emit an identical warning 3 times.
            last_error = f"URL bị chặn: {exc}"
            break
        except requests.RequestException as exc:
            last_error = str(exc)
        if attempt < _ATTEMPTS:
            time.sleep(_BACKOFF_SECONDS[min(attempt - 1, len(_BACKOFF_SECONDS) - 1)])
    try:
        job.emit("warning", message=f"Webhook thất bại sau {_ATTEMPTS} lần thử ({url}): {last_error}")
    except Exception:  # noqa: BLE001 -- logging the failure must not itself raise
        _logger.warning("webhook thất bại và không ghi được event: %s", last_error)


def fire_if_terminal(job: Any, build_payload: Callable[[Any], dict[str, Any]]) -> None:
    """No-op unless the job just reached a terminal state and carries a
    `webhook_url` option. `build_payload` is injected (rather than imported)
    so this module never needs to know about `run_status.build_run_status`'s
    signature or import it eagerly.
    """
    try:
        state = job.load()
    except (OSError, ValueError):
        return
    if state.get("status") not in TERMINAL_STATUSES:
        return
    url = str((state.get("options") or {}).get("webhook_url") or "").strip()
    if not url or not is_http_url(url):
        return
    try:
        payload = build_payload(job)
    except Exception as exc:  # noqa: BLE001 -- a bad payload must not crash the worker
        try:
            job.emit("warning", message=f"Webhook bỏ qua: không dựng được payload ({exc})")
        except Exception:
            pass
        return
    threading.Thread(target=_deliver, args=(url, payload, job), daemon=True).start()
