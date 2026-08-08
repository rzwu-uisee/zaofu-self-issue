from __future__ import annotations

from zf.core.config.schema import (
    WorkflowConfig,
    WorkflowOrchestrationConfig,
    WorkflowOrchestrationFlowPolicyConfig,
    ZfConfig,
)
from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator_agent_policy import (
    checkpoint_policy,
    orchestration_flow_kind,
)


def _semantic_policy() -> WorkflowOrchestrationFlowPolicyConfig:
    return WorkflowOrchestrationFlowPolicyConfig(
        mode="semantic_control",
        checkpoints=["stage_barrier"],
        checkpoint_policies={"stage_barrier": "blocking"},
    )


def test_flow_policy_selects_full_routes_and_keeps_light_routes_small() -> None:
    config = ZfConfig(workflow=WorkflowConfig(orchestration=(
        WorkflowOrchestrationConfig(
            flow_policies={
                "prd": _semantic_policy(),
                "refactor": _semantic_policy(),
                "research": WorkflowOrchestrationFlowPolicyConfig(),
            },
        )
    )))

    assert checkpoint_policy(
        config, "stage_barrier", flow_kind="prd"
    ) == "blocking"
    assert checkpoint_policy(
        config, "stage_barrier", flow_kind="feat"
    ) == "blocking"
    assert checkpoint_policy(config, "stage_barrier", flow_kind="issue") == ""
    assert checkpoint_policy(
        config, "stage_barrier", flow_kind="research"
    ) == ""


def test_fixed_research_marker_overrides_generic_workflow_identity() -> None:
    event = ZfEvent(
        type="fanout.aggregate.completed",
        payload={
            "flow_kind": "workflow",
            "stage_id": "research-fanout",
            "pattern_id": "research-fanout",
        },
    )

    assert orchestration_flow_kind(event) == "research"


def test_non_research_general_workflow_keeps_workflow_identity() -> None:
    event = ZfEvent(
        type="fanout.aggregate.completed",
        payload={
            "flow_kind": "workflow",
            "stage_id": "evidence-synthesis-v1",
        },
    )

    assert orchestration_flow_kind(event) == "workflow"
