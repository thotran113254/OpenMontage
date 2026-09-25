"""New spec versions: an AI revise, the user's own cuts, and rollback.

All three go through `add_version`, so every version records the same things:
the spec file, a diff, which job options it changed and what they were before
(`options_previous`), and an `outcome` the UI shows. Keeping the previous
values is what lets a rollback — or a discarded dry run — put the options back
exactly, instead of leaving a revised frame or music behind a restored spec.
"""

from __future__ import annotations

import json
from typing import Any

from lib.talking_head_edit.cut_safety import USER_CUT, current_proposals
from lib.talking_head_edit.spec_patch import NEW_CUT, apply_patch, diff_specs


def current_spec(job) -> tuple[int, dict[str, Any]]:
    version = int(job.load()["current_version"])
    return version, json.loads(job.spec_path(version).read_text(encoding="utf-8"))


def add_version(job, spec_before: dict[str, Any], new_spec: dict[str, Any], *, kind: str,
                instruction: str, report: dict[str, Any] | None = None,
                options_changed: dict[str, Any] | None = None,
                not_done: list[str] | None = None, meta: dict[str, Any] | None = None,
                entry: dict[str, Any] | None = None,
                diff_extra: dict[str, Any] | None = None) -> int:
    """Write spec v(N+1), make it current, apply its option changes. Returns N+1."""
    state = job.load()
    number = 1 + max([int(state.get("current_version", 0))]
                     + [int(v.get("version", 0)) for v in state.get("versions") or []])
    options = dict(state.get("options") or {})
    changed = dict(options_changed or {})
    report = report or {}

    new_spec = {**new_spec, "_meta": {**(spec_before.get("_meta") or {}), **(meta or {}),
                                      "version": number, "kind": kind,
                                      "instruction": instruction, "patch_report": report}}
    job.spec_path(number).write_text(json.dumps(new_spec, indent=2, ensure_ascii=False),
                                     encoding="utf-8")
    (job.dir / f"revise_diff_v{number}.json").write_text(json.dumps(
        {"instruction": instruction, "report": report,
         "diff": diff_specs(spec_before, new_spec), **(diff_extra or {})},
        indent=2, ensure_ascii=False), encoding="utf-8")

    state["current_version"] = number
    state["options"] = {**options, **changed}
    state.setdefault("versions", []).append({
        "version": number, "kind": kind, "instruction": instruction,
        "changes": {k: report.get(k) for k in ("added", "removed", "modified",
                                               "top_level_changed") if k in report},
        "outcome": {"options_changed": changed, "not_done": list(not_done or [])},
        "options_previous": {key: options.get(key) for key in changed},
        **(entry or {}),
    })
    job.save(state)
    return number


def restore_options(state: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
    """The job options with the given versions' option changes undone, newest first."""
    options = dict(state.get("options") or {})
    for entry in sorted(entries, key=lambda e: int(e.get("version", 0)), reverse=True):
        for key, value in (entry.get("options_previous") or {}).items():
            if value is None:
                options.pop(key, None)
            else:
                options[key] = value
    return options


def apply_user_cuts(job, cut: list[list[int]], keep: list[list[int]]) -> dict[str, Any]:
    """The user's own Cắt/Giữ ranges, applied without asking a model.

    A range the user marked is a decision: it is stored as `nguon: khach`, which
    `audit` applies without the verifier or the lexicon gate. "Giữ" takes every
    overlapping proposal out, so the range stays whatever the director thought.
    """
    if not cut and not keep:
        raise ValueError("Chưa chọn đoạn nào để cắt hoặc giữ.")
    base, spec = current_spec(job)
    patch = {"cut_add": [{"w": list(span), "ly_do": "khach_danh_dau", "nguon": USER_CUT}
                         for span in cut],
             "cut_restore": [list(span) for span in keep]}
    new_spec, report = apply_patch(spec, patch, trust_user_cuts=True)
    parts = ([f"cắt {len(cut)} đoạn"] if cut else []) + ([f"giữ {len(keep)} đoạn"] if keep else [])
    number = add_version(job, spec, new_spec, kind="manual_cuts",
                         instruction="Tự đánh dấu: " + ", ".join(parts), report=report,
                         entry={"based_on": base})
    job.emit("log", "edit", f"v{number}: {', '.join(parts)} theo đánh dấu của bạn")
    return {"version": number, "report": report}


def rollback_to(job, target: int) -> dict[str, Any]:
    """Make version `target` current again, as a new version, options included.

    The spec is copied forward (history stays append-only) and every option a
    later version changed is put back. The cut file is NOT copied: `src.mp4` is
    one file per job, so the caller must re-run resolve for the preview to match.
    """
    if not job.spec_path(target).exists():
        raise FileNotFoundError(f"Không có phiên bản v{target}")
    state = job.load()
    later = [e for e in state.get("versions") or [] if int(e.get("version", 0)) > target]
    restored = restore_options(state, later)
    current = state.get("options") or {}
    changed = {key: restored.get(key) for key in set(current) | set(restored)
               if current.get(key) != restored.get(key)}
    _, spec_now = current_spec(job)
    spec = json.loads(job.spec_path(target).read_text(encoding="utf-8"))
    spec["cut_proposed"] = [{k: v for k, v in p.items() if k != NEW_CUT}
                            for p in current_proposals(spec)]
    number = add_version(job, spec_now, spec, kind="rollback",
                         instruction=f"quay lại v{target}", options_changed=changed,
                         entry={"rolled_back_from": target})
    job.emit("log", "edit", f"Quay lại v{target} (tạo v{number})"
             + (f", khôi phục tuỳ chọn {sorted(changed)}" if changed else ""))
    return {"version": number, "reverted_to": target, "options_changed": changed}
