"""Pure Generic Workflow helpers for the fanout runtime."""

from __future__ import annotations

from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.fanout_payload_data import payload_or_report_value
from zf.core.workflow.flow_metadata import (
    flow_kind_from_payload,
    flow_metadata_for,
)
from zf.runtime.call_result_envelope import call_result_envelope_ref


GENERIC_WORKFLOW_HANDOFF_KEYS = (
    "goal_id",
    "workflow_generation",
    "request_revision",
    "generic_workflow_contract_digest",
    "workflow_intent",
    "workflow_template",
    "completion_profile",
    "required_delivery_artifacts",
    "goal_claim_set_ref",
    "goal_claim_set_digest",
    "generic_workflow_operation",
    "workflow_dependencies",
    "workflow_input_ports",
    "workflow_output_ports",
    "workflow_dependency_barrier_id",
    "workflow_dependency_barrier_digest",
)

GENERIC_WORKFLOW_DURABLE_TRIGGER_KEYS = (
    "workflow_generation",
    "request_revision",
    "generic_workflow_contract_digest",
    "workflow_template",
    "completion_profile",
)


def workflow_fanout_identity_scope(stage: Any, event: ZfEvent) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    flow_kind = str(
        getattr(stage, "flow_kind", "") or payload.get("flow_kind") or ""
    ).strip()
    if flow_kind != "workflow":
        return ""
    return "|".join((
        "workflow",
        str(payload.get("workflow_generation") or ""),
        str(payload.get("request_revision") or ""),
        str(payload.get("generic_workflow_contract_digest") or ""),
    ))


def fanout_stage_matches_trigger_event(
    stage: Any,
    event: ZfEvent,
) -> bool:
    payload = event.payload if isinstance(event.payload, dict) else {}
    stage_kind = str(
        getattr(stage, "flow_kind", "") or ""
    ).strip().lower()
    if stage_kind and flow_kind_from_payload(payload) != stage_kind:
        return False
    event_type = str(getattr(event, "type", "") or "")
    if event_type == "workflow.dependency_barrier.satisfied":
        return (
            str(payload.get("stage_id") or "")
            == str(getattr(stage, "id", "") or "")
            and str(payload.get("barrier_id") or "")
            == str(getattr(stage, "dependency_barrier_id", "") or "")
            and str(payload.get("barrier_digest") or "")
            == str(getattr(stage, "dependency_barrier_digest", "") or "")
        )
    if event_type != "workflow.invoke.requested":
        return True
    pattern_id = str(
        payload.get("pattern_id") or payload.get("stage_id") or ""
    ).strip()
    return bool(
        pattern_id
        and pattern_id == str(getattr(stage, "id", "") or "")
    )


def apply_generic_workflow_stage_payload(
    config: Any,
    stage: Any,
    children: list[Any],
) -> None:
    operation = str(getattr(stage, "operation", "") or "")
    if (
        str(getattr(stage, "flow_kind", "") or "") != "workflow"
        or not operation
    ):
        return
    metadata = flow_metadata_for(config, "workflow")
    stage_payload = {
        "flow_kind": "workflow",
        "generic_workflow_operation": operation,
        "workflow_dependencies": list(
            getattr(stage, "dependencies", []) or []
        ),
        "workflow_input_ports": [
            {
                "name": str(getattr(port, "name", "") or ""),
                "kind": str(getattr(port, "kind", "") or ""),
                "source": str(getattr(port, "source", "") or ""),
                "required": bool(getattr(port, "required", True)),
            }
            for port in getattr(stage, "input_ports", []) or []
        ],
        "workflow_output_ports": [
            {
                "name": str(getattr(port, "name", "") or ""),
                "kind": str(getattr(port, "kind", "") or ""),
            }
            for port in getattr(stage, "output_ports", []) or []
        ],
        "workflow_dependency_barrier_id": str(
            getattr(stage, "dependency_barrier_id", "") or ""
        ),
        "workflow_dependency_barrier_digest": str(
            getattr(stage, "dependency_barrier_digest", "") or ""
        ),
        "workflow_template": str(
            metadata.get("workflow_template") or ""
        ),
        "completion_profile": str(
            metadata.get("completion_profile") or ""
        ),
        "generic_workflow_contract_digest": str(
            metadata.get("generic_workflow_contract_digest") or ""
        ),
        "required_delivery_artifacts": [
            dict(item)
            for item in metadata.get("required_delivery_artifacts") or []
            if isinstance(item, Mapping)
        ],
    }
    for child in children:
        payload = getattr(child, "payload", None)
        if not isinstance(payload, dict):
            continue
        for key, value in stage_payload.items():
            if value not in (None, "", [], {}):
                payload.setdefault(key, value)
        if (
            operation == "agent.verify"
            and stage_payload["completion_profile"] == "artifact_delivery"
        ):
            payload["output_profile_id"] = "artifact-delivery"
            payload["output_profile_revision"] = "1"


def generic_workflow_goal_identity(
    manifest: Mapping[str, Any],
    *,
    payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    """Recover parent-owned Goal identity without trusting child aliases."""

    trigger = (
        manifest.get("trigger_payload")
        if isinstance(manifest.get("trigger_payload"), Mapping)
        else {}
    )
    result: dict[str, Any] = {}
    for key in (
        "goal_id",
        "workflow_intent",
        "required_delivery_artifacts",
        "goal_claim_set_ref",
        "goal_claim_set_digest",
    ):
        value = trigger.get(key)
        if value in (None, "", [], {}):
            value = manifest.get(key)
        if value in (None, "", [], {}):
            for child in manifest.get("children") or []:
                assignment = (
                    child.get("payload")
                    if isinstance(child, Mapping)
                    and isinstance(child.get("payload"), Mapping)
                    else {}
                )
                value = assignment.get(key)
                if value not in (None, "", [], {}):
                    break
        if value in (None, "", [], {}):
            for payload in payloads:
                value = payload_or_report_value(payload, key)
                if value not in (None, "", [], {}):
                    break
        if value not in (None, "", [], {}):
            result[key] = value
    return result


def artifact_delivery_verified_event(
    *,
    base_payload: Mapping[str, Any],
    report_payload: Mapping[str, Any],
    completed_event: ZfEvent,
    correlation_id: str,
) -> ZfEvent | None:
    artifact_result = report_payload.get("artifact_delivery_result")
    admitted_ref = base_payload.get("admitted_call_result_ref")
    control_ref = base_payload.get("control_result_ref")
    if not (
        isinstance(artifact_result, Mapping)
        and str(artifact_result.get("verdict") or "") == "passed"
        and str(artifact_result.get("completion_profile") or "")
        == "artifact_delivery"
        and call_result_envelope_ref(admitted_ref)
        and isinstance(control_ref, Mapping)
        and str(control_ref.get("ref") or "").strip()
        and _sha256(control_ref.get("sha256"))
    ):
        return None
    return ZfEvent(
        type="artifact.delivery.verified",
        actor="zf-cli",
        task_id=str(base_payload.get("task_id") or "") or None,
        payload={
            "flow_kind": "workflow",
            **{
                key: base_payload.get(key)
                for key in (
                    "workflow_run_id",
                    "trace_id",
                    "stage_id",
                    "child_id",
                    "role_instance",
                    "operation_id",
                    "request_hash",
                    "admitted_call_result_ref",
                    "control_result_ref",
                )
                if base_payload.get(key) not in (None, "")
            },
            "artifact_delivery_result": dict(artifact_result),
            "source_event_id": completed_event.id,
        },
        causation_id=completed_event.id,
        correlation_id=correlation_id,
    )


def _sha256(value: object) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


__all__ = [
    "GENERIC_WORKFLOW_DURABLE_TRIGGER_KEYS",
    "GENERIC_WORKFLOW_HANDOFF_KEYS",
    "apply_generic_workflow_stage_payload",
    "artifact_delivery_verified_event",
    "fanout_stage_matches_trigger_event",
    "generic_workflow_goal_identity",
    "workflow_fanout_identity_scope",
]
