"""Reusable edit-style packs: prompt + BGM + volume + ticks, shared across projects.

A style is what you apply to many different talking-head videos so they come out
the same way. It is NOT a look-preset (grade fragment) and NOT a director prompt
template (structure.vN.md). Those stay where they are.

One JSON file, same atomic-write pattern as look_presets.py.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from lib.talking_head_edit.job_store import REPO_ROOT, slugify

STYLES_PATH = REPO_ROOT / "config" / "edit-styles.json"

OPTION_KEYS = (
    "prompt",
    "topic",
    "card_plan",
    "brand_pill",
    "language",
    "tempo",
    "bgm",
    "bgm_name",
    "bgm_volume",
    "cold_open",
    "hook_prompt",
    "asr_provider",
    "whisper_model",
    "auto_sharpen",
    "auto_grade",
    "auto_audio_preset",
    "audio_preset",
    "frame_preset",
)

VOLUME_RANGE = (0.08, 0.22)
TITLE_MAX = 80


class EditStyleError(ValueError):
    pass


def _read(path=None) -> dict[str, Any]:
    target = path or STYLES_PATH
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(styles: list[dict[str, Any]], path=None) -> None:
    target = path or STYLES_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"styles": styles}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp, target)


def _clamp_volume(value: Any) -> float:
    try:
        volume = float(value)
    except (TypeError, ValueError) as exc:
        raise EditStyleError("bgm_volume phải là số") from exc
    lo, hi = VOLUME_RANGE
    return round(max(lo, min(hi, volume)), 3)


def clean_options(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise EditStyleError("options phải là object")
    out: dict[str, Any] = {}
    for key in OPTION_KEYS:
        if key not in raw:
            continue
        value = raw[key]
        if key == "bgm_volume":
            out[key] = _clamp_volume(value)
        elif key == "tempo":
            try:
                out[key] = round(max(1.0, min(1.2, float(value))), 2)
            except (TypeError, ValueError) as exc:
                raise EditStyleError("tempo phải là số") from exc
        elif key in ("bgm", "cold_open", "auto_sharpen", "auto_grade", "auto_audio_preset"):
            out[key] = bool(value)
        elif key in ("prompt", "topic", "card_plan", "hook_prompt", "brand_pill", "language",
                     "bgm_name", "asr_provider", "whisper_model", "audio_preset",
                     "frame_preset"):
            out[key] = str(value or "")
        else:
            out[key] = value
    return out


def clean_title(title: Any) -> str:
    text = str(title or "").strip()
    if len(text) < 2:
        raise EditStyleError("Tên kiểu cần ít nhất 2 ký tự")
    if len(text) > TITLE_MAX:
        raise EditStyleError(f"Tên kiểu tối đa {TITLE_MAX} ký tự")
    return text


def load_all(path=None) -> list[dict[str, Any]]:
    data = _read(path)
    raw = data.get("styles")
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        try:
            options = clean_options(entry.get("options") or {})
            title = str(entry.get("title") or entry["id"])
        except EditStyleError:
            continue
        out.append({
            "id": str(entry["id"]),
            "title": title,
            "source_project_id": entry.get("source_project_id") or "",
            "options": options,
            "created_at": entry.get("created_at") or 0,
            "updated_at": entry.get("updated_at") or 0,
        })
    out.sort(key=lambda item: item.get("updated_at") or 0, reverse=True)
    return out


def get(style_id: str, path=None) -> dict[str, Any] | None:
    return next((s for s in load_all(path) if s["id"] == style_id), None)


def save(
    title: str,
    options: dict[str, Any],
    style_id: str | None = None,
    source_project_id: str = "",
    path=None,
) -> dict[str, Any]:
    target = path or STYLES_PATH
    clean = clean_options(options)
    name = clean_title(title)
    now = time.time()
    existing = load_all(target)
    if style_id:
        current = next((s for s in existing if s["id"] == style_id), None)
        if not current:
            raise EditStyleError(f"Không có kiểu '{style_id}'")
        entry = {
            **current,
            "title": name,
            "options": clean,
            "source_project_id": source_project_id or current.get("source_project_id") or "",
            "updated_at": now,
        }
        styles = [s for s in existing if s["id"] != style_id] + [entry]
    else:
        stamp = time.strftime("%y%m%d-%H%M%S")
        new_id = f"{slugify(name, 32)}-{stamp}"
        entry = {
            "id": new_id,
            "title": name,
            "source_project_id": source_project_id or "",
            "options": clean,
            "created_at": now,
            "updated_at": now,
        }
        styles = existing + [entry]
    _write(styles, target)
    return entry


def delete(style_id: str, path=None) -> bool:
    target = path or STYLES_PATH
    existing = load_all(target)
    remaining = [s for s in existing if s["id"] != style_id]
    if len(remaining) == len(existing):
        return False
    _write(remaining, target)
    return True
