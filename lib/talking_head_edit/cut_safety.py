"""Mechanical safety net on cut_remove after the LLM verifier.

The verifier is good at meaning, but short ASR tokens that LOOK like fillers
and are actually content ("tết" for "test", brand shards, numbers) still slip
through as remove. A tiny lexicon gate blocks those without re-calling the model.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# Pure hesitation / empty particles. Anything not in here (for single-token cuts)
# is treated as content and kept. Multi-token cuts still rely on the verifier.
_FILLER_TOKENS = {
    # Sound-only hesitations. Do NOT put grammar words (thì/là/mà) here —
    # cutting them mid-sentence is exactly the "gãy câu" failure mode.
    "à", "á", "ạ", "ờ", "ừ", "ừm", "ờm", "ơ", "ư", "ê", "ế",
    "uh", "um", "uhm", "ah", "er", "hmm", "hm", "mm", "mhm",
    "ờờ", "ừừ",
}

# Strip punctuation / case for matching.
_PUNCT = re.compile(r"^[\W_]+|[\W_]+$", re.UNICODE)


def _norm(token: str) -> str:
    text = unicodedata.normalize("NFC", (token or "").strip().lower())
    text = _PUNCT.sub("", text)
    return text


def is_pure_filler(text: str) -> bool:
    """True only when the whole cut span is hesitation/empty particles."""
    parts = [_norm(p) for p in re.split(r"\s+", text.strip()) if _norm(p)]
    if not parts:
        return True
    return all(p in _FILLER_TOKENS for p in parts)


def filter_unsafe_cuts(
    accepted: list[Any],
    words: list[dict[str, Any]],
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Drop accepted cuts that would remove non-filler content.

    Returns (kept_cuts_as_pairs, rejected_with_reasons).
    """
    kept: list[Any] = []
    rejected: list[dict[str, Any]] = []
    n = len(words)

    for item in accepted:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            start, end = int(item[0]), int(item[1])
        elif isinstance(item, dict) and item.get("w"):
            start, end = int(item["w"][0]), int(item["w"][1])
        else:
            rejected.append({"cut": item, "reason": "dạng cut không đọc được"})
            continue

        if start < 0 or end >= n or end < start:
            rejected.append({"w": [start, end], "reason": "ngoài phạm vi spine"})
            continue

        cut_text = " ".join(
            str(words[i].get("word") or "").strip() for i in range(start, end + 1)
        )
        # Single-token or short cuts: only pure fillers may leave.
        # Longer false-start/repeat spans still need the verifier's join check
        # but we refuse anything that is clearly a content-looking single word.
        token_count = end - start + 1
        if token_count <= 2 and not is_pure_filler(cut_text):
            rejected.append({
                "w": [start, end],
                "text": cut_text,
                "reason": "từ/cụm ngắn không phải filler — giữ để khỏi gãy câu",
            })
            continue

        kept.append([start, end] if not isinstance(item, dict) else item)

    # Normalise kept to plain pairs for the resolver.
    pairs: list[list[int]] = []
    for item in kept:
        if isinstance(item, (list, tuple)):
            pairs.append([int(item[0]), int(item[1])])
        else:
            pairs.append([int(item["w"][0]), int(item["w"][1])])
    return pairs, rejected
