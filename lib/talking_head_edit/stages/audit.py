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

from lib.talking_head_edit.cut_safety import (
    USER_CUT, current_proposals, filter_unsafe_cuts, resolve_cut_level,
)
from lib.talking_head_edit.cut_verifier import look_again, verify_cuts
from lib.talking_head_edit.director_client import estimate_cost
from lib.talking_head_edit.job_store import primary_input_path
from lib.talking_head_edit.resolve_spans import MIN_CUT, is_worth_cutting
from lib.talking_head_edit.spec_patch import NEW_CUT
from lib.talking_head_edit.resources import usable_bgm, usable_sfx
from lib.talking_head_edit.stages.direct import director_words

# Event types MonaTimeline actually implements (see its TimelineEvent union).
EVENT_TYPES = {"caption", "keyword", "card", "punch_in", "sfx", "shake", "flash",
               "broll"}
WORD_INDEX_FIELDS = ("w0", "w1", "atWord")

_SENTENCE_END = ".!?…\"'"


def _word_ends_sentence(text: str) -> bool:
    stripped = (text or "").strip()
    return bool(stripped) and stripped[-1] in _SENTENCE_END


def normalize_cold_open(
    cold: dict[str, Any] | None,
    words: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Snap hook span to whole-sentence boundaries; drop choppy teasers."""
    if not isinstance(cold, dict) or "w0" not in cold or "w1" not in cold:
        return cold if isinstance(cold, dict) else None, []

    notes: list[str] = []
    count = len(words)
    w0 = max(0, min(count - 1, int(cold["w0"])))
    w1 = max(0, min(count - 1, int(cold["w1"])))
    if w0 > w1:
        return None, ["cold_open w0>w1 — bỏ hook"]

    orig = (w0, w1)
    for _ in range(8):
        if _word_ends_sentence(str(words[w1].get("word", ""))):
            break
        if w1 >= count - 1:
            break
        w1 += 1

    for _ in range(6):
        if w0 == 0:
            break
        prev = str(words[w0 - 1].get("word", ""))
        if _word_ends_sentence(prev):
            break
        w0 -= 1

    span = w1 - w0 + 1
    if span < 4:
        return None, ["cold_open quá ngắn / cắt cụt — bỏ hook"]
    if span > 28:
        return None, ["cold_open quá dài — bỏ hook"]

    if (w0, w1) != orig:
        notes.append(f"hook chỉnh biên câu w{orig[0]}-{orig[1]} → w{w0}-{w1}")

    return {**cold, "w0": w0, "w1": w1}, notes


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
                    overlay_pool: list[dict[str, Any]] | None = None,
                    words: list[dict[str, Any]] | None = None,
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
        elif words:
            cold, hook_notes = normalize_cold_open(cold, words)
            removed.extend(hook_notes)
            spec["cold_open"] = cold

    return spec, removed


def clamp_bgm_volume(value: Any, default: float = 0.16) -> float:
    """A bed under the voice: 0.08–0.22, whatever a person or a model asked for."""
    try:
        volume = float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        volume = default
    return max(0.08, min(0.22, volume))


def apply_chosen_bgm(
    spec: dict[str, Any],
    options: dict[str, Any],
    removed: list[str],
) -> dict[str, Any]:
    """Lock BGM to the user's style pick. Director guess is only a fallback.

    `bgm_name` on the job is what a saved style / create-build picker writes.
    Without this, a template that says "always use tech_pulse at 0.15" would
    still let the model swap the bed on the next video.
    """
    from lib.talking_head_edit.job_store import option_enabled

    if not option_enabled(options, "bgm"):
        spec["bgm"] = None
        return spec
    name = str(options.get("bgm_name") or "").strip()
    if not name:
        return spec
    ok = set(usable_bgm())
    if name not in ok:
        removed.append(f"bgm đã chọn '{name}' không có file — giữ bản director nếu hợp lệ")
        return spec
    spec["bgm"] = {"name": name, "volume": clamp_bgm_volume(options.get("bgm_volume"))}
    return spec


def audit_cuts(proposed: list[dict[str, Any]], words: list[dict[str, Any]],
               cold_open: dict[str, Any] | None, level: str, model: str | None = None,
               on_unsure: Any = None) -> dict[str, Any]:
    """Decide which proposals are cut, and say why each of the others is not.

    Order: the user's own ranges pass straight through; the director's go to the
    verifier, then the lexicon gate for `level`. Every survivor must still be
    worth an edit point once the silence around it is measured.
    """
    n = len(words)

    def text_of(span: list[int]) -> str:
        return " ".join(str(words[i].get("word") or "").strip()
                        for i in range(max(0, span[0]), min(n, span[1] + 1)))

    by_user = [p["w"] for p in proposed if p.get("nguon") == USER_CUT]
    director = [p for p in proposed if p.get("nguon") != USER_CUT]
    outside = [span for span in by_user if span[0] < 0 or span[1] >= n]
    by_user = [span for span in by_user if span not in outside]
    verified, decisions, usage = verify_cuts(director, words, cold_open, model=model,
                                             on_unsure=on_unsure)
    reasons = {tuple(p["w"]): str(p.get("ly_do") or "") for p in director}
    safe, rejected = filter_unsafe_cuts(verified, words, level, reasons)

    blocked = [{"w": d["w"], "text": text_of(d["w"]), "by": "verifier",
                "reason": d.get("reason") or "verifier giữ lại"}
               for d in decisions if d["decision"] != "remove"]
    blocked += [{**r, "by": "an_toan"} for r in rejected]
    blocked += [{"w": span, "text": "", "by": "ngoai_pham_vi",
                 "reason": "chỉ số từ nằm ngoài lời của bản dựng"} for span in outside]
    applied: list[list[int]] = []
    for span in by_user + safe:
        if span in applied:
            continue
        if is_worth_cutting(span, words):
            applied.append(span)
        else:
            blocked.append({"w": span, "text": text_of(span), "by": "qua_ngan",
                            "reason": f"khoảng cắt dưới {MIN_CUT}s — mối nối sẽ nuốt âm cạnh"})
    return {"applied": sorted(applied), "blocked": blocked,
            "decisions": decisions, "usage": usage}


def cut_outcome(proposed: list[dict[str, Any]], cuts: dict[str, Any]) -> dict[str, Any]:
    """What happened to the cuts this version asked for, plus the video's total.

    A version that added cuts (revise, the user's own marks) reports only those;
    the director's first version, which flags none, reports all of its own.
    """
    asked = [p["w"] for p in proposed if p.get(NEW_CUT)] or [p["w"] for p in proposed]
    return {
        "cuts_proposed": len(asked),
        "cuts_applied": sum(span in cuts["applied"] for span in asked),
        "cuts_blocked": [b for b in cuts["blocked"] if b["w"] in asked],
        "cuts_total_applied": len(cuts["applied"]),
    }


def record_outcome(state: dict[str, Any], version: int, outcome: dict[str, Any]) -> None:
    """Merge what actually happened into the version entry the UI and API show."""
    for entry in state.get("versions") or []:
        if int(entry.get("version", 0)) == version:
            entry["outcome"] = {**(entry.get("outcome") or {}), **outcome}
            return


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

    proposed = current_proposals(spec)
    level = resolve_cut_level(options)
    job.emit("log", "audit", f"Kiểm {len(proposed)} đoạn cắt đề xuất (mức cắt: {level})…")

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

    cuts = audit_cuts(proposed, words, spec.get("cold_open"), level,
                      model=options.get("verifier_model") or options.get("model"),
                      on_unsure=look_at_unsure)
    decisions, usage = cuts["decisions"], cuts["usage"]
    if cuts["blocked"]:
        job.emit("log", "audit", f"Không cắt {len(cuts['blocked'])} đoạn: " + "; ".join(
            f"«{b['text']}» — {b['reason']}" for b in cuts["blocked"][:5]))
    spec["cut_proposed"] = proposed
    spec["cut_remove"] = cuts["applied"]   # resolver consumes plain [w0, w1] pairs
    accepted = cuts["applied"]

    spine = json.loads(job.spine_path.read_text(encoding="utf-8"))
    spec, removed_resources = audit_resources(
        spec, len(words), overlay_pool=spine.get("overlay_pool"), words=words)
    spec = apply_chosen_bgm(spec, options, removed_resources)
    quality = measure_quality(spec, words)

    cost = estimate_cost(usage, options.get("model", ""))
    report = {
        "version": version,
        "cuts_proposed": len(proposed),
        "cuts_accepted": len(accepted),
        "cuts_kept": sum(1 for d in decisions if d["decision"] == "keep"),
        "cut_level": level,
        "cuts_blocked": cuts["blocked"],
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
    record_outcome(state, version, cut_outcome(proposed, cuts))
    job.save(state)

    job.emit("log", "audit",
             f"Cắt: áp dụng {len(accepted)}/{len(proposed)} đề xuất "
             f"| caption phủ {quality['caption_coverage'] * 100:.1f}% "
             f"| keyword-trong-card {len(quality['keyword_in_card'])}")
    for item in removed_resources:
        job.emit("warning", "audit", item)
    return report
