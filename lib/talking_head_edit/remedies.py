"""What autopilot is allowed to do about a verify failure — and nothing else.

The table is deliberately small. Every entry is a fix that has been diagnosed on
this pipeline before, with a note saying why it works; a code that is not in here
means autopilot **stops and reports**, because guessing at a remedy is how a
retry loop burns renders and produces a stranger video than the one it started
with.

Creative problems are absent on purpose. "The card is in the wrong place" has no
mechanical remedy, and letting a model iterate freely on it is exactly the loop
this design refuses to build. Those go to a human, or to chat revise.
"""

from __future__ import annotations

from typing import Any

# stage lists are what gets re-run — never the whole chain, so a remedy cannot
# undo work that was already correct.
REMEDY: dict[str, dict[str, Any]] = {
    "stutter": {
        "note": "Máy tranh CPU lúc render nên renderer phục vụ lại frame cũ. "
                "Nửa concurrency là cách đã kiểm chứng.",
        "options": {"render_concurrency": "half"},
        "stages": ["render", "verify"],
    },
    "bgm_missing": {
        "note": "Nhạc khai báo nhưng không nghe thấy trong khoảng ngắt lời — "
                "props nhạc phải được dựng lại rồi render lại.",
        "stages": ["resolve", "render", "verify"],
    },
    "av_drift": {
        "note": "Hình và tiếng lệch nhau; cắt lại từ span rồi render lại.",
        "stages": ["resolve", "render", "verify"],
    },
    "loudness_off": {
        "note": "LUFS ngoài khoảng — chuỗi audio tổng phải chạy lại.",
        "stages": ["resolve", "render", "verify"],
    },
    "duration_mismatch": {
        "note": "File dài/ngắn hơn props — render lại từ props hiện tại.",
        "stages": ["render", "verify"],
    },
    "broll_missing": {
        "note": "Lớp b-roll không xuất hiện trong hình; dựng lại clip rồi render lại.",
        "stages": ["resolve", "render", "verify"],
    },
}

# Codes that are information, not failure: they say a measurement could not be
# taken, or point at something for a human to look at. Neither is improved by a
# retry.
IGNORED_CODES = {"unmeasured", "seam_suspect"}

# Real defects that deliberately have NO automatic fix. Listed rather than left
# to fall through as "unknown" so the choice is visible and reviewable — and so a
# newly added verify code cannot quietly inherit "stop and report" without anyone
# deciding that is right.
NEEDS_HUMAN: dict[str, str] = {
    "black_frame": "Frame đen thường là asset thiếu hoặc props sai — retry sẽ cho "
                   "ra đúng frame đen đó lần nữa. Cần người xem frame.",
    "sfx_missing": "Thiếu file sfx trên đĩa. Không có cách máy nào tạo ra file đó; "
                   "phải thêm file vào remotion-composer/public/ rồi chạy lại.",
}


def remedy_for(code: str) -> dict[str, Any] | None:
    return REMEDY.get(code)


def plan(issues: list[dict[str, Any]]) -> dict[str, Any]:
    """What to do about a set of verify issues.

    Returns the union of stages to re-run, the options to force, the notes to
    show, and — importantly — the codes with no remedy. A caller that finds
    `unhandled` non-empty must stop rather than run the partial plan: fixing two
    of three problems and declaring success is worse than reporting all three.
    """
    stages: list[str] = []
    options: dict[str, Any] = {}
    notes: list[str] = []
    handled: list[str] = []
    unhandled: list[str] = []
    ignored: list[str] = []

    for finding in issues or []:
        code = str(finding.get("code") or "")
        if code in IGNORED_CODES:
            ignored.append(code)
            continue
        if code in NEEDS_HUMAN:
            unhandled.append(code)
            notes.append(f"{code}: {NEEDS_HUMAN[code]}")
            continue
        entry = remedy_for(code)
        if not entry:
            unhandled.append(code or "(không có code)")
            continue
        handled.append(code)
        notes.append(f"{code}: {entry['note']}")
        options.update(entry.get("options") or {})
        for stage in entry.get("stages") or []:
            if stage not in stages:
                stages.append(stage)

    # Keep pipeline order, not discovery order: resolve must precede render.
    from lib.talking_head_edit.job_store import STAGES

    stages.sort(key=lambda name: STAGES.index(name) if name in STAGES else 99)
    return {"stages": stages, "options": options, "notes": notes,
            "handled": handled, "unhandled": unhandled, "ignored": ignored,
            "actionable": bool(stages) and not unhandled}
