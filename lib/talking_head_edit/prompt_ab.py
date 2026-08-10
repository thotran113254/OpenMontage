"""Compare two prompt versions on the same spine.

Two decisions define this file.

**No render.** Six minutes twice is useless for an iteration loop, and the render
is downstream of everything a prompt controls anyway. The comparison happens at
the `spec` + `audit` level, where `audit` already measures cut quality, resource
validity, caption coverage and card count.

**The verdict is mechanical, not judged by a model.** A model scoring two of its
own prompts is a loop with no independent signal in it. So the rules are stated
in code and in this order — card count against the brief first, then coverage,
then caption length, then the audit's own score — because that is the order in
which those failures actually ruin a video.

Nothing here touches the job's own `spec_vN.json`: both branches write into
`ab/<stamp>/`, so an experiment cannot damage a build someone approved.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from lib.talking_head_edit import prompt_registry
from lib.talking_head_edit.director_client import default_model, estimate_cost
from lib.talking_head_edit.stages.audit import audit_resources, measure_quality
from lib.talking_head_edit.stages.direct import director_words, load_style_profile
from lib.talking_head_edit.cut_verifier import verify_cuts

# Rough token estimate for the cost preview shown BEFORE anyone spends money.
# Vietnamese runs about 2.2 characters per token through this tokeniser family;
# a preview that is 20% off is fine, a surprise bill is not.
CHARS_PER_TOKEN = 2.2


class AbError(RuntimeError):
    pass


def card_target(card_plan: str) -> int | None:
    """How many cards the brief asked for, if it said a number.

    "Đúng 4 card" is the common shape. No number means the director chooses, and
    then card count is not a thing to score.
    """
    match = re.search(r"\b(\d{1,2})\b", card_plan or "")
    if not match:
        return None
    count = int(match.group(1))
    return count if 1 <= count <= 12 else None


def estimate_tokens(prompt: str) -> int:
    return int(len(prompt) / CHARS_PER_TOKEN)


def preview_cost(job, options: dict[str, Any], versions: tuple[str, str]) -> dict[str, Any]:
    """What an A/B run will cost, before running it.

    Shown in the UI first: the whole objection to A/B is that it doubles a
    director call, so the number has to be visible rather than discovered.
    """
    from lib.talking_head_edit.prompt_captions import chunk_ranges
    from lib.talking_head_edit.prompt_structure import build_structure_prompt

    words, boundaries = director_words(job)
    profile = load_style_profile(options.get("style_profile"))
    chunks = chunk_ranges(words)

    per_branch = 0
    for version in versions:
        structure = build_structure_prompt(
            words, options, profile, source_boundaries=boundaries, version=version)
        per_branch = max(per_branch, estimate_tokens(structure))

    from lib.talking_head_edit.prompt_captions import build_caption_prompt

    caption_tokens = sum(
        estimate_tokens(build_caption_prompt(words, start, end))
        for start, end in chunks)
    total_in = (per_branch + caption_tokens) * 2
    return {
        "versions": list(versions),
        "words": len(words),
        "calls": (1 + len(chunks)) * 2,
        "estimated_tokens_in": total_in,
        "estimated_cost_usd": estimate_cost({"prompt_tokens": total_in}, ""),
        "note": "Ước lượng; chi phí thật ghi vào kết quả sau khi chạy.",
    }


def _branch_spec(job, options: dict[str, Any], version: str,
                 words: list[dict[str, Any]], boundaries: list[int],
                 out_dir: Path, chat: Callable[..., Any] | None = None
                 ) -> dict[str, Any]:
    """Run the structure call for ONE prompt version and audit the result.

    Captions are shared between branches deliberately: this compares the
    structure prompt, and paying for the long caption pass twice to compare
    something neither version changed is waste.
    """
    from lib.talking_head_edit.director_client import chat_json
    from lib.talking_head_edit.prompt_structure import build_structure_prompt

    call = chat or chat_json
    profile = load_style_profile(options.get("style_profile"))
    prompt = build_structure_prompt(words, options, profile,
                                   source_boundaries=boundaries, version=version)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"prompt_structure_{version}.txt").write_text(prompt, encoding="utf-8")

    structure, usage, _ = call(
        prompt, model=options.get("model") or default_model(), temperature=0.25,
        raw_dump=out_dir / f"raw_structure_{version}.txt")

    events: list[dict[str, Any]] = [
        {**card, "type": "card"} for card in (structure.get("cards") or [])
    ]
    events.extend(structure.get("events") or [])
    spec = {
        "cut_remove": structure.get("cut_remove") or [],
        "grade": structure.get("grade") or {},
        "bgm": structure.get("bgm"),
        "cold_open": structure.get("cold_open"),
        "endcard": structure.get("endcard") or {},
        "events": events,
    }
    (out_dir / f"spec_{version}.json").write_text(
        json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")

    proposed = list(spec["cut_remove"])
    accepted, decisions, verifier_usage = verify_cuts(
        proposed, words, spec.get("cold_open"),
        model=options.get("verifier_model") or options.get("model"))
    audited, removed_resources = audit_resources({**spec, "cut_remove": accepted},
                                                len(words))
    quality = measure_quality(audited, words)

    model = options.get("model") or default_model()
    cost = (estimate_cost(usage, model) + estimate_cost(verifier_usage, model))
    return {
        "version": version,
        "cards": quality["card_count"],
        "caption_coverage": quality["caption_coverage"],
        # `measure_quality` returns a count here and a list of texts there —
        # keep both as counts for the comparison table.
        "captions_over_9w": int(quality["captions_over_9_words"]),
        "keyword_in_card": len(quality["keyword_in_card"]),
        "events": quality["event_count"],
        "cuts_proposed": len(proposed),
        "cuts_rejected_by_verifier": len(proposed) - len(accepted),
        "removed_resources": len(removed_resources),
        "tokens": {"in": int(usage.get("prompt_tokens", 0) or 0),
                   "out": int(usage.get("completion_tokens", 0) or 0)},
        "cost_usd": round(cost, 6),
        "spec_path": str(out_dir / f"spec_{version}.json"),
        "cut_decisions": decisions,
    }


def verdict(a: dict[str, Any], b: dict[str, Any],
            card_goal: int | None = None) -> dict[str, Any]:
    """Which branch wins, and the one reason it won.

    Rules in priority order, each with a plain reason attached, because "B scored
    8.4 and A scored 8.1" tells nobody anything they can act on:

    1. card count against an explicit brief — a missing card is a missing section
    2. caption coverage — uncovered speech means silent stretches with no subtitle
    3. captions over 9 words — they overflow the pill and get cut off on screen
    4. keywords hidden behind a card — the layer order makes them invisible
    5. fewer cuts thrown out by the verifier — a prompt proposing safer cuts
    """
    def gap(field: str) -> float:
        return float(a.get(field, 0) or 0) - float(b.get(field, 0) or 0)

    if card_goal is not None:
        miss_a, miss_b = abs(a["cards"] - card_goal), abs(b["cards"] - card_goal)
        if miss_a != miss_b:
            winner = "a" if miss_a < miss_b else "b"
            loser = b if winner == "a" else a
            return {"winner": winner,
                    "reason": f"{winner} đúng số card theo brief ({card_goal}); "
                              f"bản kia ra {loser['cards']}"}

    if abs(gap("caption_coverage")) > 0.02:
        winner = "a" if gap("caption_coverage") > 0 else "b"
        return {"winner": winner,
                "reason": f"{winner} phủ caption cao hơn "
                          f"({a['caption_coverage']:.0%} vs {b['caption_coverage']:.0%})"}

    if a["captions_over_9w"] != b["captions_over_9w"]:
        winner = "a" if a["captions_over_9w"] < b["captions_over_9w"] else "b"
        return {"winner": winner,
                "reason": f"{winner} ít caption quá dài hơn "
                          f"({a['captions_over_9w']} vs {b['captions_over_9w']})"}

    if a["keyword_in_card"] != b["keyword_in_card"]:
        winner = "a" if a["keyword_in_card"] < b["keyword_in_card"] else "b"
        return {"winner": winner,
                "reason": f"{winner} ít keyword bị card che hơn "
                          f"({a['keyword_in_card']} vs {b['keyword_in_card']})"}

    if a["cuts_rejected_by_verifier"] != b["cuts_rejected_by_verifier"]:
        winner = "a" if a["cuts_rejected_by_verifier"] < b["cuts_rejected_by_verifier"] else "b"
        return {"winner": winner,
                "reason": f"{winner} đề xuất cut an toàn hơn, verifier loại ít hơn "
                          f"({a['cuts_rejected_by_verifier']} vs "
                          f"{b['cuts_rejected_by_verifier']})"}

    return {"winner": "tie",
            "reason": "Hai bản không khác nhau ở bất kỳ phép đo cơ học nào — "
                      "phải xem bằng mắt để quyết."}


def run(job, version_a: str, version_b: str, options: dict[str, Any] | None = None,
        chat: Callable[..., Any] | None = None,
        on_log: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Run both branches and return the comparison table."""
    if version_a == version_b:
        raise AbError("Hai version giống nhau — không có gì để so.")
    for version in (version_a, version_b):
        if not prompt_registry.template_path("structure", version).exists():
            raise AbError(f"Không có structure.{version}")

    state = job.load()
    options = {**state.get("options", {}), **(options or {})}
    words, boundaries = director_words(job)
    if not words:
        raise AbError("Job chưa có spine — chạy transcribe trước khi A/B.")

    stamp = time.strftime("%y%m%d-%H%M%S")
    out_dir = job.dir / "ab" / stamp

    def log(message: str) -> None:
        if on_log:
            on_log(message)

    log(f"A/B trên {len(words)} từ: structure.{version_a} vs structure.{version_b}")
    branches: dict[str, dict[str, Any]] = {}
    for label, version in (("a", version_a), ("b", version_b)):
        branches[label] = _branch_spec(job, options, version, words, boundaries,
                                      out_dir, chat)
        log(f"{label} ({version}): {branches[label]['cards']} card, "
            f"caption phủ {branches[label]['caption_coverage']:.0%}, "
            f"{branches[label]['tokens']['in']} token vào")

    goal = card_target(str(options.get("card_plan") or ""))
    result = {
        "job_id": job.job_id,
        "created_at": time.time(),
        "words": len(words),
        "card_goal": goal,
        "a": branches["a"],
        "b": branches["b"],
        "verdict": verdict(branches["a"], branches["b"], goal),
        "total_cost_usd": round(branches["a"]["cost_usd"] + branches["b"]["cost_usd"], 6),
        "dir": str(out_dir),
    }
    (out_dir / "comparison.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"Kết quả: {result['verdict']['winner']} — {result['verdict']['reason']}")
    return result
