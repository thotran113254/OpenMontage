"""Registry-visible entry point for cloud render (Vast.ai).

Intentionally thin. `registry.discover()` imports every module under
`tools/` and *instantiates* every concrete class it finds, so a top-level
`import vastai` here would make preflight crash for every user who has not
installed it (see `tools/video/talking_head_autoedit.py:1-6` for the same
lazy-import convention this file copies). All SDK/network work lives in
`lib.cloud_render` and is imported only inside `dry_run()`/`execute()`.

`dependencies` deliberately uses `"python:vastai"` / `"cmd:ssh"` / `"cmd:scp"`
-- `check_dependencies()` (`tools/base_tool.py:209-231`) only understands the
`cmd:`/`env:`/`python:` prefixes; a `"binary:ffmpeg"`-style prefix (see
`talking_head_autoedit.py:41`) falls through every branch silently and the
tool would falsely report AVAILABLE with no ssh/scp on PATH.

No autopilot: `execute()` refuses to rent anything without a caller-supplied
`offer_id` that appears inside a prior `dry_run()` call's `dry_run_ref` (see
`lib/cloud_render/dry_run_store.py`) -- an offer the user never saw in an
announce block cannot be rented, full stop. See `skills/core/cloud-render.md`
for the human-facing policy this tool mechanically enforces.
"""

from __future__ import annotations

import time
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
    ToolStatus,
    ToolTier,
)


class VastCloudRender(BaseTool):
    name = "vast_cloud_render"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "cloud_render"
    provider = "vastai"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC  # same props + same Remotion version => same frames
    runtime = ToolRuntime.API                # costs money, needs network

    dependencies = ["python:vastai", "cmd:ssh", "cmd:scp"]
    install_instructions = (
        "1. pip install vastai\n"
        "2. API key: ~/.config/vastai/vast_api_key (ghi bởi `vastai set api-key`) "
        "hoặc VAST_API_KEY trong .env\n"
        "3. Bật enabled: true trong config/cloud-render.json (mặc định tắt)\n"
        "4. Tạo ssh keypair riêng: python -m lib.cloud_render.setup_key "
        "(mặc định ~/.ssh/openmontage_cloud_render)"
    )
    agent_skills = ["vastai", "remotion-best-practices"]

    capabilities = ["cloud_render"]
    best_for = [
        "một render mà thời gian local ước tính vượt ~12 phút (2x overhead thuê máy)",
        "batch >= 3 job dùng chung 1 rental qua mode='flush'",
    ]
    not_good_for = [
        "short jobs where overhead dominates the total time",
        "anything confidential -- footage/render kit leaves this machine",
        "the video_compose atelier props shape (phase 02 refusal -- absolute file:// URIs)",
    ]

    input_schema = {
        "type": "object",
        "required": ["mode", "offer_id", "max_total_usd"],
        "properties": {
            "mode": {"type": "string", "enum": ["render_now", "flush"],
                      "description": "'render_now' thue + render 1 job; "
                                      "'flush' render ca batch dang queue"},
            "offer_id": {"type": "integer",
                          "description": "Lay tu dry_run()['offers'] -- phai nam trong dry_run_ref"},
            "max_total_usd": {"type": "number",
                                "description": "Ceiling tong chi cho rental nay; phai "
                                                "<= config.max_total_usd_per_rental"},
            "job_id": {"type": "string", "description": "Bat buoc khi mode='render_now'"},
            "job_ids": {"type": "array", "items": {"type": "string"},
                         "description": "Bat buoc khi mode='flush'"},
            "pricing_mode": {"type": "string", "enum": ["on-demand", "bid"],
                               "description": "Override config.pricing_mode cho lan goi nay"},
            "bid_price_usd": {"type": "number",
                                "description": "Reserved cho tuong lai -- render_now/flush hien tai "
                                                "tu chon bid price tu offer.dph, khong forward field nay"},
            "max_runtime_minutes": {"type": "number",
                                      "description": "Override config.max_runtime_minutes cho lan goi nay"},
            "max_concurrency": {"type": "integer",
                                  "description": "Reserved cho tuong lai -- render_now/flush hien tai "
                                                  "tu tinh concurrency tu offer.cpu_cores_effective"},
            "dry_run_ref": {"type": "string",
                              "description": "Token tu dry_run() -- execute() tu choi offer_id "
                                              "khong nam trong ref nay"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=512, disk_mb=2000, network_required=True)
    retry_policy = RetryPolicy(
        max_retries=1, backoff_seconds=10.0,
        retryable_errors=["offer_unavailable", "ssh_timeout", "npm_ci_failed"])
    resume_support = ResumeSupport.FROM_CHECKPOINT  # batch: already-downloaded outputs are skipped
    idempotency_key_fields = ["kit_hash", "composition_id", "offer_id"]
    side_effects = [
        "rents_paid_cloud_instance", "uploads_footage_offmachine",
        "destroys_cloud_instance", "spends_money",
    ]
    user_visible_verification = [
        "Doc data.render_location trong ToolResult: provider/pricing_mode/actual_cost_usd co dung khong",
        "Kiem tra rentals.jsonl + active.json: rental da closed, khong bi leak",
    ]

    # ---- status / info -----------------------------------------------------

    def get_status(self) -> ToolStatus:
        base = super().get_status()
        if base != ToolStatus.AVAILABLE:
            return base
        from lib.cloud_render import config as cloud_config
        try:
            resolved = cloud_config.resolve()
        except cloud_config.CloudRenderConfigError:
            return ToolStatus.DEGRADED
        return ToolStatus.AVAILABLE if resolved.get("enabled", False) else ToolStatus.DEGRADED

    def get_info(self) -> dict[str, Any]:
        info = super().get_info()
        from lib.cloud_render import config as cloud_config
        try:
            resolved = cloud_config.resolve()
            enabled = bool(resolved.get("enabled", False))
            info["cloud_render_config"] = {
                "enabled": enabled,
                "pricing_mode": resolved.get("pricing_mode"),
                "max_dph_usd": resolved.get("max_dph_usd"),
                "max_total_usd_per_rental": resolved.get("max_total_usd_per_rental"),
                "max_runtime_minutes": resolved.get("max_runtime_minutes"),
                "reason": None if enabled else (
                    "enabled: false trong config/cloud-render.json -- "
                    "moi rental bi tu choi cho den khi con nguoi bat co dinh nay"),
            }
        except Exception as exc:  # noqa: BLE001 -- get_info() must never crash preflight over a bad config
            info["cloud_render_config"] = {"enabled": False, "reason": f"config error: {exc}"}
        return info

    # ---- cost / runtime estimation -----------------------------------------

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        overhead_minutes, render_minutes, dph = self._cost_inputs(inputs)
        return round(dph * (overhead_minutes + render_minutes) / 60.0, 4)

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        overhead_minutes, render_minutes, _ = self._cost_inputs(inputs)
        return round((overhead_minutes + render_minutes) * 60.0, 1)

    def _cost_inputs(self, inputs: dict[str, Any]) -> tuple[float, float, float]:
        """(overhead_minutes, total_render_minutes, dph) -- ceiling `max_dph_usd`
        stands in for the real price here (no search performed): a worst-case
        number is the right default for a pre-dry_run estimate."""
        from lib.cloud_render import config as cloud_config
        from lib.cloud_render import cost_estimate

        try:
            resolved = self._resolve_config(inputs)
        except cloud_config.CloudRenderConfigError:
            resolved = cloud_config.BUILTIN_DEFAULTS
        dph = float(resolved.get("max_dph_usd", 0.15))
        render_seconds_per_video_second = float(resolved.get("render_seconds_per_video_second", 1.9))
        mode = inputs.get("mode", "render_now")
        render_minutes = cost_estimate.total_render_minutes(
            mode, inputs, render_seconds_per_video_second)
        return cost_estimate.OVERHEAD_MINUTES, render_minutes, dph

    @staticmethod
    def _resolve_config(inputs: dict[str, Any] | None) -> dict[str, Any]:
        """global -> job override, restricted to the per-call fields the
        input_schema actually exposes (`pricing_mode`, `max_runtime_minutes`)."""
        from lib.cloud_render import config as cloud_config
        job_override: dict[str, Any] = {}
        if inputs:
            if "pricing_mode" in inputs:
                job_override["pricing_mode"] = inputs["pricing_mode"]
            if "max_runtime_minutes" in inputs:
                job_override["max_runtime_minutes"] = inputs["max_runtime_minutes"]
        return cloud_config.resolve(job=job_override or None)

    # ---- dry_run: the announce payload -------------------------------------

    def dry_run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Delegates to `lib.cloud_render.announce.build()` -- see that
        module for the offer search + cost/time estimate assembly. This
        method's only job is resolving config and handing the tool's own
        identity (`name`/`provider`) through."""
        from lib.cloud_render import announce
        from lib.cloud_render import config as cloud_config

        mode = inputs.get("mode", "render_now")
        try:
            resolved = self._resolve_config(inputs)
        except cloud_config.CloudRenderConfigError as exc:
            return {"tool": self.name, "provider": self.provider, "would_execute": False,
                    "error": f"config/cloud-render.json khong hop le: {exc}"}
        return announce.build(self.name, self.provider, mode, inputs, resolved)

    # ---- execute: refuses without a seen offer, then delegates ------------

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        mode = inputs.get("mode")
        if mode not in ("render_now", "flush"):
            return ToolResult(success=False, error="mode phai la 'render_now' hoac 'flush'")

        offer_id = inputs.get("offer_id")
        if offer_id is None:
            return ToolResult(
                success=False,
                error="offer_id la bat buoc -- goi dry_run() truoc de lay offer_id + dry_run_ref hop le")

        max_total_usd = inputs.get("max_total_usd")
        if max_total_usd is None:
            return ToolResult(success=False, error="max_total_usd la bat buoc")

        if mode == "render_now" and not inputs.get("job_id"):
            return ToolResult(success=False, error="job_id la bat buoc cho mode='render_now'")
        if mode == "flush" and not inputs.get("job_ids"):
            return ToolResult(success=False, error="job_ids la bat buoc cho mode='flush'")

        from lib.cloud_render import config as cloud_config
        from lib.cloud_render import dry_run_store

        try:
            resolved = self._resolve_config(inputs)
        except cloud_config.CloudRenderConfigError as exc:
            return ToolResult(success=False, error=f"config/cloud-render.json khong hop le: {exc}")

        if not resolved.get("enabled", False):
            return ToolResult(
                success=False,
                error="cloud render dang tat (enabled: false trong config/cloud-render.json)")

        if float(max_total_usd) > float(resolved["max_total_usd_per_rental"]):
            return ToolResult(
                success=False,
                error=f"max_total_usd ${max_total_usd} vuot ceiling "
                      f"${resolved['max_total_usd_per_rental']}/rental")

        try:
            dry_run_store.resolve(inputs.get("dry_run_ref"), offer_id)
        except dry_run_store.DryRunRefError as exc:
            return ToolResult(success=False, error=str(exc))

        # Everything above is local config/file validation -- zero SDK calls.
        # `render_now`/`flush` do their own fresh search + rent + destroy below.
        from lib.cloud_render import flush as cloud_flush
        from lib.cloud_render import render_now as cloud_render_now
        from lib.cloud_render.queue import FlushError
        from lib.cloud_render.remote import CloudRenderError, CloudRenderUnsupported
        from lib.cloud_render.vast_client import CeilingExceeded
        from lib.talking_head_edit.job_store import find_job

        start = time.monotonic()
        try:
            if mode == "render_now":
                job = find_job(inputs["job_id"])
                version = int(job.load().get("current_version") or 0)
                result = cloud_render_now(job, version, config=resolved,
                                         max_total_usd=float(max_total_usd))
                duration = round(time.monotonic() - start, 1)
                return ToolResult(
                    success=True,
                    data={
                        "job_id": job.job_id,
                        "render_location": self._render_location_data(
                            instance_id=result.instance_id, offer_id=result.offer_id,
                            pricing_mode=resolved["pricing_mode"],
                            actual_cost_usd=result.actual_usd, rental_seconds=duration,
                            batch_size=1),
                    },
                    artifacts=[str(result.local_output_path)],
                    cost_usd=result.actual_usd,
                    duration_seconds=duration,
                )

            job_ids = list(inputs["job_ids"])
            batch_result = cloud_flush(job_ids, config=resolved,
                                       max_total_usd=float(max_total_usd))
            duration = round(time.monotonic() - start, 1)
            artifacts: list[str] = []
            for job_id in batch_result.rendered:
                try:
                    artifacts.append(str(find_job(job_id).final_path))
                except FileNotFoundError:
                    pass
            success = bool(batch_result.rendered)
            error = None if success else (
                f"Khong job nao render xong -- failed={batch_result.failed}, "
                f"pending={batch_result.pending}, blocked={batch_result.blocked}")
            return ToolResult(
                success=success,
                error=error,
                data={
                    "rendered": batch_result.rendered,
                    "failed": batch_result.failed,
                    "pending": batch_result.pending,
                    "blocked": batch_result.blocked,
                    "render_location": self._render_location_data(
                        instance_id=batch_result.instance_id, offer_id=batch_result.offer_id,
                        pricing_mode=resolved["pricing_mode"],
                        actual_cost_usd=batch_result.actual_usd, rental_seconds=duration,
                        batch_size=len(job_ids)),
                },
                artifacts=artifacts,
                cost_usd=batch_result.actual_usd,
                duration_seconds=duration,
            )
        except (CloudRenderError, CloudRenderUnsupported, CeilingExceeded, FlushError,
                FileNotFoundError) as exc:
            return ToolResult(success=False, error=str(exc),
                              duration_seconds=round(time.monotonic() - start, 1))

    @staticmethod
    def _render_location_data(*, instance_id: int | None, offer_id: int | None,
                              pricing_mode: str, actual_cost_usd: float, rental_seconds: float,
                              batch_size: int) -> dict[str, Any]:
        from lib.cloud_render import ledger
        data: dict[str, Any] = {
            "location": "cloud",
            "provider": "vastai",
            "pricing_mode": pricing_mode,
            "rental_seconds": rental_seconds,
            "actual_cost_usd": actual_cost_usd,
            "batch_size": batch_size,
            "ledger_ref": str(ledger.RENTALS_LOG_PATH),
        }
        if instance_id is not None:
            data["instance_id"] = str(instance_id)
        if offer_id is not None:
            data["offer_id"] = str(offer_id)
        if rental_seconds > 0:
            data["dph_usd"] = round(actual_cost_usd / (rental_seconds / 3600), 4)
        return data
