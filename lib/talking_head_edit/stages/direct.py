"""Stage 3 — the director calls (the only stochastic stage).

Two kinds of call, run concurrently:

  structure   one call for cards / keywords / punch-ins / sfx / cold-open /
              endcard / bgm / grade / cut candidates
  captions    one call per word chunk, each covering a bounded range

They were one call originally. Measured result of that: the model burned its
output budget on 54 captions and returned zero cards on a brief that asked for
four. Separating them keeps each output short enough to finish, and lets a
caption chunk be retried without redoing the structure.

Everything is text-only and word-anchored — no video is uploaded, and no call
is allowed to emit a second.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from lib.talking_head_edit import prompt_registry, spine_build
from lib.talking_head_edit.director_client import chat_json, default_model, estimate_cost
from lib.talking_head_edit.job_store import REPO_ROOT
from lib.talking_head_edit.prompt_captions import build_caption_prompt, chunk_ranges
from lib.talking_head_edit.prompt_structure import build_structure_prompt

MAX_PARALLEL_CALLS = 4


def director_words(job) -> tuple[list[dict[str, Any]], list[int]]:
    """(words the director sees, source-boundary indices in that view).

    The director's word 0 is the first word that SURVIVED `select`, not the first
    word of the spine. Each kept word carries `_orig_index` so `resolve` can walk
    back to the real word — and therefore to the real second in the real file.
    Dropping that link is the one mistake in this pipeline that cannot be
    recovered from downstream, which is why it lives in `spine_build` with its
    own round-trip test rather than being open-coded here.
    """
    spine = json.loads(job.spine_path.read_text(encoding="utf-8"))
    words = spine.get("word_timestamps") or []
    ranges = (job.load().get("selection") or {}).get("kept_word_ranges")

    filtered = spine_build.filter_words(words, ranges)
    # Boundaries are recomputed in the filtered view rather than mapped from the
    # spine: after select drops a take, the surviving order can put two sources
    # next to each other that were not adjacent before.
    boundaries = [index for index in range(1, len(filtered))
                  if filtered[index].get("src") != filtered[index - 1].get("src")]
    return filtered, boundaries


def load_style_profile(path_value: str | None) -> dict[str, Any] | None:
    """Load the learned user style profile, tolerating both wrapper shapes."""
    if not path_value:
        return None
    path = Path(path_value)
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return (
        data.get("metadata", {}).get("user_style_profile")
        or data.get("user_style_profile")
        or data
    )


def _merge_usage(*usages: dict[str, Any]) -> dict[str, Any]:
    total: dict[str, Any] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for usage in usages:
        for key in total:
            total[key] += int(usage.get(key, 0) or 0)
    return total


def run(job, options: dict[str, Any]) -> dict[str, Any]:
    state = job.load()
    words, boundaries = director_words(job)
    profile = load_style_profile(options.get("style_profile"))
    model = options.get("model") or default_model()
    version = int(state.get("current_version", 0)) + 1

    logs = job.dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)

    assembly = state.get("assembly_resolved") or {}
    spine = json.loads(job.spine_path.read_text(encoding="utf-8"))
    overlay_pool = (spine.get("overlay_pool") or []
                    if assembly.get("broll_overlay", True) else [])
    structure_prompt = build_structure_prompt(
        words, options, profile, source_boundaries=boundaries,
        cross_source_cut=bool(assembly.get("cross_source_cut", False)),
        overlay_pool=overlay_pool,
    )
    (logs / f"prompt_structure_v{version}.txt").write_text(structure_prompt, encoding="utf-8")
    ranges = chunk_ranges(words)

    job.emit("log", "direct",
             f"Gọi {1 + len(ranges)} lượt song song: 1 khung + {len(ranges)} nhóm caption "
             f"({len(words)} từ, model {model})")

    def call_structure() -> tuple[dict[str, Any], dict[str, Any]]:
        # Structure JSON is small (cards/events/cuts) — a 50k ceiling invites
        # long reasoning models to think until the gateway's Cloudflare 120s
        # read timeout (524). Cap output so the call finishes under that wall.
        parsed, usage, _ = chat_json(
            structure_prompt, model=model, temperature=0.25,
            max_tokens=8000, timeout=180, retries=2,
            raw_dump=logs / f"raw_structure_v{version}.txt",
        )
        return parsed, usage

    def call_captions(index_range: tuple[int, int]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        start, end = index_range
        prompt = build_caption_prompt(words, start, end)
        (logs / f"prompt_captions_v{version}_{start}-{end}.txt").write_text(prompt, encoding="utf-8")
        parsed, usage, _ = chat_json(
            prompt, model=model, temperature=0.2, max_tokens=6000,
            timeout=180, retries=2,
            raw_dump=logs / f"raw_captions_v{version}_{start}-{end}.txt",
        )
        return parsed.get("captions") or [], usage

    # Structure first, then caption chunks. Parallel structure+captions used to
    # compete for the same gateway slot; a slow structure call then 524'd while
    # captions still finished — wasted spend and a half-written version.
    structure, structure_usage = call_structure()
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_CALLS) as pool:
        caption_futures = [pool.submit(call_captions, r) for r in ranges]
        caption_results = [future.result() for future in caption_futures]

    captions: list[dict[str, Any]] = []
    caption_usages: list[dict[str, Any]] = []
    for chunk_captions, usage in caption_results:
        captions.extend(chunk_captions)
        caption_usages.append(usage)
    captions.sort(key=lambda c: int(c.get("w0", 0)))

    # cards are their own key in the structure response; the renderer wants
    # them in the same flat event list as everything else
    events: list[dict[str, Any]] = []
    for card in structure.get("cards") or []:
        events.append({**card, "type": "card"})
    events.extend(structure.get("events") or [])
    events.extend({**caption, "type": "caption"} for caption in captions)

    usage = _merge_usage(structure_usage, *caption_usages)
    # Which prompt version produced this build. Without it, "yesterday's came
    # out better" is a question with no answer.
    prompt_versions = {
        prompt_id: prompt_registry.current_version(prompt_id)
        for prompt_id in ("structure", "captions")
    }
    spec = {
        "cut_remove": structure.get("cut_remove") or [],
        "grade": structure.get("grade") or {},
        "bgm": structure.get("bgm"),
        "cold_open": structure.get("cold_open"),
        "endcard": structure.get("endcard") or {},
        "events": events,
        "_meta": {
            "model": model,
            "version": version,
            "usage": usage,
            "calls": 1 + len(ranges),
            "caption_chunks": [list(r) for r in ranges],
            "user_prompt": options.get("prompt", ""),
            "style_profile": options.get("style_profile"),
            "prompt_versions": prompt_versions,
        },
    }
    job.spec_path(version).write_text(
        json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")

    cost = estimate_cost(usage, model)
    state = job.load()
    state["current_version"] = version
    state["cost_usd"] = round(float(state.get("cost_usd", 0.0)) + cost, 6)
    state["prompt_versions"] = prompt_versions
    state.setdefault("versions", []).append({
        "version": version, "kind": "director", "model": model,
        "instruction": options.get("prompt", ""), "usage": usage, "cost_usd": cost,
        "prompt_versions": prompt_versions,
    })
    job.save(state)

    counts: dict[str, int] = {}
    for event in events:
        counts[event.get("type", "?")] = counts.get(event.get("type", "?"), 0) + 1
    job.emit("log", "direct",
             f"Spec v{version}: {counts} | tokens {usage.get('total_tokens')}")
    return {"version": version, "event_counts": counts, "usage": usage, "cost_usd": cost}
