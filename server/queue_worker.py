"""FIFO job queue.

One job at a time on purpose: ffmpeg and Remotion each want every core, and
two concurrent renders on one machine finish later than the same two in
sequence while making progress reporting meaningless.

Each run is a subprocess, so cancelling is a kill rather than a cooperative
flag the pipeline would have to check between ffmpeg calls.
"""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.talking_head_edit.cpu_budget import low_priority_prefix
from lib.talking_head_edit.job_store import REPO_ROOT, JobStore, find_job, list_all_jobs
from server import webhooks as webhooks_mod
from server.auth import auth_enabled
from server.run_status import build_run_status, resolve_base_url


def _webhook_payload(job: Any) -> dict[str, Any]:
    """Same shape `GET /api/runs/{id}` returns -- no `Request` here, so the
    base URL falls back to `AUTOEDIT_PUBLIC_HOST`/bind address."""
    return build_run_status(job, base_url=resolve_base_url(None), auth_on=auth_enabled())


class AlreadyRunningError(RuntimeError):
    """Raised by `submit` when the job's previous run is still alive on disk.

    Only reachable after a server restart: the render subprocess a dead
    server instance started keeps running (this queue is not its parent, so
    it survives), and nothing here would notice — a second submit would spawn
    a second process writing the same `src.mp4` / `final.mp4` at the same time.
    """


def _pid_alive(pid: int) -> bool:
    """True if `pid` is running, regardless of whether it is our child.

    Killing or checking an arbitrary PID (not just one this process spawned)
    is fine at the OS level — only `Popen.wait()` requires a parent/child
    relationship, and this function deliberately avoids that call.
    """
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True, timeout=10).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # A killed child stays a zombie until its parent reaps it; it runs nothing.
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return True
    return stat.rsplit(")", 1)[-1].split()[0] != "Z"


def _kill_by_pid(pid: int) -> str:
    """OS-level kill of a process tree by PID. Returns what was done.

    A bare `kill()` on the PID is not enough: the run spawns ffmpeg and node,
    and those are children. Windows has no process groups in the POSIX sense,
    so `taskkill /T` walks the tree; elsewhere the run starts in its own
    session (see `_execute`'s `isolation`), so the whole group can be signalled.
    """
    if not _pid_alive(pid):
        return "tiến trình đã tự kết thúc"
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=20)
            return "taskkill /T (kèm process con)"
        os.killpg(os.getpgid(pid), signal.SIGKILL)
        return "killpg (kèm process con)"
    except (OSError, subprocess.SubprocessError):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        return "kill tiến trình chính (không kill được cây process)"


def kill_tree(process: subprocess.Popen[str]) -> str:
    """Kill a run this queue started AND everything it spawned.

    Reaps the process afterward — the worker's `_execute` reads its stdout in
    a loop, and that loop only ends once the process actually exits.
    """
    if process.poll() is not None:
        return "tiến trình đã tự kết thúc"
    note = _kill_by_pid(process.pid)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
    return note


@dataclass
class QueuedRun:
    job_id: str
    stages: list[str] | None = None
    use_cache: bool = True
    extra_args: list[str] = field(default_factory=list)


class JobQueue:
    def __init__(self, store: JobStore | None = None):
        self.store = store or JobStore()
        # The queue serves both layouts, so a build inside a project queues the
        # same way a legacy job does. Overridable so a test can point it at a
        # temporary root.
        self.projects_root: Path | None = None
        self._queue: queue.Queue[QueuedRun] = queue.Queue()
        self._current: tuple[str, subprocess.Popen[str]] | None = None
        # (job_id, pid) for a run this INSTANCE never started — inherited from
        # a server process that died mid-render. Set once at startup, since
        # only one job runs at a time by design; self-clears in `status()`
        # and `submit()` the moment the pid is observed dead.
        self._adopted: tuple[str, int] | None = None
        self._lock = threading.Lock()
        self._reconcile_orphans()
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()

    def _find(self, job_id: str):
        return find_job(job_id, legacy_root=self.store.root,
                        projects_root=self.projects_root)

    def _reconcile_orphans(self) -> None:
        """Find a still-running subprocess a previous server instance left behind.

        `_execute` records `worker_pid` on the job before it starts waiting on
        the process, so a state file with that field set and a live PID means
        exactly one thing: this queue restarted while a render was in flight.
        Without this, `status()` reports nothing running and `cancel()` cannot
        find the process — the UI's Huỷ button silently does nothing while
        ffmpeg keeps a core pinned.
        """
        for state in list_all_jobs(self.store.root, self.projects_root):
            pid = state.get("worker_pid")
            if pid and _pid_alive(pid):
                self._adopted = (state["job_id"], pid)
                return

    # ---- public API ------------------------------------------------------
    def submit(self, run: QueuedRun) -> int:
        with self._lock:
            adopted = self._adopted
        if adopted and adopted[0] == run.job_id:
            if _pid_alive(adopted[1]):
                raise AlreadyRunningError(
                    f"Job {run.job_id} vẫn đang chạy từ trước khi server khởi động lại "
                    f"(pid {adopted[1]}) — chờ nó xong hoặc huỷ trước khi chạy lại, "
                    "kẻo hai tiến trình cùng ghi vào cùng một file."
                )
            with self._lock:
                self._adopted = None
        self._queue.put(run)
        position = self._queue.qsize()
        job = self._find(run.job_id)
        job.update(status="queued")
        job.emit("queued", message=f"Đã xếp hàng (vị trí {position})")
        return position

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            if self._current and self._current[0] == job_id:
                process = self._current[1]
                killed = kill_tree(process)
                job = self._find(job_id)
                job.mark_cancelled()
                job.emit("cancelled",
                         message=f"Đã huỷ theo yêu cầu ({killed})")
                # `_execute`'s own `process.wait()` unblocks once the kill above
                # lands and its tail fires the webhook — firing here too would
                # double-deliver it.
                return True
            if self._adopted and self._adopted[0] == job_id:
                killed = _kill_by_pid(self._adopted[1])
                self._adopted = None
                job = self._find(job_id)
                job.mark_cancelled()
                job.emit("cancelled",
                         message=f"Đã huỷ tiến trình còn sót từ lần chạy trước ({killed})")
                # No `_execute` thread is waiting on this orphaned pid in THIS
                # instance, so nothing else will fire the webhook for it.
                webhooks_mod.fire_if_terminal(job, _webhook_payload)
                return True
        # not running yet: drop it from the pending queue
        pending: list[QueuedRun] = []
        removed = False
        while not self._queue.empty():
            item = self._queue.get_nowait()
            if item.job_id == job_id and not removed:
                removed = True
                continue
            pending.append(item)
        for item in pending:
            self._queue.put(item)
        if removed:
            job = self._find(job_id)
            job.mark_cancelled()
            job.emit("cancelled", message="Đã bỏ khỏi hàng đợi")
            webhooks_mod.fire_if_terminal(job, _webhook_payload)
        return removed

    def is_busy(self, job_id: str) -> bool:
        """True if `job_id` is currently running, adopted from a previous
        server instance, or waiting in the pending queue.

        Used to refuse a concurrent chat/revise/cuts request that would
        `job.load()` -> modify -> `job.save()` against the same file the
        queued subprocess is writing (see M5 in the automation-API review).
        """
        with self._lock:
            if self._current and self._current[0] == job_id:
                return True
            if self._adopted and self._adopted[0] == job_id:
                return True
        # Reading the queue's internal deque directly: `queue.Queue` has no
        # peek, and this is inherently a best-effort snapshot anyway -- the
        # job could start running the instant after this check returns.
        return any(item.job_id == job_id for item in list(self._queue.queue))

    def status(self) -> dict[str, Any]:
        with self._lock:
            if self._current:
                running = self._current[0]
            elif self._adopted and _pid_alive(self._adopted[1]):
                running = self._adopted[0]
            else:
                # Either nothing was ever adopted, or it finished on its own
                # since the last check — either way, stop reporting it.
                self._adopted = None
                running = None
            return {"running": running, "pending": self._queue.qsize()}

    # ---- worker ----------------------------------------------------------
    def _loop(self) -> None:
        while True:
            run = self._queue.get()
            try:
                self._execute(run)
            except Exception as exc:  # noqa: BLE001 — a bad run must not kill the worker
                try:
                    job = self._find(run.job_id)
                    job.update(status="failed")
                    job.emit("stage_failed", message=f"Worker lỗi: {exc}")
                    webhooks_mod.fire_if_terminal(job, _webhook_payload)
                except Exception:
                    pass
            finally:
                self._queue.task_done()

    def _build_command(self, run: QueuedRun) -> list[str]:
        """The CLI invocation for one run, low-priority prefix included.

        Split out from `_execute` so a test can swap in a trivial `python -c`
        command and still exercise the real `low_priority_prefix()` +
        subprocess-group + `kill_tree` path end to end, without needing an
        actual pipeline run.
        """
        command = [sys.executable, "-m", "lib.talking_head_edit.cli", "--job", run.job_id]
        if run.stages:
            # explicit list, not --stage-from: `revise` is an out-of-band stage
            # with no position in the linear probe→verify order
            command += ["--stages", ",".join(run.stages)]
        if not run.use_cache:
            command.append("--no-cache")
        command += run.extra_args
        # `nice`/`ionice` exec in place (same pid), so `kill_tree`'s
        # `killpg(getpgid(process.pid), ...)` still reaches the whole tree —
        # this VPS's cores are shared with other projects (see cpu_budget.py).
        return [*low_priority_prefix(), *command]

    def _execute(self, run: QueuedRun) -> None:
        command = self._build_command(run)
        environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        # Own process group / session, so cancel can signal the whole tree
        # (see kill_tree) rather than just this process.
        isolation: dict[str, Any] = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt" else {"start_new_session": True})
        process = subprocess.Popen(
            command, cwd=str(REPO_ROOT), env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", **isolation,
        )
        with self._lock:
            self._current = (run.job_id, process)

        job = self._find(run.job_id)
        # Written to disk (not just kept in memory) so a NEW server instance
        # can find this pid if this one dies before the run finishes — that
        # is the whole point of `_reconcile_orphans`.
        job.update(worker_pid=process.pid)
        try:
            log_path = job.dir / "logs" / "runner.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            assert process.stdout is not None
            with open(log_path, "a", encoding="utf-8") as log:
                for line in process.stdout:
                    log.write(line)
            code = process.wait()
        finally:
            with self._lock:
                self._current = None
            job.update(worker_pid=None)

        state = job.load()
        if code != 0 and state.get("status") not in ("failed", "cancelled"):
            job.update(status="failed")
            job.emit("stage_failed", message=f"Tiến trình kết thúc với mã {code}")
        # Covers normal completion/failure AND the "kill while running" cancel
        # path above — either way this is the one place that reliably observes
        # the run's actual end.
        webhooks_mod.fire_if_terminal(job, _webhook_payload)
