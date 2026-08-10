"""Second-opinion pass on the director's proposed cuts.

Why an LLM and not a filler lexicon: a word list can only recognise the
hesitation sounds someone thought to write down. Real speech produces far more
shapes — trailing particles, half-restarts, regional variants — so a list both
misses real fillers and, worse, can wave through a cut that removes meaning
because the words happened to be short. Judging "does the sentence still say
the same thing without this?" is a language task, so a language model does it.

What stays mechanical here is only bookkeeping: index bounds, overlap with the
cold-open span, and recording the verdict. The payload is deliberately small —
each candidate ships with a window of surrounding words, never the whole spine.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.talking_head_edit import prompt_registry
from lib.talking_head_edit.director_client import DirectorError, chat_json

CONTEXT_WORDS = 12   # words of context on each side of a candidate cut


def _window(words: list[dict[str, Any]], start: int, end: int) -> dict[str, Any]:
    n = len(words)
    before_from = max(0, start - CONTEXT_WORDS)
    after_to = min(n, end + 1 + CONTEXT_WORDS)
    join = lambda a, b: " ".join(w["word"].strip() for w in words[a:b])  # noqa: E731
    return {
        "before": join(before_from, start),
        "cut": join(start, end + 1),
        "after": join(end + 1, after_to),
    }


def build_prompt(candidates: list[dict[str, Any]], version: str | None = None) -> str:
    return prompt_registry.render("cut_verify", {
        "payload": json.dumps(candidates, ensure_ascii=False, indent=1),
        "count": len(candidates),
    }, version=version)


def look_again(source: Path, entries: list[dict[str, Any]],
               words: list[dict[str, Any]], work_dir: Path,
               model: str | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Second look at unsure spans, with a waveform picture of each.

    A waveform genuinely answers the question the text could not: is the cut edge
    landing in silence, or in the middle of a word. This is not the model grading
    quality — the blind test settled that it cannot — it is the model reading a
    chart, which it can.

    Anything still unresolved after this comes back as `keep`. Two rounds and then
    the safe default, so an ambiguous span can never quietly become a cut.
    """
    from lib.talking_head_edit.director_client import DirectorError, chat_with_images
    from lib.talking_head_edit.timeline_view import TimelineViewError, timeline_view

    images: list[tuple[str, Path]] = []
    payload: list[dict[str, Any]] = []
    work_dir.mkdir(parents=True, exist_ok=True)

    for entry in entries:
        start_index, end_index = int(entry["w"][0]), int(entry["w"][1])
        span_start = float(words[start_index]["start"])
        span_end = float(words[min(end_index, len(words) - 1)]["end"])
        try:
            image = timeline_view(
                source, max(0.0, span_start - 1.2), span_end + 1.2,
                words=words, marks=[span_start, span_end],
                out=work_dir / f"cut_{entry['id']:03d}.png",
                title=f"đoạn nghi bỏ: «{entry.get('cut', '')}»")
        except (TimelineViewError, OSError, ImportError):
            continue
        images.append((f"đoạn id={entry['id']}", image))
        payload.append({"id": entry["id"], "before": entry.get("before", ""),
                        "cut": entry.get("cut", ""), "after": entry.get("after", ""),
                        "reason_truoc_do": entry.get("reason", "")})

    if not images:
        return ([{**entry, "decision": "keep", "was_unsure": True,
                  "reason": f"không dựng được ảnh để xem lại nên giữ ({entry['reason']})"}
                 for entry in entries], {})

    prompt = prompt_registry.render("cut_verify_look", {
        "payload": json.dumps(payload, ensure_ascii=False, indent=1),
        "count": len(payload),
    })
    try:
        result, usage = chat_with_images(prompt, images, model=model, max_tokens=4000)
    except (DirectorError, json.JSONDecodeError) as exc:
        return ([{**entry, "decision": "keep", "was_unsure": True,
                  "reason": f"xem lại thất bại ({str(exc)[:80]}) nên giữ"}
                 for entry in entries], {})

    verdicts = {int(v.get("id", -1)): v for v in (result.get("verdicts") or [])}
    resolved: list[dict[str, Any]] = []
    for entry in entries:
        verdict = verdicts.get(int(entry["id"]))
        # No answer, or still unsure → keep. The default never drifts.
        decision = normalise_decision(verdict.get("decision")) if verdict else "keep"
        if decision == "unsure":
            decision = "keep"
        resolved.append({
            **entry, "decision": decision, "source": "verifier-look",
            "was_unsure": True,
            "reason": (verdict or {}).get("reason") or "sau khi xem ảnh vẫn không chắc → giữ",
        })
    return resolved, usage


def normalise_decision(raw: Any) -> str:
    """`remove` | `keep` | `unsure` from whatever the model wrote.

    `unsure` exists so a genuinely ambiguous span can be looked at again with a
    picture instead of being silently resolved by whichever default we picked.
    Anything unrecognised is `keep`, which is the safe direction.
    """
    value = str(raw or "").strip().lower()
    if value == "remove":
        return "remove"
    if value in ("unsure", "khong_chac", "không chắc", "not_sure", "maybe"):
        return "unsure"
    return "keep"


def verify_cuts(
    proposed: list[Any],
    words: list[dict[str, Any]],
    cold_open: dict[str, Any] | None,
    model: str | None = None,
    on_unsure: Any = None,
) -> tuple[list[list[int]], list[dict[str, Any]], dict[str, Any]]:
    """Returns (accepted spans, per-cut decisions, token usage).

    On verifier failure nothing is accepted: a missed filler costs a slightly
    longer video, a wrong cut costs the meaning of a sentence.

    `on_unsure(entries) -> (resolved_entries, usage)` gets a second look at the
    spans the verifier would not commit on. Without it, unsure means keep.
    """
    n = len(words)
    candidates: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []

    for index, raw in enumerate(proposed or []):
        span = raw.get("w") if isinstance(raw, dict) else raw
        if not span or len(span) < 2:
            continue
        start, end = int(span[0]), int(span[1])
        if end < start:
            start, end = end, start
        start, end = max(0, min(n - 1, start)), max(0, min(n - 1, end))

        if cold_open and "w0" in cold_open and not (end < int(cold_open["w0"]) or start > int(cold_open["w1"])):
            decisions.append({
                "id": index, "w": [start, end], "decision": "keep",
                "reason": "trùng đoạn cold-open (sẽ phát lại làm teaser)",
                "source": "mechanical", **_window(words, start, end),
            })
            continue

        candidates.append({"id": index, "w": [start, end], **_window(words, start, end)})

    if not candidates:
        return [], decisions, {}

    try:
        result, usage, _ = chat_json(build_prompt(candidates), model=model, temperature=0.0)
    except DirectorError as exc:
        for candidate in candidates:
            decisions.append({**candidate, "decision": "keep", "source": "verifier-failed",
                              "reason": f"Không kiểm được nên giữ nguyên: {exc}"})
        return [], decisions, {}

    verdicts = {int(v.get("id", -1)): v for v in (result.get("verdicts") or [])}
    accepted: list[list[int]] = []
    unsure: list[dict[str, Any]] = []
    for candidate in candidates:
        verdict = verdicts.get(candidate["id"])
        if verdict is None:
            decisions.append({**candidate, "decision": "keep", "source": "verifier",
                              "reason": "verifier không trả lời cho đoạn này nên giữ"})
            continue
        decision = normalise_decision(verdict.get("decision"))
        entry = {
            **candidate, "decision": decision, "source": "verifier",
            "reason": verdict.get("reason", ""), "joined": verdict.get("joined", ""),
        }
        if decision == "unsure":
            # Held back for a second look rather than decided either way; the
            # caller may re-ask with a picture (see `resolve_unsure`).
            unsure.append(entry)
            continue
        decisions.append(entry)
        if decision == "remove":
            accepted.append(candidate["w"])

    if unsure and on_unsure:
        second_round, second_usage = on_unsure(unsure)
        for key in usage:
            usage[key] = int(usage.get(key, 0) or 0) + int(second_usage.get(key, 0) or 0)
        for entry in second_round:
            decisions.append(entry)
            if entry["decision"] == "remove":
                accepted.append(entry["w"])
    else:
        for entry in unsure:
            # Default safe: unresolved means keep. A missed filler costs a
            # slightly longer video; a wrong cut costs a sentence.
            decisions.append({**entry, "decision": "keep",
                              "reason": f"verifier không chắc ({entry['reason']}) nên giữ",
                              "was_unsure": True})

    return accepted, decisions, usage
