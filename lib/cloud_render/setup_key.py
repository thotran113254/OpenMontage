"""One-time setup helper: generate the dedicated ed25519 keypair used for
cloud-render SSH access.

A *team* Vast.ai API key cannot register SSH keys server-side
(`vastai create ssh-key` returns "Team SSH keys are not supported"), so the
public half is injected per-rental by the onstart script instead (see
`onstart.py`) -- that is not a workaround to revisit, it is the only path
available to a team key.

This is a dedicated keypair rather than a reuse of any existing Vast key
(e.g. `~/.ssh/vast_new`): a scoped key means a compromise or rotation only
affects cloud-render rentals, not every other box already using a shared
key.

Run standalone:

    python -m lib.cloud_render.setup_key

Never runs as a side effect of importing this module -- `ensure_keypair()`
is idempotent and only touches disk when explicitly called.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

DEFAULT_KEY_PATH = Path.home() / ".ssh" / "openmontage_cloud_render"
KEY_COMMENT = "openmontage-cloud-render"


class SetupKeyError(RuntimeError):
    pass


def _public_key_path(key_path: Path) -> Path:
    return key_path.parent / (key_path.name + ".pub")


def ensure_keypair(key_path: Path | str = DEFAULT_KEY_PATH) -> Path:
    """Create an ed25519 keypair at `key_path` if it does not already exist.

    Idempotent: an existing keypair (private + public halves both present)
    is left untouched and `key_path` is simply returned.
    """
    key_path = Path(key_path).expanduser()
    public_path = _public_key_path(key_path)
    if key_path.exists() and public_path.exists():
        return key_path

    key_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-C", KEY_COMMENT],
            check=True, capture_output=True, text=True,
        )
    except FileNotFoundError as exc:
        raise SetupKeyError(
            "không tìm thấy ssh-keygen trên PATH -- cài OpenSSH client "
            "(Windows: Settings > Optional Features > OpenSSH Client; "
            "Linux/macOS: thường có sẵn)."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise SetupKeyError(f"ssh-keygen thất bại: {exc.stderr}") from exc

    return key_path


def public_key_text(key_path: Path | str = DEFAULT_KEY_PATH) -> str:
    """Read the `.pub` half. Raises if `ensure_keypair()` has not been run."""
    public_path = _public_key_path(Path(key_path).expanduser())
    if not public_path.exists():
        raise SetupKeyError(f"không thấy public key tại {public_path}; "
                            "chạy ensure_keypair() trước")
    return public_path.read_text(encoding="utf-8").strip()


def main(argv: list[str] | None = None) -> int:
    key_path = ensure_keypair()
    print(f"cloud-render ssh keypair sẵn sàng tại {key_path} "
          f"(public: {_public_key_path(key_path)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
