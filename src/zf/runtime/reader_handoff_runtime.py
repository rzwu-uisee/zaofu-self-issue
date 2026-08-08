"""Runtime edge handling for non-admitted reader call results."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent


def handle_non_admitted_reader_result(
    runtime: Any,
    *,
    fanout_id: str,
    child_id: str,
    run_id: str,
    base_payload: dict[str, Any],
    call_mode: str,
    call_outcome: Any,
    event: ZfEvent,
    manifest: dict[str, Any],
) -> bool:
    artifact_delivery = (
        str(base_payload.get("flow_kind") or "").strip().lower() == "workflow"
        and str(base_payload.get("completion_profile") or "").strip().lower()
        == "artifact_delivery"
    )
    superseded = call_outcome.status == "superseded"
    if superseded and not artifact_delivery:
        return True
    if not superseded and (call_mode != "blocking" or call_outcome.admitted):
        return False
    reason = (
        "stale_call_result_superseded"
        if superseded
        else f"call_result_{call_outcome.status or 'invalid'}"
    )
    failure_class = (
        "call_result_currentness"
        if superseded
        else (
            "artifact_delivery_call_result_protocol"
            if artifact_delivery
            else "call_result_protocol"
        )
    )
    runtime.event_writer.append(ZfEvent(
        type="fanout.child.failed",
        actor="zf-cli",
        task_id=str(base_payload.get("task_id") or "") or None,
        payload={
            **base_payload,
            "status": "failed",
            "reason": reason,
            "failure_class": failure_class,
            "call_result_status": call_outcome.status,
            "call_result_issues": [dict(item) for item in call_outcome.issues],
            "result_event_id": event.id,
        },
        causation_id=event.id,
        correlation_id=event.correlation_id or str(manifest.get("trace_id") or ""),
    ))
    runtime._release_fanout_worker_if_terminal(
        role_instance=base_payload["role_instance"],
        fanout_id=fanout_id,
        child_id=child_id,
        run_id=run_id,
        task_id=str(base_payload.get("task_id") or ""),
    )
    runtime._evaluate_reader_fanout(fanout_id)
    return True


__all__ = ["handle_non_admitted_reader_result"]
