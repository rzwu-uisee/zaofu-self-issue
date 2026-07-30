from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.loader import load_config
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.artifact_package_policy import effective_artifact_package_mode
from zf.runtime.plan_artifact_package import (
    build_plan_artifact_package,
    write_plan_artifact_package,
)
from zf.runtime.run_contract import (
    build_run_contract,
    stable_json_sha256,
    write_run_contract_snapshot,
)


CONTROLLER_CONFIGS = (
    "prd-fanout-v3.yaml",
    "prd-fanout-v3-claude.yaml",
    "prd-light-v3.yaml",
    "prd-light-v3-claude.yaml",
    "issue-fanout-v3.yaml",
    "issue-fanout-v3-claude.yaml",
    "refactor-lane-v3.yaml",
    "refactor-lane-v3-claude.yaml",
)


@pytest.mark.parametrize("name", CONTROLLER_CONFIGS)
def test_production_controllers_pin_blocking_artifact_handoff(name: str) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "examples" / "prod" / "controller" / name)

    assert config.workflow.flow_metadata["result_protocol"]["mode"] == "blocking"
    assert config.workflow.flow_metadata["artifact_package"]["mode"] == "blocking"


def test_codex_refactor_controller_pins_file_based_semantic_submit() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(
        root / "examples" / "prod" / "controller" / "refactor-lane-v3.yaml"
    )

    assert config.workflow.flow_metadata["result_protocol"][
        "semantic_submit_profiles"
    ] == {
        "workflow-read": "blocking",
    }


def test_claude_refactor_critic_does_not_require_unwritable_result_file() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(
        root / "examples" / "prod" / "controller"
        / "refactor-lane-v3-claude.yaml"
    )

    profiles = config.workflow.flow_metadata["result_protocol"][
        "semantic_submit_profiles"
    ]
    assert "plan-synth" not in profiles


def test_run_contract_records_controller_artifact_package_mode(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    config_path = root / "examples/prod/controller/prd-light-v3.yaml"
    config = load_config(config_path)

    contract = build_run_contract(
        config,
        config_path=config_path,
        project_root=root,
        state_dir=tmp_path / ".zf",
    )

    assert contract["protocols"]["artifact_package"] == {
        "schema_version": "plan-artifact-package.v1",
        "mode": "blocking",
    }


def test_package_mode_is_run_pinned_across_upgrade_and_rollback(
    tmp_path: Path,
) -> None:
    run_contract_body = {
        "schema_version": "run-contract.v1",
        "workflow": {"kind": "prd"},
    }
    run_contract = {
        **run_contract_body,
        "contract_digest": stable_json_sha256(run_contract_body),
    }
    run_contract_ref = write_run_contract_snapshot(tmp_path, run_contract)
    port = write_immutable_json_sidecar(
        tmp_path,
        {"schema_version": "task-map.v1", "tasks": []},
        root="fixtures",
        kind="task_map",
        schema_version="task-map.v1",
        created_by="test",
    )
    produced = [{
        "logical_name": "task_map",
        "artifact_kind": "task_map",
        "schema_version": "task-map.v1",
        "producer_stage_id": "plan",
        "ref": port["ref"],
        "sha256": port["sha256"],
    }]

    legacy = build_plan_artifact_package(
        workflow_run_id="legacy-run",
        flow_kind="prd",
        producer_stage_id="plan",
        run_contract=run_contract_ref,
        plan_revision="R1",
        task_map_generation="G1",
        produced=produced,
    )
    legacy_ref = write_plan_artifact_package(tmp_path, legacy)
    assert effective_artifact_package_mode(
        state_dir=tmp_path,
        payload={
            "plan_artifact_package_ref": legacy_ref["ref"],
            "plan_artifact_package_digest": legacy_ref["sha256"],
        },
        metadata={"artifact_package": {"mode": "blocking"}},
    ) == "shadow"

    blocking = build_plan_artifact_package(
        workflow_run_id="blocking-run",
        flow_kind="prd",
        producer_stage_id="plan",
        run_contract=run_contract_ref,
        plan_revision="R2",
        task_map_generation="G2",
        produced=produced,
        package_mode="blocking",
    )
    blocking_ref = write_plan_artifact_package(tmp_path, blocking)
    assert effective_artifact_package_mode(
        state_dir=tmp_path,
        payload={
            "artifact_package_mode": "shadow",
            "plan_artifact_package_ref": blocking_ref["ref"],
            "plan_artifact_package_digest": blocking_ref["sha256"],
        },
        metadata={"artifact_package": {"mode": "shadow"}},
    ) == "blocking"
