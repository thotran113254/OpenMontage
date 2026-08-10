"""Caption pass — the long, repetitive half of the edit, done in chunks.

Captions must cover 100% of the speech in 3-9 word pills. That is a lot of
output, and when it shared a call with the structure work the model ran out of
budget and dropped cards entirely. Here each call owns a bounded word range,
so the output stays short, coverage is checkable per chunk, and the chunks can
run concurrently.

Chunk boundaries are placed at the longest silences inside the target window —
using the spine's own gaps, so a pill never gets split mid-phrase.
"""

from __future__ import annotations

from typing import Any

from lib.talking_head_edit import prompt_registry

TARGET_CHUNK_WORDS = 110
SEARCH_WINDOW = 25          # how far around the target index to look for a pause


def chunk_ranges(words: list[dict[str, Any]], target: int = TARGET_CHUNK_WORDS) -> list[tuple[int, int]]:
    """Split [0, n) into inclusive ranges, cutting at the biggest pause nearby."""
    n = len(words)
    if n <= target:
        return [(0, n - 1)] if n else []

    ranges: list[tuple[int, int]] = []
    start = 0
    while start < n:
        if n - start <= target * 1.4:
            ranges.append((start, n - 1))
            break
        ideal = start + target
        low = max(start + 1, ideal - SEARCH_WINDOW)
        high = min(n - 1, ideal + SEARCH_WINDOW)
        # biggest silence between consecutive words in the window
        best_index, best_gap = ideal, -1.0
        for index in range(low, high):
            gap = float(words[index + 1]["start"]) - float(words[index]["end"])
            if gap > best_gap:
                best_index, best_gap = index, gap
        ranges.append((start, best_index))
        start = best_index + 1
    return ranges


def build_caption_prompt(words: list[dict[str, Any]], start: int, end: int,
                         version: str | None = None) -> str:
    """Prompt for one chunk. Only the chunk's words are sent, plus a little
    context either side so the model can see where a phrase begins and ends."""
    return prompt_registry.render("captions", {
        "start": start,
        "end": end,
        "context_before": " ".join(
            w["word"].strip() for w in words[max(0, start - 8):start]),
        "context_after": " ".join(w["word"].strip() for w in words[end + 1:end + 9]),
        "body": " ".join(f'{i}:{words[i]["word"].strip()}'
                         for i in range(start, end + 1)),
    }, version=version)
