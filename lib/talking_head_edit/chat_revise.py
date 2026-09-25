"""Revise as a conversation, not a series of unrelated requests.

Today each `revise` call is independent, which breaks the obvious sequence:
"bỏ card 2" then "thêm lại card đó" — the second turn has no idea what "đó"
refers to. So the last few turns go into the prompt.

Only the last few, and only the request plus a one-line summary of what happened.
The entire point of `revise` is that it costs ~8k tokens instead of ~55k; pasting
old specs back in would spend that advantage on context nobody asked for.

History is `chat_history.jsonl` in the job directory — append-only, same shape as
`events.jsonl`, so a crash mid-turn cannot corrupt what came before.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

HISTORY_TURNS = 5
MAX_MESSAGE_CHARS = 2000
# A turn beyond this has stopped being cheap. Revise sends the whole spine (a
# cut can be asked for anywhere), so a ~4-minute take lands near 25-30k; a
# fresh director pass on the same take measured ~89k.
TOKEN_WARN_THRESHOLD = 40_000


class ChatReviseError(RuntimeError):
    pass


def history_path(job):
    return job.dir / "chat_history.jsonl"


def read_history(job, limit: int | None = None) -> list[dict[str, Any]]:
    path = history_path(job)
    if not path.exists():
        return []
    turns: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                turns.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return turns[-limit:] if limit else turns


def append_turn(job, turn: dict[str, Any]) -> None:
    path = history_path(job)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(turn, ensure_ascii=False) + "\n")


TOP_LEVEL_LABELS = {"cut_remove": "đề xuất cắt", "bgm": "nhạc", "grade": "màu",
                    "cold_open": "hook đầu", "endcard": "endcard"}


def summarise(report: dict[str, Any] | None) -> str:
    """One plain line describing what a turn changed — and what it did not.

    Cuts are only PROPOSED here; `audit` decides afterwards, and the version's
    `outcome` says how many were actually applied.
    """
    if not report:
        return "không áp được"
    parts = []
    for key, label in (("added", "thêm"), ("removed", "bỏ"), ("modified", "sửa")):
        count = int(report.get(key) or 0)
        if count:
            parts.append(f"{label} {count}")
    changed = [TOP_LEVEL_LABELS.get(k, k) for k in report.get("top_level_changed") or []]
    if changed:
        parts.append("đổi " + ", ".join(changed))
    if report.get("options_changed"):
        parts.append("tuỳ chọn " + ", ".join(
            f"{k}={v}" for k, v in report["options_changed"].items()))
    if report.get("not_done"):
        parts.append(f"chưa làm {len(report['not_done'])} ý")
    return ", ".join(parts) or "không có gì đổi"


def history_block(job, limit: int = HISTORY_TURNS) -> str:
    """The recent turns, as prompt text. Empty when there is no history."""
    turns = [t for t in read_history(job, limit * 2) if t.get("applied")][-limit:]
    if not turns:
        return ""
    lines = [f"{index}. «{turn['message']}» → {turn.get('result', '?')}"
             for index, turn in enumerate(turns, start=1)]
    return ("\n\n" + str(limit) + " YÊU CẦU TRƯỚC ĐÓ (để hiểu ngữ cảnh khi khách nói "
            "\"cái đó\", \"như lúc trước\"):\n" + "\n".join(lines))


def chat(job, message: str, options: dict[str, Any] | None = None,
         dry_run: bool = False,
         revise_runner: Callable[..., dict[str, Any]] | None = None
         ) -> dict[str, Any]:
    """One conversational turn.

    `dry_run` returns the patch and the diff WITHOUT creating a version, so a
    request can be inspected before it becomes part of the history. A dry run is
    still recorded, marked `applied: false`, so "I asked and looked but did not
    apply" is visible rather than lost.
    """
    message = (message or "").strip()
    if not message:
        raise ChatReviseError("Yêu cầu rỗng")
    if len(message) > MAX_MESSAGE_CHARS:
        raise ChatReviseError(
            f"Yêu cầu quá dài ({len(message)} ký tự, tối đa {MAX_MESSAGE_CHARS})")

    from lib.talking_head_edit.stages import revise as revise_stage

    execute = revise_runner or revise_stage.run
    state = job.load()
    before_version = int(state.get("current_version", 0))
    merged = {**state.get("options", {}), **(options or {}),
              "revise_instruction": message,
              # The stage renders the history into the prompt; passing it through
              # options keeps the stage's signature unchanged.
              "revise_history": history_block(job)}

    turn: dict[str, Any] = {
        "ts": round(time.time(), 3),
        "message": message,
        "dry_run": dry_run,
        "from_version": before_version,
    }

    try:
        result = execute(job, merged)
    except Exception as exc:  # noqa: BLE001 — reported to the user, and recorded
        turn.update({"applied": False, "error": str(exc)[:400],
                     "result": f"lỗi: {str(exc)[:120]}"})
        append_turn(job, turn)
        raise

    version = int(result.get("version") or before_version + 1)
    report = result.get("report") or {}
    diff = _read_diff(job, version)
    usage = result.get("usage") or {}
    tokens = int(usage.get("total_tokens") or 0)

    if dry_run:
        # Roll the version back: the patch was computed and shown, not adopted.
        _discard_version(job, version, before_version)

    turn.update({
        "applied": not dry_run,
        "to_version": None if dry_run else version,
        "result": summarise(report),
        "options_changed": report.get("options_changed") or {},
        "not_done": report.get("not_done") or [],
        "tokens": tokens,
        "cost_usd": result.get("cost_usd", 0.0),
    })
    append_turn(job, turn)

    if tokens > TOKEN_WARN_THRESHOLD:
        job.emit("warning", "chat",
                 f"Lượt này tốn {tokens} token (ngưỡng {TOKEN_WARN_THRESHOLD}) — "
                 "lịch sử hoặc spec đang phình, revise mất lợi thế rẻ.")

    return {
        "message": message,
        "applied": not dry_run,
        "version": None if dry_run else version,
        "report": report,
        "diff": diff,
        "tokens": tokens,
        "history": read_history(job, HISTORY_TURNS),
    }


def _read_diff(job, version: int) -> Any:
    path = job.dir / f"revise_diff_v{version}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _discard_version(job, version: int, previous: int) -> None:
    """Undo a dry run's side effects on the job state.

    The revise stage always writes a version — it has no dry-run mode of its own —
    so the artefacts are moved aside rather than left to look like an applied
    change. The spec file is KEPT, renamed, so the proposal can still be read.
    """
    from lib.talking_head_edit.versions import restore_options

    state = job.load()
    discarded = [v for v in state.get("versions", []) if int(v.get("version", 0)) == version]
    # Only the keys this turn changed go back — not a snapshot from before the
    # model call, which would undo anything else changed in the meantime.
    state["options"] = restore_options(state, discarded)
    state["current_version"] = previous
    state["versions"] = [v for v in state.get("versions", [])
                         if int(v.get("version", 0)) != version]
    job.save(state)

    spec = job.spec_path(version)
    if spec.exists():
        spec.replace(job.dir / f"spec_dryrun_v{version}.json")
