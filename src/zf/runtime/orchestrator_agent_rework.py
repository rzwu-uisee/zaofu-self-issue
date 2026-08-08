"""Mechanical target admission for OA semantic rework directives."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent


SEMANTIC_REWORK_REQUESTED = "orchestrator.semantic.rework.requested"


def semantic_rework_target_role(event: ZfEvent) -> str:
    if event.type != SEMANTIC_REWORK_REQUESTED:
        return ""
    payload = event.payload if isinstance(event.payload, dict) else {}
    return str(payload.get("target_role_instance") or "")


def admit_semantic_rework_target(
    runtime: Any,
    task: Any,
    event: ZfEvent,
) -> bool:
    if event.type != SEMANTIC_REWORK_REQUESTED:
        return True
    payload = event.payload if isinstance(event.payload, dict) else {}
    rejection = ""
    if str(event.task_id or "") != str(task.id):
        rejection = "target_task_mismatch"
    elif (
        str(getattr(task, "active_dispatch_id", "") or "")
        and str(payload.get("target_attempt_id") or "")
        != str(getattr(task, "active_dispatch_id", "") or "")
    ):
        rejection = "target_attempt_stale"
    elif str(payload.get("target_role_instance") or "") not in {
        str(role.instance_id or role.name) for role in runtime.config.roles
    }:
        rejection = "target_role_missing"
    if not rejection:
        return True
    runtime.event_writer.append(ZfEvent(
        type="orchestrator.semantic.rework.rejected",
        actor="zf-cli",
        origin="kernel",
        task_id=task.id,
        payload={"source_event_id": event.id, "reason": rejection},
        causation_id=event.id,
        correlation_id=event.correlation_id,
    ))
    return False


__all__ = [
    "admit_semantic_rework_target",
    "semantic_rework_target_role",
]
