"""Revise stage — turn a plain-language instruction into a patch on the spec.

The model sees a compacted view of the current timeline and the user's request,
and answers with a small patch. It never re-emits the whole edit, so anything
the user already approved survives untouched, and the token cost is a fraction
of a fresh director pass.

Every point of the request ends up either in the patch or in `not_done`, and
that — plus which cuts `audit` then blocks — is recorded as the version's
`outcome`, so the user sees what did not happen instead of assuming it did.
"""

from __future__ import annotations

import json
from typing import Any

from lib.talking_head_edit import prompt_registry
from lib.talking_head_edit.director_client import chat_json, default_model, estimate_cost
from lib.talking_head_edit.cut_safety import CUT_LEVELS, normalize_cut_proposals
from lib.talking_head_edit.resources import bgm_table, sfx_table, usable_bgm
from lib.talking_head_edit.stages.audit import clamp_bgm_volume
from lib.talking_head_edit.spec_patch import apply_patch
from lib.talking_head_edit.stages.direct import director_words
from lib.talking_head_edit.stages.resolve import FRAME_PRESETS
from lib.talking_head_edit.versions import add_version

# A cut request can point anywhere in the take, so the model must see all of it.
# Past this the prompt stops being cheap, and the tail is marked as truncated.
SPINE_WORD_LIMIT = 3000


def _number_in(low: float, high: float):
    def check(value: Any) -> float | None:
        if isinstance(value, bool):   # float(True) == 1.0 is not a tempo
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return round(number, 3) if low <= number <= high else None
    return check


# Job options a revise may change, each with its validator (None = invalid).
# Anything else the model puts under "options" is reported, never applied.
REVISABLE_OPTIONS: dict[str, Any] = {
    "frame_preset": lambda v: v if v in FRAME_PRESETS else None,
    "tempo": _number_in(0.9, 1.25),
    "cut_level": lambda v: v if v in CUT_LEVELS else None,
    "cold_open": lambda v: v if isinstance(v, bool) else None,
}


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


def _compact_cuts(spec: dict[str, Any], words: list[dict[str, Any]]) -> str:
    cuts = normalize_cut_proposals(spec.get("cut_remove") or [])
    if not cuts:
        return "(chưa cắt đoạn nào)"
    return "\n".join(
        f'w{a}-{b}: "{" ".join(str(w.get("word", "")).strip() for w in words[a:b + 1])}"'
        for a, b in (c["w"] for c in cuts))


def _spine_excerpt(words: list[dict[str, Any]], limit: int = SPINE_WORD_LIMIT) -> str:
    """Word indices the model can address. Truncated only for very long takes."""
    head = " ".join(f'{i}:{w["word"].strip()}' for i, w in enumerate(words[:limit]))
    return head + ("" if len(words) <= limit else f" … (còn {len(words) - limit} từ)")


def build_revise_prompt(spec: dict[str, Any], words: list[dict[str, Any]],
                        instruction: str, version: str | None = None,
                        history: str = "", options: dict[str, Any] | None = None) -> str:
    current = {key: (options or {}).get(key) for key in REVISABLE_OPTIONS}
    return prompt_registry.render("revise", {
        "instruction": instruction.strip(),
        "events": _compact_events(spec, words),
        # v3+ only; render ignores variables a template does not ask for.
        "cuts": _compact_cuts(spec, words),
        "options": json.dumps(current, ensure_ascii=False),
        "sfx_table": sfx_table(),
        "bgm_table": bgm_table(),
        "spine": _spine_excerpt(words),
        # v2+ only. Empty for a first turn, so a one-off revise renders exactly
        # the prompt it always did.
        "history": history,
    }, version=version)


def apply_option_changes(requested: dict[str, Any], spec: dict[str, Any],
                         options: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validated option changes, plus the bgm the patch chose.

    A revised bgm is written back to the options too: `audit` re-applies the
    options' bgm on every pass, so otherwise "đổi nhạc" would be silently undone
    by whatever the style template had locked in.
    """
    changed: dict[str, Any] = {}
    refused: list[str] = []
    for key, raw in (requested or {}).items():
        check = REVISABLE_OPTIONS.get(key)
        value = check(raw) if check else None
        if value is None:
            refused.append(f"không đổi được tuỳ chọn {key}={raw!r}")
        elif options.get(key) != value:
            changed[key] = value
    if "bgm" in spec:
        bgm = spec.get("bgm")
        if bgm is None:
            changed["bgm"] = False
        elif isinstance(bgm, dict) and bgm.get("name") in usable_bgm():
            volume = _number_in(0.0, 1.0)(bgm.get("volume"))
            changed.update(bgm=True, bgm_name=bgm["name"],
                           bgm_volume=clamp_bgm_volume(volume if volume is not None
                                                       else options.get("bgm_volume")))
        else:
            refused.append(f"không có file nhạc {bgm!r} — giữ nhạc cũ")
        changed = {k: v for k, v in changed.items() if options.get(k) != v}
    return changed, refused


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
                                 history=str(options.get("revise_history") or ""),
                                 options=options)
    (job.dir / "logs").mkdir(parents=True, exist_ok=True)
    (job.dir / "logs" / f"prompt_revise_v{version + 1}.txt").write_text(prompt, encoding="utf-8")

    job.emit("log", "revise", f"Gửi yêu cầu sửa cho {model}: “{instruction[:80]}”")
    patch, usage, _ = chat_json(
        prompt, model=model, temperature=0.15, max_tokens=16000,
        raw_dump=job.dir / "logs" / f"raw_revise_v{version + 1}.txt",
    )

    if patch.get("errors") and not any(patch.get(k) for k in (
            "add", "remove", "modify", "set", "cut_add", "cut_restore", "options")):
        raise RuntimeError(
            "Model từ chối yêu cầu này: " + "; ".join(str(e) for e in patch["errors"])
        )

    new_spec, report = apply_patch(spec, patch)
    bgm_touched = "bgm" in (patch.get("set") or {})
    options_changed, refused = apply_option_changes(
        patch.get("options") or {}, new_spec if bgm_touched else {}, options)
    report["options_changed"] = options_changed
    report["not_done"] = [str(item) for item in patch.get("not_done") or []] + refused
    if report["errors"] and not (report["removed"] or report["added"] or report["modified"]
                                 or report["top_level_changed"] or options_changed):
        raise RuntimeError(
            "Bản vá không áp được, giữ nguyên v%d: %s" % (version, "; ".join(report["errors"]))
        )

    if options_changed.get("cold_open") and not new_spec.get("cold_open"):
        report["not_done"].append("bật hook đầu nhưng chưa chọn câu hook — hãy nói câu nào làm hook")

    cost = estimate_cost(usage, model)
    new_version = add_version(
        job, spec, new_spec, kind="revise", instruction=instruction, report=report,
        options_changed=options_changed, not_done=report["not_done"],
        meta={"model": model, "usage": usage},
        entry={"model": model, "usage": usage, "cost_usd": cost},
        diff_extra={"patch": patch})
    state = job.load()
    state["cost_usd"] = round(float(state.get("cost_usd", 0.0)) + cost, 6)
    job.save(state)

    for error in report["errors"]:
        job.emit("warning", "revise", error)
    for item in report["not_done"]:
        job.emit("warning", "revise", f"Chưa làm: {item}")
    job.emit("log", "revise",
             f"v{new_version}: thêm {report['added']}, bỏ {report['removed']}, "
             f"sửa {report['modified']}, đổi {report['top_level_changed'] or 'không có khoá chung'}"
             + (f", tuỳ chọn {options_changed}" if options_changed else ""))
    return {"version": new_version, "report": report, "usage": usage, "cost_usd": cost}
