from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zf.core.config.loader import ConfigError, load_config
from zf.core.events.model import ZfEvent
from zf.core.workflow.generic_workflow import (
    GENERIC_WORKFLOW_CONTRACT_VERSION,
    GenericWorkflowError,
    build_registered_template_spec,
    generic_workflow_catalog_projection,
)
from zf.core.workflow.workflow_kind import (
    WorkflowKindError,
    compile_workflow_kind,
)
from zf.runtime.flow_roles import initial_role_configs, role_configs_for_flow
from zf.runtime.stage_failure_replan import plan_reader_stage_replan


def _research_spec() -> dict:
    return {
        "contractVersion": GENERIC_WORKFLOW_CONTRACT_VERSION,
        "intent": "research",
        "template": "evidence-synthesis-v1",
        "entry": "scope",
        "completionProfile": {
            "id": "artifact_delivery",
            "requiredArtifacts": ["synthesize.report"],
            "independentVerify": True,
        },
        "tasks": [
            {
                "name": "scope",
                "operation": "agent.read",
                "role": "scoper",
                "inputs": [
                    {
                        "name": "request",
                        "kind": "text/request",
                        "from": "external.request",
                    }
                ],
                "outputs": [{"name": "brief", "kind": "text/brief"}],
            },
            {
                "name": "collect-a",
                "operation": "agent.read",
                "role": "collector-a",
                "dependencies": ["scope"],
                "inputs": [
                    {
                        "name": "brief",
                        "kind": "text/brief",
                        "from": "scope.brief",
                    }
                ],
                "outputs": [{"name": "evidence", "kind": "evidence/bundle"}],
            },
            {
                "name": "collect-b",
                "operation": "agent.read",
                "role": "collector-b",
                "dependencies": ["scope"],
                "inputs": [
                    {
                        "name": "brief",
                        "kind": "text/brief",
                        "from": "scope.brief",
                    }
                ],
                "outputs": [{"name": "evidence", "kind": "evidence/bundle"}],
            },
            {
                "name": "synthesize",
                "operation": "agent.synthesize",
                "role": "synthesizer",
                "dependencies": ["collect-a", "collect-b"],
                "inputs": [
                    {
                        "name": "evidence-a",
                        "kind": "evidence/bundle",
                        "from": "collect-a.evidence",
                    },
                    {
                        "name": "evidence-b",
                        "kind": "evidence/bundle",
                        "from": "collect-b.evidence",
                    },
                ],
                "outputs": [{"name": "report", "kind": "report/markdown"}],
            },
            {
                "name": "verify",
                "operation": "agent.verify",
                "role": "verifier",
                "dependencies": ["synthesize"],
                "inputs": [
                    {
                        "name": "report",
                        "kind": "report/markdown",
                        "from": "synthesize.report",
                    }
                ],
                "outputs": [{"name": "verdict", "kind": "verdict/json"}],
            },
        ],
    }


def _envelope(spec: dict) -> str:
    roles = [
        {
            "name": name,
            "instance_id": name,
            "backend": "mock",
            "role_kind": "reader",
        }
        for name in (
            "scoper",
            "collector-a",
            "collector-b",
            "synthesizer",
            "verifier",
        )
    ]
    documents = [
        {
            "apiVersion": "zaofu.dev/v1",
            "kind": "Workflow",
            "metadata": {"name": "evidence-synthesis"},
            "spec": spec,
        },
        {
            "apiVersion": "zaofu.dev/v1",
            "kind": "ZfConfig",
            "metadata": {"name": "generic-workflow-test"},
            "spec": {
                "version": "1.0",
                "project": {"name": "generic-workflow-test"},
                "roles": roles,
            },
        },
    ]
    return yaml.safe_dump_all(documents, sort_keys=False)


def test_safe_generic_workflow_compiles_registered_contract() -> None:
    compilation = compile_workflow_kind(_research_spec())
    stages = {stage["id"]: stage for stage in compilation.stages}

    assert stages["scope"]["trigger"] == "workflow.invoke.requested"
    assert stages["scope"]["operation"] == "agent.read"
    assert stages["scope"]["result_semantics"] == "artifact_production"
    assert stages["synthesize"]["result_semantics"] == "artifact_production"
    assert stages["verify"]["result_semantics"] == "subject_gate"
    assert stages["synthesize"]["trigger"] == (
        "workflow.dependency_barrier.satisfied"
    )
    assert stages["synthesize"]["dependency_events"] == [
        "collect-a.completed",
        "collect-b.completed",
    ]
    assert stages["synthesize"]["dependency_barrier_id"].startswith(
        "barrier:synthesize:"
    )
    for stage_id, stage in stages.items():
        assert stage["aggregate"]["child_success_event"] == (
            "workflow.child.completed"
        )
        assert stage["aggregate"]["child_failure_event"] == (
            "workflow.child.failed"
        )
        assert stage["aggregate"]["failure_event"] == (
            f"{stage_id}.failed"
        )
    assert compilation.generic_contract["contract_digest"]
    assert compilation.flow_metadata["completion_profile"] == (
        "artifact_delivery"
    )
    assert compilation.flow_metadata["result_protocol"] == {
        "mode": "blocking",
        "semantic_submit_profiles": {
            "workflow-read": "blocking",
            "artifact-delivery": "blocking",
        },
    }
    assert compilation.flow_metadata["required_delivery_artifacts"] == [
        {
            "name": "report",
            "kind": "report/markdown",
            "source_ref": "synthesize.report",
            "required_for": "standard",
        }
    ]


def test_safe_generic_workflow_contract_digest_is_deterministic() -> None:
    first = compile_workflow_kind(_research_spec()).generic_contract
    second = compile_workflow_kind(_research_spec()).generic_contract

    assert first == second


def test_generic_workflow_envelope_loads_canonical_sidecars(
    tmp_path: Path,
) -> None:
    path = tmp_path / "zf.yaml"
    path.write_text(_envelope(_research_spec()), encoding="utf-8")

    config = load_config(path)

    assert [stage.id for stage in config.workflow.stages] == [
        "scope",
        "collect-a",
        "collect-b",
        "synthesize",
        "verify",
    ]
    assert config.workflow.stages[3].dependency_barrier_digest
    assert config.workflow.stages[0].result_semantics == "artifact_production"
    assert config.workflow.stages[4].result_semantics == "subject_gate"
    assert config.workflow.stages[3].input_ports[0].source == (
        "collect-a.evidence"
    )
    assert len(config.workflow.generic_workflows) == 1
    assert "workflow.invoke.requested" in (
        config.workflow.dag.external_triggers
    )
    assert (
        config.workflow.flow_metadata_by_kind["workflow"]["workflow_template"]
        == "evidence-synthesis-v1"
    )
    assert config.workflow.flow_metadata_by_kind["workflow"][
        "result_protocol"
    ]["semantic_submit_profiles"] == {
        "workflow-read": "blocking",
        "artifact-delivery": "blocking",
    }
    assert {role.flow_kind for role in config.roles} == {"workflow"}
    assert config.goal.enabled is True
    assert "workflow" not in config.workflow.dag.event_schemas_by_kind
    assert config.workflow.dag.event_schemas["run.goal.completed"][
        "enum"
    ]["completion_profile"] == ["artifact_delivery"]


def test_generic_stage_failure_replans_only_the_failed_stage(
    tmp_path: Path,
) -> None:
    path = tmp_path / "zf.yaml"
    path.write_text(_envelope(_research_spec()), encoding="utf-8")
    config = load_config(path)
    origin = ZfEvent(
        id="evt-synthesize-complete",
        type="synthesize.completed",
        correlation_id="run-generic",
        payload={
            "workflow_run_id": "run-generic",
            "workflow_generation": "a" * 64,
            "goal_claim_set_ref": "artifacts/claims/current.json",
            "goal_claim_set_digest": "b" * 64,
        },
    )
    failure = ZfEvent(
        id="evt-verify-failed",
        type="verify.failed",
        correlation_id="run-generic",
        payload={
            "workflow_run_id": "run-generic",
            "stage_id": "verify",
            "reason": "timeout",
        },
    )

    replan, note = plan_reader_stage_replan(
        config,
        [origin, failure],
        failure,
    )

    assert note == "replan verify"
    assert replan is not None
    assert replan.type == "synthesize.completed"
    assert replan.causation_id == failure.id
    assert replan.payload["rework_attempt"] == 1
    assert replan.payload["goal_claim_set_digest"] == "b" * 64


def test_generic_workflow_preserves_explicit_goal_opt_out(
    tmp_path: Path,
) -> None:
    documents = list(yaml.safe_load_all(_envelope(_research_spec())))
    documents[1]["spec"]["goal"] = {"enabled": False}
    path = tmp_path / "zf.yaml"
    path.write_text(
        yaml.safe_dump_all(documents, sort_keys=False),
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.goal.enabled is False


def test_generic_workflow_roles_form_lazy_multi_flow_closure(
    tmp_path: Path,
) -> None:
    documents = list(yaml.safe_load_all(_envelope(_research_spec())))
    documents[1]["spec"]["roles"].extend([
        {
            "name": "controller",
            "instance_id": "controller",
            "backend": "mock",
            "role_kind": "reader",
        },
        {
            "name": "prd-dev",
            "instance_id": "prd-dev",
            "backend": "mock",
            "role_kind": "writer",
            "flow_kind": "prd",
        },
    ])
    path = tmp_path / "zf.yaml"
    path.write_text(
        yaml.safe_dump_all(documents, sort_keys=False),
        encoding="utf-8",
    )

    config = load_config(path)

    assert [
        role.instance_id for role in initial_role_configs(config)
    ] == ["controller"]
    assert [
        role.instance_id for role in role_configs_for_flow(config, "workflow")
    ] == [
        "scoper",
        "collector-a",
        "collector-b",
        "synthesizer",
        "verifier",
    ]


def test_generic_workflow_rejects_cross_flow_role_binding(
    tmp_path: Path,
) -> None:
    documents = list(yaml.safe_load_all(_envelope(_research_spec())))
    documents[1]["spec"]["roles"][0]["flow_kind"] = "prd"
    path = tmp_path / "zf.yaml"
    path.write_text(
        yaml.safe_dump_all(documents, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(
        ConfigError,
        match="role 'scoper' already belongs to Flow 'prd'",
    ):
        load_config(path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda spec: spec.update(template="unknown-template"),
            "not registered",
        ),
        (
            lambda spec: spec.update(intent="delivery"),
            "does not allow intent",
        ),
        (
            lambda spec: spec["tasks"][3]["inputs"][0].update(
                kind="evidence/other"
            ),
            "does not match producer",
        ),
        (
            lambda spec: spec["tasks"][4].update(inputs=[]),
            "Verify must consume",
        ),
    ],
)
def test_generic_workflow_contract_fails_closed(mutate, message: str) -> None:
    spec = _research_spec()
    mutate(spec)

    with pytest.raises(WorkflowKindError, match=message):
        compile_workflow_kind(spec)


def test_generic_workflow_loader_wraps_contract_error(
    tmp_path: Path,
) -> None:
    spec = _research_spec()
    spec["template"] = "unknown-template"
    path = tmp_path / "zf.yaml"
    path.write_text(_envelope(spec), encoding="utf-8")

    with pytest.raises(ConfigError, match="not registered"):
        load_config(path)


def test_generic_workflow_catalog_is_registered_and_bounded() -> None:
    catalog = generic_workflow_catalog_projection()

    assert catalog["contract_version"] == GENERIC_WORKFLOW_CONTRACT_VERSION
    assert set(catalog["operations"]) == {
        "agent.read",
        "agent.synthesize",
        "agent.verify",
        "agent.write",
    }
    research = catalog["templates"]["evidence-synthesis-v1"]
    assert research["intents"] == ["research"]
    assert research["completion_profiles"] == ["artifact_delivery"]
    assert research["parameters"]["collector_roles"] == {
        "min_items": 2,
        "max_items": 8,
    }


def test_evidence_synthesis_template_expands_to_registered_graph() -> None:
    spec = build_registered_template_spec(
        "evidence-synthesis-v1",
        {
            "scoper_role": "scoper",
            "collector_roles": ["collector-a", "collector-b"],
            "synthesizer_role": "synthesizer",
            "verifier_role": "verifier",
            "artifact_name": "research-report",
            "artifact_kind": "report/markdown",
        },
    )

    compilation = compile_workflow_kind(spec)
    stages = {stage["id"]: stage for stage in compilation.stages}

    assert list(stages) == [
        "scope",
        "collect-1",
        "collect-2",
        "synthesize",
        "verify",
    ]
    assert stages["synthesize"]["dependencies"] == [
        "collect-1",
        "collect-2",
    ]
    assert stages["synthesize"]["dependency_barrier_digest"]
    assert stages["verify"]["roles"] == ["verifier"]
    assert compilation.flow_metadata["completion_profile"] == (
        "artifact_delivery"
    )
    assert compilation.flow_metadata["result_protocol_mode"] == "blocking"
    assert compilation.flow_metadata["required_delivery_artifacts"] == [{
        "name": "research-report",
        "kind": "report/markdown",
        "source_ref": "synthesize.research-report",
        "required_for": "standard",
    }]


def test_evidence_synthesis_template_rejects_self_verify_role() -> None:
    with pytest.raises(GenericWorkflowError, match="must differ"):
        build_registered_template_spec(
            "evidence-synthesis-v1",
            {
                "scoper_role": "scoper",
                "collector_roles": ["collector-a", "collector-b"],
                "synthesizer_role": "author",
                "verifier_role": "author",
            },
        )


def test_generic_contract_rejects_shared_producer_and_verifier_role() -> None:
    spec = _research_spec()
    spec["tasks"][4]["role"] = "synthesizer"

    with pytest.raises(WorkflowKindError, match="also produce"):
        compile_workflow_kind(spec)
