"""NineRouter image generation gateway (OpenAI-Images-compatible, SSE response).

Wraps the user's hosted gateway at NINE_ROUTER_BASE_URL for b-roll images
(chat-UI / product-demo mocks) when no real footage is available.

Response is nonstandard: `Content-Type: text/event-stream`, not plain JSON.
Verified live (one real call, 2026-07-09, prompt "a single red circle on
white background"): repeated `event: progress` / `event: partial_image`
lines, then a terminal `event: done` whose `data.data[0]` carries the final
image as `b64_json` (base64 PNG). `url` is handled defensively as a
fallback since the spec allows it, though it was not observed live.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from typing import Any

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


def _slugify(prompt: str, limit: int = 40) -> str:
    """Turn a prompt into a filesystem-safe filename stem."""
    slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")
    return (slug[:limit] or "image") + f"-{int(time.time())}"


class NineRouterImage(BaseTool):
    name = "nine_router_image"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "image_generation"
    provider = "nine_router"
    stability = ToolStability.EXPERIMENTAL  # SSE shape verified with one live call only
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = []  # checked dynamically via env vars in get_status()
    install_instructions = (
        "Set NINE_ROUTER_API_KEY (gateway bearer token) and "
        "NINE_ROUTER_BASE_URL (e.g. https://<your-gateway-host>/v1) in .env."
    )
    agent_skills = []

    capabilities = ["generate_image", "generate_illustration", "text_to_image"]
    supports = {"negative_prompt": False, "seed": False, "custom_size": True, "aspect_ratio": False}
    best_for = [
        "b-roll mock images (chat UI, product demo screens)",
        "quick image generation via a self-hosted gateway",
    ]
    not_good_for = [
        "guaranteed uptime (single self-hosted gateway, no SLA)",
        "exact cost prediction (gateway pricing not published)",
    ]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "description": "Image description"},
            "size": {"type": "string", "default": "auto"},
            "quality": {"type": "string", "default": "auto"},
            "background": {"type": "string", "default": "auto"},
            "image_detail": {"type": "string", "default": "high"},
            "output_format": {"type": "string", "default": "png"},
            "output_dir": {"type": "string", "default": "generated_images"},
            "model": {"type": "string", "default": "cx/gpt-5.5-image"},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=100, network_required=True)
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["timeout", "connection_error"])
    idempotency_key_fields = ["prompt", "size", "quality", "model"]
    side_effects = ["writes image file to output_dir", "calls NineRouter image gateway API"]
    user_visible_verification = ["Inspect generated image for relevance and quality"]

    def get_status(self) -> ToolStatus:
        if os.environ.get("NINE_ROUTER_API_KEY") and os.environ.get("NINE_ROUTER_BASE_URL"):
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        # Gateway pricing is not published; conservative placeholder in line
        # with other GPT-image-family providers.
        return 0.04

    def _parse_sse(self, response: Any) -> tuple[str | None, str | None, str | None]:
        """Consume the SSE stream. Returns (b64_json, url, error)."""
        event_name: str | None = None
        for raw_line in response.iter_lines(decode_unicode=True):
            if raw_line is None or raw_line == "":
                event_name = None
                continue
            if raw_line.startswith(":"):
                continue  # SSE comment/keepalive
            if raw_line.startswith("event:"):
                event_name = raw_line[len("event:"):].strip()
                continue
            if not raw_line.startswith("data:"):
                continue
            payload = raw_line[len("data:"):].strip()
            if payload == "[DONE]":
                continue
            if event_name == "error":
                return None, None, payload
            if event_name == "done":
                try:
                    parsed = json.loads(payload)
                except ValueError:
                    return None, None, f"Malformed done event payload: {payload[:200]}"
                items = parsed.get("data") or []
                if not items:
                    return None, None, "done event carried no image data"
                return items[0].get("b64_json"), items[0].get("url"), None
            # progress / partial_image / other events: ignore, keep streaming
        return None, None, "Stream ended without a done event"

    def _request_once(self, url: str, headers: dict, body: dict) -> tuple[bytes | None, str, bool]:
        """One POST + SSE consume + (decode|download) attempt.

        Returns (image_bytes, error_message, retryable). retryable is True
        only for transient network failures (timeout/connection error).
        """
        import requests

        try:
            response = requests.post(url, headers=headers, json=body, stream=True, timeout=180)
        except requests.exceptions.Timeout:
            return None, "NineRouter gateway request timed out", True
        except requests.exceptions.ConnectionError as exc:
            return None, f"NineRouter gateway connection error: {exc}", True

        if response.status_code >= 400:
            return None, f"NineRouter gateway HTTP {response.status_code}: {response.text[:500]}", False
        b64_json, image_url, sse_error = self._parse_sse(response)
        if sse_error:
            return None, f"NineRouter gateway stream error: {sse_error}", False
        if b64_json:
            return base64.b64decode(b64_json), "", False
        if image_url:
            img_resp = requests.get(image_url, timeout=60)
            img_resp.raise_for_status()
            return img_resp.content, "", False
        return None, "NineRouter gateway returned no image (no b64_json or url)", False

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        if self.get_status() != ToolStatus.AVAILABLE:
            return ToolResult(success=False, error="NineRouter gateway not configured. " + self.install_instructions)

        base_url = os.environ["NINE_ROUTER_BASE_URL"].rstrip("/")
        prompt = inputs["prompt"]
        model = inputs.get("model", "cx/gpt-5.5-image")
        output_format = inputs.get("output_format", "png")
        body = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": inputs.get("size", "auto"),
            "quality": inputs.get("quality", "auto"),
            "background": inputs.get("background", "auto"),
            "image_detail": inputs.get("image_detail", "high"),
            "output_format": output_format,
        }
        headers = {
            "Authorization": f"Bearer {os.environ['NINE_ROUTER_API_KEY']}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }

        start = time.time()
        image_bytes: bytes | None = None
        last_error = "Unknown NineRouter gateway failure"
        try:
            for _attempt in range(self.retry_policy.max_retries + 1):
                image_bytes, last_error, retryable = self._request_once(
                    f"{base_url}/images/generations", headers, body
                )
                if image_bytes is not None or not retryable:
                    break
        except Exception as exc:  # noqa: BLE001 - surface any unexpected failure
            return ToolResult(success=False, error=f"NineRouter gateway request failed: {exc}")

        if image_bytes is None:
            return ToolResult(success=False, error=last_error)

        output_dir = Path(inputs.get("output_dir", "generated_images"))
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{_slugify(prompt)}.{output_format}"
        output_path.write_bytes(image_bytes)
        return ToolResult(
            success=True,
            data={"provider": "nine_router", "model": model, "prompt": prompt, "output": str(output_path)},
            artifacts=[str(output_path)],
            cost_usd=self.estimate_cost(inputs),
            duration_seconds=round(time.time() - start, 2),
            model=model,
        )
