"""Rental ledger: `active.json` (current state) + `rentals.jsonl` (append-only
event history), and the 4-layer reaper sweep that guarantees no rental is
ever leaked.

Mirrors the orphan-reconciliation shape in `server/queue_worker.py`
(`_reconcile_orphans`): durable state written to disk, not kept only in
memory, so a NEW process (this one restarted, or a human running
`python -m lib.cloud_render.reap` from a cron job) can recover it.

Module-level import is deliberately `vastai`-free (only `reap()` needs the
account, and it imports `vast_client` lazily inside the function body) so
that reading/writing the ledger never requires the SDK to be installed.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.cloud_render.config import REPO_ROOT

STATE_DIR = REPO_ROOT / "projects" / "cloud-render"
ACTIVE_PATH = STATE_DIR / "active.json"
RENTALS_LOG_PATH = STATE_DIR / "rentals.jsonl"

# Reserved prefix -- the reaper only ever touches instances labelled with
# this, so it can never destroy a box a human rented manually on the same
# account. Kept here (not just in vast_client) because reap()'s decision
# table is pure mechanics over this prefix.
LABEL_PREFIX = "openmontage"

# A `pending` record with no matching remote instance after this many seconds
# is stale -- `rent()` failed before `create_instance` ever returned (or the
# process died first). Generous on purpose: a false-positive close on a
# record that is genuinely mid-flight would just make a legitimate rental
# look like an orphan to a *later* reap, which still only destroys real
# labelled instances -- never data loss, only a log line.
PENDING_STALE_SECONDS = 900


class LedgerError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso_epoch(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        return int(datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
                    .replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Atomic read/write of active.json
# ---------------------------------------------------------------------------

def _read_active() -> dict[str, Any]:
    if not ACTIVE_PATH.exists():
        return {"rentals": []}
    try:
        data = json.loads(ACTIVE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"rentals": []}
    if not isinstance(data, dict) or not isinstance(data.get("rentals"), list):
        return {"rentals": []}
    return data


def _write_active(data: dict[str, Any]) -> None:
    """Write temp + `os.replace` -- a crash between the two leaves the real
    `active.json` untouched, so the instance id of a live rental is never
    lost mid-write."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = ACTIVE_PATH.with_name(ACTIVE_PATH.name + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, ACTIVE_PATH)


def _upsert_active(record: dict[str, Any]) -> None:
    data = _read_active()
    data["rentals"] = [r for r in data["rentals"] if r.get("intent_id") != record["intent_id"]]
    data["rentals"].append(record)
    _write_active(data)


def _remove_active(intent_id: str) -> dict[str, Any] | None:
    data = _read_active()
    removed = None
    remaining = []
    for record in data["rentals"]:
        if record.get("intent_id") == intent_id and removed is None:
            removed = record
        else:
            remaining.append(record)
    data["rentals"] = remaining
    _write_active(data)
    return removed


def _append_event(event: dict[str, Any]) -> None:
    """Append-only event log. One `write()` call per line: at worst a crash
    mid-write corrupts only the trailing, not-yet-committed line, never a
    previously written one."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n"
    with open(RENTALS_LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(line)


# ---------------------------------------------------------------------------
# Public ledger API
# ---------------------------------------------------------------------------

def active() -> list[dict[str, Any]]:
    """Every rental currently tracked locally (any status)."""
    return _read_active()["rentals"]


def append(event: dict[str, Any]) -> None:
    """Low-level: append one raw event line without touching `active.json`.
    Used by `reap()` for orphans that never had a local record to begin with."""
    _append_event(event)


def open_pending(intent_id: str, *, label: str, dph_usd: float | None, mode: str,
                  deadline_epoch: int | None = None, max_total_usd: float | None = None,
                  purpose: str = "render_now",
                  job_ids: list[str] | None = None) -> dict[str, Any]:
    """Write the `pending` ledger record *before* the `create_instance` API
    call (see `vast_client.rent`). The failure this guards against is
    "create succeeded, response lost" -- a local record exists even if the
    instance id is not yet known."""
    record = {
        "intent_id": intent_id,
        "instance_id": None,
        "label": label,
        "created_at": _now_iso(),
        "deadline_epoch": deadline_epoch,
        "dph_usd": dph_usd,
        "max_total_usd": max_total_usd,
        "mode": mode,
        "purpose": purpose,
        "job_ids": list(job_ids or []),
        "status": "pending",
    }
    _upsert_active(record)
    _append_event({**record, "event": "pending"})
    return record


def promote(intent_id: str, *, instance_id: int) -> dict[str, Any]:
    """Promote a `pending` record to `active` once `create_instance` returns
    a real instance id."""
    data = _read_active()
    for record in data["rentals"]:
        if record.get("intent_id") == intent_id:
            record["instance_id"] = instance_id
            record["status"] = "active"
            _write_active(data)
            _append_event({**record, "event": "active"})
            return record
    raise LedgerError(f"không có ledger record pending cho intent_id={intent_id!r}")


def close(intent_id: str, *, actual_usd: float | None = None,
          duration_seconds: float | None = None, status: str = "closed") -> dict[str, Any] | None:
    """Remove a rental from `active.json` and append the terminal event
    (`closed`, `reaped`, or `failed`). Returns the closed record, or `None` if
    no local record existed for `intent_id`."""
    removed = _remove_active(intent_id)
    if removed is None:
        return None
    closed_record = {**removed, "status": status,
                      "actual_usd": actual_usd, "duration_seconds": duration_seconds}
    _append_event({**closed_record, "event": status})
    return closed_record


# ---------------------------------------------------------------------------
# Reaper -- pure mechanics, no policy (see phase doc's decision table)
# ---------------------------------------------------------------------------

@dataclass
class ReapReport:
    destroyed: list[dict[str, Any]] = field(default_factory=list)
    closed: list[dict[str, Any]] = field(default_factory=list)
    left_alone: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.destroyed) + len(self.closed) + len(self.left_alone)


def reap(dry_run: bool = False, *, adopt_unknown: bool = False,
         api_key: str | None = None, now_epoch: int | None = None) -> ReapReport:
    """4-layer sweep, per the decision table:

    | local active.json | labelled instance on account | past deadline | action |
    |---|---|---|---|
    | yes | yes | no  | leave alone |
    | yes | yes | yes | destroy, ledger `reaped`, warn |
    | yes | no  | -   | ledger `closed` (already gone) |
    | no  | yes | no  | destroy unless `adopt_unknown` (orphan by definition) |
    | no  | yes | yes | destroy, ledger `reaped` |

    Only ever touches instances whose label starts with
    `f"{LABEL_PREFIX}-"` -- a human-rented instance on the same account is
    never in scope.
    """
    from lib.cloud_render import vast_client  # local import breaks the ledger<->vast_client cycle

    now = int(now_epoch) if now_epoch is not None else int(time.time())
    report = ReapReport()

    # Include `pending` records alongside `active` ones. A record can be
    # `pending` here for two reasons: `create_instance` is still genuinely
    # in flight (rare, single caller), or it failed/lost its response before
    # `promote()` ever ran -- the exact case `open_pending`'s docstring
    # guards against. Either way, if the account shows a matching labelled
    # instance, the sweep below must treat it exactly like `active` (real
    # money is accruing regardless of local promote state). If the account
    # shows *no* matching instance and the record is older than
    # `PENDING_STALE_SECONDS`, it never became real and is safe to close --
    # otherwise a failed `rent()` leaves a zombie row in `active.json`
    # forever (see phase-02 review note).
    local_by_intent = {r["intent_id"]: r for r in active()
                       if r.get("status") in ("active", "pending")}

    raw_instances = vast_client.list_labelled_instances(api_key=api_key)
    remote_by_intent: dict[str, dict[str, Any]] = {}
    for raw in raw_instances:
        parsed = vast_client.parse_label(raw.get("label") or "")
        if parsed is None:
            continue
        intent_id, deadline_epoch = parsed
        remote_by_intent[intent_id] = {"instance_id": raw.get("id"), "deadline_epoch": deadline_epoch}

    # Rows 1-3: rentals this process still remembers locally.
    for intent_id, record in local_by_intent.items():
        remote = remote_by_intent.pop(intent_id, None)
        if remote is None:
            if record.get("status") == "pending":
                created_epoch = _parse_iso_epoch(record.get("created_at"))
                is_stale = created_epoch is None or (now - created_epoch) > PENDING_STALE_SECONDS
                if not is_stale:
                    report.left_alone.append(record)
                    continue
                if not dry_run:
                    close(intent_id, status="closed")
                report.closed.append(record)
                report.warnings.append(
                    f"closed stale pending rental intent_id={intent_id} "
                    f"(never promoted, no matching remote instance, "
                    f"created_at={record.get('created_at')})")
                continue
            if not dry_run:
                close(intent_id, status="closed")
            report.closed.append(record)
            continue
        if record.get("status") == "pending" and not dry_run:
            report.warnings.append(
                f"pending rental intent_id={intent_id} has a live remote "
                f"instance_id={remote['instance_id']} -- promote() likely "
                f"failed to run; treating as active for this sweep")
        if now > remote["deadline_epoch"]:
            if not dry_run:
                vast_client.destroy(remote["instance_id"], api_key=api_key)
                close(intent_id, status="reaped")
            report.destroyed.append(record)
            report.warnings.append(
                f"reaped past-deadline rental intent_id={intent_id} "
                f"instance_id={remote['instance_id']}")
        else:
            report.left_alone.append(record)

    # Rows 4-5: openmontage-labelled instances with no matching local record.
    for intent_id, remote in remote_by_intent.items():
        orphan = {"intent_id": intent_id, "instance_id": remote["instance_id"]}
        past_deadline = now > remote["deadline_epoch"]
        if not past_deadline and adopt_unknown:
            report.left_alone.append(orphan)
            continue
        if not dry_run:
            vast_client.destroy(remote["instance_id"], api_key=api_key)
            append({
                "intent_id": intent_id, "instance_id": remote["instance_id"],
                "status": "reaped", "event": "reaped", "created_at": _now_iso(),
                "note": "past deadline, no local record" if past_deadline
                        else "no local record, adopt_unknown not set",
            })
        report.destroyed.append(orphan)
        report.warnings.append(
            ("reaped orphan past-deadline instance " if past_deadline
             else "destroyed unknown-label orphan instance (adopt_unknown not set) ")
            + f"intent_id={intent_id} instance_id={remote['instance_id']}")

    return report
