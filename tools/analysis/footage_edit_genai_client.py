"""Gemini client helpers for FootageEditAnalyzer: upload, cost estimation,
and JSON-repair-retry generation.

Split out of footage_edit_analyzer.py to keep both files under the
project's ~200-line convention (CLAUDE.md "Consider Modularization").
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from tools.analysis.footage_edit_prompt import DEFAULT_MODEL, parse_json_response

# Empirically observed this session (proven prompt, 80s sample video,
# gemini-3.1-flash-lite/HIGH thinking): prompt_token_count=8037,
# candidates=5393, thoughts=1972 -> ~100 input tok/s of video, ~92 output
# tok/s. Rounded for a small safety margin. This is a pre-call ESTIMATE for
# cost announcement, not the billed total — see actual_cost() for the real
# run's usage-derived cost.
_VIDEO_INPUT_TOKENS_PER_SECOND = 100
_PROMPT_OVERHEAD_TOKENS = 700
_OUTPUT_TOKENS_PER_SECOND = 100
_OUTPUT_OVERHEAD_TOKENS = 500

PRICING_PER_MILLION = {
    "gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50},
    "gemini-3.5-flash": {"input": 1.50, "output": 9.00},
}

INLINE_UPLOAD_MAX_BYTES = 20 * 1024 * 1024  # Gemini inline-bytes limit


def estimate_cost(model: str, duration_seconds: float) -> float:
    """Pre-call cost estimate from video duration (see module docstring)."""
    price = PRICING_PER_MILLION.get(model, PRICING_PER_MILLION[DEFAULT_MODEL])
    input_tokens = duration_seconds * _VIDEO_INPUT_TOKENS_PER_SECOND + _PROMPT_OVERHEAD_TOKENS
    output_tokens = duration_seconds * _OUTPUT_TOKENS_PER_SECOND + _OUTPUT_OVERHEAD_TOKENS
    return round(
        (input_tokens / 1_000_000) * price["input"] + (output_tokens / 1_000_000) * price["output"], 4
    )


def actual_cost(model: str, usage: Any) -> float:
    """Post-call cost from the response's real usage_metadata."""
    if not usage:
        return 0.0
    price = PRICING_PER_MILLION.get(model, PRICING_PER_MILLION[DEFAULT_MODEL])
    prompt_tok = getattr(usage, "prompt_token_count", 0) or 0
    output_tok = (getattr(usage, "candidates_token_count", 0) or 0) + (
        getattr(usage, "thoughts_token_count", 0) or 0
    )
    return round(
        (prompt_tok / 1_000_000) * price["input"] + (output_tok / 1_000_000) * price["output"], 4
    )


def upload_video(client: Any, input_path: Path, types: Any) -> Any:
    """Upload via File API for large files, inline bytes for small ones."""
    size = input_path.stat().st_size
    if size <= INLINE_UPLOAD_MAX_BYTES:
        return types.Part.from_bytes(data=input_path.read_bytes(), mime_type="video/mp4")

    uploaded = client.files.upload(file=str(input_path))
    elapsed = 0
    while uploaded.state.name == "PROCESSING" and elapsed < 300:
        time.sleep(5)
        uploaded = client.files.get(name=uploaded.name)
        elapsed += 5
    if uploaded.state.name == "FAILED":
        raise RuntimeError("Gemini File API processing failed")
    if uploaded.state.name == "PROCESSING":
        raise TimeoutError("Gemini File API processing timed out (300s)")
    return uploaded


class GeminiJSONError(Exception):
    """Raised when Gemini's JSON response is unparseable even after one
    strict-repair retry. Carries the raw text so the caller can attach it
    to a failed ToolResult for debugging (never silently discarded)."""

    def __init__(self, message: str, raw_text: str | None):
        super().__init__(message)
        self.raw_text = raw_text


def generate_with_repair(
    client: Any, model: str, prompt: str, part: Any, config: Any
) -> tuple[dict[str, Any], Any, bool]:
    """Call generate_content; on invalid JSON, retry once with a strict
    repair instruction. Returns (parsed_dict, last_response, repair_attempted).

    Raises GeminiJSONError (with raw_text attached) if the second attempt is
    still invalid JSON, or the underlying API exception on a hard failure.
    """
    response = client.models.generate_content(model=model, contents=[prompt, part], config=config)
    try:
        return parse_json_response(response.text), response, False
    except Exception:
        repair_prompt = (
            prompt
            + "\n\nYour previous response was not valid JSON. Return ONLY "
            "the JSON object, no markdown fences, no commentary."
        )
        response = client.models.generate_content(model=model, contents=[repair_prompt, part], config=config)
        try:
            return parse_json_response(response.text), response, True
        except Exception as exc:
            raise GeminiJSONError(
                f"Gemini returned invalid JSON twice (repair failed): {exc}",
                getattr(response, "text", None),
            ) from exc
