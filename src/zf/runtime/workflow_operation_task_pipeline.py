"""Task Pipeline extensions to the replayable WorkflowOperation contract."""

from __future__ import annotations

from typing import Any, Mapping

from zf.core.events.model import ZfEvent


TASK_PIPELINE_REQUEST_KEYS = (
    "task_pipeline_stage",
    "operation_generation",
    "task_map_generation",
    "workspace_generation",
    "placement_epoch",
    "pipeline_key",
    "task_stage_session_binding",
    "risk_class",
    "integration_admission_profile",
    "exact_task_target_commit",
    "verification_result_ref",
    "verification_result_digest",
    "risk_review_timeout_seconds",
    "risk_review_max_turns",
    "risk_review_budget_usd",
)
_INTEGER_REQUEST_KEYS = frozenset({
    "operation_generation",
    "workspace_generation",
    "placement_epoch",
})


def task_pipeline_operation_seed(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "task_pipeline_stage": str(payload.get("task_pipeline_stage") or ""),
        "operation_generation": _int_value(payload.get("operation_generation")),
        "task_map_generation": str(payload.get("task_map_generation") or ""),
        "workspace_generation": _int_value(payload.get("workspace_generation")),
        "placement_epoch": _int_value(payload.get("placement_epoch")),
        "pipeline_key": str(payload.get("pipeline_key") or ""),
        "task_stage_session_binding": str(
            payload.get("task_stage_session_binding") or ""
        ),
        "admitted_control_result_ref": {},
        "semantic_verdict": "",
        "call_result_admitted_event_id": "",
        "redrive_count": 0,
        "redrive_source_attempt_ids": [],
    }


def apply_call_result_admission(
    operations: Mapping[str, dict[str, Any]],
    event: ZfEvent,
) -> bool:
    if event.type != "workflow.call.result.admitted":
        return False
    payload = event.payload if isinstance(event.payload, dict) else {}
    row = operations.get(str(payload.get("operation_id") or ""))
    if row is not None:
        row["semantic_verdict"] = str(payload.get("semantic_verdict") or "")
        control_ref = payload.get("control_result_ref")
        if isinstance(control_ref, Mapping):
            row["admitted_control_result_ref"] = dict(control_ref)
        row["call_result_admitted_event_id"] = event.id
    return True


def task_pipeline_request_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in TASK_PIPELINE_REQUEST_KEYS:
        value = payload.get(key)
        if value in (None, "", 0):
            continue
        result[key] = _int_value(value) if key in _INTEGER_REQUEST_KEYS else str(value)
    return result


def apply_task_pipeline_operation_event(
    row: dict[str, Any],
    event: ZfEvent,
    payload: Mapping[str, Any],
) -> bool:
    if event.type != "workflow.operation.redrive_admitted":
        return False
    if row.get("status") in {"suspended", "requested"}:
        row["status"] = "requested"
        row["redrive_count"] = int(row.get("redrive_count") or 0) + 1
        source_attempt_id = str(payload.get("source_attempt_id") or "")
        if source_attempt_id and source_attempt_id not in row[
            "redrive_source_attempt_ids"
        ]:
            row["redrive_source_attempt_ids"].append(source_attempt_id)
        for key in (
            "role_instance",
            "active_attempt_id",
            "dispatch_id",
            "lease_id",
            "provider_session_id",
        ):
            row[key] = ""
        row["reason"] = str(payload.get("reason") or "")
    return True


def task_pipeline_request_hash_body(
    request_body: Mapping[str, Any],
    request: Mapping[str, Any],
) -> Mapping[str, Any]:
    if not str(request.get("task_pipeline_stage") or "").strip():
        return request_body
    semantic_request = dict(request)
    for key in (
        "role_instance",
        "active_attempt_id",
        "lease_id",
        "placement_epoch",
        "task_stage_session_binding",
        "attempt_source_manifest_ref",
        "attempt_source_manifest_digest",
        "attempt_source_manifest",
        "input_consumption_policy_ref",
        "input_consumption_policy_digest",
        "input_consumption_policy",
    ):
        semantic_request.pop(key, None)
    result_identity = semantic_request.get("result_identity")
    if isinstance(result_identity, Mapping):
        semantic_identity = dict(result_identity)
        for key in (
            "role_instance",
            "active_attempt_id",
            "attempt_id",
            "lease_id",
            "placement_epoch",
            "task_stage_session_binding",
        ):
            semantic_identity.pop(key, None)
        semantic_request["result_identity"] = semantic_identity
    from zf.runtime.workflow_operation import canonicalize_operation_request

    return {
        **dict(request_body),
        "role_instance": "",
        "active_attempt_id": "",
        "lease_id": "",
        "request": canonicalize_operation_request(semantic_request),
    }


def admit_task_pipeline_redrive(
    service: Any,
    *,
    operation_id: str,
    request_hash: str,
    workflow_run_id: str,
    task_id: str,
    source_attempt_id: str,
    recovery_decision_event_id: str,
    reason: str,
) -> ZfEvent | None:
    from zf.runtime.workflow_operation import (
        WORKFLOW_OPERATION_SCHEMA,
        WorkflowOperationError,
        load_workflow_operation,
    )

    operation = load_workflow_operation(service.event_log, operation_id)
    if operation is None:
        raise WorkflowOperationError("redrive operation does not exist")
    if str(operation.get("request_hash") or "") != request_hash:
        raise WorkflowOperationError("redrive request hash diverged")
    if str(operation.get("status") or "") not in {"suspended", "requested"}:
        raise WorkflowOperationError("redrive requires a suspended operation")
    for event in service.event_log.read_all():
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            event.type == "workflow.operation.redrive_admitted"
            and str(payload.get("operation_id") or "") == operation_id
            and str(payload.get("source_attempt_id") or "") == source_attempt_id
        ):
            return None
    return service.event_writer.append(ZfEvent(
        type="workflow.operation.redrive_admitted",
        actor="zf-cli",
        origin="kernel",
        task_id=task_id or None,
        payload={
            "schema_version": WORKFLOW_OPERATION_SCHEMA,
            "workflow_run_id": workflow_run_id,
            "operation_id": operation_id,
            "request_hash": request_hash,
            "task_id": task_id,
            "source_attempt_id": source_attempt_id,
            "recovery_decision_event_id": recovery_decision_event_id,
            "recovery_decision_owner": "run_manager",
            "recovery_effect_owner": "workflow_runtime_coordinator",
            "reason": reason,
        },
        causation_id=recovery_decision_event_id or None,
        correlation_id=workflow_run_id or None,
    ))


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "admit_task_pipeline_redrive",
    "apply_call_result_admission",
    "apply_task_pipeline_operation_event",
    "task_pipeline_operation_seed",
    "task_pipeline_request_fields",
    "task_pipeline_request_hash_body",
]
