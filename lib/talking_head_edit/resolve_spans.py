"""Word indices → spans of source time. Pure arithmetic, no ffmpeg.

This is the only place a word index becomes a second, and it is deliberately
separated from `resolve_cut` (which spends those seconds on ffmpeg) along the
line that decides how a bug is found: everything here is checkable by reading
numbers, so it is covered by tests that need no media at all. A wrong span is a
wrong number — a wrong *encode* needs a file to see.

Read with `resolve_cut`: this module says WHICH pieces, that one produces them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.talking_head_edit.resolve_media import ResolveError

PAD = 0.08          # keep this much speech next to a cut edge
# A 0.1s "À" padded to ~0.22s is not worth a jump cut: the seam + expander
# swallow the neighbouring phoneme and the viewer hears a missing word.
MIN_CUT = 0.28      # skip cuts shorter than this — not worth an edit point
# A kept piece shorter than this with no word in it is the PAD tail left between
# a cut and a run edge: two frames of silence that only add a jump cut.
MIN_SLIVER = 0.25
# Above this many spans, adjacent ones get merged first: 80 ffmpeg invocations
# plus 80 temp files costs more in process and I/O overhead than it saves.
MAX_SPANS_BEFORE_MERGE = 80


def cut_window(pair: list[int], words: list[dict[str, Any]]) -> tuple[float, float]:
    """The time span a [w0, w1] cut actually removes: the words plus the silence
    around them, less `PAD` of breathing room on each side."""
    n = len(words)
    a, b = sorted(max(0, min(n - 1, int(i))) for i in pair[:2])
    start = min(float(words[a - 1]["end"]) + PAD, float(words[a]["start"])) if a > 0 \
        else float(words[a]["start"])
    end = max(float(words[b + 1]["start"]) - PAD, float(words[b]["end"])) if b + 1 < n \
        else float(words[b]["end"])
    return round(start, 3), round(end, 3)


def is_worth_cutting(pair: list[int], words: list[dict[str, Any]]) -> bool:
    start, end = cut_window(pair, words)
    return end - start >= MIN_CUT


def compute_removes(cuts: list[list[int]], words: list[dict[str, Any]]) -> list[tuple[float, float]]:
    """Word-index cut list → merged, padded time spans to remove."""
    spans = [cut_window(pair, words) for pair in cuts
             if pair and len(pair) >= 2 and is_worth_cutting(pair, words)]

    spans.sort()
    merged: list[tuple[float, float]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1] + 0.02:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def kept_spans(removes: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    kept: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in removes:
        if start > cursor:
            kept.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration:
        kept.append((cursor, duration))
    return kept or [(0.0, duration)]


@dataclass(frozen=True)
class Span:
    """One kept piece of one source, in that source's own time base."""
    src_id: str
    path: Path
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def source_runs(words: list[dict[str, Any]]) -> list[tuple[str, int, int]]:
    """Contiguous (src, first, last) blocks over the word list.

    A run is where one file's material plays without interruption. Runs — not
    sources — are the unit of cutting, because after `select` the same source can
    contribute two separate blocks with another source's block between them.
    """
    if not words:
        return []
    runs: list[tuple[str, int, int]] = []
    current = str(words[0].get("src") or "s0")
    start = 0
    for index in range(1, len(words)):
        src = str(words[index].get("src") or "s0")
        if src != current:
            runs.append((current, start, index - 1))
            current, start = src, index
        # A jump in the underlying word index means select dropped words in
        # between, so the material is not contiguous even within one source.
        elif (words[index].get("_orig_index") is not None
              and int(words[index]["_orig_index"]) != int(words[index - 1]["_orig_index"]) + 1):
            runs.append((current, start, index - 1))
            start = index
    runs.append((current, start, len(words) - 1))
    return runs


def plan_spans(words: list[dict[str, Any]], cut_pairs: list[list[int]],
               path_for: dict[str, Path],
               durations: dict[str, float] | None = None
               ) -> tuple[list[Span], list[dict[str, Any]]]:
    """Director word indices + cut list → spans to extract, in playback order.

    Cuts are applied inside each run, never across one: a cut that straddled a
    run boundary would be asking to remove the join between two different takes,
    which is not a thing that exists in the source files.

    Run windows follow one rule with a measured reason behind it:

    * the FIRST run keeps everything from second 0, and the LAST run keeps
      everything to the end of its source — this is what the single-filtergraph
      path did, and matching it is what keeps a one-source job producing the same
      video (measured: word-tight windows made a 90.8 s edit come out at 89.0 s
      by silently dropping the head and tail room tone)
    * every INTERNAL run is word-tight plus `PAD`, because that is a join between
      two takes, and carrying one take's trailing silence into the next take's
      opening word is a pause nobody asked for

    Returns (spans, removes) where `removes` is the per-source report of what was
    dropped, for the resolve report.
    """
    spans: list[Span] = []
    removes_report: list[dict[str, Any]] = []
    runs = source_runs(words)
    lengths = durations or {}

    for position, (src_id, first, last) in enumerate(runs):
        run_words = words[first:last + 1]
        local_cuts: list[list[int]] = []
        for pair in cut_pairs or []:
            if not pair or len(pair) < 2:
                continue
            a, b = int(pair[0]), int(pair[1])
            if b < a:
                a, b = b, a
            # Clip to this run; skip cuts that do not touch it at all.
            a, b = max(a, first), min(b, last)
            if a > b:
                continue
            local_cuts.append([a - first, b - first])

        removes = compute_removes(local_cuts, run_words)
        source_length = float(lengths.get(src_id) or 0.0)
        is_first, is_last = position == 0, position == len(runs) - 1
        window_start = 0.0 if is_first else max(0.0, float(run_words[0]["start"]) - PAD)
        word_end = float(run_words[-1]["end"])
        window_end = (max(word_end, source_length) if is_last and source_length
                      else word_end + PAD)

        path = path_for.get(src_id)
        if path is None:
            raise ResolveError(
                f"Không tra được file nguồn cho '{src_id}' — mọi event phải map về "
                "một nguồn có thật, dừng lại thay vì cắt sai file."
            )

        kept = [(max(start, window_start), end)
                for start, end in kept_spans(removes, window_end)]
        kept = [(start, end) for start, end in kept
                if end - start > 0 and not _is_empty_sliver(start, end, run_words)]
        # Cuts that leave nothing but a sliver remove the whole run: after audit,
        # that is a decision (often the user dropping a whole take), not an
        # accident. Only a single-run video keeps it — an empty video is not one.
        if sum(end - start for start, end in kept) <= MIN_CUT:
            if len(runs) > 1:
                removes_report.append({"src": src_id, "start": round(window_start, 3),
                                       "end": round(window_end, 3),
                                       "seconds": round(window_end - window_start, 3)})
                continue
            kept = [(window_start, window_end)]
            removes = []

        for start, end in kept:
            spans.append(Span(src_id, Path(path), round(start, 3), round(end, 3)))
        for start, end in removes:
            removes_report.append({"src": src_id, "start": start, "end": end,
                                   "seconds": round(end - start, 3)})

    if not spans:
        raise ResolveError("Sau khi cắt không còn đoạn nào — kiểm tra lại danh sách cut.")
    return spans, removes_report


def _is_empty_sliver(start: float, end: float, words: list[dict[str, Any]]) -> bool:
    if end - start >= MIN_SLIVER:
        return False
    return not any(start <= (float(w["start"]) + float(w["end"])) / 2 <= end for w in words)


def merge_adjacent_spans(spans: list[Span], limit: int = MAX_SPANS_BEFORE_MERGE) -> list[Span]:
    """Join touching spans of the same source when there are too many.

    Only when over the limit, and only for spans that are genuinely contiguous —
    merging a gap would un-do a cut the director asked for.
    """
    if len(spans) <= limit:
        return spans
    merged: list[Span] = []
    for span in spans:
        previous = merged[-1] if merged else None
        if (previous and previous.src_id == span.src_id
                and abs(span.start - previous.end) < 0.005):
            merged[-1] = Span(previous.src_id, previous.path, previous.start, span.end)
        else:
            merged.append(span)
    return merged
