"""Select canonical tasks eligible for candidate-level terminal settlement."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task


_ACTIVE_SETTLEMENT_STATUSES = frozenset({
    "backlog",
    "in_progress",
    "review",
    "verify",
    "testing",
    "test",
    "judge",
})


def select_candidate_terminal_tasks(
    tasks: Iterable[Task],
    event: ZfEvent,
    *,
    pdd_id: str,
    feature_id: str,
    is_bootstrap_task: Callable[[Task], bool],
) -> list[Task]:
    """Return tasks that the candidate terminal may mechanically close."""
    payload = event.payload if isinstance(event.payload, dict) else {}
    completed_task_ids = {
        str(task_id).strip()
        for task_id in (
            payload.get("completed_task_ids")
            if isinstance(payload.get("completed_task_ids"), list)
            else []
        )
        if str(task_id).strip()
    }
    exact_goal_settlement = (
        event.type == "run.goal.completed"
        and bool(completed_task_ids)
    )
    selected: list[Task] = []
    for task in tasks:
        if task.status not in _ACTIVE_SETTLEMENT_STATUSES:
            continue
        task_feature = str(
            getattr(getattr(task, "contract", None), "feature_id", "") or ""
        ).strip()
        is_container = task.id in {pdd_id, feature_id}
        bootstrap_container = is_container and is_bootstrap_task(task)
        managed_workflow_parent = _is_current_workflow_managed_task(
            task,
            payload,
        )
        explicitly_completed = (
            exact_goal_settlement
            and task.id in completed_task_ids
        )
        if (
            explicitly_completed or managed_workflow_parent
        ) and not goal_terminal_matches_current_task(
            task, payload
        ):
            continue
        if exact_goal_settlement and not (
            explicitly_completed
            or bootstrap_container
            or managed_workflow_parent
        ):
            continue
        if task.status == "backlog" and not (
            explicitly_completed
            or bootstrap_container
            or managed_workflow_parent
        ):
            continue
        if (
            task_feature == feature_id
            or is_container
            or managed_workflow_parent
        ):
            selected.append(task)
    return selected


def _is_current_workflow_managed_task(task: Task, terminal_payload: dict) -> bool:
    contract = getattr(task, "contract", None)
    evidence = getattr(contract, "evidence_contract", None)
    if not isinstance(evidence, dict):
        return False
    if str(evidence.get("execution_owner") or "") != "workflow":
        return False
    workflow_request_id = str(
        evidence.get("workflow_request_id") or ""
    ).strip()
    terminal_run_id = str(
        terminal_payload.get("workflow_run_id")
        or terminal_payload.get("run_id")
        or ""
    ).strip()
    return bool(
        workflow_request_id
        and terminal_run_id
        and workflow_request_id == terminal_run_id
    )


def goal_terminal_matches_current_task(
    task: Task,
    terminal_payload: dict,
) -> bool:
    """Reject a late terminal after the canonical task contract was replaced."""
    contract = getattr(task, "contract", None)
    evidence = getattr(contract, "evidence_contract", None)
    if not isinstance(evidence, dict):
        return True
    source_refs = evidence.get("source_refs")
    source_refs = source_refs if isinstance(source_refs, dict) else {}
    current_ref = str(source_refs.get("task_map_ref") or "").strip()
    terminal_ref = str(terminal_payload.get("task_map_ref") or "").strip()
    if current_ref and terminal_ref and current_ref != terminal_ref:
        return False
    current_generation = str(
        evidence.get("task_map_generation")
        or source_refs.get("task_map_generation")
        or ""
    ).strip()
    terminal_generation = str(
        terminal_payload.get("task_map_generation") or ""
    ).strip()
    return not (
        current_generation
        and terminal_generation
        and current_generation != terminal_generation
    )


__all__ = [
    "goal_terminal_matches_current_task",
    "select_candidate_terminal_tasks",
]
