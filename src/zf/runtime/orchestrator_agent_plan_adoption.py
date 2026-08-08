"""Plan Candidate shadow and blocking semantic adoption."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator_agent_operations import (
    canonical_checkpoint_source_event_id,
    orchestrator_agent_checkpoint_operation_id,
    request_orchestrator_agent_checkpoint,
)
from zf.runtime.orchestrator_agent_policy import (
    checkpoint_policy,
    orchestration_flow_kind,
)


@dataclass(frozen=True)
class PlanCandidateCheckpointState:
    enabled: bool
    blocking: bool
    satisfied: bool
    operation_id: str = ""
    status: str = ""


def plan_candidate_checkpoint_state(
    runtime: Any,
    *,
    stage_id: str,
    trigger_event: ZfEvent,
    loaded: Any,
    trace_id: str,
) -> PlanCandidateCheckpointState:
    flow_kind = orchestration_flow_kind(
        {"stage_id": stage_id},
        loaded,
        trigger_event,
    )
    policy = checkpoint_policy(
        runtime.config,
        "plan_candidate",
        flow_kind=flow_kind,
    )
    if not policy:
        return PlanCandidateCheckpointState(False, False, False)
    event_payload = (
        trigger_event.payload
        if isinstance(trigger_event.payload, dict)
        else {}
    )
    payload = {
        **event_payload,
        "workflow_run_id": str(
            getattr(loaded, "workflow_run_id", "") or trace_id
        ),
        "flow_kind": flow_kind,
        "stage_id": stage_id,
        "plan_revision": str(event_payload.get("plan_revision") or ""),
        "task_map_generation": str(
            getattr(loaded, "task_map_generation", "")
            or event_payload.get("task_map_generation")
            or ""
        ),
        "plan_artifact_package_id": str(
            getattr(loaded, "plan_artifact_package_id", "")
            or event_payload.get("plan_artifact_package_id")
            or ""
        ),
        "plan_artifact_package_ref": str(
            getattr(loaded, "plan_artifact_package_ref", "")
            or event_payload.get("plan_artifact_package_ref")
            or ""
        ),
        "plan_artifact_package_digest": str(
            getattr(loaded, "plan_artifact_package_digest", "")
            or event_payload.get("plan_artifact_package_digest")
            or ""
        ),
        "task_map_ref": str(getattr(loaded, "task_map_ref", "") or ""),
    }
    source_event_id = canonical_checkpoint_source_event_id(
        trigger_event,
        payload,
    )
    operation_id = orchestrator_agent_checkpoint_operation_id(
        checkpoint="plan_candidate",
        workflow_run_id=payload["workflow_run_id"],
        source_event_id=source_event_id,
        payload=payload,
    )
    applied = _applied_plan_decision(
        runtime,
        plan_id=source_event_id,
        operation_id=operation_id,
    )
    if applied:
        decision = str(applied.get("decision") or "")
        return PlanCandidateCheckpointState(
            enabled=True,
            blocking=policy == "blocking",
            satisfied=decision == "adopt",
            operation_id=str(applied.get("operation_id") or operation_id),
            status=f"applied:{decision}",
        )
    prepared = request_orchestrator_agent_checkpoint(
        runtime,
        checkpoint="plan_candidate",
        checkpoint_policy=policy,
        workflow_run_id=payload["workflow_run_id"],
        source_event=trigger_event,
        payload=payload,
    )
    return PlanCandidateCheckpointState(
        enabled=True,
        blocking=policy == "blocking",
        satisfied=False,
        operation_id=prepared.operation_id,
        status=prepared.status,
    )


def _applied_plan_decision(
    runtime: Any,
    *,
    plan_id: str,
    operation_id: str,
) -> dict:
    for event in reversed(runtime.event_log.read_all()):
        if event.type != "orchestrator.semantic.decision.applied":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            str(payload.get("checkpoint") or "") == "plan_candidate"
            and (
                str(payload.get("operation_id") or "") == operation_id
                or str(payload.get("source_event_id") or "") == plan_id
            )
        ):
            return payload
    return {}


__all__ = [
    "PlanCandidateCheckpointState",
    "checkpoint_policy",
    "plan_candidate_checkpoint_state",
]
