"""Restart reconciliation for terminal Goal task settlement."""

from __future__ import annotations

from typing import Any

from zf.runtime.feature_completion import close_feature_if_all_tasks_done
from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision


def reconcile_goal_terminal_task_settlement(
    runtime: Any,
) -> list[WorkflowRuntimeDecision]:
    """Replay terminal task settlement once after a runtime restart."""

    if getattr(runtime, "_goal_terminal_task_settlement_reconciled", False):
        return []
    try:
        events = runtime.event_log.read_all()
    except Exception:
        return []
    runtime._goal_terminal_task_settlement_reconciled = True
    decisions: list[WorkflowRuntimeDecision] = []
    for event in reversed(events):
        if event.type != "run.goal.completed":
            continue
        decision = runtime._settle_candidate_tasks_done(event)
        if decision is not None:
            decisions.append(decision)
        _reconcile_terminal_feature(runtime, event)
    return decisions


def _reconcile_terminal_feature(runtime: Any, event: Any) -> None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    completed = payload.get("completed_task_ids")
    completed = completed if isinstance(completed, list) else []
    task_ids = [
        *(str(value or "") for value in completed),
        str(payload.get("pdd_id") or ""),
        str(payload.get("feature_id") or ""),
        str(getattr(event, "task_id", "") or ""),
    ]
    for task_id in dict.fromkeys(value for value in task_ids if value):
        task = runtime.task_store.get(task_id)
        if task is None:
            continue
        close_feature_if_all_tasks_done(
            state_dir=runtime.state_dir,
            task=task,
            task_store=runtime.task_store,
            event_writer=runtime.event_writer,
            event_log=runtime.event_log,
            source="goal_terminal_reconciliation",
            trigger_event=event.type,
        )


__all__ = ["reconcile_goal_terminal_task_settlement"]
