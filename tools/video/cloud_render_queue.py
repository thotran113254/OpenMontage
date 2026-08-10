"""Registry-visible entry point for the local, free cloud-render batch queue.

Thin wrapper only -- the real logic (durable upsert queue, threshold check,
ledger reaper) lives in `lib/cloud_render/`. Every operation here is pure
local file work: no network, no `vastai` import anywhere in this module or
its call graph, so it works even when `vast_cloud_render` itself is
unavailable (SDK missing) or disabled (`enabled: false`) -- exactly what
lets an agent inspect/plan a batch during planning without ever touching
money.
"""

from __future__ import annotations

from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolTier,
)

_OPERATIONS = ("enqueue", "list", "remove", "clear", "flush_check", "reap")


class CloudRenderQueue(BaseTool):
    name = "cloud_render_queue"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "cloud_render"
    provider = "openmontage"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies: list[str] = []
    install_instructions = "Khong can cai gi -- chi doc/ghi projects/cloud-render/batch-queue.json"
    agent_skills = ["vastai", "remotion-best-practices"]

    capabilities = ["cloud_render_queue"]
    best_for = [
        "xem/duyet batch queue truoc khi quyet dinh flush (mode='flush' o vast_cloud_render)",
        "don rental mo coi (operation='reap') an toan, offline",
    ]
    not_good_for = ["thue hoac render thuc su -- dung vast_cloud_render cho viec do"]

    input_schema = {
        "type": "object",
        "required": ["operation"],
        "properties": {
            "operation": {"type": "string", "enum": list(_OPERATIONS)},
            "job_id": {"type": "string", "description": "enqueue | remove"},
            "version": {"type": "integer", "description": "enqueue"},
            "estimated_render_seconds": {"type": "number", "description": "enqueue"},
            "duration_seconds": {"type": "number", "description": "enqueue"},
            "note": {"type": "string", "description": "enqueue"},
            "project_id": {"type": "string", "description": "enqueue"},
            "dry_run": {"type": "boolean", "description": "reap: chi report, khong destroy gi"},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=64, disk_mb=10, network_required=False)
    side_effects = ["writes_local_queue_file"]

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        operation = inputs.get("operation")
        if operation not in _OPERATIONS:
            return ToolResult(success=False, error=f"operation phai la mot trong {_OPERATIONS}")

        from lib.cloud_render import config as cloud_config
        from lib.cloud_render import ledger
        from lib.cloud_render import queue as cloud_queue

        try:
            if operation == "enqueue":
                if not inputs.get("job_id"):
                    return ToolResult(success=False, error="job_id la bat buoc cho enqueue")
                entry = cloud_queue.enqueue(
                    inputs["job_id"],
                    version=int(inputs.get("version", 0)),
                    estimated_render_seconds=float(inputs.get("estimated_render_seconds", 0.0)),
                    duration_seconds=float(inputs.get("duration_seconds", 0.0)),
                    note=str(inputs.get("note", "")),
                    project_id=inputs.get("project_id"),
                )
                return ToolResult(success=True, data={"entry": entry.to_dict()})

            if operation == "list":
                entries = cloud_queue.list_entries()
                return ToolResult(success=True, data={"entries": [e.to_dict() for e in entries]})

            if operation == "remove":
                if not inputs.get("job_id"):
                    return ToolResult(success=False, error="job_id la bat buoc cho remove")
                removed = cloud_queue.remove(inputs["job_id"])
                return ToolResult(success=True, data={"removed": removed})

            if operation == "clear":
                cloud_queue.clear()
                return ToolResult(success=True, data={})

            if operation == "flush_check":
                resolved = cloud_config.resolve()
                report = cloud_queue.flush_check(resolved)
                return ToolResult(success=True, data=report)

            # operation == "reap"
            report = ledger.reap(dry_run=bool(inputs.get("dry_run", False)))
            return ToolResult(success=True, data={
                "destroyed": report.destroyed,
                "closed": report.closed,
                "left_alone": report.left_alone,
                "warnings": report.warnings,
            })
        except cloud_config.CloudRenderConfigError as exc:
            return ToolResult(success=False, error=f"config/cloud-render.json khong hop le: {exc}")
        except cloud_queue.QueueLockError as exc:
            return ToolResult(success=False, error=str(exc))
