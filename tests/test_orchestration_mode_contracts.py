"""Current orchestration-mode ownership contracts from design 142."""

from pathlib import Path

from zf.core.config.loader import load_config
from zf.core.config.presets import get_preset


ROOT = Path(__file__).resolve().parents[1]
SEMANTIC_CHECKPOINTS = [
    "plan_candidate",
]
SEMANTIC_POLICIES = {
    "plan_candidate": "shadow",
}
FULL_CONTROLLERS = {
    "issue-fanout-v3.yaml",
    "issue-fanout-v3-claude.yaml",
    "prd-fanout-v3.yaml",
    "prd-fanout-v3-claude.yaml",
    "refactor-lane-v3.yaml",
    "refactor-lane-v3-claude.yaml",
}
PILOT_CONTROLLERS = {
    "issue-fanout-v3-oa-pilot.yaml": (
        "issue",
        "issue-plan-candidate-v1",
    ),
    "prd-fanout-v3-oa-pilot.yaml": (
        "prd",
        "prd-plan-candidate-v1",
    ),
    "refactor-lane-v3-oa-pilot.yaml": (
        "refactor",
        "refactor-plan-candidate-v1",
    ),
}


def test_legacy_safe_team_explicitly_enables_layer2_decision_maker() -> None:
    preset = get_preset("safe-team")
    orchestrator = next(
        role for role in preset["roles"] if role["name"] == "orchestrator"
    )

    assert orchestrator["transport"] == "stream-json"
    assert any("zf kanban" in tool for tool in orchestrator["allowed_tools"])


def test_product_controller_profiles_declare_explicit_layer2_authority() -> None:
    controller_dir = ROOT / "examples" / "prod" / "controller"

    for path in sorted(controller_dir.glob("*-v3*.yaml")):
        config = load_config(path)
        orchestrator = next(role for role in config.roles if role.name == "orchestrator")
        stage_roles = {
            role
            for stage in config.workflow.stages
            for role in stage.roles
        }

        expected_triggers = [
            "dispatch.silent_stall",
            "orchestrator.rework.triage.requested",
        ]
        policy = config.workflow.orchestration
        if path.name in FULL_CONTROLLERS:
            expected_triggers.append(
                "orchestrator.semantic.checkpoint.requested"
            )
            assert policy.mode == "semantic_control"
            assert policy.checkpoints == SEMANTIC_CHECKPOINTS
            assert policy.checkpoint_policies == SEMANTIC_POLICIES
            assert policy.flow_policies["research"].mode == (
                "exception_advisor"
            )
            assert policy.flow_policies["workflow"].mode == (
                "exception_advisor"
            )
        elif path.name in PILOT_CONTROLLERS:
            expected_triggers.append(
                "orchestrator.semantic.checkpoint.requested"
            )
            assert policy.mode == "exception_advisor"
            assert policy.checkpoints == []
            flow_kind, pilot_id = PILOT_CONTROLLERS[path.name]
            pilot = policy.flow_policies[flow_kind]
            assert pilot.mode == "semantic_control"
            assert pilot.pilot_id == pilot_id
            assert pilot.checkpoints == ["plan_candidate"]
            assert pilot.checkpoint_policies == {
                "plan_candidate": "blocking",
            }
        else:
            assert policy.mode == "exception_advisor"
            assert policy.checkpoints == []
        assert orchestrator.triggers == expected_triggers, path.name
        assert orchestrator.publishes == [
            "orchestrator.rework.triage.recorded",
        ], path.name
        assert "orchestrator" not in stage_roles, path.name
        assert config.workflow.stages, path.name
        if path.name.startswith("general-workflow"):
            assert len(config.workflow.generic_workflows) == 1
            assert config.workflow.flow_metadata["completion_profile"] == (
                "artifact_delivery"
            )
        else:
            assert config.workflow.pipelines, path.name


def test_all_product_controller_variants_declare_orchestrator_agent() -> None:
    controller_dir = ROOT / "examples" / "prod" / "controller"

    for path in sorted(controller_dir.glob("*-v3*.yaml")):
        config = load_config(path)
        orchestrators = [role for role in config.roles if role.name == "orchestrator"]

        assert len(orchestrators) == 1, path.name
        assert orchestrators[0].transport == "tmux", path.name
        assert "zf-yoke-orchestrator-role-context" in orchestrators[0].skills, path.name
