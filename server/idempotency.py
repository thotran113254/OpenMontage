"""`Idempotency-Key` -> job_id, so a retried `POST /api/runs` never doubles a job.

One small JSON file under `projects/` (not a database -- consistent with the
rest of this server, see `job_store.py` / `project_store.py`). The key is
reserved *before* any download or job creation starts (`reserve`), not after
`remember`-ing a finished job: a client that times out during a slow `url`
download and retries with the same key must see "already in progress", not
sail through and create a second job (see H6 in the automation-API review).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, NamedTuple

from lib.talking_head_edit.job_store import write_json_atomic

DONE_TTL_SECONDS = 24 * 60 * 60
# A "pending" reservation older than this is assumed abandoned (the request
# that made it crashed before calling `complete`/`release`) and may be
# reclaimed by a fresh retry, rather than blocking that key forever.
PENDING_STALE_SECONDS = 10 * 60


class IdempotencyConflict(RuntimeError):
    """Same key, different request body."""


class IdempotencyInProgress(RuntimeError):
    """Same key, same body, still being processed by an earlier call."""


class Reservation(NamedTuple):
    reserved: bool           # True: this call owns the key, must complete()/release() it
    job_id: str | None       # set when an earlier call already finished with this key


class IdempotencyStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.path, data)

    def _is_stale(self, entry: dict[str, Any]) -> bool:
        age = time.time() - float(entry.get("created_at") or 0)
        ttl = PENDING_STALE_SECONDS if entry.get("state") == "pending" else DONE_TTL_SECONDS
        return age > ttl

    def reserve(self, key: str, body_hash: str) -> Reservation:
        """Atomically: same key+body already `done` -> return its job_id (no
        reservation made, caller should just replay); same key+body still
        `pending` -> raise `IdempotencyInProgress`; same key, different body ->
        raise `IdempotencyConflict`; otherwise claim the key as `pending` and
        return `reserved=True` -- the caller MUST call `complete()` or
        `release()` on it, exactly once.
        """
        with self._lock:
            data = self._read()
            entry = data.get(key)
            if entry and not self._is_stale(entry):
                if entry.get("body_hash") != body_hash:
                    raise IdempotencyConflict(key)
                if entry.get("state") == "pending":
                    raise IdempotencyInProgress(key)
                return Reservation(reserved=False, job_id=entry.get("job_id"))
            data[key] = {"state": "pending", "body_hash": body_hash, "created_at": time.time()}
            self._write(data)
            return Reservation(reserved=True, job_id=None)

    def complete(self, key: str, job_id: str) -> None:
        with self._lock:
            data = self._read()
            data[key] = {"state": "done", "body_hash": (data.get(key) or {}).get("body_hash", ""),
                        "job_id": job_id, "created_at": time.time()}
            self._write(data)

    def release(self, key: str) -> None:
        """Drop a reservation that will never be completed (the request that
        made it failed) -- a later retry with the same key must not see
        `IdempotencyInProgress` forever."""
        with self._lock:
            data = self._read()
            data.pop(key, None)
            self._write(data)
