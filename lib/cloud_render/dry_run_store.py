"""dry_run_ref persistence: the mechanical half of "no unilateral substitution".

`VastCloudRender.dry_run()` persists the offer_ids a human actually saw in an
announce block, plus the ceilings that were in force at the time, keyed by a
short-lived token (see phase doc step 4: "an offer the user never saw cannot
be rented"). `execute()` then refuses any `offer_id` that is not in the
referenced list, or whose ref has expired -- both checks are pure local-file
reads, so a refusal here costs zero SDK calls.

Same atomic read/write shape as `ledger.py`/`queue.py` (temp + `os.replace`)
so a crash mid-write never corrupts a previously committed ref.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

from lib.cloud_render.config import REPO_ROOT

STATE_DIR = REPO_ROOT / "projects" / "cloud-render"
REFS_PATH = STATE_DIR / "dry_runs.json"

# "An offer the user never saw cannot be rented" -- but a slow human
# conversation between announce and approval must not force a re-search;
# 10 minutes balances both (see phase doc risk table).
DEFAULT_TTL_SECONDS = 600.0


class DryRunRefError(ValueError):
    """The offer_id/dry_run_ref pair does not satisfy "the user saw this"."""


def _read() -> dict[str, Any]:
    if not REFS_PATH.exists():
        return {}
    try:
        data = json.loads(REFS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = REFS_PATH.with_name(REFS_PATH.name + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, REFS_PATH)


def create(offer_ids: list[int], ceilings: dict[str, Any], *,
           ttl_seconds: float = DEFAULT_TTL_SECONDS, now: float | None = None) -> str:
    """Persist the offer_ids a `dry_run()` call actually showed the user.

    Returns the token (`dry_run_ref`) the announce block hands back for
    `execute()` to validate against. Offers are already ceiling-filtered by
    the caller before this runs, so membership in `offer_ids` is itself
    evidence an offer passed the ceiling check in force at announce time.
    """
    ref = f"dr_{uuid.uuid4().hex[:6]}"
    now = now if now is not None else time.time()
    data = _read()
    data[ref] = {
        "offer_ids": list(offer_ids),
        "ceilings": dict(ceilings),
        "expires_at": now + ttl_seconds,
    }
    _write(data)
    return ref


def resolve(dry_run_ref: str | None, offer_id: Any, *, now: float | None = None) -> dict[str, Any]:
    """Raise `DryRunRefError` unless `offer_id` was in the offer list a prior
    `dry_run()` call returned, and that ref has not expired.

    Returns the ref's stored record on success. Reads a local JSON file only
    -- never the network/SDK -- so a refusal here costs zero SDK calls.
    """
    if not dry_run_ref:
        raise DryRunRefError("dry_run_ref là bắt buộc -- gọi dry_run() trước và dùng token của nó")

    data = _read()
    record = data.get(dry_run_ref)
    if record is None:
        raise DryRunRefError(
            f"dry_run_ref {dry_run_ref!r} không tồn tại -- gọi dry_run() để lấy ref hợp lệ")

    now = now if now is not None else time.time()
    if now > record.get("expires_at", 0):
        raise DryRunRefError(
            f"dry_run_ref {dry_run_ref!r} đã hết hạn -- chạy lại dry_run() để thấy offer hiện tại")

    offer_ids = record.get("offer_ids", [])
    if offer_id not in offer_ids and str(offer_id) not in {str(o) for o in offer_ids}:
        raise DryRunRefError(
            f"offer_id {offer_id!r} không nằm trong dry_run_ref {dry_run_ref!r} -- "
            "offer này người dùng chưa từng thấy trong dry_run(), không được thuê")

    return record
