"""Canonical result routing predicates for blocking Task Pipeline stages."""

from __future__ import annotations

from typing import Any, Mapping

from zf.core.events.model import ZfEvent


def is_admitted_task_pipeline_stage_result(
    runtime: Any,
    event: ZfEvent,
) -> bool:
    """Return whether a canonical result belongs to a current v4 operation.

    A worker-provided marker is not authority.  The operation must already
    exist in the admitted generation and expose a controlled result ref before
    legacy routing can be bypassed.
    """

    if not event.task_id:
        return False
    payload = event.payload if isinstance(event.payload, dict) else {}
    stage = str(payload.get("task_pipeline_stage") or "").strip()
    expected_events = {
        "impl": {"dev.build.done", "dev.blocked"},
        "verify": {
            "task.pipeline.verify.completed",
            "task.pipeline.verify.failed",
        },
        "acceptance_review": {
            "task.pipeline.acceptance.completed",
            "task.pipeline.acceptance.failed",
        },
    }
    if event.type not in expected_events.get(stage, set()):
        return False
    operation_id = str(payload.get("operation_id") or "").strip()
    control_ref = payload.get("control_result_ref")
    if (
        not operation_id
        or not isinstance(control_ref, Mapping)
        or not str(control_ref.get("ref") or "").strip()
        or not str(control_ref.get("sha256") or "").strip()
    ):
        return False
    from zf.runtime.task_pipeline_runtime import task_pipeline_generation_contexts

    contexts = task_pipeline_generation_contexts(runtime.event_log.read_all())
    context = contexts.get(str(event.task_id))
    if context is None:
        return False
    from zf.runtime.task_pipeline_reconciler import task_pipeline_blocking

    if not task_pipeline_blocking(
        runtime.config,
        flow_kind=str(context.get("flow_kind") or ""),
    ):
        return False
    from zf.runtime.workflow_operation import reduce_workflow_operations

    operation = reduce_workflow_operations(runtime.event_log.read_all()).get(
        operation_id
    )
    if operation is None:
        return False
    return (
        str(operation.get("status") or "") == "settled"
        and str(operation.get("task_id") or "") == str(event.task_id)
        and str(operation.get("workflow_run_id") or "")
        == str(context.get("workflow_run_id") or "")
        and str(operation.get("task_map_generation") or "")
        == str(context.get("task_map_generation") or "")
        and str(operation.get("task_pipeline_stage") or "") == stage
        and int(operation.get("operation_generation") or 0)
        == int(payload.get("operation_generation") or 0)
    )


__all__ = ["is_admitted_task_pipeline_stage_result"]
