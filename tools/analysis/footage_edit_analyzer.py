"""Footage edit-analysis tool: Gemini video analysis producing a
footage_edit_plan artifact, constrained to a real, curated resource
inventory (Phase 01b) so transition/sfx choices are always renderable.

Genuinely new — no prior Gemini video tool existed in this project
(tools/analysis/video_analyzer.py is local-only, zero API key).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from styles.playbook_loader import load_playbook
from tools.analysis.audio_probe import probe_duration
from tools.analysis.footage_edit_artifact import build_artifact, load_removal_spans
from tools.analysis.footage_edit_broll_matcher import build_broll_candidates
from tools.analysis.footage_edit_disfluency_prompt import MIN_DISFLUENCY_CONFIDENCE_DEFAULT
from tools.analysis.footage_edit_genai_client import (
    GeminiJSONError,
    actual_cost,
    estimate_cost,
    generate_with_repair,
    upload_video,
)
from tools.analysis.footage_edit_prompt import (
    DEFAULT_MODEL,
    DEFAULT_THINKING_LEVEL,
    build_prompt,
    inventory_names,
    load_inventory,
)
from tools.analysis.footage_edit_schema import INPUT_SCHEMA
from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)


class FootageEditAnalyzer(BaseTool):
    name = "footage_edit_analyzer"
    version = "0.1.0"
    tier = ToolTier.ANALYZE
    capability = "analysis"
    provider = "google_genai"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = ["env:GEMINI_API_KEY", "python:google.genai"]
    install_instructions = (
        "pip install google-genai\n"
        "Set GEMINI_API_KEY in .env (https://aistudio.google.com/apikey)"
    )
    agent_skills = ["ai-multimodal"]

    capabilities = ["footage_edit_analysis", "video_beat_planning"]
    best_for = [
        "talking-head UGC re-cut planning (beats, zoom, transition, b-roll, sfx)",
        "constrained-choice creative grounding (no hallucinated resources)",
    ]
    not_good_for = [
        "multi-speaker/complex scene editorial (built for single-take talking-head)",
        "frame-accurate cut points (beat boundaries are seconds-granular)",
    ]

    input_schema = INPUT_SCHEMA

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=200, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["input_path", "playbook_path", "inventory_path", "model", "thinking_level"]
    side_effects = ["uploads video to Google Generative AI API", "writes footage_edit_plan.json to output_dir"]
    user_visible_verification = [
        "Confirm every beat's transition/sound_effect is a real inventory name (not hallucinated)",
        "Confirm transitions/effects vary across beats rather than repeating one choice",
    ]

    def get_status(self) -> ToolStatus:
        if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        model = inputs.get("model", DEFAULT_MODEL)
        duration = probe_duration(inputs.get("input_path", "")) or 60.0
        return estimate_cost(model, duration)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        input_path = Path(inputs["input_path"])
        if not input_path.exists():
            return ToolResult(success=False, error=f"Input video not found: {input_path}")

        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            return ToolResult(success=False, error="GEMINI_API_KEY not set. " + self.install_instructions)

        playbook_path = Path(inputs.get("playbook_path", "styles/ugc-talking-head.yaml"))
        inventory_path = Path(inputs.get("inventory_path", "assets_library/resource_inventory.json"))
        model = inputs.get("model", DEFAULT_MODEL)
        thinking_level = inputs.get("thinking_level", DEFAULT_THINKING_LEVEL)
        output_dir = Path(inputs.get("output_dir", "output/footage_edit_plan"))

        try:
            playbook = load_playbook(playbook_path.stem, styles_dir=playbook_path.parent)
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to load style playbook {playbook_path}: {e}")

        try:
            inventory = load_inventory(inventory_path)
        except (FileNotFoundError, ValueError) as e:
            return ToolResult(success=False, error=str(e))

        transitions, sound_effects = inventory_names(inventory)
        real_duration = probe_duration(str(input_path))

        # Learned user style: metadata.user_style_profile exported by
        # tools/editor/edit-decisions-editor.html. Explicit path wins;
        # otherwise fall back to the shared styles/user-edit-profile.json.
        user_style_profile = None
        profile_path = Path(
            inputs.get("user_style_profile_path", "styles/user-edit-profile.json")
        )
        if profile_path.exists():
            try:
                with open(profile_path, encoding="utf-8") as f:
                    profile_data = json.load(f)
                user_style_profile = (
                    profile_data.get("metadata", {}).get("user_style_profile")
                    or profile_data.get("user_style_profile")
                    or profile_data
                )
            except Exception as e:
                return ToolResult(
                    success=False,
                    error=f"Failed to load user style profile {profile_path}: {e}",
                )

        prompt = build_prompt(
            playbook, transitions, sound_effects,
            duration_seconds=real_duration,
            user_style_profile=user_style_profile,
        )

        try:
            from google import genai
            from google.genai import types
        except ImportError:
            return ToolResult(success=False, error="google-genai not installed. pip install google-genai")

        start = time.time()
        client = genai.Client(api_key=api_key)

        try:
            part = upload_video(client, input_path, types)
        except Exception as e:
            return ToolResult(success=False, error=f"Video upload to Gemini failed: {e}")

        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
            thinking_config=types.ThinkingConfig(
                thinking_level=getattr(types.ThinkingLevel, thinking_level)
            ),
        )

        try:
            parsed, response, repair_attempted = generate_with_repair(client, model, prompt, part, config)
        except GeminiJSONError as e:
            return ToolResult(success=False, error=str(e), data={"raw_response": e.raw_text})
        except Exception as e:
            return ToolResult(success=False, error=f"Gemini generate_content failed: {e}")

        elapsed = time.time() - start
        usage = getattr(response, "usage_metadata", None)
        removal_spans = load_removal_spans(inputs)
        min_disfluency_confidence = float(
            inputs.get("min_disfluency_confidence", MIN_DISFLUENCY_CONFIDENCE_DEFAULT)
        )

        # Real b-roll clips this project declared (never fabricated). One
        # extra, cheap Gemini call per clip — see footage_edit_broll_matcher.
        broll_sources = inputs.get("broll_sources") or []
        broll_candidates = (
            build_broll_candidates(client, model, types, broll_sources) if broll_sources else []
        )

        artifact = build_artifact(
            parsed=parsed,
            input_path=input_path,
            playbook_path=playbook_path,
            transitions=transitions,
            sound_effects=sound_effects,
            model=model,
            thinking_level=thinking_level,
            elapsed=elapsed,
            usage=usage,
            repair_attempted=repair_attempted,
            removal_spans=removal_spans,
            real_duration_seconds=real_duration,
            min_disfluency_confidence=min_disfluency_confidence,
            broll_candidates=broll_candidates,
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "footage_edit_plan.json"
        output_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")

        return ToolResult(
            success=True,
            data=artifact,
            artifacts=[str(output_path)],
            cost_usd=actual_cost(model, usage),
            duration_seconds=round(elapsed, 2),
            model=model,
        )
