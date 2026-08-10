"""Match declared b-roll source clips to `footage_edit_plan` beats by actual
visual content, instead of fabricating a visual for every `b_roll.needed`
beat.

Two concerns, deliberately split:
  - `describe_broll_clip()` -- genuinely requires visual judgment, so it
    reuses `footage_edit_analyzer`'s proven Gemini-video-upload mechanism
    (one extra, cheap call per candidate clip). Stochastic, like the rest
    of that tool.
  - `match_broll_to_beats()` -- once descriptions exist, matching them
    against a beat's `suggested_visual` is a plain scoring problem. Kept
    deterministic and dependency-free (word-overlap, no embedding API) so
    it is fully unit-testable and never itself a source of "why did it pick
    that clip" mystery.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

DEFAULT_MATCH_THRESHOLD = 0.35

# Words too generic to count as a real content match (they'd make almost
# any suggestion match almost any clip). Kept short and conservative --
# under-filtering is safer than over-filtering here, since a spurious match
# gets caught by the threshold anyway.
_STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "with", "and",
    "shot", "clip", "footage", "scene", "showing", "shows", "show",
}


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-zA-ZÀ-ỹ0-9]+", (text or "").lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def _similarity(a: str, b: str) -> float:
    """Word-overlap ratio (intersection / smaller set size).

    Deliberately not Jaccard (intersection / union): a short, precise
    suggestion ("phone screen with chatbot reply") matching a longer, more
    detailed description should still score high if every suggested word is
    present, rather than being diluted by the description's extra detail.
    """
    tokens_a, tokens_b = _tokenize(a), _tokenize(b)
    if not tokens_a or not tokens_b:
        return 0.0
    overlap = len(tokens_a & tokens_b)
    return overlap / min(len(tokens_a), len(tokens_b))


def match_broll_to_beats(
    beats: list[dict[str, Any]],
    broll_candidates: list[dict[str, Any]],
    threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> dict[int, dict[str, Any]]:
    """For each `b_roll.needed=true` beat, find the best-scoring candidate
    clip above `threshold`.

    Each candidate clip can match at most one beat (a real clip in a bin
    gets used once, not stretched across every vaguely-similar beat) --
    candidates are claimed greedily in descending score order.

    Returns `{beat_index: {"path", "content_description", "score"}}` for
    matched beats only. A beat missing from the result has no qualifying
    match and must fall back to no-insert (never a forced weak match, and
    never fabrication -- see `skills/pipelines/hybrid/edit-director.md`
    §6b).
    """
    candidates_pool = list(broll_candidates)
    scored: list[tuple[float, int, dict[str, Any]]] = []

    for beat in beats:
        b_roll = beat.get("b_roll") or {}
        if not b_roll.get("needed"):
            continue
        suggestion = b_roll.get("suggested_visual", "")
        for candidate in candidates_pool:
            score = _similarity(suggestion, candidate.get("content_description", ""))
            if score >= threshold:
                scored.append((score, int(beat["beat_index"]), candidate))

    scored.sort(key=lambda t: t[0], reverse=True)
    matches: dict[int, dict[str, Any]] = {}
    claimed_paths: set[str] = set()

    for score, beat_index, candidate in scored:
        if beat_index in matches:
            continue
        path = candidate.get("path")
        if path in claimed_paths:
            continue
        matches[beat_index] = {**candidate, "score": round(score, 3)}
        claimed_paths.add(path)

    return matches


def describe_broll_clip(client: Any, model: str, types: Any, path: Path) -> Optional[str]:
    """One short Gemini call: "what does this clip show?" Reuses
    `footage_edit_genai_client.upload_video` for the same proven
    upload path the main beats-analysis call already uses.

    Best-effort: returns None (never raises) on any failure -- a candidate
    clip that can't be described just never matches, same as if it had not
    been declared at all. This is intentionally NOT the JSON-repair path
    (`generate_with_repair`) since the response is one plain sentence, not
    structured data.
    """
    from tools.analysis.footage_edit_genai_client import upload_video

    try:
        part = upload_video(client, path, types)
        response = client.models.generate_content(
            model=model,
            contents=[
                "In one plain sentence, describe exactly what this video clip "
                "shows (subject, action, setting). No commentary, no markdown.",
                part,
            ],
        )
        text = (response.text or "").strip()
        return text or None
    except Exception:
        return None


def build_broll_candidates(
    client: Any, model: str, types: Any, broll_sources: list[str]
) -> list[dict[str, Any]]:
    """Describe every declared b-roll source path; skips (does not fabricate
    a description for) any clip Gemini couldn't describe."""
    candidates: list[dict[str, Any]] = []
    for source in broll_sources:
        path = Path(source)
        description = describe_broll_clip(client, model, types, path)
        if description:
            candidates.append({"path": str(path), "content_description": description})
    return candidates
