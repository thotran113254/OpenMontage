"""Named look presets: reusable grade fragments, shared across every job.

A preset stores a *fragment* of `grade_overrides` — never the merged result a
director produced for one job. `resolve.py` layers `{**spec_grade, **shared,
**per_source}`, so a saved fragment sits ON TOP of whatever the next director
run decides; a merged snapshot would freeze that job's grade forever and quietly
stop future re-directs from having any effect (see `grade-preview.tsx`).

`sharpen` and `clarity` are refused outright. `resolve.py`'s `_auto_sharpen`
checks `"sharpen" in (options.get("grade_overrides") or {})` to decide whether
to skip its own per-source sharpness measurement. A preset saved from one video
that happens to carry `sharpen` would silently disable that measurement on
every other video the preset is later applied to — softer output with nothing
in the UI explaining why. Keeping the deny-list here, in the one place every
preset must pass through, is what stops that from happening again.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.talking_head_edit.job_store import REPO_ROOT

PRESETS_PATH = REPO_ROOT / "config" / "look-presets.json"

# Keys `build_grade_chain` actually reads (resolve_media.py:222-303).
ALLOWED_KEYS = frozenset({
    "skin_smooth", "blemish_reduce", "warmth", "tone_curve", "vibrance",
    "vignette", "brightness", "contrast", "saturation", "gamma",
    "color_preset", "hsl_preset", "hsl", "lut", "lut_path",
    "hsl_red_sat", "hsl_red_bright", "hsl_yellow_sat", "hsl_yellow_bright",
    "hsl_magenta_sat", "hsl_magenta_bright", "hsl_green_sat", "hsl_green_bright",
    "hsl_blue_sat", "hsl_blue_bright",
})
# Refused even though `build_grade_chain` reads them: baking either into a
# reusable preset defeats per-source auto-sharpening on every future video.
DENIED_KEYS = frozenset({"sharpen", "clarity"})

NAME_MAX_LEN = 60
_NAME_RE = re.compile(r"^[A-Za-z0-9 _-]+$")


class LookPresetError(ValueError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    """Missing file or corrupt JSON both fall back to empty — a bad preset
    file must not take down every UI page that lists presets."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def validate_name(name: Any) -> str:
    cleaned = str(name or "").strip()
    if not cleaned:
        raise LookPresetError("Tên preset không được để trống")
    if len(cleaned) > NAME_MAX_LEN:
        raise LookPresetError(f"Tên preset tối đa {NAME_MAX_LEN} ký tự")
    if not _NAME_RE.match(cleaned):
        raise LookPresetError(
            "Tên preset chỉ được chứa chữ, số, dấu cách, '_' và '-' "
            "(không được có '/', '\\', '.')")
    return cleaned


def validate(grade: Any) -> dict[str, float]:
    if not isinstance(grade, dict) or not grade:
        raise LookPresetError("Grade phải là object khác rỗng")
    denied = DENIED_KEYS & grade.keys()
    if denied:
        raise LookPresetError(
            f"Preset không được chứa khoá {sorted(denied)}: chúng sẽ tắt "
            "auto-sharpen theo từng nguồn khi áp cho video khác")
    unknown = grade.keys() - ALLOWED_KEYS
    if unknown:
        raise LookPresetError(f"Khoá grade không hợp lệ: {sorted(unknown)}")
    cleaned: dict[str, Any] = {}
    for key, value in grade.items():
        if key in ("hsl",):
            if not isinstance(value, dict):
                raise LookPresetError(f"Giá trị '{key}' phải là dict, nhận {value!r}")
            cleaned[key] = value
        elif key in ("color_preset", "hsl_preset", "lut", "lut_path"):
            if not isinstance(value, str):
                raise LookPresetError(f"Giá trị '{key}' phải là chuỗi, nhận {value!r}")
            cleaned[key] = value.strip()
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise LookPresetError(f"Giá trị '{key}' phải là số, nhận {value!r}")
            cleaned[key] = value
    return cleaned


def load_all(path: Path | None = None) -> list[dict[str, Any]]:
    """Presets sorted newest-first. Entries that fail validation are dropped
    silently rather than raising, so one bad hand-edit doesn't blank the UI."""
    data = _read_json(path or PRESETS_PATH)
    raw = data.get("presets")
    out: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            try:
                name = validate_name(entry.get("name"))
                grade = validate(entry.get("grade"))
            except LookPresetError:
                continue
            out.append({
                "name": name,
                "grade": grade,
                "created_at": entry.get("created_at") or "",
            })
    out.sort(key=lambda p: p["created_at"], reverse=True)
    return out


def _write_all(presets: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"presets": presets}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    os.replace(tmp, path)


def save(name: str, grade: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    """Create or overwrite (same-name entries are replaced, not duplicated)."""
    target = path or PRESETS_PATH
    clean_name = validate_name(name)
    clean_grade = validate(grade)
    entry = {
        "name": clean_name,
        "grade": clean_grade,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    presets = [p for p in load_all(target) if p["name"] != clean_name]
    presets.append(entry)
    _write_all(presets, target)
    return entry


def delete(name: str, path: Path | None = None) -> bool:
    target = path or PRESETS_PATH
    clean_name = validate_name(name)
    presets = load_all(target)
    remaining = [p for p in presets if p["name"] != clean_name]
    if len(remaining) == len(presets):
        return False
    _write_all(remaining, target)
    return True
