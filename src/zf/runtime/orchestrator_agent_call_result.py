"""Call-result adapters and profiles owned by Orchestrator Agent flows."""

from __future__ import annotations

from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_adapters import (
    CallResultProfile,
    ControlResultAdapter,
)
from zf.runtime.orchestrator_agent_contracts import (
    ORCHESTRATION_DECISION_SCHEMA,
    OWNER_DELIVERY_NARRATIVE_SCHEMA,
    OrchestratorAgentContractError,
    normalize_orchestration_decision,
    normalize_owner_delivery_narrative,
)


ORCHESTRATOR_DECISION_PROFILE_ID = "orchestrator-semantic-decision"
OWNER_DELIVERY_NARRATIVE_PROFILE_ID = "owner-delivery-narrative"


def orchestrator_agent_control_result_adapters() -> list[ControlResultAdapter]:
    return [
        ControlResultAdapter(
            adapter_id="owner-delivery-narrative-v1",
            schema_version=OWNER_DELIVERY_NARRATIVE_SCHEMA,
            accepts=_is_owner_delivery_narrative_event,
            normalize=_normalize_owner_delivery_narrative,
        ),
        ControlResultAdapter(
            adapter_id="orchestration-decision-v1",
            schema_version=ORCHESTRATION_DECISION_SCHEMA,
            accepts=_is_orchestration_decision_event,
            normalize=_normalize_orchestration_decision,
        ),
    ]


def orchestrator_agent_call_result_profiles() -> list[CallResultProfile]:
    return [
        CallResultProfile(
            profile_id=OWNER_DELIVERY_NARRATIVE_PROFILE_ID,
            revision="1",
            schema_version=OWNER_DELIVERY_NARRATIVE_SCHEMA,
            adapter_id="owner-delivery-narrative-v1",
            semantic_field="owner_delivery_narrative",
            allowed_event_types=(
                "owner.delivery.narrative.submitted",
                "owner.delivery.narrative.failed",
            ),
        ),
        CallResultProfile(
            profile_id=ORCHESTRATOR_DECISION_PROFILE_ID,
            revision="1",
            schema_version=ORCHESTRATION_DECISION_SCHEMA,
            adapter_id="orchestration-decision-v1",
            semantic_field="orchestration_decision",
            allowed_event_types=(
                "orchestrator.semantic.decision.submitted",
                "orchestrator.semantic.decision.failed",
            ),
        ),
    ]


def _is_orchestration_decision_event(event: ZfEvent) -> bool:
    payload = event.payload if isinstance(event.payload, dict) else {}
    decision = payload.get("orchestration_decision")
    return (
        isinstance(decision, Mapping)
        and str(decision.get("schema_version") or "")
        == ORCHESTRATION_DECISION_SCHEMA
    )


def _is_owner_delivery_narrative_event(event: ZfEvent) -> bool:
    payload = event.payload if isinstance(event.payload, dict) else {}
    narrative = payload.get("owner_delivery_narrative")
    return (
        isinstance(narrative, Mapping)
        and str(narrative.get("schema_version") or "")
        == OWNER_DELIVERY_NARRATIVE_SCHEMA
    )


def _normalize_owner_delivery_narrative(
    event: ZfEvent,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    raw = payload.get("owner_delivery_narrative")
    try:
        return normalize_owner_delivery_narrative(raw), []
    except OrchestratorAgentContractError as exc:
        result = dict(raw) if isinstance(raw, Mapping) else {
            "schema_version": OWNER_DELIVERY_NARRATIVE_SCHEMA,
        }
        return result, [{
            "field": "control_result",
            "code": "schema_invalid",
            "message": str(exc),
        }]


def _normalize_orchestration_decision(
    event: ZfEvent,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    raw = payload.get("orchestration_decision")
    try:
        return normalize_orchestration_decision(raw), []
    except OrchestratorAgentContractError as exc:
        result = dict(raw) if isinstance(raw, Mapping) else {
            "schema_version": ORCHESTRATION_DECISION_SCHEMA,
        }
        return result, [{
            "field": "control_result",
            "code": "schema_invalid",
            "message": str(exc),
        }]


__all__ = [
    "orchestrator_agent_call_result_profiles",
    "orchestrator_agent_control_result_adapters",
]
