"""Renderable-resource inventory (SFX / BGM).

Single source of truth: `remotion-composer/src/mona/resource-manifest.json`,
imported by MonaTimeline.tsx and read here. The renderer and the pipeline
therefore cannot disagree about which audio files are usable.

Two ground-truth checks (not heuristics): the manifest entry must exist, and
the file must actually be on disk. A name failing either is unusable — the
renderer skips unknown names silently, which would otherwise show up as a
mysteriously missing sound instead of a warning.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from lib.talking_head_edit.job_store import REPO_ROOT

PUBLIC_DIR = REPO_ROOT / "remotion-composer" / "public"
MANIFEST_PATH = REPO_ROOT / "remotion-composer" / "src" / "mona" / "resource-manifest.json"


@lru_cache(maxsize=1)
def manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def inventory() -> dict[str, Any]:
    data = manifest()
    warnings: list[str] = []

    def present(entries: list[dict[str, Any]], label: str) -> list[dict[str, Any]]:
        usable = []
        for entry in entries:
            if (PUBLIC_DIR / entry["name"]).exists():
                usable.append(entry)
            else:
                warnings.append(
                    f"{label} '{entry['name']}' có trong manifest nhưng thiếu file trong public/"
                )
        return usable

    sfx = present(data.get("sfx", []), "SFX")
    bgm = present(data.get("bgm", []), "BGM")

    declared = {e["name"] for e in data.get("sfx", [])} | {e["name"] for e in data.get("bgm", [])}
    for path in sorted(list(PUBLIC_DIR.glob("sfx_*.mp3")) + list(PUBLIC_DIR.glob("bgm_*.mp3"))):
        if path.name not in declared:
            warnings.append(
                f"'{path.name}' có trên đĩa nhưng chưa khai trong resource-manifest.json "
                "— renderer sẽ bỏ qua"
            )

    return {"sfx": sfx, "bgm": bgm, "warnings": warnings,
            "group_labels": data.get("groupLabels", {})}


def usable_sfx() -> list[str]:
    return [entry["name"] for entry in inventory()["sfx"]]


def usable_bgm() -> list[str]:
    return [entry["name"] for entry in inventory()["bgm"]]


def sfx_table() -> str:
    """Prompt-ready SFX catalogue, only files that can actually play."""
    entries = inventory()["sfx"]
    listed = " | ".join(f"{e['name']} ({e.get('use', 'hiệu ứng')})" for e in entries)
    return f"SFX CÓ THẬT (chỉ {len(entries)} file này): {listed}"


def bgm_table() -> str:
    """Prompt-ready BGM catalogue grouped by mood, only files that exist."""
    data = inventory()
    labels: dict[str, str] = data["group_labels"]
    grouped: dict[str, list[str]] = {}
    for entry in data["bgm"]:
        group = entry.get("group", "other")
        grouped.setdefault(group, []).append(
            f"{entry['name']} ({entry.get('mood', '')})".replace(" ()", "")
        )
    return "\n".join(
        f"- {labels.get(group, group)}: " + " | ".join(names)
        for group, names in grouped.items()
    )
