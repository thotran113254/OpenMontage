"""Start the talking-head autoedit job server and web UI together.

`docs/talking-head-autoedit.md` documents `make autoedit-server` +
`make autoedit-ui` as two separate targets for two terminals — and `make`
itself isn't installed on every dev machine (this one included). This script
launches both as child processes so one command gets you from a raw job to
the UI at http://localhost:5173.

Each child gets its own process group/session (same isolation
`server/queue_worker.py` uses for job runs), so Ctrl+C here does not reach
them directly — instead the `finally` block below kills each process TREE
explicitly (`taskkill /T` on Windows, `killpg` elsewhere). Vite's dev server
is a shell wrapper around node; a bare kill() on the wrapper would leave node
running and the port held.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SERVER_URL = "http://127.0.0.1:8756"
UI_URL = "http://localhost:5173"

_WINDOWS = os.name == "nt"
_ISOLATION = (
    {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    if _WINDOWS else {"start_new_session": True}
)


def _kill_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if _WINDOWS:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            capture_output=True, timeout=20)
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        process.kill()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _stream(process: subprocess.Popen, tag: str) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        print(f"[{tag}] {line}", end="")


def main() -> int:
    # Vite prints unicode (e.g. the "➜" arrow); this process's own stdout
    # defaults to the console codepage (cp1252 on plain Windows terminals),
    # which raises UnicodeEncodeError on those bytes. Force utf-8 here too,
    # not just in the children's env, since we're the one calling print().
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

    server = subprocess.Popen(
        [sys.executable, "-m", "server.app"],
        cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", **_ISOLATION,
    )
    ui = subprocess.Popen(
        ["npx", "vite", "--config", "ui/vite.config.ts"],
        cwd=str(REPO_ROOT / "remotion-composer"), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        shell=_WINDOWS, **_ISOLATION,
    )

    print(f"==> Job server: {SERVER_URL}")
    print(f"==> Web UI:     {UI_URL}  (calls the server above)")
    print("    Ctrl+C stops both.\n")

    for proc, tag in ((server, "server"), (ui, "ui")):
        threading.Thread(target=_stream, args=(proc, tag), daemon=True).start()

    try:
        while True:
            for proc, tag in ((server, "server"), (ui, "ui")):
                code = proc.poll()
                if code is not None:
                    print(f"\n[{tag}] exited early with code {code} — stopping the other one too.")
                    return 1
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    finally:
        print("\n==> Stopping server + UI...")
        _kill_tree(server)
        _kill_tree(ui)


if __name__ == "__main__":
    raise SystemExit(main())
