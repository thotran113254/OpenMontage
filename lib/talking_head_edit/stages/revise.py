"""Revise stage — turn a plain-language instruction into a patch on the spec.

The model sees a compacted view of the current timeline and the user's request,
and answers with a small patch. It never re-emits the whole edit, so anything
the user already approved survives untouched, and the token cost is a fraction
of a fresh director pass.

The word spine is NOT re-sent in full: only the words around the parts being
discussed, plus the existing event list, which already carries the anchors.
"""

from __future__ import annotations

import json
from typing import Any

from lib.talking_head_edit import prompt_registry
from lib.talking_head_edit.director_client import chat_json, default_model, estimate_cost
from lib.talking_head_edit.resources import bgm_table, sfx_table
from lib.talking_head_edit.spec_patch import apply_patch, diff_specs
from lib.talking_head_edit.stages.direct import director_words

CONTEXT_WORDS_PER_EVENT = 6


def _compact_events(spec: dict[str, Any], words: list[dict[str, Any]]) -> str:
    """One line per event: type, anchor, and just enough content to identify it."""
    lines: list[str] = []
    for event in spec.get("events") or []:
        kind = event.get("type")
        anchor = event.get("w0", event.get("atWord"))
        if kind == "caption":
            lines.append(f'caption w{anchor}-{event.get("w1")}: "{event.get("text", "")}"')
        elif kind == "keyword":
            lines.append(f'keyword w{anchor}: "{event.get("text", "")}" ({event.get("color", "")})')
        elif kind == "card":
            lines.append(
                f'card w{anchor}-{event.get("w1")}: badge {event.get("badge")} '
                f'"{event.get("title", "")}" bullets={event.get("bullets", [])}'
            )
        elif kind == "sfx":
            lines.append(f'sfx w{anchor}: {event.get("name")}')
        else:
            lines.append(f"{kind} w{anchor}")
    return "\n".join(lines)


def _spine_excerpt(words: list[dict[str, Any]], limit: int = 400) -> str:
    """Word indices the model can address. Truncated for very long takes."""
    head = " ".join(f'{i}:{w["word"].strip()}' for i, w in enumerate(words[:limit]))
    return head + ("" if len(words) <= limit else f" … (còn {len(words) - limit} từ)")


def build_revise_prompt(spec: dict[str, Any], words: list[dict[str, Any]],
                        instruction: str, version: str | None = None,
                        history: str = "") -> str:
    return prompt_registry.render("revise", {
        "instruction": instruction.strip(),
        "events": _compact_events(spec, words),
        "sfx_table": sfx_table(),
        "bgm_table": bgm_table(),
        "spine": _spine_excerpt(words),
        # v2+ only. Empty for a first turn, so a one-off revise renders exactly
        # the prompt it always did.
        "history": history,
    }, version=version)


def run(job, options: dict[str, Any]) -> dict[str, Any]:
    instruction = (options.get("revise_instruction") or "").strip()
    if not instruction:
        raise ValueError("Chưa có yêu cầu sửa (revise_instruction).")

    state = job.load()
    version = int(state["current_version"])
    spec = json.loads(job.spec_path(version).read_text(encoding="utf-8"))
    # The filtered spine, matching the indices the spec's events actually use.
    words, _ = director_words(job)
    model = options.get("model") or default_model()

    prompt = build_revise_prompt(spec, words, instruction,
                                 history=str(options.get("revise_history") or ""))
    (job.dir / "logs").mkdir(parents=True, exist_ok=True)
    (job.dir / "logs" / f"prompt_revise_v{version + 1}.txt").write_text(prompt, encoding="utf-8")

    job.emit("log", "revise", f"Gửi yêu cầu sửa cho {model}: “{instruction[:80]}”")
    patch, usage, _ = chat_json(
        prompt, model=model, temperature=0.15, max_tokens=16000,
        raw_dump=job.dir / "logs" / f"raw_revise_v{version + 1}.txt",
    )

    if patch.get("errors") and not any(patch.get(k) for k in ("add", "remove", "modify", "set")):
        raise RuntimeError(
            "Model từ chối yêu cầu này: " + "; ".join(str(e) for e in patch["errors"])
        )

    new_spec, report = apply_patch(spec, patch)
    if report["errors"] and not (report["removed"] or report["added"] or report["modified"]
                                 or report["top_level_changed"]):
        raise RuntimeError(
            "Bản vá không áp được, giữ nguyên v%d: %s" % (version, "; ".join(report["errors"]))
        )

    new_version = version + 1
    new_spec["_meta"] = {
        **(spec.get("_meta") or {}),
        "version": new_version, "kind": "revise", "model": model,
        "instruction": instruction, "usage": usage, "patch_report": report,
    }
    job.spec_path(new_version).write_text(
        json.dumps(new_spec, indent=2, ensure_ascii=False), encoding="utf-8")

    difference = diff_specs(spec, new_spec)
    (job.dir / f"revise_diff_v{new_version}.json").write_text(
        json.dumps({"instruction": instruction, "patch": patch, "report": report,
                    "diff": difference}, indent=2, ensure_ascii=False), encoding="utf-8")

    cost = estimate_cost(usage, model)
    state = job.load()
    state["current_version"] = new_version
    state["cost_usd"] = round(float(state.get("cost_usd", 0.0)) + cost, 6)
    state.setdefault("versions", []).append({
        "version": new_version, "kind": "revise", "model": model,
        "instruction": instruction, "usage": usage, "cost_usd": cost,
        "changes": {k: report[k] for k in ("added", "removed", "modified", "top_level_changed")},
    })
    job.save(state)

    for error in report["errors"]:
        job.emit("warning", "revise", error)
    job.emit("log", "revise",
             f"v{new_version}: thêm {report['added']}, bỏ {report['removed']}, "
             f"sửa {report['modified']}, đổi {report['top_level_changed'] or 'không có khoá chung'}")
    return {"version": new_version, "report": report, "usage": usage, "cost_usd": cost}
