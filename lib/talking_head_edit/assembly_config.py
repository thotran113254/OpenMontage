"""How several sources become one video: the `assembly` config and its 3 layers.

Four separate user needs (best-take, sequential assembly, b-roll overlay,
multi-speaker interviews) collapse onto two axes rather than four code paths:

* **mode** — mutually exclusive: `sequential` | `best_take` | `auto`
* **modifiers** — additive: `speaker_aware`, `broll_overlay`

That distinction is the whole design. Four branches would mean four variants of
the director prompt, and nobody can keep four prompts saying the same thing.

Config resolves global → project → job, each layer overriding per key, so a
project can pin `mode: sequential` for a whole shoot while one job inside it
still tries `best_take`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.talking_head_edit.job_store import REPO_ROOT

GLOBAL_CONFIG_PATH = REPO_ROOT / "config" / "autoedit-defaults.json"

MODES = ("sequential", "best_take", "auto")
# "auto" means: decide from the material. Tri-state rather than boolean because
# "the user did not choose" and "the user chose off" must not look alike.
TRISTATE = ("auto", True, False)

BUILTIN_DEFAULTS: dict[str, Any] = {
    # `auto` is what makes the system run unattended: `select` looks at the
    # sources and picks best_take when it finds overlapping content, sequential
    # when it does not.
    "mode": "auto",
    "speaker_aware": "auto",
    "broll_overlay": True,
    # Cutting across a source boundary joins two different takes mid-sentence.
    # Off by default: it sounds wrong far more often than it sounds clever.
    "cross_source_cut": False,
    # "suggest" = propose take groups, never apply them unasked. A wrong guess
    # here deletes real content, so a human confirms.
    "take_detect": "suggest",
}

KEYS = tuple(BUILTIN_DEFAULTS)


class AssemblyConfigError(ValueError):
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
    """Layer 1. Missing or malformed file falls back to the built-ins, because a
    typo in a config file should not stop a render."""
    data = _read_json(path or GLOBAL_CONFIG_PATH).get("assembly")
    return {**BUILTIN_DEFAULTS, **(data if isinstance(data, dict) else {})}


def _clean(layer: Any) -> dict[str, Any]:
    """Keep only known keys with a real value.

    `None` is dropped rather than treated as False: a JSON client sends
    `{"speaker_aware": null}` far more readily than it omits the key, and a null
    that silently means "off" is a feature turning itself off with nothing in
    any log to say why.
    """
    if not isinstance(layer, dict):
        return {}
    return {key: value for key, value in layer.items()
            if key in BUILTIN_DEFAULTS and value is not None}


def resolve(project: dict[str, Any] | None = None,
            job: dict[str, Any] | None = None,
            global_path: Path | None = None) -> dict[str, Any]:
    """global → project → job, last non-null wins per key."""
    merged = {**global_defaults(global_path), **_clean(project), **_clean(job)}
    return validate(merged)


def validate(config: dict[str, Any]) -> dict[str, Any]:
    mode = config.get("mode")
    if mode not in MODES:
        raise AssemblyConfigError(
            f"assembly.mode không hợp lệ: {mode!r}. Hợp lệ: {', '.join(MODES)}")
    if config.get("speaker_aware") not in TRISTATE:
        raise AssemblyConfigError(
            f"assembly.speaker_aware phải là true/false/'auto', "
            f"nhận {config.get('speaker_aware')!r}")
    if config.get("take_detect") not in ("suggest", "apply", "off"):
        raise AssemblyConfigError(
            f"assembly.take_detect phải là 'suggest'|'apply'|'off', "
            f"nhận {config.get('take_detect')!r}")
    return {
        **config,
        "broll_overlay": bool(config.get("broll_overlay", True)),
        "cross_source_cut": bool(config.get("cross_source_cut", False)),
    }


def sources_of(config: dict[str, Any], project: dict[str, Any] | None = None,
               job: dict[str, Any] | None = None,
               global_path: Path | None = None) -> dict[str, str]:
    """Which layer each resolved value came from.

    The UI needs this: someone editing project settings has to see which values
    they are actually overriding and which are simply inherited.
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


def from_job_state(state: dict[str, Any], project: dict[str, Any] | None = None,
                   global_path: Path | None = None) -> dict[str, Any]:
    """Resolve for one job, reading its `options.assembly` as the job layer."""
    job_layer = (state.get("options") or {}).get("assembly")
    project_layer = (project or {}).get("assembly")
    return resolve(project_layer, job_layer, global_path)
