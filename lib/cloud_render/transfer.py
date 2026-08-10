"""Local <-> remote transfer for a rented Vast.ai instance, via Cloudflare R2
presigned URLs -- and remote command execution over raw `ssh`.

The SDK's `copy()` method (and the CLI's equivalent `copy` subcommand) must
NEVER be used here, and this is not a style preference -- it is actively
unsafe with Windows paths. Verified against the installed SDK (`vastai==1.0.4`):

  `vastai/utils.py::parse_vast_url` splits its argument on the *first* `:`,
  so a Windows path like `"D:/CODE/x.txt"` parses as `("D", "/CODE/x.txt")`
  -- the drive letter silently becomes an instance id, and it does not raise.
  Downstream, `vastai/api/storage.py::copy` PUTs `/commands/rsync/`, which is
  a *server-side* rsync between two rented instances -- it never reads local
  bytes at all. There is no local<->remote path through the SDK's copy().

Likewise `VastAI.execute(id, cmd)` PUTs `/instances/command/{id}/`, an async
job-queue endpoint polled via a `result_url` -- it is not a shell and does
not give a real exit code. Raw `ssh` subprocess calls are the only correct
way to run a remote command. Do not "simplify" this module back onto the SDK.

Transfer itself goes through R2, not `scp`: the rented box is ephemeral
compute, never storage, and it never holds a long-lived R2 credential (it is
destroyed and re-leased to strangers). A presigned URL is generated locally
and handed to the remote `curl` on **stdin**, never in argv -- an argv value
lands in the remote's `ps` output and possibly Vast.ai's own instance logs.
"""

from __future__ import annotations

import subprocess
import tarfile
import tempfile
from pathlib import Path

# Ephemeral, unknowable-in-advance host key -> accepted. Paired with
# UserKnownHostsFile=/dev/null so the local known_hosts is never polluted
# with dozens of dead rental hosts.
_SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "BatchMode=yes",
]

# curl exit codes worth naming explicitly -- a bare "exit 22" inside a paid
# rental is hostile; these are the ones a presign/network problem produces.
_CURL_ERROR_HINTS = {
    22: "presign hết hạn hoặc bị từ chối (HTTP >= 400)",
    28: "timeout khi tải/đẩy qua R2",
    56: "mất kết nối khi nhận dữ liệu -- rental có thể đã bị preempt",
}


class TransferError(RuntimeError):
    """Raised when an ssh/curl subprocess call cannot complete at all
    (e.g. times out). A non-zero exit code from a *successful* connection is
    NOT raised here -- that is a normal outcome the caller inspects via
    `CompletedProcess.returncode`."""


def _run(args: list[str], *, timeout: float, input: str | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                               shell=False, input=input)
    except subprocess.TimeoutExpired as exc:
        raise TransferError(f"timed out after {timeout}s: {args[0]} ...") from exc
    except OSError as exc:
        raise TransferError(f"could not start {args[0]!r}: {exc}") from exc


def ssh(host: str, port: int, key_path: str | Path, command: str, *,
        timeout: float = 60.0, input: str | None = None) -> subprocess.CompletedProcess:
    """Run one non-interactive command over ssh. Real exit codes, real stdout/
    stderr -- unlike `VastAI.execute()` (see module docstring).

    `input`, when given, is piped to the remote command's stdin (ssh forwards
    local stdin to the remote process by default) -- the only safe way to
    hand a presigned URL to a remote `curl` without it appearing in argv.
    """
    args = ["ssh", "-i", str(key_path), "-p", str(port), *_SSH_OPTS,
            f"root@{host}", command]
    return _run(args, timeout=timeout, input=input)


def _curl_error(returncode: int, stderr: str) -> str:
    hint = _CURL_ERROR_HINTS.get(returncode, f"curl thoát mã {returncode}")
    tail = (stderr or "").strip()[-300:]
    return f"{hint}{f': {tail}' if tail else ''}"


def push_kit(kit_dir: str | Path, key: str, *, settings=None) -> tuple[str, int, bool]:
    """tar.gz `kit_dir` and upload it to R2 under `key`. Skips the upload
    (returns `skipped=True`) when an object already exists at `key` -- the
    content-addressed reuse that makes a repeat rental of an unchanged
    composer kit cost zero home-uplink bytes."""
    from lib.r2_storage.client import build_client, transfer_config
    from lib.r2_storage.config import resolve
    from lib.r2_storage.presign import object_exists

    settings = settings or resolve()
    if object_exists(key, settings=settings):
        return key, 0, True

    with tempfile.TemporaryDirectory() as tmp:
        archive_path = Path(tmp) / "kit.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(kit_dir, arcname=".")
        client = build_client(settings)
        client.upload_file(str(archive_path), settings.bucket, key, Config=transfer_config(settings))
        size = archive_path.stat().st_size
    return key, size, False


def fetch_to_remote(host: str, port: int, key_path: str | Path, key: str, remote_dir: str, *,
                     timeout: float = 300.0, ttl: int = 1800,
                     settings=None) -> subprocess.CompletedProcess:
    """Presign a GET for `key`, hand the URL to the remote box on stdin, and
    have it `curl | tar -xz` into `remote_dir`. `-C -` makes the transfer
    resumable; `--retry 3` absorbs a transient network blip."""
    from lib.r2_storage.presign import get_url

    url = get_url(key, ttl, settings=settings)
    command = (f"mkdir -p {remote_dir} && read -r U && "
               f'curl -fsSL --retry 3 -C - "$U" -o /tmp/openmontage-kit.tgz && '
               f"tar -xzf /tmp/openmontage-kit.tgz -C {remote_dir} && "
               "rm -f /tmp/openmontage-kit.tgz")
    return ssh(host, port, key_path, command, timeout=timeout, input=url + "\n")


def push_from_remote(host: str, port: int, key_path: str | Path, remote_path: str, key: str, *,
                      ttl: float, timeout: float = 300.0,
                      settings=None) -> subprocess.CompletedProcess:
    """Presign a PUT for `key`, hand the URL to the remote box on stdin, and
    have it `curl -X PUT --upload-file` its render output straight to R2."""
    from lib.r2_storage.presign import put_url

    url = put_url(key, int(ttl), settings=settings)
    command = f'read -r U && curl -fsS --retry 3 -X PUT --upload-file {remote_path} "$U"'
    return ssh(host, port, key_path, command, timeout=timeout, input=url + "\n")
