"""Cloudflare R2 object storage -- registry-visible BaseTool.

Thin wrapper: all logic lives in `lib.r2_storage` (config/client/actions).
`registry.discover()` imports and instantiates every module under `tools/`,
so boto3 is imported lazily inside `execute()` only -- a top-level import
would crash preflight for anyone who has not installed it.

Three URL actions, deliberately not one: `presigned_url`/`presigned_put_url`
are always signed (internal use, e.g. the Vast.ai transfer); `delivery_url`
is the human-sharing path -- public when configured, else a presigned GET
plus a warning. Collapsing these risks handing a rented box a public URL.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ResumeSupport,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolTier,
)

CLASS_A_USD_PER_MILLION = 4.50   # PutObject, ListObjectsV2, UploadPart, ...
CLASS_B_USD_PER_MILLION = 0.36   # GetObject, HeadObject, ...
STORAGE_USD_PER_GB_MONTH = 0.015


class CloudflareR2(BaseTool):
    name = "cloudflare_r2"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "object_storage"
    provider = "cloudflare_r2"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.API

    dependencies = [
        "python:boto3",
        "env:CLOUDFLARE_R2_ACCESS_KEY_ID",
        "env:CLOUDFLARE_R2_SECRET_ACCESS_KEY",
        "env:CLOUDFLARE_R2_ENDPOINT_URL",
    ]
    install_instructions = (
        "1. pip install boto3\n"
        "2. Tạo bucket trong Cloudflare dashboard (R2 > Create bucket)\n"
        "3. R2 > Manage API Tokens > Create API token (Object Read & Write)\n"
        "4. Điền CLOUDFLARE_R2_* vào .env (xem .env.example)\n"
        "5. Bật enabled: true trong config/r2-storage.json (mặc định tắt)"
    )

    side_effects = ["network", "remote_write"]
    resource_profile = ResourceProfile(network_required=True)
    retry_policy = RetryPolicy(max_retries=0)  # botocore adaptive retries own this
    resume_support = ResumeSupport.NONE  # s3transfer restarts a failed multipart

    capabilities = ["upload", "download", "list", "delete", "presigned_url",
                     "presigned_put_url", "delivery_url"]
    best_for = ["durable off-machine storage for project assets",
                "presigned transfer with a rented render box"]
    not_good_for = ["a CDN tier -- see delivery_url's public fallback instead"]

    input_schema = {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {"type": "string", "enum": capabilities},
            "local_path": {"type": "string"},
            "remote_key": {"type": "string"},
            "content_type": {"type": "string"},
            "prefix": {"type": "string"},
            "max_keys": {"type": "integer", "default": 1000},
            "expires_in": {"type": "integer"},
            "force": {"type": "boolean", "default": False,
                      "description": "Act on one object even when enabled=false"},
        },
    }
    output_schema = {
        "type": "object",
        "description": "Shape depends on action. presigned_url/presigned_put_url "
                        "return a bearer-token URL in `url` -- caller must never "
                        "persist it as an artifact or write it to a log.",
    }

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        action = inputs.get("action")
        op_cost = 0.0
        if action in ("upload", "presigned_put_url", "list"):
            op_cost = CLASS_A_USD_PER_MILLION / 1_000_000
        elif action in ("download", "presigned_url", "delivery_url", "delete"):
            op_cost = CLASS_B_USD_PER_MILLION / 1_000_000
        storage_cost = 0.0
        if action == "upload":
            local_path = inputs.get("local_path")
            if local_path and Path(local_path).exists():
                storage_cost = (Path(local_path).stat().st_size / 1e9) * STORAGE_USD_PER_GB_MONTH
        return round(op_cost + storage_cost, 8)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        start = time.monotonic()
        action = inputs.get("action")
        if action not in self.capabilities:
            return ToolResult(success=False, error=f"action không hợp lệ: {action!r}")

        from lib.r2_storage import resolve, validate, build_client, actions
        from lib.r2_storage.client import transfer_config, mask_endpoint
        from lib.r2_storage.config import R2ConfigError
        import botocore.exceptions as botocore_exceptions

        try:
            settings = validate(resolve())
        except R2ConfigError as exc:
            return ToolResult(success=False, error=str(exc))

        if not settings.enabled and not inputs.get("force"):
            return ToolResult(
                success=False,
                error="R2 storage tắt (enabled:false trong config/r2-storage.json). "
                      "Đặt force:true để thao tác một object, hoặc bật enabled.")

        try:
            client = build_client(settings)
            data = self._dispatch(actions, client, settings, action, inputs, transfer_config)
        except botocore_exceptions.ClientError as exc:
            error = exc.response.get("Error", {})
            return ToolResult(
                success=False,
                error=f"R2 {error.get('Code', '?')}: {error.get('Message', str(exc))} "
                      f"(endpoint={mask_endpoint(settings.endpoint_url)})")
        except (ValueError, KeyError, FileNotFoundError) as exc:
            return ToolResult(success=False, error=str(exc))

        return ToolResult(success=True, data=data, cost_usd=self.estimate_cost(inputs),
                           duration_seconds=time.monotonic() - start)

    def _dispatch(self, actions: Any, client: Any, settings: Any, action: str,
                   inputs: dict[str, Any], transfer_config: Any) -> dict[str, Any]:
        if action == "upload":
            return actions.upload(client, settings, inputs, transfer_config)
        if action == "download":
            return actions.download(client, settings, inputs, transfer_config)
        if action == "list":
            return actions.list_objects(client, settings, inputs)
        if action == "delete":
            return actions.delete(client, settings, inputs)
        if action == "presigned_url":
            return actions.presigned(client, settings, inputs, "get_object")
        if action == "presigned_put_url":
            return actions.presigned(client, settings, inputs, "put_object")
        return actions.delivery_url(client, settings, inputs)  # action == "delivery_url"
