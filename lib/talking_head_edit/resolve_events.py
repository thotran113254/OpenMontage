"""Word-index → real-time mapping and the mechanical guards.

This is the only place word indices become seconds. The guards below fix
things the director cannot see (it reasons over text, not pixels), while
keeping its creative intent:

* a keyword scheduled inside a card span would be hidden by the card layer
* a caption under 0.5s makes the pill blink
Text fitting deliberately does NOT live here: sizing a word to the frame
needs real font metrics, and a per-character estimate under-read accented
Vietnamese uppercase badly enough to push keywords off screen. The renderer
measures the actual text (see KeywordView) and clamps the size there.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from lib.talking_head_edit.resolve_spans import timeline_frames

CAPTION_MIN_SECONDS = 0.5
# Stacked SFX (riser+whoosh every card, pop on every bullet) is the most common
# "this edit is exhausting" complaint. The director is asked to space them, but
# it still over-fires — enforce a floor here so bad density cannot reach render.
MIN_SFX_GAP_SECONDS = 1.15
# Prefer the more structural hit when two cues collide.
_SFX_PRIORITY = {
    "sfx_whoosh.mp3": 50,
    "sfx_ding.mp3": 45,
    "sfx_riser.mp3": 30,
    "sfx_page_turn.mp3": 28,
    "sfx_pop_high.mp3": 20,
    "sfx_pop.mp3": 18,
    "sfx_pop_low.mp3": 16,
    "sfx_tick.mp3": 10,
}


class TimeMapper:
    """Word index → its time on the joined, cut, tempo-adjusted output timeline.

    Span-based rather than remove-based, because with several sources there is no
    single time axis to subtract removed intervals from: word 900 might live at
    second 3 of the second file. Each word carries `src`, each span knows which
    source it came from and where it lands on the output, and the lookup is
    (src, second in that source) → second on the output.

    `from_removes` keeps the single-source construction available for callers and
    tests that reason in "one file minus some cuts".
    """

    def __init__(self, words: list[dict[str, Any]], spans: list[Any],
                 new_duration: float, tempo: float,
                 default_src: str = "s0", fps: int | None = None):
        self.words = words
        self.n = len(words)
        self.starts = [float(w["start"]) for w in words]
        self.ends = [float(w["end"]) for w in words]
        self.srcs = [str(w.get("src") or default_src) for w in words]
        self.tempo = tempo or 1.0
        self.new_duration = new_duration
        # (src, start, end, output offset). Offsets accumulate the span lengths
        # the segments actually have on disk: tempo-scaled, and with `fps`
        # rounded to whole frames exactly as `resolve_cut` encodes them.
        self.spans: list[tuple[str, float, float, float]] = []
        offset = 0.0
        for span in spans:
            src_id = str(getattr(span, "src_id", default_src))
            start = float(getattr(span, "start", span[0] if isinstance(span, tuple) else 0.0))
            end = float(getattr(span, "end", span[1] if isinstance(span, tuple) else 0.0))
            self.spans.append((src_id, start, end, offset))
            length = max(0.0, end - start)
            offset += (timeline_frames(length, fps, self.tempo) / fps if fps and length > 0
                       else length / self.tempo)

    @classmethod
    def from_removes(cls, words: list[dict[str, Any]],
                     removes: list[tuple[float, float]], new_duration: float,
                     tempo: float, duration: float | None = None) -> "TimeMapper":
        """Single-source form: everything not removed, in one file."""
        from lib.talking_head_edit.resolve_spans import Span, kept_spans

        total = duration if duration is not None else (
            max((float(w["end"]) for w in words), default=0.0))
        src = str(words[0].get("src") or "s0") if words else "s0"
        spans = [Span(src, Path("."), start, end)
                 for start, end in kept_spans(removes, total)]
        return cls(words, spans, new_duration, tempo, default_src=src)

    def _clamp(self, index: int) -> int:
        return max(0, min(self.n - 1, int(index)))

    def map_time(self, src_id: str, moment: float) -> float:
        """(source, second in that source) → second on the output timeline.

        A moment that fell inside a cut has no output time of its own, so it
        clamps to the nearest kept edge — the same behaviour as before, which is
        what keeps a caption on a partly-cut phrase from jumping elsewhere.
        """
        candidates = [s for s in self.spans if s[0] == src_id]
        if not candidates:
            # No span from this source survived. Anchoring to 0 would silently
            # move the event to the top of the video; the caller drops it instead.
            raise KeyError(src_id)

        best: tuple[float, float] | None = None      # (distance, mapped second)
        for _, start, end, offset in candidates:
            if start <= moment <= end:
                return round(min(self.new_duration,
                                 offset + (moment - start) / self.tempo), 3)
            # Nearest kept edge, measured in source time.
            edge = start if moment < start else end
            distance = abs(edge - moment)
            mapped = round(min(self.new_duration,
                               offset + (edge - start) / self.tempo), 3)
            if best is None or distance < best[0]:
                best = (distance, mapped)
        return max(0.0, best[1] if best else 0.0)

    def at(self, word_index: int, use_end: bool = False) -> float:
        index = self._clamp(word_index)
        raw = self.ends[index] if use_end else self.starts[index]
        try:
            return self.map_time(self.srcs[index], raw)
        except KeyError:
            return 0.0

    def src_of(self, word_index: int) -> str:
        return self.srcs[self._clamp(word_index)]

    def seams(self) -> list[float]:
        """Output-timeline seconds where one span hands over to the next."""
        return [round(offset, 3) for _, _, _, offset in self.spans[1:]]


HOOK_LEAD_MAX = 0.12   # breath kept before the hook's first word
HOOK_TAIL_MIN = 0.06   # breath kept after its last word, room allowing
HOOK_TAIL_MAX = 0.22
HOOK_TAIL_GUARD = 0.02  # never run this close to the next word


def cold_open_window(mapper: TimeMapper, w0: int, w1: int, timeline: float,
                     fps: int) -> tuple[float, float]:
    """(start, end) on the cut timeline for the teaser of words w0..w1.

    Built so the teaser can never sound chopped:

    * it starts in the silence before w0 (half of it, at most HOOK_LEAD_MAX),
      because ASR word starts land late and the first consonant went missing;
    * it keeps a breath after w1 but never reaches the next word — the old
      60ms minimum tail swallowed the next word's onset whenever the speaker
      ran on;
    * it is whole frames long starting on a frame, so the teaser's video and
      audio begin together and the frame snap rounds UP unless that would
      touch the next word.
    """
    first = mapper.at(w0)
    before = mapper.at(w0 - 1, use_end=True) if w0 > 0 else 0.0
    start = max(0.0, first - min(HOOK_LEAD_MAX, max(0.0, first - before) / 2))

    last_end = mapper.at(w1, use_end=True)
    next_start = mapper.at(w1 + 1) if w1 + 1 < mapper.n else timeline
    room = max(0.0, next_start - last_end - HOOK_TAIL_GUARD)
    tail = min(HOOK_TAIL_MAX, room, max(HOOK_TAIL_MIN, room * 0.65))
    end = min(timeline, last_end + tail)

    start = math.floor(start * fps + 1e-6) / fps
    limit = min(timeline, max(end, next_start))
    frames = math.ceil((end - start) * fps - 1e-6)
    if start + frames / fps > limit + 1e-9:
        frames = max(1, math.floor((limit - start) * fps + 1e-6))
    return round(start, 6), round(start + frames / fps, 6)


def resolve_events(spec: dict[str, Any], mapper: TimeMapper) -> tuple[list[dict[str, Any]], list[str]]:
    """Turn word-anchored spec events into real-time renderer events."""
    events: list[dict[str, Any]] = []
    skipped: list[str] = []

    for event in spec.get("events") or []:
        kind = event.get("type")
        try:
            if kind == "caption":
                w0, w1 = int(event["w0"]), int(event["w1"])
                if w1 < w0:
                    w0, w1 = w1, w0
                start, end = mapper.at(w0), mapper.at(w1, use_end=True)
                if end <= start:
                    end = min(mapper.new_duration, start + 0.4)
                resolved = {"type": "caption", "at": start, "end": end, "text": event["text"]}
                if event.get("highlight"):
                    resolved["highlight"] = event["highlight"]
                if event.get("highlightColor"):
                    resolved["highlightColor"] = event["highlightColor"]
                events.append(resolved)

            elif kind == "keyword":
                start = mapper.at(int(event.get("atWord", event.get("w0", 0))))
                duration = float(event.get("durSec", 1.5))
                events.append({
                    "type": "keyword", "at": start,
                    "end": round(min(mapper.new_duration, start + duration), 3),
                    "text": event["text"], "color": event.get("color", "#FFFFFF"),
                    "xPct": event.get("xPct", 20), "yPct": event.get("yPct", 15),
                    "rotation": event.get("rotation", 0),
                    "fontSize": event.get("fontSize", 80),
                    "anim": event.get("anim", "pop"),
                })

            elif kind == "card":
                start, end = mapper.at(int(event["w0"])), mapper.at(int(event["w1"]), use_end=True)
                if end - start < 1.0:
                    end = min(mapper.new_duration, start + 1.0)
                resolved = {
                    "type": "card", "at": start, "end": end,
                    "kicker": event.get("kicker", ""), "title": event.get("title", ""),
                    "badge": str(event.get("badge", "")), "bullets": event.get("bullets", []),
                }
                if event.get("bulletWords"):
                    resolved["bulletTimes"] = [mapper.at(int(k)) for k in event["bulletWords"]]
                events.append(resolved)

            elif kind == "broll":
                w0, w1 = int(event["w0"]), int(event["w1"])
                if w1 < w0:
                    w0, w1 = w1, w0
                start, end = mapper.at(w0), mapper.at(w1, use_end=True)
                if end - start < 0.4:
                    # Too short to register as an overlay; a 0.2s flash of other
                    # footage reads as a glitch.
                    skipped.append(f"broll:{event.get('src')}:qua-ngan")
                    continue
                events.append({
                    "type": "broll", "at": start, "end": end,
                    # `src` stays a SOURCE ID here, not a path. resolve_broll
                    # turns it into a real file after checking it against the
                    # job's overlay_pool — the same "only real resources" rule
                    # that governs sfx and bgm.
                    "src": str(event["src"]),
                    "fit": event.get("fit", "cover"),
                    "opacity": float(event.get("opacity", 1.0)),
                })

            elif kind == "punch_in":
                events.append({"type": "punchIn", "at": mapper.at(int(event["atWord"])),
                               "scale": float(event.get("scale", 1.08)),
                               "holdSeconds": float(event.get("holdSec", 1.1))})

            elif kind == "sfx":
                start = max(0.0, mapper.at(int(event["atWord"])) + float(event.get("offsetSec", 0)))
                events.append({"type": "sfx", "at": round(start, 3), "name": event["name"],
                               "volume": float(event.get("volume", 0.18))})

            elif kind == "shake":
                events.append({"type": "shake", "at": mapper.at(int(event["atWord"])),
                               "durSeconds": float(event.get("durSec", 0.4)),
                               "intensity": float(event.get("intensity", 10))})

            elif kind == "flash":
                events.append({"type": "flash", "at": mapper.at(int(event["atWord"])),
                               "durSeconds": float(event.get("durSec", 0.15))})
            else:
                skipped.append(f"unknown:{kind}")
        except (KeyError, TypeError, ValueError) as exc:
            skipped.append(f"{kind}:{exc!r}"[:80])

    return events, skipped


MIN_BROLL_SECONDS = 0.4


def _trim_broll_under_cards(events: list[dict[str, Any]],
                            card_spans: list[tuple[float, float]]) -> list[dict[str, Any]]:
    """Shorten (or drop) b-roll that a card would sit on top of.

    A card is a full-screen panel with the speaker in a corner PiP, so an overlay
    underneath it is simply invisible — and paying an encode for an invisible clip
    is waste on top of being wrong.
    """
    if not card_spans:
        return events
    kept: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") != "broll":
            kept.append(event)
            continue
        start, end = float(event["at"]), float(event["end"])
        for card_start, card_end in sorted(card_spans):
            if card_start <= start and end <= card_end:
                start = end = 0.0          # fully hidden
                break
            if card_start <= start < card_end:
                start = card_end
            elif start < card_start < end:
                end = card_start
        if end - start >= MIN_BROLL_SECONDS:
            kept.append({**event, "at": round(start, 3), "end": round(end, 3)})
    return kept


def apply_guards(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fix invisible/blinking artefacts. Returns (events, what-was-changed)."""
    card_spans = [(e["at"], e["end"]) for e in events if e["type"] == "card"]
    # B-roll under a card would be covered by it — the card is a full-screen white
    # panel. Trim the overlap rather than dropping the overlay outright.
    events = _trim_broll_under_cards(events, card_spans)

    def in_card(moment: float):
        return next(((a, b) for a, b in card_spans if a <= moment < b), None)

    moved: list[tuple[float, float, str]] = []
    dropped: list[tuple[float, str]] = []
    kept: list[dict[str, Any]] = []

    for event in events:
        if event["type"] != "keyword":
            kept.append(event)
            continue
        span = in_card(event["at"])
        if not span:
            kept.append(event)
            continue
        duration = event["end"] - event["at"]
        new_at = span[0] - duration - 0.3        # land just before the card wipes in
        if new_at >= 0.2 and not in_card(new_at):
            old_at = event["at"]
            event = {**event, "at": round(new_at, 3), "end": round(new_at + duration, 3)}
            kept.append(event)
            moved.append((old_at, event["at"], event["text"]))
        else:
            dropped.append((event["at"], event["text"]))

    # SFX glued to a moved/dropped keyword must follow it, or become an orphan pop
    drop_times = [t for t, _ in dropped]
    move_map = [(old, new) for old, new, _ in moved]
    adjusted: list[dict[str, Any]] = []
    for event in kept:
        if event["type"] == "sfx":
            if any(abs(event["at"] - t) < 0.25 for t in drop_times):
                continue
            match = next(((o, n) for o, n in move_map if abs(event["at"] - o) < 0.25), None)
            if match:
                event = {**event, "at": round(match[1] + (event["at"] - match[0]), 3)}
        adjusted.append(event)

    # a moved keyword that now overlaps another keyword just clutters the hook
    keywords = sorted([e for e in adjusted if e["type"] == "keyword"], key=lambda e: e["at"])
    moved_times = {new for _, new in move_map}
    clutter = [
        k["at"] for k in keywords
        if k["at"] in moved_times
        and any(o is not k and not (k["end"] <= o["at"] or k["at"] >= o["end"]) for o in keywords)
    ]
    if clutter:
        adjusted = [
            e for e in adjusted
            if not (e["type"] == "keyword" and e["at"] in clutter)
            and not (e["type"] == "sfx" and any(abs(e["at"] - t) < 0.25 for t in clutter))
        ]
        dropped.extend((t, "clutter-after-move") for t in clutter)

    # Thin SFX that landed too close together. Keep the higher-priority cue.
    adjusted, sfx_dropped = _thin_sfx(adjusted)

    # merge captions shorter than 0.5s into the next one (the pill would blink)
    captions = sorted([e for e in adjusted if e["type"] == "caption"], key=lambda c: c["at"])
    others = [e for e in adjusted if e["type"] != "caption"]
    merged_captions: list[dict[str, Any]] = []
    merged_count = 0
    index = 0
    while index < len(captions):
        caption = captions[index]
        if caption["end"] - caption["at"] < CAPTION_MIN_SECONDS and index + 1 < len(captions):
            nxt = captions[index + 1]
            joined = {"type": "caption", "at": caption["at"], "end": nxt["end"],
                      "text": (caption["text"].rstrip() + " " + nxt["text"].lstrip()).strip()}
            highlight = nxt.get("highlight") or caption.get("highlight")
            if highlight and highlight in joined["text"]:
                joined["highlight"] = highlight
            merged_captions.append(joined)
            merged_count += 1
            index += 2
        else:
            merged_captions.append(caption)
            index += 1

    return others + merged_captions, {
        "keywords_moved": [{"from": a, "to": b, "text": t} for a, b, t in moved],
        "keywords_dropped": [{"at": a, "text": t} for a, t in dropped],
        "captions_merged": merged_count,
        "sfx_thinned": sfx_dropped,
    }


def _sfx_priority(name: str) -> int:
    return _SFX_PRIORITY.get(str(name or ""), 5)


def _thin_sfx(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Drop SFX that sit closer than MIN_SFX_GAP_SECONDS to a stronger/earlier cue.

    Card choreography used to stack riser (offset -0.5s) + whoosh on every card
    entrance — five cards = ten hits in pairs 0.5s apart, which listens as
    machine-gun. Prefer the whoosh (structural) and drop the riser; then enforce
    a global gap so bullet pops cannot fire every half-second either.
    """
    sfx = sorted(
        [e for e in events if e.get("type") == "sfx"],
        key=lambda e: (float(e.get("at") or 0), -_sfx_priority(str(e.get("name") or ""))),
    )
    kept_sfx: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for event in sfx:
        at = float(event.get("at") or 0)
        name = str(event.get("name") or "")
        conflict = next(
            (k for k in kept_sfx
             if abs(at - float(k.get("at") or 0)) < MIN_SFX_GAP_SECONDS),
            None,
        )
        if conflict is None:
            kept_sfx.append(event)
            continue
        # Same-time or near: keep higher priority; if equal, keep the earlier one.
        if _sfx_priority(name) > _sfx_priority(str(conflict.get("name") or "")):
            dropped.append({
                "at": float(conflict.get("at") or 0),
                "name": conflict.get("name"),
                "reason": f"thua ưu tiên trước {name}@{at:.2f}",
            })
            kept_sfx = [k for k in kept_sfx if k is not conflict]
            kept_sfx.append(event)
        else:
            dropped.append({
                "at": at,
                "name": name,
                "reason": f"gần {conflict.get('name')}@{float(conflict.get('at') or 0):.2f}",
            })

    if not dropped:
        return events, []
    drop_keys = {(round(float(d["at"]), 3), d["name"]) for d in dropped}
    kept_events = [
        e for e in events
        if e.get("type") != "sfx"
        or (round(float(e.get("at") or 0), 3), e.get("name")) not in drop_keys
    ]
    return kept_events, dropped
