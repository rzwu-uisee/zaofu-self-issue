"""Mechanical dispatch fences for task-local rework."""

from __future__ import annotations

from typing import Any

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent
from zf.core.state.task_attempts import TASK_ATTEMPT_IDENTITY_OPERATION_V2
from zf.core.task.schema import Task


def rework_dispatch_block_reason(
    runtime: Any,
    task: Task,
    role: RoleConfig,
    trigger_event: ZfEvent | None,
) -> str:
    active_dispatch_id = str(
        getattr(task, "active_dispatch_id", "") or ""
    )
    trigger_payload = (
        trigger_event.payload
        if trigger_event is not None
        and isinstance(trigger_event.payload, dict)
        else {}
    )
    operation_v2_reason = _operation_v2_block_reason(
        runtime,
        task,
        trigger_payload,
    )
    if operation_v2_reason:
        return operation_v2_reason
    trigger_dispatch_ids = {
        str(trigger_payload.get(key) or "")
        for key in ("dispatch_id", "attempt_id", "run_id")
    }
    if (
        active_dispatch_id
        and runtime._task_dispatch_id_is_active(task.id, active_dispatch_id)
        and active_dispatch_id not in trigger_dispatch_ids
        and trigger_event is not None
        and trigger_event.type == "task.ref.repair.requested"
    ):
        return "active_task_attempt_fence"
    if not runtime._worker_dispatchable(role.instance_id):
        if not runtime._can_repair_task_ref_on_blocked_owner(
            task,
            role,
            trigger_event,
        ):
            return "rework_target_not_dispatchable"
    try:
        runtime_events = runtime.event_log.read_all()
    except Exception:
        runtime_events = []
    latest_dispatch_meta = runtime._latest_dispatch_meta_by_task(runtime_events)
    active_others: list[str] = []
    for other in runtime.task_store.list_all():
        if other.id == task.id or other.status != "in_progress":
            continue
        dispatch_idx, dispatched_to, dispatch_id = latest_dispatch_meta.get(
            other.id,
            (-1, "", ""),
        )
        if not dispatched_to:
            continue
        if runtime._dispatch_has_terminal_after(
            events=runtime_events,
            task_id=other.id,
            dispatch_idx=dispatch_idx,
            dispatch_id=dispatch_id,
        ):
            continue
        try:
            same_worker = runtime._assignee_equivalent(
                dispatched_to,
                role.instance_id,
            )
        except Exception:
            same_worker = dispatched_to == role.instance_id
        if same_worker:
            active_others.append(other.id)
    if active_others:
        return "rework_target_busy:" + ",".join(sorted(active_others))
    return ""


def _operation_v2_block_reason(
    runtime: Any,
    task: Task,
    trigger_payload: dict[str, Any],
) -> str:
    attempt_id = str(trigger_payload.get("attempt_id") or "").strip()
    if not attempt_id:
        return ""
    claims_operation_v2 = any(
        str(trigger_payload.get(key) or "").strip()
        for key in ("operation_id", "lease_id", "operation_generation")
    )
    try:
        from zf.runtime.task_attempt_runtime import task_attempt_store

        attempt = task_attempt_store(runtime).get(attempt_id)
    except Exception:
        return (
            "operation_v2_identity_unavailable"
            if claims_operation_v2
            else ""
        )
    if not attempt:
        return "operation_v2_identity_missing" if claims_operation_v2 else ""
    if str(attempt.get("identity_version") or "") != (
        TASK_ATTEMPT_IDENTITY_OPERATION_V2
    ):
        return "operation_v2_identity_mismatch" if claims_operation_v2 else ""
    if str(attempt.get("task_id") or "") != str(task.id):
        return "operation_v2_identity_mismatch"
    trigger_operation_id = str(
        trigger_payload.get("operation_id") or ""
    ).strip()
    if trigger_operation_id and trigger_operation_id != str(
        attempt.get("operation_id") or ""
    ):
        return "operation_v2_identity_mismatch"
    return "operation_v2_rework_owned_by_task_pipeline"
