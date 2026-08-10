"""Join per-source transcripts into one word spine (spine v3).

The contract with the director does not change and must not: it still sees a
flat list of `index:word` and nothing else. It does not know how many files
there are, and it never learns a path or a second.

Two decisions here are load-bearing:

* **`start`/`end` stay in each source's own time base.** Converting them to a
  joined-timeline second would mean regenerating the entire spine every time the
  source order changes, and `resolve` needs the per-source time anyway to cut the
  right file. So each word carries `src` and `resolve` looks it up.
* **B-roll never enters the spine.** A silent clip has no words; a clip with
  incidental talking would inject words that are not part of the spoken thread,
  and every index after that point would refer to something the speaker never
  said. B-roll goes to `overlay_pool` instead.
"""

from __future__ import annotations

from typing import Any

from lib.talking_head_edit.asr.base import SPINE_SCHEMA

MULTISOURCE_SCHEMA = 3


def build(per_source: dict[str, dict[str, Any]],
          sources: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-source spines + source specs → one spine v3.

    `per_source` is keyed by source id and only needs entries for speaking
    sources; b-roll has no transcript to contribute.
    """
    from lib.talking_head_edit import sources as sources_mod

    speaking = sources_mod.aroll(sources)
    overlays = sources_mod.broll(sources)

    words: list[dict[str, Any]] = []
    takes: list[dict[str, Any]] = []
    audio_events: list[dict[str, Any]] = []
    speakers: list[str] = []
    providers: list[str] = []
    models: list[str] = []
    languages: list[str] = []
    duration_total = 0.0
    fell_back = False

    for spec in speaking:
        source_id = str(spec["id"])
        spine = per_source.get(source_id)
        if not spine:
            continue
        source_words = spine.get("word_timestamps") or []
        if not source_words:
            continue

        first_index = len(words)
        for word in source_words:
            words.append({**word, "src": source_id})
        takes.append({
            "src": source_id,
            "w0": first_index,
            "w1": len(words) - 1,
            "take_group": str(spec.get("take_group") or "main"),
            "order": int(spec.get("order", 0)),
        })

        for event in spine.get("audio_events") or []:
            audio_events.append({**event, "src": source_id})
        for speaker in spine.get("speakers") or []:
            # Speaker labels are per-file, so the same "speaker_0" in two files
            # is probably two different people. Namespacing keeps them apart.
            label = speaker if len(speaking) == 1 else f"{source_id}:{speaker}"
            if label not in speakers:
                speakers.append(label)
        providers.append(str(spine.get("provider") or ""))
        models.append(str(spine.get("model") or ""))
        if spine.get("language"):
            languages.append(str(spine["language"]))
        duration_total += float(spine.get("duration_seconds") or spec.get("duration") or 0.0)
        fell_back = fell_back or bool(spine.get("asr_fallback"))

    if len(speaking) > 1:
        # Namespaced labels above only kick in for multi-source, so re-stamp the
        # words to match or the two lists disagree.
        for word in words:
            if word.get("speaker"):
                word["speaker"] = f"{word['src']}:{word['speaker']}"

    return {
        "schema": MULTISOURCE_SCHEMA,
        "provider": _one_or_mixed(providers),
        "model": _one_or_mixed(models),
        "language": _one_or_mixed(languages),
        "duration_seconds": round(duration_total, 3),
        "sources": [dict(spec) for spec in sorted(
            (dict(s) for s in sources), key=lambda s: int(s.get("order", 0)))],
        "takes": takes,
        "word_timestamps": words,
        "overlay_pool": [
            {"src": str(spec["id"]),
             "duration": float(spec.get("duration") or 0.0),
             "label": str(spec.get("label") or spec["id"]),
             "path": spec.get("path", "")}
            for spec in overlays
        ],
        "audio_events": audio_events,
        "speakers": speakers,
        "asr_fallback": fell_back,
    }


def _one_or_mixed(values: list[str]) -> str:
    unique = [v for v in dict.fromkeys(values) if v]
    if not unique:
        return ""
    return unique[0] if len(unique) == 1 else "mixed"


def upgrade_v2(spine: dict[str, Any], source_id: str = "s0",
               source: dict[str, Any] | None = None) -> dict[str, Any]:
    """A single-source spine v2 read as v3, so old jobs keep running.

    Word order and indices are untouched — that is the whole point of this
    function. It only adds the fields the multi-source code paths look for.
    """
    if int(spine.get("schema", SPINE_SCHEMA)) >= MULTISOURCE_SCHEMA:
        return spine
    words = [{**w, "src": source_id} for w in spine.get("word_timestamps") or []]
    spec = source or {"id": source_id, "path": "", "role": "aroll", "order": 0,
                      "take_group": "main", "speech": True,
                      "duration": spine.get("duration_seconds") or 0.0}
    return {
        **spine,
        "schema": MULTISOURCE_SCHEMA,
        "sources": [spec],
        "takes": [{"src": source_id, "w0": 0, "w1": max(0, len(words) - 1),
                   "take_group": "main", "order": 0}],
        "word_timestamps": words,
        "overlay_pool": spine.get("overlay_pool") or [],
    }


def source_map(spine: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(s["id"]): s for s in spine.get("sources") or []}


def normalise_ranges(ranges: list[list[int]] | None, word_count: int) -> list[tuple[int, int]]:
    """Clamp, order and merge word-index ranges. Empty input = keep everything.

    Overlapping ranges are merged rather than rejected: the caller (`select`)
    already refuses a proposal whose ranges overlap, so anything reaching here
    overlapping is our own arithmetic, and duplicating a word would corrupt the
    index mapping in a way that is very hard to see later.
    """
    if not ranges:
        return [(0, word_count - 1)] if word_count else []

    spans: list[tuple[int, int]] = []
    for pair in ranges:
        if not pair or len(pair) < 2:
            continue
        start, end = int(pair[0]), int(pair[1])
        if end < start:
            start, end = end, start
        start = max(0, min(word_count - 1, start))
        end = max(0, min(word_count - 1, end))
        spans.append((start, end))

    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def filter_words(words: list[dict[str, Any]],
                 ranges: list[list[int]] | None) -> list[dict[str, Any]]:
    """Words inside `ranges`, re-indexed from 0, each keeping `_orig_index`.

    `_orig_index` is mandatory, not a convenience: after `select` drops a take,
    the director's word 42 is not the spine's word 42, and `resolve` cuts media
    by looking the original word back up. Lose it and the cut lands in the wrong
    place in the wrong file — the worst failure this pipeline has.
    """
    kept: list[dict[str, Any]] = []
    for start, end in normalise_ranges(ranges, len(words)):
        for index in range(start, end + 1):
            kept.append({**words[index], "_orig_index": index})
    return kept


def orig_indices(filtered: list[dict[str, Any]]) -> list[int]:
    """filtered index → original index, as a plain list (the inverse is a dict)."""
    return [int(w.get("_orig_index", i)) for i, w in enumerate(filtered)]


def to_filtered_index(filtered: list[dict[str, Any]]) -> dict[int, int]:
    return {int(w.get("_orig_index", i)): i for i, w in enumerate(filtered)}


def coverage(ranges: list[list[int]] | None, word_count: int) -> float:
    """Fraction of the spine the ranges keep. Used by `select`'s guards."""
    if not word_count:
        return 0.0
    kept = sum(end - start + 1 for start, end in normalise_ranges(ranges, word_count))
    return round(kept / word_count, 4)


def source_boundaries(spine: dict[str, Any]) -> list[int]:
    """First word index of every source after the first.

    This is what `compact_spine` turns into `--- NGUỒN k ---` markers, so the
    director can see where one take ends and the next begins without being told
    a filename.
    """
    takes = sorted(spine.get("takes") or [], key=lambda t: int(t.get("w0", 0)))
    return [int(take["w0"]) for take in takes[1:]]
