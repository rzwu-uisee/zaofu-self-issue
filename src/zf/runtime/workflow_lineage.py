"""Deterministic Workflow Run to Task lineage resolution.

The event ledger owns the parent binding.  Child payloads may carry a task id,
but they cannot redefine the Task that admitted the Workflow Run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from zf.core.events.model import ZfEvent


_RUN_PARENT_ANCHOR_EVENTS = frozenset({
    "workflow.invoke.accepted",
    "run.admission.admitted",
    "run.admission.released",
})


class WorkflowLineageError(ValueError):
    """One Workflow Run has conflicting parent Task anchors."""


@dataclass(frozen=True)
class WorkflowRunLineage:
    workflow_run_id: str
    parent_task_id: str = ""
    source_event_ids: tuple[str, ...] = ()


def resolve_workflow_run_lineage(
    events: Iterable[ZfEvent],
    workflow_run_id: str,
) -> WorkflowRunLineage:
    """Resolve the one parent Task from direct, canonical Run anchors."""

    run_id = str(workflow_run_id or "").strip()
    if not run_id:
        return WorkflowRunLineage(workflow_run_id="")
    task_ids: dict[str, list[str]] = {}
    for event in events:
        if event.type not in _RUN_PARENT_ANCHOR_EVENTS:
            continue
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        event_run_id = str(
            payload.get("workflow_run_id")
            or payload.get("run_id")
            or event.correlation_id
            or ""
        ).strip()
        if event_run_id != run_id:
            continue
        task_id = str(event.task_id or payload.get("task_id") or "").strip()
        if task_id:
            task_ids.setdefault(task_id, []).append(event.id)
    if len(task_ids) > 1:
        raise WorkflowLineageError(
            f"workflow run {run_id!r} has conflicting parent tasks: "
            + ", ".join(sorted(task_ids))
        )
    if not task_ids:
        return WorkflowRunLineage(workflow_run_id=run_id)
    parent_task_id, source_ids = next(iter(task_ids.items()))
    return WorkflowRunLineage(
        workflow_run_id=run_id,
        parent_task_id=parent_task_id,
        source_event_ids=tuple(source_ids),
    )


def bind_workflow_task_lineage(
    events: Iterable[ZfEvent],
    *,
    workflow_run_id: str,
    payload: dict[str, Any],
    task_id: str = "",
) -> tuple[str, str]:
    """Bind effective child and parent task ids into one dispatch payload."""

    lineage = resolve_workflow_run_lineage(events, workflow_run_id)
    explicit_parent = str(payload.get("parent_task_id") or "").strip()
    if (
        explicit_parent
        and lineage.parent_task_id
        and explicit_parent != lineage.parent_task_id
    ):
        raise WorkflowLineageError(
            f"workflow run {workflow_run_id!r} parent task mismatch: "
            f"{explicit_parent!r} != {lineage.parent_task_id!r}"
        )
    parent_task_id = lineage.parent_task_id or explicit_parent
    effective_task_id = str(task_id or payload.get("task_id") or "").strip()
    if not effective_task_id and parent_task_id:
        effective_task_id = parent_task_id
    if workflow_run_id:
        payload["workflow_run_id"] = workflow_run_id
    if effective_task_id:
        payload["task_id"] = effective_task_id
    if parent_task_id:
        payload["parent_task_id"] = parent_task_id
    return effective_task_id, parent_task_id


__all__ = [
    "WorkflowLineageError",
    "WorkflowRunLineage",
    "bind_workflow_task_lineage",
    "resolve_workflow_run_lineage",
]
