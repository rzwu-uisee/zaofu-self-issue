"""Thin runtime callers for the canonical Task contract authority boundary."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.task_contract_authority import (
    TaskContractMutation,
    TaskContractAuthorityService,
    allowed_task_contract_change_actors,
)


def apply_contract_change_request(
    *,
    task_store: TaskStore,
    event_writer: EventWriter,
    state_dir: Path,
    config: Any,
    event: ZfEvent,
) -> None:
    """Apply one role-authored contract intent through the canonical CAS path."""

    TaskContractAuthorityService(
        task_store=task_store,
        event_writer=event_writer,
        state_dir=state_dir,
    ).apply_change_request(
        event,
        allowed_actors=allowed_task_contract_change_actors(config),
    )


def update_contract_artifact_refs(
    *,
    task_store: TaskStore,
    event_writer: EventWriter,
    state_dir: Path,
    task_id: str,
    contract_refs: object,
    artifact_refs: object,
    causation_id: str,
    correlation_id: str,
) -> None:
    """Merge accepted artifact refs without replacing unrelated semantics."""

    if not isinstance(contract_refs, Mapping) or not contract_refs:
        return
    task = task_store.get(task_id)
    if task is None:
        return
    updates = dict(contract_refs)
    paths = [
        str(ref.get("path") or "")
        for ref in artifact_refs if isinstance(ref, Mapping)
        and str(ref.get("path") or "").strip()
    ] if isinstance(artifact_refs, list) else []
    if paths:
        updates["handoff_artifacts"] = paths
    contract = asdict(task.contract)
    evidence_update = updates.pop("evidence_contract", None)
    if isinstance(evidence_update, Mapping):
        evidence = dict(contract.get("evidence_contract") or {})
        evidence.update(evidence_update)
        contract["evidence_contract"] = evidence
    contract.update(updates)
    TaskContractAuthorityService(
        task_store=task_store,
        event_writer=event_writer,
        state_dir=state_dir,
    ).replace(
        task,
        contract=contract,
        source="task.artifact_refs.updated",
        causation_id=causation_id,
        correlation_id=correlation_id,
        audit_payload={"artifact_refs_event_id": causation_id},
    )


def bind_writer_dispatch_contract(
    *,
    task_store: TaskStore,
    event_writer: EventWriter,
    state_dir: Path,
    task: Task,
    role: Any,
    config: Any,
    task_item: Mapping[str, Any],
    task_updates: Mapping[str, Any] | None = None,
) -> TaskContractMutation:
    """Bind one admitted writer task through the canonical contract CAS."""

    from zf.runtime.writer_fanout_admission import (
        bind_writer_task_dispatch_owner,
    )

    contract = bind_writer_task_dispatch_owner(
        task=task,
        role=role,
        config=config,
        event_writer=event_writer,
        task_item=task_item,
    )
    return TaskContractAuthorityService(
        task_store=task_store,
        event_writer=event_writer,
        state_dir=state_dir,
    ).replace(
        task,
        contract=contract,
        source="writer_dispatch_owner_binding",
        task_updates=task_updates,
        audit_payload={
            "flow_kind": str(
                (contract.evidence_contract or {}).get("flow_kind") or ""
            ),
            "owner_role": role.name,
            "owner_instance": role.instance_id,
        },
    )


def refresh_writer_task_contract(
    *,
    task_store: TaskStore,
    event_writer: EventWriter,
    state_dir: Path,
    existing: Task,
    refreshed_task: Task,
    source: str,
    feature_id: str,
    old_task_map_ref: str,
    new_task_map_ref: str,
    is_replan: bool,
) -> None:
    """Apply a task-map refresh and any mechanical reopen in one CAS."""

    old_status = str(existing.status or "")
    reset_for_replan = bool(
        is_replan
        and old_status in {"done", "blocked", "failed", "review", "test"}
    )
    reopened = bool(reset_for_replan and old_status == "done")
    task_updates: dict[str, Any] = {
        "title": refreshed_task.title,
        "skills_required": list(refreshed_task.skills_required),
        "blocked_by": list(refreshed_task.blocked_by),
    }
    if reset_for_replan:
        task_updates.update({
            "status": "backlog",
            "assigned_to": None,
            "active_dispatch_id": "",
            "dispatched_at": None,
            "started_at": None,
            "completed_at": None,
            "cancelled_at": None,
            "evidence": None,
            "retry_count": 0,
            "blocked_reason": "",
        })
    TaskContractAuthorityService(
        task_store=task_store,
        event_writer=event_writer,
        state_dir=state_dir,
    ).replace(
        existing,
        contract=refreshed_task.contract,
        source=source,
        task_updates=task_updates,
        reopen_terminal=reopened,
        audit_payload={
            "feature_id": feature_id,
            "old_task_map_ref": old_task_map_ref,
            "new_task_map_ref": new_task_map_ref,
            "replan": is_replan,
            "old_status": old_status,
            "reopened_from_terminal": reopened,
            "reset_for_replan": reset_for_replan,
        },
    )


__all__ = [
    "apply_contract_change_request",
    "bind_writer_dispatch_contract",
    "refresh_writer_task_contract",
    "update_contract_artifact_refs",
]
