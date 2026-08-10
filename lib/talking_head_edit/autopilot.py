"""Run the whole chain, fix one known problem, report honestly.

Three hard limits, each with a number behind it:

* **one retry, not three.** A 1080x1920 render measured ~370 s here, so three
  blind passes is eighteen minutes of nothing to look at. One retry plus a clear
  report is the right amount of patience.
* **only codes with a known remedy.** `remedies.py` is the whole vocabulary. An
  unrecognised failure stops the run — a retry that guesses is how you burn
  renders and end up with a stranger video than you started with.
* **a total time ceiling.** Default 45 minutes, so an autopilot left running
  overnight cannot hold the machine.

The report is written whether or not the second pass succeeded. "Tried this,
here is what is still wrong" is useful; silently stopping at the first green-ish
result is not.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from lib.talking_head_edit import remedies
from lib.talking_head_edit.job_store import STAGES
from lib.talking_head_edit.runner import run_job

MAX_RETRY = 1
DEFAULT_TIME_BUDGET_SECONDS = 45 * 60


class AutopilotError(RuntimeError):
    pass


def _verify_issues(job) -> list[dict[str, Any]]:
    state = job.load()
    version = int(state.get("current_version", 0))
    path = job.dir / f"verify_report_v{version}.json"
    if not path.exists():
        return []
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    issues = report.get("issues") or []
    # Tolerate the pre-code report shape so an old job can still be autopiloted.
    return [i if isinstance(i, dict) else {"code": "", "message": str(i)}
            for i in issues]


def _render_count(job) -> int:
    """How many times `render` has actually run, from the append-only event log.

    Counted from events rather than tracked in a variable because the hard cap
    ("never more than two renders") has to hold across a resumed or re-entered
    autopilot too.
    """
    return sum(1 for event in job.read_events()
               if event.get("stage") == "render" and event.get("type") == "stage_end")


def run(job, stages: list[str] | None = None, max_retry: int = MAX_RETRY,
        time_budget: float = DEFAULT_TIME_BUDGET_SECONDS,
        runner: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Run to completion, apply at most `max_retry` known remedies, report."""
    execute = runner or run_job
    started = time.time()
    plan_stages = stages or STAGES
    attempts: list[dict[str, Any]] = []

    job.emit("log", "autopilot",
             f"Autopilot bắt đầu: {', '.join(plan_stages)} "
             f"(tối đa {max_retry} lần sửa, trần {time_budget / 60:.0f} phút)")
    execute(job, stages=plan_stages)
    attempts.append({"pass": 1, "stages": list(plan_stages),
                     "issues": _verify_issues(job)})

    retries = 0
    while retries < max_retry:
        issues = attempts[-1]["issues"]
        if not issues:
            break

        decision = remedies.plan(issues)
        if decision["unhandled"]:
            job.emit("warning", "autopilot",
                     f"Dừng: chưa có remedy cho {', '.join(decision['unhandled'])}. "
                     "Không thử ngẫu nhiên — cần người xem.")
            break
        if not decision["stages"]:
            job.emit("log", "autopilot",
                     "Chỉ còn cảnh báo không sửa được bằng máy "
                     f"({', '.join(decision['ignored'])}) — coi như xong.")
            break

        elapsed = time.time() - started
        if elapsed > time_budget:
            job.emit("warning", "autopilot",
                     f"Dừng: đã chạy {elapsed / 60:.0f} phút, vượt trần "
                     f"{time_budget / 60:.0f} phút.")
            break

        for note in decision["notes"]:
            job.emit("log", "autopilot", f"Remedy — {note}")
        job.emit("log", "autopilot",
                 f"Chạy lại: {', '.join(decision['stages'])}"
                 + (f" với {decision['options']}" if decision["options"] else ""))

        # Cache off for the stages being retried: they produced the bad output,
        # so their cached result is exactly what must not be reused.
        execute(job, options=decision["options"] or None,
                stages=decision["stages"], use_cache=False)
        retries += 1
        attempts.append({"pass": retries + 1, "stages": decision["stages"],
                         "remedies": decision["handled"],
                         "options": decision["options"],
                         "issues": _verify_issues(job)})

    state = job.load()
    remaining = attempts[-1]["issues"]
    report = {
        "job_id": job.job_id,
        "passes": len(attempts),
        "retries": retries,
        "renders": _render_count(job),
        "attempts": attempts,
        "remaining_issues": remaining,
        "resolved": not remaining,
        "seconds": round(time.time() - started, 1),
        "cost_usd": state.get("cost_usd", 0.0),
        "status": state.get("status"),
    }
    (job.dir / "autopilot_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    job.update(autopilot=report)

    if remaining:
        job.emit("warning", "autopilot",
                 f"Xong sau {len(attempts)} lượt, còn {len(remaining)} vấn đề: "
                 + "; ".join(str(i.get("code") or i.get("message")) for i in remaining))
    else:
        job.emit("log", "autopilot",
                 f"Xong sau {len(attempts)} lượt, {retries} lần sửa, "
                 f"{report['renders']} lần render, không còn vấn đề.")
    return report
