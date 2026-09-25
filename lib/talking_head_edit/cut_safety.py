"""Mechanical safety net on cuts, applied after the LLM verifier.

The verifier is good at meaning, but short ASR tokens that LOOK like fillers
and are actually content ("tết" for "test", brand shards, numbers) still slip
through as remove. A tiny lexicon gate blocks those without re-calling the model.

How much it lets through depends on `cut_level`, the user's own dial:

* light  — only pure fillers may leave as a short (<=2 token) cut
* normal — same gate; this is the default
* tight  — short verifier-approved repeats, false starts and stretched
            hesitations ("vàaa…", which no lexicon lists) may leave too

Duration is NOT judged here. Whether a cut is worth an edit point depends on
the silence around the word, which only `resolve_spans.cut_window` measures —
an ASR "ờ" is often 0.06s long inside a full second of pause.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

CUT_LEVELS = ("light", "normal", "tight")
DEFAULT_CUT_LEVEL = "normal"

# Pure hesitation / empty particles. Anything not in here (for single-token cuts)
# is treated as content and kept. Multi-token cuts still rely on the verifier.
_FILLER_TOKENS = {
    # Sound-only hesitations. Do NOT put grammar words (thì/là/mà) here —
    # cutting them mid-sentence is exactly the "gãy câu" failure mode.
    "à", "á", "ạ", "ờ", "ừ", "ừm", "ờm", "ơ", "ư", "ê", "ế",
    "uh", "um", "uhm", "ah", "er", "hmm", "hm", "mm", "mhm",
    "ờờ", "ừừ",
}
# Reasons for which `tight` trusts the verifier on a short non-lexicon span.
_TIGHT_REASONS = {"repeat", "false_start", "soft_restart", "filler"}

# Asked for in plain words when the level was left on auto.
_TIGHT_WORDS = re.compile(r"cắt\s+(kỹ|kĩ|mạnh|sạch|gọn|hết|nhiều)|cắt\s+.{0,20}(vấp|lặp)", re.I)
_LIGHT_WORDS = re.compile(r"cắt\s+(nhẹ|ít)|ít\s+cắt|không\s+cắt|giữ\s+nguyên\s+lời", re.I)

_PUNCT = re.compile(r"^[\W_]+|[\W_]+$", re.UNICODE)


def resolve_cut_level(options: dict[str, Any]) -> str:
    """The explicit `cut_level`, else what the user's prompt asks for, else normal."""
    level = str(options.get("cut_level") or "").strip().lower()
    if level in CUT_LEVELS:
        return level
    prompt = str(options.get("prompt") or "")
    if _TIGHT_WORDS.search(prompt):
        return "tight"
    if _LIGHT_WORDS.search(prompt):
        return "light"
    return DEFAULT_CUT_LEVEL


# Who proposed a cut. A range the user marked themselves is a decision, not a
# suggestion: it skips the verifier and the lexicon gate.
USER_CUT = "khach"


def current_proposals(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """What `audit` decides on. An empty `cut_proposed` means "nothing proposed" —
    falling back to `cut_remove` on emptiness resurrected the last cut the user
    had just restored."""
    raw = spec["cut_proposed"] if "cut_proposed" in spec else spec.get("cut_remove")
    return normalize_cut_proposals(raw or [])


def normalize_cut_proposals(raw: list[Any]) -> list[dict[str, Any]]:
    """Every proposal as {"w": [a, b], "ly_do", "nguon"}; bare pairs are the director's.

    Malformed entries are skipped, never raised on: one bad cut from a model
    must not sink every other point of the same revise. `count_malformed` says
    how many were skipped.
    """
    proposals: list[dict[str, Any]] = []
    for item in raw or []:
        entry = dict(item) if isinstance(item, dict) else {"w": item}
        span = entry.get("w")
        try:
            entry["w"] = sorted((int(span[0]), int(span[1])))
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        proposals.append(entry)
    return proposals


def count_malformed(raw: list[Any]) -> int:
    return len(raw or []) - len(normalize_cut_proposals(raw))


def _norm(token: str) -> str:
    return _PUNCT.sub("", unicodedata.normalize("NFC", (token or "").strip().lower()))


def is_pure_filler(text: str) -> bool:
    """True only when the whole cut span is hesitation/empty particles."""
    parts = [p for p in (_norm(t) for t in re.split(r"\s+", text.strip())) if p]
    return all(p in _FILLER_TOKENS for p in parts)


def filter_unsafe_cuts(
    accepted: list[list[int]],
    words: list[dict[str, Any]],
    level: str = DEFAULT_CUT_LEVEL,
    reasons: dict[tuple[int, int], str] | None = None,
) -> tuple[list[list[int]], list[dict[str, Any]]]:
    """Drop verifier-accepted cuts that would remove content.

    `reasons` maps (w0, w1) to the director's `ly_do`, which `tight` consults.
    Returns (kept [w0, w1] pairs, rejected entries with a reason).
    """
    kept: list[list[int]] = []
    rejected: list[dict[str, Any]] = []
    reasons = reasons or {}

    for start, end in accepted:
        if start < 0 or end >= len(words) or end < start:
            rejected.append({"w": [start, end], "text": "", "reason": "ngoài phạm vi lời"})
            continue
        text = " ".join(str(words[i].get("word") or "").strip() for i in range(start, end + 1))
        short = end - start + 1 <= 2
        trusted = level == "tight" and reasons.get((start, end)) in _TIGHT_REASONS
        if short and not is_pure_filler(text) and not trusted:
            rejected.append({"w": [start, end], "text": text,
                             "reason": "từ ngắn không phải tiếng đệm — giữ để khỏi gãy câu"
                                       + ("" if level == "tight" else " (mức cắt kỹ sẽ cho cắt nếu là lặp/nói hỏng)")})
            continue
        kept.append([start, end])
    return kept, rejected
