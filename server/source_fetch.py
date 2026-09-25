"""Fetch a `url` source (or `webhook_url`) without becoming an SSRF proxy.

An automation caller can name any URL, and this server will connect to it —
so "any URL" would let an attacker probe this VPS's private network (metadata
services, other containers, localhost-only admin ports) through it. Every hop
(the initial URL AND every redirect target, plus a webhook re-checked at each
delivery attempt) is resolved and vetted, and the connection this process
actually makes is pinned to the vetted IP: resolving once for the check and
letting `requests` resolve again independently would leave a DNS-rebinding
window between the two lookups.
"""

from __future__ import annotations

import contextlib
import ipaddress
import socket
import threading
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import ParseResult, urljoin, urlparse

import requests

_MAX_REDIRECTS = 5
_TIMEOUT_SECONDS = 30.0
_CHUNK_BYTES = 1 << 20
_USER_AGENT = "OpenMontage-Automation/1.0"

# RFC 6598 CGNAT space (100.64.0.0/10) is not part of Python's `is_private` on
# the versions this project targets, but Tailscale and similar overlay
# networks route through it -- an attacker on one would otherwise reach it.
_EXTRA_BLOCKED_NETWORKS = (ipaddress.ip_network("100.64.0.0/10"),)


class SourceFetchError(RuntimeError):
    """Base for user-facing `url` source / webhook download failures."""


class BlockedURLError(SourceFetchError):
    """The URL (or a redirect target) is not allowed: bad scheme, no host, or
    resolves to a private/loopback/link-local/reserved/CGNAT address."""


class SourceUnreachableError(SourceFetchError):
    """A legitimate network failure: DNS, timeout, non-2xx, over the size cap."""


def is_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.hostname)


def _is_disallowed_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    if (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
        return True
    return any(ip in network for network in _EXTRA_BLOCKED_NETWORKS)


def _validate_scheme(parsed: ParseResult) -> str:
    if parsed.scheme not in ("http", "https"):
        raise BlockedURLError(f"Chỉ hỗ trợ URL http/https, nhận: {parsed.scheme or '?'}")
    if not parsed.hostname:
        raise BlockedURLError("URL thiếu hostname")
    return parsed.hostname


def _resolve_and_check(hostname: str) -> str:
    """Resolve `hostname`, reject if ANY answer is disallowed, and return one
    vetted IP -- the caller pins the actual connection to it (see
    `resolve_and_pin`)."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, OSError) as exc:
        raise SourceUnreachableError(f"Không phân giải được host: {hostname}") from exc
    if not infos:
        raise SourceUnreachableError(f"Không phân giải được host: {hostname}")
    ips: list[str] = []
    for info in infos:
        ip_str = info[4][0]
        if _is_disallowed_ip(ip_str):
            raise BlockedURLError(
                f"URL trỏ tới địa chỉ nội bộ/không cho phép ({ip_str}) -- bị chặn để tránh SSRF")
        ips.append(ip_str)
    return ips[0]


def validate_public_url(url: str) -> None:
    """Scheme + SSRF host check, no connection made. For input that will only
    be *dialled later* (a `webhook_url` stored at run-creation time) -- use
    `resolve_and_pin` around the actual request instead."""
    parsed = urlparse(url)
    hostname = _validate_scheme(parsed)
    _resolve_and_check(hostname)


# ---------------------------------------------------------------------------
# DNS pinning: closes the gap between "we checked this resolves safely" and
# "this is what requests/urllib3 actually connects to" a moment later.
# ---------------------------------------------------------------------------

_real_getaddrinfo = socket.getaddrinfo
_pin_local = threading.local()
_patch_lock = threading.Lock()


def _pinned_getaddrinfo(host: str, *args: Any, **kwargs: Any) -> Any:
    pins = getattr(_pin_local, "pins", None)
    if pins and host in pins:
        host = pins[host]
    return _real_getaddrinfo(host, *args, **kwargs)


def _install_dns_pin_once() -> None:
    with _patch_lock:
        if socket.getaddrinfo is not _pinned_getaddrinfo:
            socket.getaddrinfo = _pinned_getaddrinfo


@contextlib.contextmanager
def _pinned_dns(hostname: str, ip: str) -> Iterator[None]:
    """Force `socket.getaddrinfo(hostname, ...)` to answer `ip` for the
    current thread only, for the duration of the block. Thread-local so two
    concurrent downloads (the API server can run request handlers in a thread
    pool) never see each other's pin.
    """
    _install_dns_pin_once()
    pins: dict[str, str] = getattr(_pin_local, "pins", None) or {}
    _pin_local.pins = pins
    previous = pins.get(hostname)
    pins[hostname] = ip
    try:
        yield
    finally:
        if previous is None:
            pins.pop(hostname, None)
        else:
            pins[hostname] = previous


@contextlib.contextmanager
def resolve_and_pin(url: str) -> Iterator[None]:
    """Validate `url` and pin DNS resolution to the vetted IP for the
    duration of the block. Wrap exactly one outbound call
    (`requests.get`/`.post`) in this -- the pin only needs to hold until that
    call's connection is established."""
    parsed = urlparse(url)
    hostname = _validate_scheme(parsed)
    ip = _resolve_and_check(hostname)
    with _pinned_dns(hostname, ip):
        yield


def download_url_to(url: str, dest: Path, *, max_mb: int,
                     timeout: float = _TIMEOUT_SECONDS) -> int:
    """Stream `url` to `dest`, following redirects manually so every hop is
    re-checked and re-pinned. Returns the number of bytes written.

    Raises `BlockedURLError` for a rejected host/scheme and
    `SourceUnreachableError` for any other failure (network, non-2xx, size
    cap). `dest` is removed on any failure -- a partial file must never look
    like a finished download to a later stage.
    """
    max_bytes = max_mb * 1024 * 1024
    current_url = url
    response: Any = None
    for _ in range(_MAX_REDIRECTS + 1):
        try:
            with resolve_and_pin(current_url):
                response = requests.get(
                    current_url, stream=True, timeout=timeout, allow_redirects=False,
                    headers={"User-Agent": _USER_AGENT},
                )
        except SourceFetchError:
            raise
        except requests.RequestException as exc:
            raise SourceUnreachableError(f"Tải URL thất bại: {exc}") from exc

        if response.is_redirect or response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise SourceUnreachableError("Redirect thiếu Location header")
            current_url = urljoin(current_url, location)
            continue
        break
    else:
        raise SourceUnreachableError("Quá nhiều lượt redirect")

    if response.status_code != 200:
        response.close()
        raise SourceUnreachableError(f"Tải URL thất bại: HTTP {response.status_code}")

    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                response.close()
                raise SourceUnreachableError(
                    f"File vượt giới hạn {max_mb} MB (Content-Length={content_length})")
        except ValueError:
            pass

    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with open(dest, "wb") as out:
            for chunk in response.iter_content(chunk_size=_CHUNK_BYTES):
                if not chunk:
                    continue
                written += len(chunk)
                if written > max_bytes:
                    raise SourceUnreachableError(f"File vượt giới hạn {max_mb} MB khi đang tải")
                out.write(chunk)
    except SourceUnreachableError:
        dest.unlink(missing_ok=True)
        raise
    except OSError as exc:
        dest.unlink(missing_ok=True)
        raise SourceUnreachableError(f"Ghi file thất bại: {exc}") from exc
    finally:
        response.close()
    return written
