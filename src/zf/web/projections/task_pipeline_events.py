"""Task Pipeline overlay for trace projections."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent


def task_pipeline_trace(
    events: list[tuple[int, ZfEvent]],
) -> dict[str, Any]:
    from zf.runtime.workflow_operation import reduce_workflow_operations

    event_rows = [event for _, event in events]
    operations = [
        row
        for row in reduce_workflow_operations(event_rows).values()
        if str(row.get("task_pipeline_stage") or "")
    ]
    active_statuses = {"requested", "reserved", "running", "suspended"}
    generations = []
    for event in event_rows:
        if event.type != "task.pipeline.generation.admitted":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        generations.append({
            "generation_id": str(payload.get("generation_id") or ""),
            "workflow_run_id": str(payload.get("workflow_run_id") or ""),
            "task_map_generation": str(
                payload.get("task_map_generation") or ""
            ),
            "task_ids": sorted(
                str(item) for item in payload.get("task_ids", []) if str(item)
            ),
            "event_id": event.id,
        })
    projected_operations = [{
        "operation_id": str(row.get("operation_id") or ""),
        "workflow_run_id": str(row.get("workflow_run_id") or ""),
        "task_id": str(row.get("task_id") or ""),
        "stage": str(row.get("task_pipeline_stage") or ""),
        "operation_generation": int(row.get("operation_generation") or 0),
        "status": str(row.get("status") or ""),
        "current_worker": (
            str(row.get("role_instance") or "")
            if str(row.get("status") or "") in active_statuses
            else ""
        ),
        "current_worker_source": (
            "workflow_operation"
            if str(row.get("status") or "") in active_statuses
            else ""
        ),
        "placement_epoch": int(row.get("placement_epoch") or 0),
        "workspace_generation": int(row.get("workspace_generation") or 0),
        "session_binding_key": str(
            row.get("task_stage_session_binding") or ""
        ),
        "last_event_id": str(row.get("last_event_id") or ""),
    } for row in operations]
    projected_operations.sort(key=lambda row: (
        row["task_id"], row["operation_generation"], row["operation_id"]
    ))
    return {
        "schema_version": "task-pipeline-trace.v1",
        "generation_count": len(generations),
        "operation_count": len(projected_operations),
        "active_operation_ids": sorted(
            row["operation_id"]
            for row in projected_operations
            if row["status"] in active_statuses
        ),
        "generations": generations,
        "operations": projected_operations,
        "current_worker_source": "workflow_operation",
    }


__all__ = ["task_pipeline_trace"]
