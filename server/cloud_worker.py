"""One-at-a-time background runner for paid cloud-render operations.

Cloud flush/render_now can take 10+ minutes (boot + transfer + Remotion). The
HTTP request must not wait that long — the UI polls `status()` instead.

Only one cloud rental may run at a time: concurrent rentals defeat the batch
amortization argument and make progress reporting meaningless.
"""

from __future__ import annotations

import threading
import time
import traceback
from typing import Any, Callable


class AlreadyRunningError(RuntimeError):
    pass


class CloudWorker:
    """In-process worker. State is process-local — a server restart loses the
    live operation handle, but the rental still has its own deadline + reaper.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = self._idle_state()

    @staticmethod
    def _idle_state() -> dict[str, Any]:
        return {
            "status": "idle",  # idle | running | completed | failed
            "mode": None,
            "job_ids": [],
            "started_at": None,
            "finished_at": None,
            "message": "",
            "error": None,
            "result": None,
            "cost_usd": None,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def start(self, *, mode: str, job_ids: list[str],
              work: Callable[[], dict[str, Any]], message: str = "") -> dict[str, Any]:
        """Start `work()` on a daemon thread. Raises AlreadyRunningError if busy."""
        with self._lock:
            if self._state["status"] == "running":
                raise AlreadyRunningError(
                    f"Cloud đang chạy {self._state.get('mode')} "
                    f"({', '.join(self._state.get('job_ids') or [])}) — chờ xong rồi thử lại")
            self._state = {
                "status": "running",
                "mode": mode,
                "job_ids": list(job_ids),
                "started_at": time.time(),
                "finished_at": None,
                "message": message or f"Đang chạy {mode}…",
                "error": None,
                "result": None,
                "cost_usd": None,
            }

        def runner() -> None:
            try:
                result = work()
                with self._lock:
                    self._state.update({
                        "status": "completed",
                        "finished_at": time.time(),
                        "message": "Xong",
                        "result": result.get("data"),
                        "cost_usd": result.get("cost_usd"),
                        "error": result.get("error"),
                    })
                    if result.get("error") and not result.get("success", True):
                        self._state["status"] = "failed"
            except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
                with self._lock:
                    self._state.update({
                        "status": "failed",
                        "finished_at": time.time(),
                        "message": "Lỗi",
                        "error": f"{exc}\n{traceback.format_exc()[-800:]}",
                    })

        threading.Thread(target=runner, name=f"cloud-{mode}", daemon=True).start()
        return self.status()

    def clear_finished(self) -> dict[str, Any]:
        """Reset a completed/failed banner so the next op starts from a clean slate."""
        with self._lock:
            if self._state["status"] == "running":
                raise AlreadyRunningError("Không xoá được khi cloud đang chạy")
            self._state = self._idle_state()
            return dict(self._state)


cloud_worker = CloudWorker()
