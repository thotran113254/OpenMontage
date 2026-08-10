"""Pure string builder for the Vast.ai onstart script.

No I/O -- so it is trivially unit-testable without a network or an account.
The caller (vast_client.rent) passes the result straight to
`create_instance(onstart_cmd=...)`.
"""

from __future__ import annotations

import time

READY_MARKER = "/root/.openmontage-ready"


def build(pubkey: str, apt_packages: list[str], deadline_epoch: int,
          now_epoch: int | None = None) -> str:
    """Build the script run once, as root, when the rented instance boots.

    Order matters:

    1. Inject the local SSH public key -- without this an operator can never
       reach the box at all (a team API key cannot register keys server-side,
       so this onstart injection is the only path, not a workaround).
    2. `touch /root/.no_auto_tmux` *before* anything else that could hang.
       The default images hijack every SSH session into tmux and silently
       drop a non-interactive command otherwise -- the very first
       `ssh host 'npm ci'` would do nothing and nobody would know why.
    3. `apt-get install` the Chrome/Remotion headless-render dependencies.
       This step is deliberately NOT `|| true`-guarded: a failed install must
       be visible, because a box with no Chrome deps fails every render after
       the account has already paid for boot. `READY_MARKER` is written only
       on success so a caller (a later phase's remote.py) can wait for it and
       abort+destroy if it never appears.
    4. A deadline watchdog: `shutdown -h` after `deadline_epoch - now` seconds
       -- a backstop against a leaked local process, not the primary
       guarantee (that lives in the ledger + reaper). Every other line is
       `|| true`-guarded so one cosmetic failure never skips this line.

    `now_epoch` defaults to the current wall clock; tests pass a fixed value
    to make the resulting sleep duration deterministic.
    """
    pubkey_line = pubkey.strip().replace("'", "'\\''")  # safe inside single quotes
    now = int(now_epoch) if now_epoch is not None else int(time.time())
    sleep_seconds = max(int(deadline_epoch) - now, 0)
    packages = " ".join(apt_packages)

    lines = [
        "#!/bin/bash",
        "mkdir -p /root/.ssh || true",
        f"echo '{pubkey_line}' >> /root/.ssh/authorized_keys || true",
        "chmod 700 /root/.ssh || true",
        "chmod 600 /root/.ssh/authorized_keys || true",
        "touch /root/.no_auto_tmux || true",
        f"apt-get update && apt-get install -y {packages} && touch {READY_MARKER}",
        f'nohup sh -c "sleep {sleep_seconds}; shutdown -h now" &',
    ]
    return "\n".join(lines) + "\n"
