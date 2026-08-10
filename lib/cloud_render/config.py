"""Cloud-render config: global -> project -> job, 3 layers merged per key.

Mirrors `lib/talking_head_edit/assembly_config.py` (`resolve`/`sources_of`/
`_clean` trio) so the render-kit UI (a later phase) can show which layer a
value came from the same way the assembly settings UI already does.

Every value here guards real money on a real Vast.ai account. A bad ceiling
must fail loud at `validate()` time -- it must never fall back to a silent
default that quietly widens (or removes) a spend cap.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
GLOBAL_CONFIG_PATH = REPO_ROOT / "config" / "cloud-render.json"

PRICING_MODES = ("on-demand", "bid")

BUILTIN_DEFAULTS: dict[str, Any] = {
    # Opt-in: nothing rents until a human flips this in config/cloud-render.json
    # (or a project/job layer explicitly overrides it).
    "enabled": False,
    "provider": "vastai",
    "image": "node:22-bookworm",
    "disk_gb": 12,
    # "bid" (interruptible) is cheaper (~15%) but preemptible; chosen as the
    # default over "on-demand" -- cheaper-plus-risk over safer-plus-costlier.
    "pricing_mode": "bid",
    "max_dph_usd": 0.15,
    "max_total_usd_per_rental": 0.50,
    "max_runtime_minutes": 60,
    # Ceiling for a *batch* flush's deadline (phase 03): a single-job render
    # never needs this much time, but N jobs sharing one rental legitimately
    # can. `queue.flush()` derives its actual deadline from the sum of the
    # batch's own estimates and only clamps to this as a backstop.
    "max_batch_runtime_minutes": 90,
    "offer_query": "reliability>0.95 rentable=True cpu_cores_effective>=32",
    "apt_packages": [
        "ffmpeg", "ca-certificates", "fonts-liberation", "libnss3",
        "libatk-bridge2.0-0", "libatk1.0-0", "libcups2", "libdrm2", "libgbm1",
        "libasound2", "libpangocairo-1.0-0", "libxss1", "libxtst6",
        "libx11-xcb1", "libxcomposite1", "libxdamage1", "libxrandr2",
        "libgtk-3-0", "xdg-utils",
    ],
    # Dedicated keypair for cloud-render only (see setup_key.py) -- not the
    # shared ~/.ssh/vast_new key, so revocation stays scoped to this feature.
    "ssh_key_path": "~/.ssh/openmontage_cloud_render",
    "batch": {"min_jobs": 3, "min_total_render_minutes": 20, "max_wait_minutes": 240},
    "render_seconds_per_video_second": 1.9,
}

KEYS = tuple(BUILTIN_DEFAULTS)


class CloudRenderConfigError(ValueError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def global_defaults(path: Path | None = None) -> dict[str, Any]:
    """Layer 1. A missing or malformed file falls back to the built-ins -- a
    typo in the committed config must not stop every render from starting."""
    data = _read_json(path or GLOBAL_CONFIG_PATH)
    return {**BUILTIN_DEFAULTS, **data}


def _clean(layer: Any) -> dict[str, Any]:
    """Keep only known keys with a real value.

    `None` is dropped rather than treated as an override, for the same
    reason as `assembly_config._clean`: a null override that silently
    disables a spend ceiling is worse than no override at all.
    """
    if not isinstance(layer, dict):
        return {}
    return {key: value for key, value in layer.items()
            if key in BUILTIN_DEFAULTS and value is not None}


def resolve(project: dict[str, Any] | None = None, job: dict[str, Any] | None = None,
            global_path: Path | None = None) -> dict[str, Any]:
    """global -> project -> job, last non-null wins per key."""
    merged = {**global_defaults(global_path), **_clean(project), **_clean(job)}
    return validate(merged)


def validate(config: dict[str, Any]) -> dict[str, Any]:
    """Fail loud on a bad ceiling/mode -- never default silently.

    Only the four fields called out in the phase spec are checked here; every
    other key is passed through as-is (already constrained to known keys by
    `_clean`/`global_defaults`).
    """
    max_dph = config.get("max_dph_usd")
    if not isinstance(max_dph, (int, float)) or isinstance(max_dph, bool) or max_dph <= 0:
        raise CloudRenderConfigError(f"max_dph_usd phải > 0, nhận {max_dph!r}")

    max_runtime = config.get("max_runtime_minutes")
    if (not isinstance(max_runtime, (int, float)) or isinstance(max_runtime, bool)
            or not (5 <= max_runtime <= 240)):
        raise CloudRenderConfigError(
            f"max_runtime_minutes phải trong [5, 240], nhận {max_runtime!r}")

    max_batch_runtime = config.get("max_batch_runtime_minutes")
    if (not isinstance(max_batch_runtime, (int, float)) or isinstance(max_batch_runtime, bool)
            or not (5 <= max_batch_runtime <= 480)):
        raise CloudRenderConfigError(
            f"max_batch_runtime_minutes phải trong [5, 480], nhận {max_batch_runtime!r}")

    pricing_mode = config.get("pricing_mode")
    if pricing_mode not in PRICING_MODES:
        raise CloudRenderConfigError(
            f"pricing_mode phải là một trong {PRICING_MODES}, nhận {pricing_mode!r}")

    disk_gb = config.get("disk_gb")
    if not isinstance(disk_gb, (int, float)) or isinstance(disk_gb, bool) or disk_gb < 10:
        raise CloudRenderConfigError(f"disk_gb phải >= 10, nhận {disk_gb!r}")

    return dict(config)


def sources_of(config: dict[str, Any], project: dict[str, Any] | None = None,
               job: dict[str, Any] | None = None,
               global_path: Path | None = None) -> dict[str, str]:
    """Which layer each resolved value came from.

    The UI needs this: someone editing project/job overrides has to see which
    values they are actually overriding and which are simply inherited.
    """
    layers = [("global", global_defaults(global_path)),
              ("project", _clean(project)),
              ("job", _clean(job))]
    origin: dict[str, str] = {}
    for name, layer in layers:
        for key in layer:
            if key in config:
                origin[key] = name
    return origin
