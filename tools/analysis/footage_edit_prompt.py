"""Prompt building, inventory loading, and response validation helpers for
``FootageEditAnalyzer`` (tools/analysis/footage_edit_analyzer.py).

Split out of the tool module to keep both files under the ~200-line
project convention (CLAUDE.md "Consider Modularization").
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from tools.analysis.footage_edit_disfluency_prompt import (
    DISFLUENCY_INSTRUCTIONS,
    DISFLUENCY_SCHEMA_SNIPPET,
)

DEFAULT_MODEL = "gemini-3.1-flash-lite"
SUPPORTED_MODELS = ["gemini-3.1-flash-lite", "gemini-3.5-flash"]
DEFAULT_THINKING_LEVEL = "HIGH"
SUPPORTED_THINKING_LEVELS = ["MINIMAL", "LOW", "MEDIUM", "HIGH"]


def load_inventory(inventory_path: str | Path) -> dict[str, Any]:
    """Load resource_inventory.json (Phase 01b) and return the raw dict.

    Raises FileNotFoundError / ValueError with actionable messages — the
    caller (FootageEditAnalyzer) turns these into a failed ToolResult
    instead of a free-choice fallback (constraining Gemini to real
    resources is the whole point of this tool).
    """
    path = Path(inventory_path)
    if not path.exists():
        raise FileNotFoundError(
            f"resource_inventory.json not found at {path}. Run Phase 01b "
            "(assets_library/resource_inventory.json) before using this tool."
        )
    with open(path, encoding="utf-8") as f:
        inventory = json.load(f)
    if "sound_effects" not in inventory or "transitions" not in inventory:
        raise ValueError(
            f"{path} is missing required 'sound_effects' or 'transitions' keys."
        )
    return inventory


def inventory_names(inventory: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Extract (transition_names, sfx_names) lists from a loaded inventory."""
    transitions = [t["name"] for t in inventory.get("transitions", [])]
    sfx = [s["name"] for s in inventory.get("sound_effects", [])]
    return transitions, sfx


def render_user_style_rules(profile: dict[str, Any]) -> str:
    """Convert a user_style_profile (exported by
    tools/editor/edit-decisions-editor.html) into concrete MUST-follow prompt
    rules. The profile captures how the USER actually edited a sample video,
    so these constraints outrank generic playbook aesthetics.
    """
    rules: list[str] = []
    ratio = profile.get("overlay_time_ratio")
    if ratio is not None:
        rules.append(
            f"Total time covered by card/overlay beats must stay around "
            f"{round(ratio * 100)}% of the video — do NOT exceed it."
        )
    scrim = profile.get("avg_scrim")
    if scrim is not None:
        rules.append(
            f"When a card sits over the footage, dim the footage with a scrim "
            f"of about {scrim} opacity — never darker."
        )
    zoom_ratio = profile.get("zoomed_cut_ratio")
    if zoom_ratio is not None:
        rules.append(
            f"Only about {round(zoom_ratio * 100)}% of beats should carry any "
            f"zoom; the rest stay static."
        )
    card_types = profile.get("card_types_used")
    if card_types:
        rules.append(
            "Prefer these card types the user actually kept: "
            + ", ".join(str(t) for t in card_types if t) + "."
        )
    trans = profile.get("transitions_used")
    if isinstance(trans, dict):
        if trans:
            rules.append(
                "Transitions the user used (with counts): "
                + ", ".join(f"{k}×{v}" for k, v in trans.items())
                + " — match this density, everything else is a hard cut."
            )
        else:
            rules.append("The user used NO soft transitions — hard cuts only.")
    # Free-form rules the numeric fields can't express (e.g. "always
    # word-level captions", "no SFX", "punch-zoom as hard cuts not animated").
    for note in profile.get("notes", []) or []:
        rules.append(str(note))
    if not rules:
        return ""
    return (
        "\nLearned user editing style (from the user's own sample edit — "
        "these OVERRIDE the playbook rules above when they conflict):\n"
        + "\n".join(f"- {r}" for r in rules) + "\n"
    )


def build_prompt(
    playbook: dict[str, Any],
    transitions: list[str],
    sound_effects: list[str],
    duration_seconds: float | None = None,
    user_style_profile: dict[str, Any] | None = None,
) -> str:
    """Build the constrained-choice Gemini prompt.

    Starts from the proven edit_workflow_prompt.txt structure (validated
    against a real sample video this session) but replaces the free-choice
    transition/sound_effect enums with the real, injected inventory lists
    plus an explicit anti-repetition instruction — the architectural fix
    for the earlier flash-lite "repetitive cut" finding.
    """
    motion = playbook.get("motion", {})
    pacing = motion.get("pacing_rules", {})
    min_hold = pacing.get("min_scene_hold_seconds", 3)
    max_hold = pacing.get("max_scene_hold_seconds", 8)
    quality_rules = playbook.get("quality_rules", [])
    quality_rules_text = "\n".join(f"- {rule}" for rule in quality_rules) or "- (none)"

    transitions_list = ", ".join(f'"{t}"' for t in transitions)
    sfx_list = ", ".join(f'"{s}"' for s in sound_effects)

    duration_instruction = (
        f"The video is EXACTLY {duration_seconds:.1f} seconds long (verified by ffprobe, not your own estimate). "
        f"Your transcript and beats MUST cover the ENTIRE video, from 00:00 to "
        f"{int(duration_seconds // 60):02d}:{duration_seconds % 60:04.1f}. "
        "Do NOT stop early just because the narrative feels complete — talking-head videos often continue past "
        "the last key point with a call-to-action, recap, or sign-off. The final beat's end_time MUST land at "
        "the real duration above, not before it. If you reach what feels like a natural conclusion with time "
        "still remaining, keep transcribing — there is more spoken content after it."
        if duration_seconds
        else "Cover the entire video from start to true end — do not stop early once the narrative feels complete."
    )

    user_style_text = render_user_style_rules(user_style_profile or {})

    return f"""You are a professional short-form video editor analyzing a talking-head video to produce a machine-readable EDIT PLAN for re-cutting it with maximum engagement, following the style playbook rules below.

{duration_instruction}

Style playbook rules (MUST follow):
{quality_rules_text}
{user_style_text}
Steps:
1. Transcribe all spoken narration with accurate timestamps (MM:SS format), segmented by natural sentence/phrase boundaries.
2. Break the video into "beats" of roughly {min_hold}-{max_hold} seconds each. Beat boundaries MUST align with natural speech rhythm (end of a sentence, end of a clause, a pause, or a strong emphasis word) — NEVER cut a beat mid-word or mid-idea. A beat can be shorter or longer ONLY if forcing it into range would break a sentence.
3. For EACH beat, decide:
   - zoom: whether the edit should zoom_in, zoom_out, or stay static during this beat, and WHY. Zoom changes should feel motivated by the rhythm of speech, not mechanical/every-beat.
   - transition: the cut style INTO this beat and why. YOU MAY ONLY CHOOSE FROM THIS EXACT LIST — never invent a value not on it: [{transitions_list}]. Vary your choice across beats: do not repeat the same transition on more than ~2 consecutive beats unless the rhythm truly demands a hard cut throughout.
   - b_roll: whether cutting away to a b-roll/supporting visual would strengthen this beat, and a concrete suggestion of what that b-roll should show.
   - sound_effect: whether a sound effect should trigger on this beat's cut or on a specific keyword, and why. YOU MAY ONLY CHOOSE FROM THIS EXACT LIST (or "none") — never invent a value not on it: [{sfx_list}, "none"]. Vary your choice across beats: do not repeat the same effect on two consecutive beats.
4. Also give an overall pacing_profile: total beats, average beat length, and a one-line "editing philosophy" that ties the zoom/transition/sfx choices together into a consistent style.
{DISFLUENCY_INSTRUCTIONS}

Output STRICT JSON only, matching this schema exactly (no markdown fences, no commentary):

{{
  "source_duration_seconds": number,
  "transcript": [
    {{"start": "MM:SS", "end": "MM:SS", "text": string}}
  ],
  "pacing_profile": {{
    "total_beats": number,
    "avg_beat_length_seconds": number,
    "editing_philosophy": string
  }},
  "beats": [
    {{
      "beat_index": number,
      "start_time": "MM:SS",
      "end_time": "MM:SS",
      "spoken_text": string,
      "zoom": {{
        "action": "zoom_in" | "zoom_out" | "static",
        "reason": string,
        "recommended_scale_range": string
      }},
      "transition": {{
        "type": string,
        "reason": string
      }},
      "b_roll": {{
        "needed": boolean,
        "suggested_visual": string,
        "reason": string
      }},
      "sound_effect": {{
        "type": string,
        "trigger": string,
        "reason": string
      }}
    }}
  ]{DISFLUENCY_SCHEMA_SNIPPET}
}}
"""


def strip_json_fences(text: str) -> str:
    """Strip ```json ... ``` / ``` ... ``` markdown fences if present."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def parse_json_response(text: str) -> dict[str, Any]:
    """Parse a Gemini text response as JSON, tolerating markdown fences.

    Raises json.JSONDecodeError on failure — caller decides whether to
    retry with a repair prompt.
    """
    return json.loads(strip_json_fences(text))


def validate_and_clamp_beats(
    beats: list[dict[str, Any]],
    transitions: list[str],
    sound_effects: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Assert every beat's transition/sfx choice is inventory-constrained.

    Deviation policy (documented, not a silent workaround): if Gemini still
    names a value outside the injected lists, we CLAMP it to the safe
    "none" default (guarantees the artifact is always renderable) AND
    record the violation in the returned list so it surfaces in
    `_analysis_meta.inventory_violations` for QA — clamp-and-flag, not
    clamp-and-hide.
    """
    valid_sfx = set(sound_effects) | {"none"}
    violations: list[dict[str, Any]] = []

    for beat in beats:
        idx = beat.get("beat_index")
        transition = beat.get("transition", {})
        if transition.get("type") not in transitions:
            violations.append({
                "beat_index": idx,
                "field": "transition.type",
                "invented_value": transition.get("type"),
                "clamped_to": "none",
            })
            transition["type"] = "none"

        sfx = beat.get("sound_effect", {})
        if sfx.get("type") not in valid_sfx:
            violations.append({
                "beat_index": idx,
                "field": "sound_effect.type",
                "invented_value": sfx.get("type"),
                "clamped_to": "none",
            })
            sfx["type"] = "none"

    return beats, violations
