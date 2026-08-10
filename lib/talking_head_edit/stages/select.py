"""Stage 3 — choose which take says each thing (`select`).

Runs between `transcribe` and `direct`, and its only output that matters
downstream is `kept_word_ranges`. The director then receives a spine filtered to
those ranges, re-indexed from 0, so the structure and caption prompts do not
change by a single character when several takes are involved. That is the whole
reason this stage exists as its own step rather than as a branch inside `direct`.

The guards are mechanical and deliberately harsh. A model that hallucinates a
range here does not produce a slightly worse edit — it deletes content the
speaker actually said, silently. So a proposal that fails any check falls back to
keeping everything, with a warning. Guessing is not an option this stage has.
"""

from __future__ import annotations

import json
from typing import Any

from lib.talking_head_edit import assembly_config, prompt_registry, spine_build
from lib.talking_head_edit.director_client import (
    DirectorError,
    chat_json,
    default_model,
    estimate_cost,
)

# A chosen take must cover at least this much of the group it belongs to.
# Below it the model has almost certainly mistaken two different passages for
# takes of the same thing and is about to throw one of them away.
MIN_TAKE_COVERAGE = 0.60
# Total kept content below this fraction of the spine means the same mistake at
# whole-video scale.
MIN_TOTAL_COVERAGE = 0.35

class SelectError(RuntimeError):
    pass


def build_select_prompt(words: list[dict[str, Any]], boundaries: list[int],
                        version: str | None = None) -> str:
    return prompt_registry.render("select_take", {
        "source_count": len(boundaries) + 1,
        "n": len(words),
        "last": max(0, len(words) - 1),
        "spine": marked_spine(words, boundaries),
    }, version=version)


def marked_spine(words: list[dict[str, Any]], boundaries: list[int]) -> str:
    """`index:word` pairs with a source-boundary marker inserted.

    The marker is how the model learns there are several takes without learning
    anything about files or seconds — the same contract the director works under.
    """
    marks = {index: number for number, index in enumerate(boundaries, start=2)}
    parts: list[str] = []
    for index, word in enumerate(words):
        if index in marks:
            parts.append(f"\n--- NGUỒN {marks[index]} ---\n")
        parts.append(f'{index}:{word["word"].strip()}')
    return " ".join(parts)


def check_ranges(ranges: Any, word_count: int,
                 takes: list[dict[str, Any]]) -> tuple[list[list[int]], list[str]]:
    """(clean ranges, reasons it is unusable). Empty reasons = accept."""
    problems: list[str] = []
    if not isinstance(ranges, list) or not ranges:
        return [], ["kept_word_ranges rỗng hoặc không phải danh sách"]

    clean: list[list[int]] = []
    for pair in ranges:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            problems.append(f"khoảng không hợp lệ: {pair!r}")
            continue
        try:
            start, end = int(pair[0]), int(pair[1])
        except (TypeError, ValueError):
            problems.append(f"khoảng không phải số: {pair!r}")
            continue
        if end < start:
            start, end = end, start
        if start < 0 or end >= word_count:
            problems.append(f"khoảng [{start},{end}] ra ngoài dãy 0..{word_count - 1}")
            continue
        clean.append([start, end])

    clean.sort()
    for previous, current in zip(clean, clean[1:]):
        if current[0] <= previous[1]:
            problems.append(
                f"khoảng chồng nhau: [{previous[0]},{previous[1]}] và "
                f"[{current[0]},{current[1]}]")

    if not clean:
        problems.append("không còn khoảng nào hợp lệ")
        return [], problems

    total = spine_build.coverage(clean, word_count)
    if total < MIN_TOTAL_COVERAGE:
        problems.append(
            f"chỉ giữ {total:.0%} số từ — quá ít, khả năng cao là cắt mất nội dung thật")

    # Every take group must keep a substantial part of at least one of its takes;
    # otherwise a whole passage vanished rather than a duplicate being dropped.
    groups: dict[str, list[dict[str, Any]]] = {}
    for take in takes:
        groups.setdefault(str(take.get("take_group") or "main"), []).append(take)
    for group, members in groups.items():
        best = 0.0
        for take in members:
            span = int(take["w1"]) - int(take["w0"]) + 1
            if span <= 0:
                continue
            kept = sum(
                max(0, min(int(take["w1"]), end) - max(int(take["w0"]), start) + 1)
                for start, end in clean)
            best = max(best, kept / span)
        if members and best < MIN_TAKE_COVERAGE:
            problems.append(
                f"nhóm take '{group}': bản được giữ nhiều nhất chỉ còn {best:.0%} "
                f"(cần >= {MIN_TAKE_COVERAGE:.0%})")

    return clean, problems


def _write(job, payload: dict[str, Any]) -> dict[str, Any]:
    (job.dir / "selection_v1.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    job.update(selection=payload)
    return payload


def _keep_everything(job, spine: dict[str, Any], mode: str, reason: str,
                     warnings: list[str] | None = None) -> dict[str, Any]:
    words = spine.get("word_timestamps") or []
    last = max(0, len(words) - 1)
    for warning in warnings or []:
        job.emit("warning", "select", warning)
    job.emit("log", "select", f"mode_resolved=sequential — {reason}")
    return _write(job, {
        "mode_requested": mode,
        "mode_resolved": "sequential",
        "reason": reason,
        "segments": [],
        "kept_word_ranges": [[0, last]] if words else [],
        "coverage": 1.0 if words else 0.0,
        "warnings": warnings or [],
    })


def run(job, options: dict[str, Any]) -> dict[str, Any]:
    state = job.load()
    spine = json.loads(job.spine_path.read_text(encoding="utf-8"))
    words = spine.get("word_timestamps") or []
    takes = spine.get("takes") or []
    assembly = state.get("assembly_resolved") or assembly_config.from_job_state(
        state, state.get("project"))
    mode = str(assembly.get("mode", "auto"))

    if len(takes) <= 1:
        return _keep_everything(job, spine, mode, "chỉ có một nguồn có lời nói")
    if mode == "sequential":
        return _keep_everything(job, spine, mode,
                                "assembly.mode=sequential — ghép tuần tự, không chọn take")

    boundaries = spine_build.source_boundaries(spine)
    prompt = build_select_prompt(words, boundaries)
    logs = job.dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "prompt_select.txt").write_text(prompt, encoding="utf-8")

    model = options.get("model") or default_model()
    job.emit("log", "select",
             f"Chọn take giữa {len(takes)} nguồn ({len(words)} từ, model {model})")
    try:
        parsed, usage, _ = chat_json(prompt, model=model, temperature=0.15,
                                     max_tokens=8000,
                                     raw_dump=logs / "raw_select.txt")
    except (DirectorError, json.JSONDecodeError) as exc:
        return _keep_everything(
            job, spine, mode, "call chọn take thất bại — ghép tuần tự cho an toàn",
            [f"select không gọi được model: {str(exc)[:200]}"])

    cost = estimate_cost(usage, model)
    if cost:
        job.update(cost_usd=round(float(state.get("cost_usd", 0.0)) + cost, 6))

    if parsed.get("no_overlap"):
        return _keep_everything(job, spine, mode,
                                "model không thấy đoạn nào trùng nội dung")

    clean, problems = check_ranges(parsed.get("kept_word_ranges"), len(words), takes)
    if problems:
        return _keep_everything(
            job, spine, mode,
            "đề xuất chọn take không qua được kiểm cơ học — ghép tuần tự",
            [f"select bị loại: {p}" for p in problems])

    segments = parsed.get("segments") or []
    payload = {
        "mode_requested": mode,
        "mode_resolved": "best_take",
        "reason": f"chọn giữa {len(takes)} nguồn, {len(segments)} nhóm trùng nội dung",
        "segments": segments,
        "kept_word_ranges": clean,
        "coverage": spine_build.coverage(clean, len(words)),
        "dropped_words": len(words) - sum(e - s + 1 for s, e in clean),
        "usage": usage,
        "cost_usd": cost,
        "warnings": [],
    }
    for segment in segments:
        job.emit("log", "select",
                 f"«{segment.get('content', '?')}» → {segment.get('chosen', {}).get('w')} "
                 f"— {segment.get('reason', '')}")
    job.emit("log", "select",
             f"mode_resolved=best_take — giữ {payload['coverage']:.0%} số từ, "
             f"bỏ {payload['dropped_words']} từ trùng")
    return _write(job, payload)
