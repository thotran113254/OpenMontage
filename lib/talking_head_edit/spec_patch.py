"""Apply a revision patch to an existing spec.

Patching instead of regenerating is the whole point of the revise loop: a
regenerated timeline throws away every choice the user already approved, so
"bỏ card 2" would silently reshuffle the other three cards too.

Events have no stable id — the director invents them fresh each call — so a
patch addresses them by (type, anchor word). An address that matches nothing,
or matches more than one event, is refused rather than guessed at: applying a
"modify" to the wrong caption is worse than reporting that the instruction was
ambiguous.
"""

from __future__ import annotations

from typing import Any

ANCHOR_FIELDS = ("w0", "atWord")


def anchor_of(event: dict[str, Any]) -> int | None:
    for field in ANCHOR_FIELDS:
        if field in event:
            try:
                return int(event[field])
            except (TypeError, ValueError):
                return None
    return None


def _matches(event: dict[str, Any], target: dict[str, Any]) -> bool:
    if target.get("type") and event.get("type") != target["type"]:
        return False
    wanted = target.get("w0", target.get("atWord"))
    if wanted is None:
        return False
    return anchor_of(event) == int(wanted)


def _find(events: list[dict[str, Any]], target: dict[str, Any]) -> tuple[list[int], str]:
    hits = [index for index, event in enumerate(events) if _matches(event, target)]
    if not hits:
        return [], f"không tìm thấy event {target.get('type')} tại từ {target.get('w0', target.get('atWord'))}"
    if len(hits) > 1:
        return hits, (
            f"có {len(hits)} event {target.get('type')} cùng neo tại từ "
            f"{target.get('w0', target.get('atWord'))} — không rõ sửa cái nào"
        )
    return hits, ""


def apply_patch(spec: dict[str, Any], patch: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (new spec, report). The input spec is never mutated."""
    events = [dict(event) for event in spec.get("events") or []]
    report: dict[str, Any] = {"removed": 0, "added": 0, "modified": 0,
                              "top_level_changed": [], "errors": []}

    # removals first, so an add can reuse the same anchor in one patch
    for target in patch.get("remove") or []:
        hits, error = _find(events, target)
        if error:
            report["errors"].append(f"remove: {error}")
            continue
        events.pop(hits[0])
        report["removed"] += 1

    for change in patch.get("modify") or []:
        target = change.get("match") or {}
        updates = change.get("set") or {}
        hits, error = _find(events, target)
        if error:
            report["errors"].append(f"modify: {error}")
            continue
        if not updates:
            report["errors"].append("modify: không có trường nào để sửa")
            continue
        events[hits[0]].update(updates)
        report["modified"] += 1

    for event in patch.get("add") or []:
        if not event.get("type") or anchor_of(event) is None:
            report["errors"].append(f"add: event thiếu type hoặc chỉ số từ ({event.get('type')})")
            continue
        events.append(dict(event))
        report["added"] += 1

    new_spec = {**spec, "events": events}
    for key, value in (patch.get("set") or {}).items():
        if key in ("cold_open", "endcard", "bgm", "grade", "cut_remove"):
            new_spec[key] = value
            report["top_level_changed"].append(key)
        else:
            report["errors"].append(f"set: không cho phép sửa khoá '{key}'")

    return new_spec, report


def diff_specs(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Human-readable difference between two specs, addressed by anchor."""
    def key(event: dict[str, Any]) -> tuple[Any, Any]:
        return (event.get("type"), anchor_of(event))

    before_map = {key(e): e for e in before.get("events") or []}
    after_map = {key(e): e for e in after.get("events") or []}

    added = [after_map[k] for k in after_map.keys() - before_map.keys()]
    removed = [before_map[k] for k in before_map.keys() - after_map.keys()]
    changed = [
        {"key": list(k), "before": before_map[k], "after": after_map[k]}
        for k in before_map.keys() & after_map.keys()
        if before_map[k] != after_map[k]
    ]
    top_level = [
        field for field in ("cold_open", "endcard", "bgm", "grade", "cut_remove")
        if before.get(field) != after.get(field)
    ]
    return {"added": added, "removed": removed, "changed": changed, "top_level": top_level}
