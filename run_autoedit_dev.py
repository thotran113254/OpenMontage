"""Start the talking-head autoedit job server and web UI together.

`docs/talking-head-autoedit.md` documents `make autoedit-server` +
`make autoedit-ui` as two separate targets for two terminals — and `make`
itself isn't installed on every dev machine (this one included). This script
launches both as child processes so one command gets you from a raw job to
the UI (AUTOEDIT_UI_PORT, default 5617).

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

from lib.env_loader import load_env

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_API_PORT = "8861"
DEFAULT_UI_PORT = "5617"

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

    load_env()
    api_port = os.environ.get("AUTOEDIT_PORT", DEFAULT_API_PORT)
    ui_port = os.environ.get("AUTOEDIT_UI_PORT", DEFAULT_UI_PORT)
    bind = os.environ.get("AUTOEDIT_BIND", "127.0.0.1")
    public_host = os.environ.get("AUTOEDIT_PUBLIC_HOST", "").strip()
    api_url = os.environ.get("AUTOEDIT_API", f"http://127.0.0.1:{api_port}")
    local_ui = f"http://127.0.0.1:{ui_port}"
    public_ui = f"http://{public_host}:{ui_port}" if public_host else local_ui
    public_api = f"http://{public_host}:{api_port}" if public_host else api_url

    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "AUTOEDIT_BIND": bind,
        "AUTOEDIT_PORT": api_port,
        "AUTOEDIT_UI_PORT": ui_port,
        "AUTOEDIT_API": api_url,
        "AUTOEDIT_PUBLIC_HOST": public_host,
    }

    server = subprocess.Popen(
        [sys.executable, "-m", "server.app"],
        cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", **_ISOLATION,
    )
    ui = subprocess.Popen(
        ["npm", "run", "ui"],
        cwd=str(REPO_ROOT / "remotion-composer"), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        shell=_WINDOWS, **_ISOLATION,
    )

    print(f"==> Job server: {api_url}  (bind {bind}:{api_port})")
    print(f"==> Web UI:     {local_ui}  (proxies /api to the server above)")
    if public_host:
        print(f"==> Public UI:  {public_ui}")
        print(f"==> Public API: {public_api}")
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
