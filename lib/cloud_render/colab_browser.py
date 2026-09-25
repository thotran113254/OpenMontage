"""Drive Colab account 1 through agent-browser attached to Chrome Stable over CDP.

Only account 1 is ever touched: session `colab-cdp`, CDP `127.0.0.1:9222`,
profile `~/.chrome-colab-profile` (see FineTune-Model/docs/colab-login-runbook.md).
Account 2 (`colab2`) belongs to other workloads and is never attached to.

Every step here was first done by hand and is written the way it worked:
elements are found by role and accessible name (CDP does not keep `@eN` refs
between CLI calls), menus that the header covers are reached through the
"Additional connection options" dropdown, and the runtime is released with
`google.colab.runtime.unassign()` because the Runtime menu is not reachable
through the accessibility tree.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

AGENT_BROWSER = "agent-browser"
COLAB_HOME = "https://colab.research.google.com/"
# The /notebook path matters: "/#create=true" from the Colab home page only
# changes the hash, so nothing reloads and no notebook is created.
NEW_NOTEBOOK = "https://colab.research.google.com/notebook#create=true"
CONNECTED = re.compile(r'button "RAM Disk Connected to [^"]*"')


class ColabBrowserError(RuntimeError):
    pass


class ColabLoginRequired(ColabBrowserError):
    """The profile's Google session expired. Signing in needs a 2FA code only
    the user can read, so this is reported, never retried automatically."""


@dataclass
class ColabBrowser:
    session: str = "colab-cdp"
    cdp_port: int = 9222
    command_timeout: float = 60.0

    # ---- low level -------------------------------------------------------
    def _run(self, *args: str, timeout: float | None = None) -> tuple[bool, str]:
        result = subprocess.run(
            [AGENT_BROWSER, "--session", self.session, "--cdp", str(self.cdp_port),
             "--idle-timeout", "0", *args],
            capture_output=True, text=True, timeout=timeout or self.command_timeout)
        output = (result.stdout or "") + (result.stderr or "")
        return result.returncode == 0 and "✗" not in output, output

    def _must(self, *args: str, what: str) -> str:
        ok, output = self._run(*args)
        if not ok:
            raise ColabBrowserError(f"Colab: {what} thất bại — {output.strip()[-300:]}")
        return output

    def snapshot(self, interactive: bool = True) -> str:
        return self._run("snapshot", *(["-i"] if interactive else []))[1]

    def click(self, role: str, name: str, what: str) -> None:
        self._must("find", "role", role, "click", "--name", name, what=what)

    def _cdp_pages(self) -> list[dict]:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.cdp_port}/json/list", timeout=5) as r:
            return [t for t in json.load(r) if t.get("type") == "page"]

    # ---- readiness -------------------------------------------------------
    def ensure_chrome(self) -> None:
        """Chrome Stable must own the port: Google refuses Chrome for Testing."""
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.cdp_port}/json/version", timeout=5) as r:
                version = json.load(r)
        except OSError as exc:
            raise ColabBrowserError(
                f"Chrome của tài khoản Colab 1 không chạy ở cổng {self.cdp_port} "
                "(PM2 quản lý, xem colab-login-runbook)") from exc
        if "Testing" in version.get("User-Agent", ""):
            raise ColabBrowserError("Cổng CDP đang là Chrome for Testing — Google sẽ chặn đăng nhập")
        self._drop_stale_target()

    def _drop_stale_target(self) -> None:
        """agent-browser pins a target id; a tab that no longer exists makes every
        command fail with "tab is not responding"."""
        target = Path.home() / ".agent-browser" / f"{self.session}.target"
        if not target.exists():
            return
        try:
            pinned = json.loads(target.read_text()).get("targetId")
        except (OSError, ValueError):
            pinned = None
        if pinned not in {page.get("id") for page in self._cdp_pages()}:
            target.unlink(missing_ok=True)

    def ensure_logged_in(self) -> None:
        self._must("open", COLAB_HOME, what="mở Colab")
        time.sleep(5)
        if "Google Account:" not in self.snapshot():
            raise ColabLoginRequired(
                "Tài khoản Colab 1 đã đăng xuất — cần đăng nhập lại bằng mã 2FA "
                "(FineTune-Model/docs/colab-login-runbook.md)")

    # ---- notebook and runtime -------------------------------------------
    def open_notebook(self, url: str | None) -> str:
        """Open the dedicated render notebook, creating it on first use."""
        self._must("open", url or NEW_NOTEBOOK, what="mở notebook render")
        for _ in range(12):
            time.sleep(3)
            current = self._run("get", "url")[1].strip().splitlines()[-1:]
            if current and "/drive/" in current[0]:
                return current[0].split("#")[0]
        raise ColabBrowserError("Colab: không mở được notebook render")

    def _connection_menu(self, item: str) -> None:
        self._run("press", "Escape")
        self.click("button", "Additional connection options", "mở menu kết nối")
        time.sleep(2)
        self.click("menuitem", item, f"chọn '{item}'")
        time.sleep(3)

    def set_runtime(self, label: str) -> None:
        """Select a runtime type ("v6e-1 TPU"). Switching away from a live runtime
        asks to delete it first; that dialog is confirmed."""
        self._connection_menu("Change runtime type")
        if f'radio "{label}" [checked=true' not in self.snapshot():
            self.click("radio", label, f"chọn runtime {label}")
            time.sleep(2)
            if "Disconnect and delete runtime" in self.snapshot():
                self.click("button", "OK", "xác nhận xoá runtime cũ")
                time.sleep(3)
        self.click("button", "Save", "lưu loại runtime")
        time.sleep(3)

    def connect(self, timeout_s: float = 300.0) -> str:
        """Connect and wait until the status button reports RAM/Disk. Returns it."""
        already = CONNECTED.search(self.snapshot())
        if already:
            return already.group(0)
        # The toolbar's "Connect" button ignores a synthetic click; the menu item
        # of the same name starts the runtime (measured: connected in ~20 s).
        self._connection_menu("Connect to a hosted runtime")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            page = self.snapshot()
            match = CONNECTED.search(page)
            if match:
                return match.group(0)
            if re.search(r"Cannot connect|unavailable|usage limits|not available", page, re.I):
                raise ColabBrowserError("Colab từ chối cấp runtime (hết quota hoặc TPU không sẵn)")
            time.sleep(10)
        raise ColabBrowserError(f"Colab: chưa nối được runtime sau {int(timeout_s)}s")

    def run_cell(self, code: str) -> None:
        """Type `code` into a fresh cell below and run it."""
        self._run("press", "Escape")
        self.click("button", "Code Insert code cell below", "chèn ô code")
        time.sleep(2)
        self._must("keyboard", "type", code, what="gõ lệnh vào ô")
        time.sleep(1)
        self._must("press", "Control+Enter", what="chạy ô")

    def unassign(self, timeout_s: float = 90.0) -> bool:
        """Delete the runtime (stops compute-unit billing). True once it is gone."""
        if not CONNECTED.search(self.snapshot()):
            return True
        # A render cell still running (failure, timeout) would make the new cell
        # queue behind it forever: interrupt first (Colab's Ctrl+M I).
        self._run("press", "Escape")
        self._run("press", "Control+m")
        self._run("press", "i")
        time.sleep(3)
        self.run_cell("from google.colab import runtime; runtime.unassign()")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            time.sleep(5)
            if not CONNECTED.search(self.snapshot()):
                return True
        return False
