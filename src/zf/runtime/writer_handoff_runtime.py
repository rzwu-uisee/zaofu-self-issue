"""Runtime edge helpers for bounded writer handoff failures."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.lane_stage_handoff import LANE_STAGE_HANDOFF_FAILURE_EVENT
from zf.runtime.task_contract_snapshot import current_task_contract_identity
from zf.runtime.writer_handoff_failure import (
    writer_call_result_failure_payload,
    writer_redispatch_block,
)
from zf.runtime.writer_failure_continuation import (
    capture_writer_failure_continuation,
)


def guard_writer_redispatch(
    runtime: Any,
    *,
    context: Any,
    child: Any,
    task_item: dict[str, Any],
    task_id: str,
    run_id: str,
    pdd_id: str,
    feature_id: str,
    task_map_ref: str,
    source_index_ref: str,
    causation_id: str,
) -> bool:
    contract_revision = str(task_item.get("contract_revision") or "")
    task_map_generation = str(task_item.get("task_map_generation") or "")
    current_task = runtime.task_store.get(task_id) if task_id else None
    if current_task is not None and (
        not contract_revision or not task_map_generation
    ):
        identity = current_task_contract_identity(
            current_task,
            task_map_ref=str(task_item.get("task_map_ref") or task_map_ref),
        )
        contract_revision = contract_revision or str(
            identity.get("contract_revision") or ""
        )
        task_map_generation = task_map_generation or str(
            identity.get("task_map_generation") or ""
        )
    block = writer_redispatch_block(
        runtime.event_log.read_all(),
        task_id=task_id,
        contract_revision=contract_revision,
        task_map_generation=task_map_generation,
    )
    if not block:
        return False
    payload = {
        "fanout_id": context.fanout_id,
        "trace_id": context.trace_id,
        "stage_id": context.stage_id,
        "child_id": child.child_id,
        "run_id": run_id,
        "role_instance": child.role_instance,
        "task_id": task_id,
        "pdd_id": pdd_id,
        "feature_id": feature_id,
        "task_map_ref": task_map_ref,
        "source_index_ref": source_index_ref,
        "contract_revision": contract_revision,
        "task_map_generation": task_map_generation,
        **block,
    }
    runtime._copy_fanout_assignment_metadata(payload, task_item)
    runtime.event_writer.append(ZfEvent(
        type="fanout.child.failed",
        actor="zf-cli",
        task_id=task_id or None,
        payload=payload,
        causation_id=causation_id,
        correlation_id=context.trace_id,
    ))
    return True


def handle_blocking_writer_call_failure(
    runtime: Any,
    *,
    fanout_id: str,
    base_payload: dict[str, Any],
    call_mode: str,
    call_outcome: Any,
    event: ZfEvent,
    manifest: dict[str, Any],
) -> bool:
    if call_mode != "blocking" or call_outcome.admitted:
        return False
    failure = writer_call_result_failure_payload(
        runtime.event_log.read_all(),
        task_id=str(base_payload.get("task_id") or ""),
        contract_revision=str(base_payload.get("contract_revision") or ""),
        task_map_generation=str(base_payload.get("task_map_generation") or ""),
        call_result_status=call_outcome.status,
        issues=call_outcome.issues,
        source_event_id=event.id,
    )
    runtime._record_writer_fanout_child_failed(
        fanout_id=fanout_id,
        base_payload=base_payload,
        failure_payload=failure,
        event=event,
        manifest=manifest,
    )
    return True


def record_writer_fanout_child_failed(
    runtime: Any,
    *,
    fanout_id: str,
    base_payload: dict[str, Any],
    failure_payload: dict[str, Any],
    event: ZfEvent,
    manifest: dict[str, Any],
) -> None:
    continuation_payload = capture_writer_failure_continuation(base_payload)
    failed_event = runtime.event_writer.append(ZfEvent(
        type="fanout.child.failed",
        actor="zf-cli",
        payload={
            **base_payload,
            **failure_payload,
            **continuation_payload,
        },
        causation_id=event.id,
        correlation_id=event.correlation_id or manifest.get("trace_id", ""),
    ))
    _escalate_no_progress(
        runtime,
        failed_event=failed_event,
        base_payload=base_payload,
        failure_payload=failure_payload,
        event=event,
        manifest=manifest,
    )
    runtime._emit_lane_stage_result(
        event_type=LANE_STAGE_HANDOFF_FAILURE_EVENT,
        status="failed",
        source_event=failed_event,
        manifest=manifest,
        child_payload=dict(failed_event.payload),
        extra_payload=failure_payload,
    )
    runtime._release_fanout_worker_if_terminal(
        role_instance=base_payload["role_instance"],
        fanout_id=fanout_id,
        child_id=base_payload["child_id"],
        run_id=base_payload["run_id"],
        task_id=str(base_payload.get("task_id") or ""),
    )
    if base_payload.get("assignment_strategy") == "affinity_stage_slots":
        runtime._release_affinity_writer_slot_and_dispatch_next(
            fanout_id=fanout_id,
            completed_payload=base_payload,
            causation_id=failed_event.id,
        )
    runtime._evaluate_writer_fanout(fanout_id)


def _escalate_no_progress(
    runtime: Any,
    *,
    failed_event: ZfEvent,
    base_payload: dict[str, Any],
    failure_payload: dict[str, Any],
    event: ZfEvent,
    manifest: dict[str, Any],
) -> None:
    if not failure_payload.get("no_progress"):
        return
    fingerprint = str(failure_payload.get("handoff_failure_fingerprint") or "")
    duplicate = any(
        item.type == "human.escalate"
        and isinstance(item.payload, dict)
        and str(item.payload.get("handoff_failure_fingerprint") or "") == fingerprint
        for item in runtime.event_log.read_all()
    )
    if not fingerprint or duplicate:
        return
    recovery_action = str(
        failure_payload.get("recovery_action") or "operator_review"
    )
    runtime.event_writer.append(ZfEvent(
        type="human.escalate",
        actor="zf-cli",
        task_id=str(base_payload.get("task_id") or "") or None,
        payload={
            "reason": "writer handoff repeated without progress",
            "failure_class": "writer_handoff_no_progress",
            "failure_scope": str(
                failure_payload.get("failure_scope") or "worker_result"
            ),
            "handoff_failure_fingerprint": fingerprint,
            "source_event_id": failed_event.id,
            "evidence_event_ids": [
                *list(failure_payload.get("evidence_event_ids") or []),
                failed_event.id,
            ],
            "recovery_owner": "operator",
            "allowed_actions": [
                recovery_action,
                "operator_review",
                "start_new_generation",
            ],
            "max_auto_attempts": 0,
            "max_rescans": 0,
            "terminalization_condition": "auto_recovery_exhausted",
            "operator_required": True,
            "recoverable": False,
        },
        causation_id=failed_event.id,
        correlation_id=event.correlation_id or manifest.get("trace_id", ""),
    ))


__all__ = [
    "guard_writer_redispatch",
    "handle_blocking_writer_call_failure",
    "record_writer_fanout_child_failed",
]
