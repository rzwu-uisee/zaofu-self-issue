"""Deterministic Candidate integration bridge for Task Pipeline v4."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision


def reconcile_task_pipeline_integration(
    runtime: Any,
    *,
    projection: Mapping[str, Any],
    generation_contexts: Mapping[str, Mapping[str, Any]],
    operation_rows: list[dict[str, Any]],
) -> list[WorkflowRuntimeDecision]:
    """Drain at most one ready Task through the serial Candidate reducer."""

    ready = list(
        (projection.get("queues") or {}).get("integration_ready") or []
    )
    if not ready:
        return []
    task_id = str(ready[0] or "").strip()
    context = generation_contexts.get(task_id)
    task = runtime.task_store.get(task_id)
    if not task_id or context is None or task is None:
        return []
    verify = _current_verify_operation(operation_rows, task_id)
    if verify is None:
        return []
    generation = int(verify.get("operation_generation") or 1)
    from zf.runtime.candidates import CandidateRebuilder

    rebuilder = CandidateRebuilder(
        state_dir=Path(runtime.state_dir),
        project_root=Path(runtime.project_root),
        config=runtime.config,
        event_log=runtime.event_log,
    )
    result = rebuilder.integrate_task_pipeline_task(
        task_id=task_id,
        workflow_run_id=str(context.get("workflow_run_id") or ""),
        task_map_generation=str(context.get("task_map_generation") or ""),
        operation_generation=generation,
        pipeline_key=str(verify.get("pipeline_key") or ""),
        dispatch_base_commit=str(context.get("dispatch_base_commit") or ""),
        contract_revision=_contract_revision(task),
        event_writer=runtime.event_writer,
        causation_id=str(
            verify.get("call_result_admitted_event_id")
            or context.get("generation_admitted_event_id")
            or ""
        ),
    )
    return [WorkflowRuntimeDecision(
        action=(
            "task_pipeline_integrated"
            if result.status == "integrated"
            else "task_pipeline_integration_blocked"
        ),
        task_id=task_id,
        role="candidate-integrator",
        reason=(
            "Task integration receipt admitted"
            if result.status == "integrated"
            else f"Task integration requires review: {result.status}"
        ),
    )]


def _current_verify_operation(
    operations: list[dict[str, Any]],
    task_id: str,
) -> dict[str, Any] | None:
    candidates = [
        row
        for row in operations
        if str(row.get("task_id") or "") == task_id
        and str(row.get("task_pipeline_stage") or "") == "verify"
        and str(row.get("status") or "") == "settled"
        and str(row.get("semantic_verdict") or "").lower()
        in {"", "passed", "pass", "approved", "approve", "admit"}
    ]
    return max(
        candidates,
        key=lambda row: (
            int(row.get("operation_generation") or 1),
            str(row.get("operation_id") or ""),
        ),
        default=None,
    )


def _contract_revision(task: Any) -> str:
    from zf.runtime.task_contract_snapshot import effective_contract_revision

    return effective_contract_revision(task)


__all__ = ["reconcile_task_pipeline_integration"]
