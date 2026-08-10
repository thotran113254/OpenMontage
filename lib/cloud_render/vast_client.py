"""Vast.ai account operations: search offers, rent (adopt-or-create), wait for
boot, destroy.

`vastai` is imported *inside* each function that calls it, never at module
level -- `import lib.cloud_render` (or anything that imports this module)
must not require the SDK to be installed. This mirrors
`tools/video/talking_head_autoedit.py`'s lazy-import convention: a missing
optional dependency must degrade a caller's feature detection to
`status=unavailable`, never break it for everyone.

Provider dicts never escape this module: every account read is normalized
into `Offer` / `Instance` before returning (except `list_labelled_instances`,
which the reaper needs raw so it can read the label string).

Verified against the installed SDK (`vastai==1.0.4`,
`vastai/api/instances.py`, `vastai/sdk.py`):

- `VastAI.create_instance(id, image=None, disk=10, **kwargs)` forwards
  `**kwargs` to `instances.create_instance`, whose signature has NO
  `**kwargs` of its own -- it lists keywords explicitly (`env`, `price`,
  `label`, `onstart_cmd`, `force`, `cancel_unavail`, `runtype`, ...). Passing
  `ssh=True, direct=True` (the manual-CLI style) raises `TypeError`. The
  CLI's `--ssh --direct` combination is `runtype="ssh_direc ssh_proxy"`.
- `destroy_instance(id)` needs no `-y` -- the interactive confirm prompt is a
  CLI-only behaviour, not part of the API call.
- `VastAI.__init__` (`vastai/sdk.py`) only auto-reads the *legacy*
  `~/.vast_api_key` when `api_key=None`. It does NOT read `VAST_API_KEY`,
  and it does NOT read `~/.config/vastai/vast_api_key` -- the file the
  `vastai` CLI itself writes to and prefers (`vastai/cli/util.py:APIKEY_FILE`,
  `DIRS['config']` = `~/.config/vastai` without the optional `xdg` package).
  A user who only ever ran `vastai set api-key` (the documented CLI setup
  flow) has a key at the CLI path, not the legacy SDK path, so the SDK class
  alone would silently authenticate as nobody (403, not a clear error).
  `_client()` below checks, in order: the explicit `api_key` argument, the
  `VAST_API_KEY` env var, then the CLI's config-dir file -- falling through
  to the SDK's own legacy-file behaviour only if none of those exist.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.cloud_render import ledger

LABEL_PREFIX = ledger.LABEL_PREFIX
_LABEL_RE = re.compile(rf"^{re.escape(LABEL_PREFIX)}-(?P<intent_id>.+)-until-(?P<deadline>\d+)$")


class CloudRenderError(Exception):
    """Base error for cloud-render account operations."""


class CeilingExceeded(CloudRenderError):
    """Raised when a rental would exceed a configured $/hr ceiling. Refused
    before any `create_instance` call -- see `rent()`."""


@dataclass(frozen=True)
class Offer:
    id: int
    dph: float
    cpu_cores_effective: float | None
    ram_gb: float | None
    disk_avail_gb: float | None
    reliability: float | None
    geolocation: str | None
    gpu_name: str | None


@dataclass(frozen=True)
class Instance:
    id: int
    label: str | None
    actual_status: str | None
    ssh_host: str | None
    ssh_port: int | None
    dph_total: float | None


def _cli_config_api_key() -> str | None:
    """Read the API key from the `vastai` CLI's own config file, since the
    SDK class does not (see module docstring). Best-effort: any read error
    (missing file, permissions) just means "no key found here"."""
    candidate = Path.home() / ".config" / "vastai" / "vast_api_key"
    try:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8").strip() or None
    except OSError:
        pass
    return None


def _client(api_key: str | None = None) -> Any:
    """Lazy SDK import -- see module docstring."""
    from vastai import VastAI  # noqa: PLC0415 -- intentionally lazy, not a top-level import
    key = api_key or os.environ.get("VAST_API_KEY") or _cli_config_api_key()
    return VastAI(api_key=key)


def _offer_from_dict(raw: dict[str, Any]) -> Offer:
    dph = raw.get("dph_total")
    if dph is None:
        dph = raw.get("dph_base")
    if dph is None:
        dph = raw.get("min_bid")
    cpu_ram_mb = raw.get("cpu_ram")
    return Offer(
        id=raw["id"],
        dph=float(dph) if dph is not None else 0.0,
        cpu_cores_effective=raw.get("cpu_cores_effective"),
        ram_gb=(cpu_ram_mb / 1024) if cpu_ram_mb else None,
        disk_avail_gb=raw.get("disk_space"),
        reliability=raw.get("reliability"),
        geolocation=raw.get("geolocation"),
        gpu_name=raw.get("gpu_name"),
    )


def _instance_from_dict(raw: dict[str, Any]) -> Instance:
    return Instance(
        id=raw["id"],
        label=raw.get("label"),
        actual_status=raw.get("actual_status"),
        ssh_host=raw.get("ssh_host"),
        ssh_port=raw.get("ssh_port"),
        dph_total=raw.get("dph_total"),
    )


def parse_label(label: str) -> tuple[str, int] | None:
    """Parse `"openmontage-{intent_id}-until-{deadline_epoch}"`. Returns
    `None` for anything else (including a human's manually-labelled
    instance) -- the reaper must never touch what it cannot parse as ours."""
    match = _LABEL_RE.match(label or "")
    if not match:
        return None
    return match.group("intent_id"), int(match.group("deadline"))


def search(query: str, *, mode: str = "on-demand", order: str = "score-",
           limit: int | None = None, api_key: str | None = None) -> list[Offer]:
    """`mode` maps 1:1 onto `VastAI.search_offers(type=mode)` -- Vast accepts
    `on-demand` | `bid` | `reserved` for that parameter."""
    if mode not in ("on-demand", "bid", "reserved"):
        raise CloudRenderError(f"pricing mode không hợp lệ: {mode!r}")
    client = _client(api_key)
    raw_offers = client.search_offers(query=query, type=mode, order=order, limit=limit)
    return [_offer_from_dict(o) for o in raw_offers]


def list_labelled_instances(*, api_key: str | None = None) -> list[dict[str, Any]]:
    """Raw (unnormalized) instance dicts whose label carries the
    `openmontage-` prefix. Read-only, free -- the reaper needs the raw label
    string to parse `intent_id` + deadline via `parse_label`."""
    client = _client(api_key)
    return [raw for raw in client.show_instances()
            if (raw.get("label") or "").startswith(f"{LABEL_PREFIX}-")]


def _find_labelled(client: Any, intent_id: str) -> Instance | None:
    prefix = f"{LABEL_PREFIX}-{intent_id}-"
    for raw in client.show_instances():
        if (raw.get("label") or "").startswith(prefix):
            return _instance_from_dict(raw)
    return None


def rent(offer_id: int, offer_dph: float, *, intent_id: str, deadline_epoch: int,
         ceiling_dph: float, image: str, disk_gb: float, onstart: str,
         bid_price: float | None = None, api_key: str | None = None) -> Instance:
    """Adopt-or-create, in this exact order (guards a double-rent on retry):

    1. Look up the account for an instance already labelled with
       `intent_id` -- if found, adopt it and return; `create_instance` is
       never called for a retry of the same intent.
    2. Re-check `offer_dph <= ceiling_dph`; refuse otherwise
       (`CeilingExceeded`) -- before any API call that spends money.
    3. Write the ledger `pending` record *before* `create_instance` -- the
       failure this guards against is "create succeeded, response lost".
    4. `create_instance(...)` with the durable label
       `f"{LABEL_PREFIX}-{intent_id}-until-{deadline_epoch}"`.
    5. Promote the ledger record to `active` with the returned instance id.

    `offer_dph` is the caller's already-known price for `offer_id` (from a
    prior `search()`); re-checking it here (rather than re-querying the
    account) keeps the ceiling check synchronous and network-free.
    """
    client = _client(api_key)
    mode = "bid" if bid_price else "on-demand"
    label = f"{LABEL_PREFIX}-{intent_id}-until-{deadline_epoch}"

    existing = _find_labelled(client, intent_id)
    if existing is not None:
        # Reconcile local ledger even on adopt, so a process that lost its
        # local state (but not the account's memory) still ends up with a
        # correct active.json entry.
        ledger.open_pending(intent_id, label=existing.label or label,
                            dph_usd=existing.dph_total or offer_dph, mode=mode,
                            deadline_epoch=deadline_epoch)
        ledger.promote(intent_id, instance_id=existing.id)
        return existing

    if offer_dph > ceiling_dph:
        raise CeilingExceeded(
            f"offer {offer_id} dph {offer_dph} vượt ceiling {ceiling_dph} "
            f"cho intent_id={intent_id!r}")

    ledger.open_pending(intent_id, label=label, dph_usd=offer_dph, mode=mode,
                        deadline_epoch=deadline_epoch)

    response = client.create_instance(
        id=offer_id, image=image, disk=disk_gb, label=label,
        runtype="ssh_direc ssh_proxy", onstart_cmd=onstart,
        price=bid_price or None, force=False, cancel_unavail=False,
    )
    instance_id = response.get("new_contract") or response.get("id")
    if not instance_id:
        raise CloudRenderError(
            f"create_instance response thiếu instance id: {response!r}")

    ledger.promote(intent_id, instance_id=instance_id)
    return Instance(id=instance_id, label=label, actual_status=None,
                    ssh_host=None, ssh_port=None, dph_total=offer_dph)


def wait_running(instance_id: int, timeout_s: float = 300.0, *,
                 poll_interval_s: float = 5.0, api_key: str | None = None) -> Instance:
    """Poll `show_instance()` until `actual_status == "running"`, surfacing
    `ssh_host`/`ssh_port` from the same payload."""
    import time as _time  # local import: keep this module's top-level imports minimal/stable

    client = _client(api_key)
    deadline = _time.monotonic() + timeout_s
    while True:
        raw = client.show_instance(id=instance_id)
        instance = _instance_from_dict(raw)
        if instance.actual_status == "running":
            return instance
        if _time.monotonic() >= deadline:
            raise CloudRenderError(
                f"instance {instance_id} không đạt trạng thái running trong {timeout_s}s "
                f"(status cuối={instance.actual_status!r})")
        _time.sleep(poll_interval_s)


def destroy(instance_id: int, *, api_key: str | None = None) -> None:
    """Idempotent: an instance already gone is success, not an error."""
    client = _client(api_key)
    try:
        client.destroy_instance(id=instance_id)
    except Exception as exc:  # noqa: BLE001 -- must inspect any SDK/HTTP error shape
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code == 404:
            return
        message = str(exc).lower()
        if "not found" in message or "does not exist" in message:
            return
        raise
