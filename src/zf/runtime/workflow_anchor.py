"""Workflow fanout anchor helpers.

Workflow invoke creates a kernel-owned root task so the fanout can be traced
through the canonical task store. That root is not an ordinary worker task.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskExecutionBinding
from zf.core.task.store import TaskStore


WORKFLOW_INVOKE_BOOTSTRAP_SOURCE = "workflow_invoke_bootstrap"
WORKFLOW_MANAGED_EXECUTION_OWNER = "workflow"
WORKFLOW_TASK_REQUEST_ROTATION_SOURCE = "workflow_request_terminal_rotation"


def mark_workflow_fanout_anchor(
    task: Task,
    *,
    request_id: str = "",
    workflow_run_id: str = "",
    workflow_input_manifest_ref: str = "",
    pattern_id: str = "",
) -> Task:
    evidence = dict(getattr(task.contract, "evidence_contract", {}) or {})
    evidence.update({
        "source": WORKFLOW_INVOKE_BOOTSTRAP_SOURCE,
        "workflow_fanout_anchor": True,
        "request_id": request_id,
        "workflow_run_id": workflow_run_id or request_id,
        "workflow_input_manifest_ref": workflow_input_manifest_ref,
        "pattern_id": pattern_id,
    })
    task.contract.evidence_contract = evidence
    return task


def is_workflow_fanout_anchor_task(task: Task) -> bool:
    contract = getattr(task, "contract", None)
    evidence = getattr(contract, "evidence_contract", {}) if contract else {}
    if not isinstance(evidence, dict):
        return False
    return bool(
        evidence.get("workflow_fanout_anchor") is True
        or str(evidence.get("source") or "") == WORKFLOW_INVOKE_BOOTSTRAP_SOURCE
    )


def workflow_anchor_task_ids(
    state_dir: Path,
    task_ids: list[str],
    events: Iterable[ZfEvent],
) -> list[str]:
    """Identify control-plane invoke ids that are not delivery tasks."""

    event_list = list(events)
    store = TaskStore(Path(state_dir) / "kanban.json")
    anchors = [
        task_id
        for task_id in task_ids
        if (task := store.get(task_id)) is not None
        and is_workflow_dispatch_managed_task(task)
    ]
    for event in event_list:
        if event.type != "workflow.invoke.accepted":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        for candidate in (event.task_id, payload.get("task_id")):
            task_id = str(candidate or "").strip()
            if not task_id or task_id not in task_ids or task_id in anchors:
                continue
            task = store.get(task_id)
            if task is not None and not is_workflow_dispatch_managed_task(task):
                continue
            anchors.append(task_id)
    for event in event_list:
        if event.type != "workflow.invoke.requested":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        for candidate in (event.task_id, payload.get("task_id")):
            task_id = str(candidate or "").strip()
            if not task_id or task_id not in task_ids or task_id in anchors:
                continue
            task = store.get(task_id)
            if task is not None:
                continue
            if _has_workflow_anchor_creation(task_id, event_list):
                anchors.append(task_id)
                continue
            if _has_delivery_task_fact(task_id, event_list):
                continue
            anchors.append(task_id)
    return anchors


def _has_workflow_anchor_creation(
    task_id: str,
    events: Iterable[ZfEvent],
) -> bool:
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            event.type == "task.created"
            and _event_targets_task(event, payload, task_id)
            and str(payload.get("source") or "")
            == WORKFLOW_INVOKE_BOOTSTRAP_SOURCE
        ):
            return True
    return False


def _has_delivery_task_fact(task_id: str, events: Iterable[ZfEvent]) -> bool:
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == "task.created" and _event_targets_task(
            event,
            payload,
            task_id,
        ):
            return True
        completed = payload.get("completed_task_ids")
        if isinstance(completed, list) and task_id in {
            str(item or "").strip() for item in completed
        }:
            return True
    return False


def _event_targets_task(
    event: ZfEvent,
    payload: dict[str, Any],
    task_id: str,
) -> bool:
    return task_id in {
        str(event.task_id or "").strip(),
        str(payload.get("task_id") or "").strip(),
    }


def mark_workflow_managed_task(task: Task) -> Task:
    """Reserve an ordinary parent Task for its selected Workflow."""
    current = getattr(task, "execution_binding", TaskExecutionBinding())
    origin_task_digest = str(
        getattr(current, "origin_task_digest", "") or ""
    )
    if not origin_task_digest:
        from zf.runtime.task_workflow_plans import task_workflow_binding_digest

        origin_task_digest = task_workflow_binding_digest(task)
    evidence = dict(getattr(task.contract, "evidence_contract", {}) or {})
    evidence["execution_owner"] = WORKFLOW_MANAGED_EXECUTION_OWNER
    task.contract.evidence_contract = evidence
    task.execution_binding = TaskExecutionBinding(
        owner=WORKFLOW_MANAGED_EXECUTION_OWNER,
        request_id=str(getattr(current, "request_id", "") or ""),
        request_revision=int(getattr(current, "request_revision", 0) or 0),
        workflow_run_id=str(getattr(current, "workflow_run_id", "") or ""),
        origin_binding_digest=str(
            getattr(current, "origin_binding_digest", "") or ""
        ),
        origin_task_digest=origin_task_digest,
    )
    return task


def bind_workflow_request_to_task(
    task: Task,
    *,
    request_id: str,
    request_revision: int,
    origin_binding_digest: str,
    workflow_run_id: str = "",
) -> Task:
    """Pin one workflow-managed Task to the Request revision it executes."""

    evidence = dict(getattr(task.contract, "evidence_contract", {}) or {})
    evidence.update({
        "workflow_request_id": str(request_id or "").strip(),
        "workflow_request_revision": int(request_revision),
        "workflow_origin_binding_digest": str(
            origin_binding_digest or ""
        ).strip(),
    })
    task.contract.evidence_contract = evidence
    current = getattr(task, "execution_binding", TaskExecutionBinding())
    task.execution_binding = TaskExecutionBinding(
        owner=WORKFLOW_MANAGED_EXECUTION_OWNER,
        request_id=str(request_id or "").strip(),
        request_revision=int(request_revision),
        workflow_run_id=str(workflow_run_id or "").strip(),
        origin_binding_digest=str(origin_binding_digest or "").strip(),
        origin_task_digest=str(
            getattr(current, "origin_task_digest", "") or ""
        ),
    )
    return task


def workflow_task_request_binding(task: Task) -> dict[str, Any]:
    binding = getattr(task, "execution_binding", None)
    if (
        binding is not None
        and str(getattr(binding, "request_id", "") or "").strip()
        and int(getattr(binding, "request_revision", 0) or 0) > 0
    ):
        return {
            "request_id": str(binding.request_id).strip(),
            "request_revision": int(binding.request_revision),
            "origin_binding_digest": str(
                binding.origin_binding_digest or ""
            ).strip(),
        }
    contract = getattr(task, "contract", None)
    evidence = getattr(contract, "evidence_contract", {}) if contract else {}
    if not isinstance(evidence, dict):
        return {}
    request_id = str(evidence.get("workflow_request_id") or "").strip()
    try:
        request_revision = int(
            evidence.get("workflow_request_revision") or 0
        )
    except (TypeError, ValueError):
        request_revision = 0
    if not request_id or request_revision < 1:
        return {}
    return {
        "request_id": request_id,
        "request_revision": request_revision,
        "origin_binding_digest": str(
            evidence.get("workflow_origin_binding_digest") or ""
        ).strip(),
    }


def is_workflow_managed_task(task: Task) -> bool:
    binding = getattr(task, "execution_binding", None)
    if (
        binding is not None
        and str(getattr(binding, "owner", "") or "")
        == WORKFLOW_MANAGED_EXECUTION_OWNER
    ):
        return True
    contract = getattr(task, "contract", None)
    evidence = getattr(contract, "evidence_contract", {}) if contract else {}
    return (
        isinstance(evidence, dict)
        and str(evidence.get("execution_owner") or "")
        == WORKFLOW_MANAGED_EXECUTION_OWNER
    )


def is_workflow_dispatch_managed_task(task: Task) -> bool:
    return (
        is_workflow_fanout_anchor_task(task)
        or is_workflow_managed_task(task)
    )


def legacy_pending_handoff_tasks(
    runtime: Any,
    events: Iterable[ZfEvent],
) -> list[Task]:
    """Exclude blocking v4 Tasks from the legacy handoff state machine."""

    tasks = runtime.task_store.list_all()
    from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts

    managed = set(task_pipeline_generation_contexts(events))
    return [task for task in tasks if task.id not in managed]
