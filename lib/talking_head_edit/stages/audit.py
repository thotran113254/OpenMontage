"""Stage 4 — audit the director's spec before anything gets cut or rendered.

Split by who is qualified to judge:

* language judgement (may this span be removed without losing meaning?) goes
  to `cut_verifier`, a separate small LLM call — a word list can't cover real
  speech, and being wrong here destroys a sentence.
* ground truth stays here: does the referenced audio file exist, is the event
  type one the renderer implements, are word indices inside the spine. These
  are facts, not guesses.
* quality signals (caption coverage, keyword-in-card, card count) are measured
  and reported, never silently "fixed" — the UI shows them so a human decides.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.talking_head_edit.cut_verifier import look_again, verify_cuts
from lib.talking_head_edit.director_client import estimate_cost
from lib.talking_head_edit.job_store import primary_input_path
from lib.talking_head_edit.resources import usable_bgm, usable_sfx
from lib.talking_head_edit.stages.direct import director_words

# Event types MonaTimeline actually implements (see its TimelineEvent union).
EVENT_TYPES = {"caption", "keyword", "card", "punch_in", "sfx", "shake", "flash",
               "broll"}
WORD_INDEX_FIELDS = ("w0", "w1", "atWord")


DEFAULT_BGM_VOLUME = 0.22


def normalise_bgm(bgm: Any) -> tuple[dict[str, Any] | None, str]:
    """Accept the shapes a model actually produces; report anything unusable.

    A revise pass answered with `"bgm": "bgm_energy_drive.mp3"` — a bare string
    rather than the documented object. Both this stage and the resolver only
    understood the object form, so the music vanished with no warning at all.
    Silently losing an approved element is worse than a rejected patch.
    """
    if bgm is None:
        return None, ""
    if isinstance(bgm, str):
        return {"name": bgm, "volume": DEFAULT_BGM_VOLUME}, ""
    if isinstance(bgm, dict) and bgm.get("name"):
        return {**bgm, "volume": float(bgm.get("volume", DEFAULT_BGM_VOLUME))}, ""
    return None, f"bgm có dạng không hiểu được ({type(bgm).__name__}) — bỏ nhạc nền"


def audit_resources(spec: dict[str, Any], word_count: int,
                    overlay_pool: list[dict[str, Any]] | None = None
                    ) -> tuple[dict[str, Any], list[str]]:
    """Drop what the renderer provably cannot play or place."""
    sfx_ok, bgm_ok = set(usable_sfx()), set(usable_bgm())
    # B-roll is per-job, not a shared library, so its allow-list is the job's own
    # overlay_pool rather than resource-manifest.json. Same rule as sfx and bgm:
    # only resources that really exist may reach the renderer.
    broll_ok = {str(entry.get("src")) for entry in (overlay_pool or [])}
    removed: list[str] = []
    kept: list[dict[str, Any]] = []

    for event in spec.get("events") or []:
        kind = event.get("type")
        if kind not in EVENT_TYPES:
            removed.append(f"event '{kind}' renderer không có — bỏ")
            continue
        if kind == "sfx" and event.get("name") not in sfx_ok:
            removed.append(f"sfx '{event.get('name')}' không có file thật — bỏ")
            continue
        if kind == "broll" and str(event.get("src")) not in broll_ok:
            removed.append(
                f"broll '{event.get('src')}' không có trong overlay_pool của job "
                f"(có: {sorted(broll_ok) or 'không có nguồn b-roll nào'}) — bỏ")
            continue

        out_of_range = [
            f"{field}={event[field]}" for field in WORD_INDEX_FIELDS
            if field in event and not (0 <= int(event[field]) < word_count)
        ]
        if out_of_range:
            removed.append(f"event '{kind}' trỏ ra ngoài xương sống ({', '.join(out_of_range)}) — bỏ")
            continue
        for field in ("bulletWords",):
            if event.get(field):
                event[field] = [i for i in event[field] if 0 <= int(i) < word_count]
        kept.append(event)

    spec["events"] = kept

    bgm, bgm_error = normalise_bgm(spec.get("bgm"))
    if bgm_error:
        removed.append(bgm_error)
        spec["bgm"] = None
    elif bgm and bgm["name"] not in bgm_ok:
        removed.append(f"bgm '{bgm['name']}' không có file thật — bỏ nhạc nền")
        spec["bgm"] = None
    else:
        spec["bgm"] = bgm

    cold = spec.get("cold_open")
    if isinstance(cold, dict) and "w0" in cold and "w1" in cold:
        if not (0 <= int(cold["w0"]) < word_count and 0 <= int(cold["w1"]) < word_count):
            removed.append("cold_open trỏ ra ngoài xương sống — bỏ cold-open")
            spec["cold_open"] = None

    return spec, removed


def measure_quality(spec: dict[str, Any], words: list[dict[str, Any]]) -> dict[str, Any]:
    """Measured signals for the UI. Reported, not enforced."""
    n = len(words)
    events = spec.get("events") or []
    cards = [(int(e["w0"]), int(e["w1"])) for e in events
             if e.get("type") == "card" and "w0" in e and "w1" in e]

    covered: set[int] = set()
    long_captions = 0
    for event in events:
        if event.get("type") != "caption":
            continue
        w0, w1 = int(event.get("w0", 0)), int(event.get("w1", 0))
        lo, hi = min(w0, w1), max(w0, w1)
        covered.update(range(lo, hi + 1))
        if hi - lo + 1 > 9:
            long_captions += 1

    missing = [i for i in range(n) if i not in covered]
    keyword_in_card = [
        e.get("text") for e in events
        if e.get("type") == "keyword"
        and any(a <= int(e.get("atWord", -1)) <= b for a, b in cards)
    ]
    return {
        "caption_coverage": round((n - len(missing)) / n, 4) if n else 0.0,
        "uncovered_words": missing[:40],
        "captions_over_9_words": long_captions,
        "keyword_in_card": keyword_in_card,
        "card_count": len(cards),
        "event_count": len(events),
    }


def run(job, options: dict[str, Any]) -> dict[str, Any]:
    state = job.load()
    version = int(state["current_version"])
    spec = json.loads(job.spec_path(version).read_text(encoding="utf-8"))
    # The SAME word list the director saw: after `select` drops a take, spec
    # indices are indices into the filtered spine, so validating them against the
    # full spine would accept out-of-range indices and mis-window every cut.
    words, _ = director_words(job)

    proposed = spec.get("cut_remove") or []
    job.emit("log", "audit", f"Kiểm {len(proposed)} đoạn cắt đề xuất bằng verifier riêng…")

    def look_at_unsure(entries: list[dict[str, Any]]):
        """Second look, with a waveform picture, at spans the verifier hedged on.

        Runs on the SOURCE footage: `audit` is before `resolve`, so the cut file
        does not exist yet — and the source is the right thing to look at anyway,
        since the question is where the cut edge lands in the original audio.
        """
        job.emit("log", "audit",
                 f"{len(entries)} đoạn verifier không chắc — xem lại kèm ảnh dạng sóng")
        # Each candidate's words may come from different files once there are
        # several sources, and `word.start` is a second in ITS OWN file — so the
        # picture has to be taken from that file, not from source zero.
        by_source: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            src_id = str(words[int(entry["w"][0])].get("src") or "s0")
            by_source.setdefault(src_id, []).append(entry)

        paths = {str(s["id"]): Path(s["path"]) for s in (state.get("sources") or [])
                 if s.get("path")}
        fallback = primary_input_path(state)
        resolved: list[dict[str, Any]] = []
        total: dict[str, Any] = {}
        for src_id, group in by_source.items():
            group_resolved, usage = look_again(
                paths.get(src_id, fallback), group, words,
                job.dir / "preview" / "cut_look" / src_id,
                model=options.get("verifier_model") or options.get("model"))
            resolved.extend(group_resolved)
            for key, value in usage.items():
                total[key] = int(total.get(key, 0) or 0) + int(value or 0)
        return resolved, total

    accepted, decisions, usage = verify_cuts(
        proposed, words, spec.get("cold_open"),
        model=options.get("verifier_model") or options.get("model"),
        on_unsure=look_at_unsure,
    )
    # Lexicon safety net: single/short non-filler tokens (e.g. ASR "tết" for
    # "test") must never leave even if the verifier said remove.
    from lib.talking_head_edit.cut_safety import filter_unsafe_cuts

    accepted, safety_rejected = filter_unsafe_cuts(accepted, words)
    if safety_rejected:
        job.emit(
            "log", "audit",
            f"Chặn {len(safety_rejected)} cut nguy hiểm (không phải filler thuần): "
            + "; ".join(
                f"[{r.get('w')}] {r.get('text') or ''} — {r.get('reason')}"
                for r in safety_rejected[:5]
            ),
        )
    spec["cut_remove"] = accepted   # resolver consumes plain [w0, w1] pairs

    spine = json.loads(job.spine_path.read_text(encoding="utf-8"))
    spec, removed_resources = audit_resources(
        spec, len(words), overlay_pool=spine.get("overlay_pool"))
    quality = measure_quality(spec, words)

    cost = estimate_cost(usage, options.get("model", ""))
    report = {
        "version": version,
        "cuts_proposed": len(proposed),
        "cuts_accepted": len(accepted),
        "cuts_kept": sum(1 for d in decisions if d["decision"] == "keep"),
        "cut_decisions": decisions,
        "removed_resources": removed_resources,
        "quality": quality,
        "verifier_usage": usage,
        "verifier_cost_usd": cost,
    }

    job.spec_path(version).write_text(
        json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    (job.dir / f"audit_report_v{version}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    state = job.load()
    state["cost_usd"] = round(float(state.get("cost_usd", 0.0)) + cost, 6)
    job.save(state)

    job.emit("log", "audit",
             f"Cắt: giữ lại {report['cuts_kept']}/{len(proposed)} đề xuất, "
             f"chấp nhận {len(accepted)} | caption phủ {quality['caption_coverage'] * 100:.1f}% "
             f"| keyword-trong-card {len(quality['keyword_in_card'])}")
    for item in removed_resources:
        job.emit("warning", "audit", item)
    return report
