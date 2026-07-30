from __future__ import annotations

import pytest

from zf.core.config.schema import (
    RoleConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.runtime.flow_roles import (
    FlowRoleBindingError,
    initial_role_configs,
    resolve_writer_owner,
    role_configs_for_flow,
)


def test_multi_flow_start_keeps_only_resident_roles() -> None:
    config = ZfConfig(roles=[
        RoleConfig(
            name="orchestrator",
            instance_id="orchestrator",
            role_kind="reader",
        ),
        RoleConfig(
            name="issue-fix",
            instance_id="issue-fix",
            role_kind="writer",
            flow_kind="issue",
        ),
        RoleConfig(
            name="prd-dev",
            instance_id="prd-dev",
            role_kind="writer",
            flow_kind="prd",
        ),
    ])

    assert [
        role.instance_id for role in initial_role_configs(config)
    ] == ["orchestrator"]
    assert [
        role.instance_id for role in role_configs_for_flow(config, "prd")
    ] == ["prd-dev"]


def test_single_flow_start_preserves_eager_compatibility() -> None:
    config = ZfConfig(roles=[
        RoleConfig(
            name="prd-plan",
            instance_id="prd-plan",
            role_kind="reader",
            flow_kind="prd",
        ),
        RoleConfig(
            name="prd-dev",
            instance_id="prd-dev",
            role_kind="writer",
            flow_kind="prd",
        ),
    ])

    assert [
        role.instance_id for role in initial_role_configs(config)
    ] == ["prd-plan", "prd-dev"]


def test_legacy_writer_stage_supplies_writer_identity_without_role_kind() -> None:
    config = ZfConfig(
        roles=[RoleConfig(name="dev", backend="mock")],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="legacy-impl",
                trigger="task_map.ready",
                topology="fanout_writer_scoped",
                roles=["dev"],
            ),
        ]),
    )

    binding = resolve_writer_owner(
        config,
        flow_kind="",
        owner_role="dev",
    )

    assert binding.owner_role == "dev"
    assert binding.owner_instance == "dev"


def test_replica_owner_accepts_semantic_role_with_exact_instance() -> None:
    config = ZfConfig(roles=[
        RoleConfig(
            name="prd-dev",
            replicas=2,
            role_kind="writer",
            flow_kind="prd",
        ),
    ])

    binding = resolve_writer_owner(
        config,
        flow_kind="prd",
        owner_role="prd-dev",
        owner_instance="prd-dev-2",
    )

    assert binding.owner_role == "prd-dev"
    assert binding.owner_instance == "prd-dev-2"
    assert binding.semantic_owner_role == ""


def test_replica_owner_instance_must_be_exact_and_unambiguous() -> None:
    config = ZfConfig(roles=[
        RoleConfig(
            name="prd-dev",
            replicas=2,
            role_kind="writer",
            flow_kind="prd",
        ),
    ])

    with pytest.raises(
        FlowRoleBindingError,
        match="flow_owner_instance_unknown",
    ):
        resolve_writer_owner(
            config,
            flow_kind="prd",
            owner_instance="prd-dev",
        )
