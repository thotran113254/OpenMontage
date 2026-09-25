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

from lib.talking_head_edit.cut_safety import (
    USER_CUT, count_malformed, current_proposals, normalize_cut_proposals,
)

# Marks the cuts THIS patch asked for, so the version's outcome can say what
# happened to them rather than to every cut the video has ever had.
NEW_CUT = "moi"

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


def apply_patch(spec: dict[str, Any], patch: dict[str, Any],
                trust_user_cuts: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (new spec, report). The input spec is never mutated.

    `trust_user_cuts` is for the user's own marked ranges only. A model's patch
    may not label its cuts as the user's: that label skips the verifier.
    """
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
            if key == "cut_remove":   # a full replacement, proposals included
                new_spec["cut_proposed"] = [{**entry, NEW_CUT: True} for entry in
                                            _as_ai(normalize_cut_proposals(value or []))]
        else:
            report["errors"].append(f"set: không cho phép sửa khoá '{key}'")

    if patch.get("cut_add") or patch.get("cut_restore"):
        for key in ("cut_add", "cut_restore"):
            if skipped := count_malformed(patch.get(key) or []):
                report["errors"].append(f"{key}: bỏ {skipped} đoạn sai dạng")
        added = normalize_cut_proposals(patch.get("cut_add") or [])
        new_spec["cut_proposed"], report["cuts_added"], report["cuts_restored"] = _patch_cuts(
            new_spec, added if trust_user_cuts else _as_ai(added), patch.get("cut_restore") or [])
        if "cut_remove" not in report["top_level_changed"]:
            report["top_level_changed"].append("cut_remove")

    return new_spec, report


def _as_ai(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**entry, "nguon": "ai"} for entry in entries]


def _patch_cuts(spec: dict[str, Any], add: list[dict[str, Any]],
                restore: list[Any]) -> tuple[list[dict[str, Any]], int, int]:
    """Add cuts to, and take ranges back out of, the proposals `audit` decides on.

    Adds and restores are relative to what is already there — a revise that asks
    for one more cut must not wipe the cuts the user already approved. A user
    cut on a range the director already proposed upgrades that proposal.
    """
    proposals = current_proposals(spec)
    keep_ranges = [entry["w"] for entry in normalize_cut_proposals(restore)]

    def overlaps(span: list[int]) -> bool:
        return any(not (span[1] < a or span[0] > b) for a, b in keep_ranges)

    kept = [{k: v for k, v in p.items() if k != NEW_CUT}
            for p in proposals if not overlaps(p["w"])]
    restored = len(proposals) - len(kept)
    by_span = {tuple(p["w"]): p for p in kept}
    added = 0
    for entry in add:
        existing = by_span.get(tuple(entry["w"]))
        if existing is None:
            entry = {**entry, NEW_CUT: True}
            kept.append(entry)
            by_span[tuple(entry["w"])] = entry
            added += 1
        elif entry.get("nguon") == USER_CUT:
            existing.update({"nguon": USER_CUT, NEW_CUT: True,
                             "ly_do": entry.get("ly_do", existing.get("ly_do"))})
            added += 1
    return sorted(kept, key=lambda p: p["w"]), added, restored


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
