from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.plan_synth_handoff import render_plan_synth_completion_command
from zf.runtime.plan_synth_handoff import build_plan_synth_call_payload
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.stage_execution_card import (
    compact_stage_context,
    prepare_result_file_command,
)


def test_plan_synth_completion_command_round_trips_shell_sensitive_json(
    tmp_path,
) -> None:
    payload = {
        "fanout_id": "fanout-plan",
        "stage_id": "plan",
        "child_id": "synth",
        "operation_id": "wop-plan-synth",
        "result_scratch_ref": "tmp/result-submit/wop-plan-synth/a/result.json",
        "report": {
            "child_id": "synth",
            "plan_md": (
                "Run python3 -c \"from pathlib import Path; "
                "assert Path('app/result.txt').read_bytes() == b'ok\\n'\"."
            ),
        },
    }

    command = render_plan_synth_completion_command(
        cli_command="uv --project /repo run zf",
        actor="plan-critic",
        state_dir=tmp_path / ".zf",
        payload=payload,
    )

    parsed = subprocess.run(
        ["bash", "-n"],
        input=command,
        text=True,
        capture_output=True,
        check=False,
    )
    assert parsed.returncode == 0, parsed.stderr
    argv = shlex.split(command)
    assert argv[-2] == "--result-file"
    scratch = tmp_path / ".zf" / payload["result_scratch_ref"]
    assert Path(argv[-1]) == scratch
    assert json.loads(scratch.read_text(encoding="utf-8")) == payload


def test_result_scratch_is_bounded_and_preserves_agent_edits(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    command, scratch = prepare_result_file_command(
        state_dir=state_dir,
        result_scratch_ref="tmp/result-submit/op-1/a/result.json",
        operation_id="op-1",
        cli_command="zf",
        semantic_template={"summary": "initial"},
    )
    scratch.write_text('{"summary":"agent edit"}\n', encoding="utf-8")

    repeated, repeated_scratch = prepare_result_file_command(
        state_dir=state_dir,
        result_scratch_ref="tmp/result-submit/op-1/a/result.json",
        operation_id="op-1",
        cli_command="zf",
        semantic_template={"summary": "replacement"},
    )

    assert repeated == command
    assert repeated_scratch == scratch
    assert json.loads(scratch.read_text(encoding="utf-8")) == {
        "summary": "agent edit",
    }
    with pytest.raises(ValueError, match="escapes state dir"):
        prepare_result_file_command(
            state_dir=state_dir,
            result_scratch_ref="../outside.json",
            operation_id="op-1",
            cli_command="zf",
            semantic_template={},
        )


def test_compact_stage_context_excludes_copied_semantic_bodies() -> None:
    compact = compact_stage_context({
        "workflow_run_id": "run-1",
        "task_id": "T1",
        "contract_revision": "r2",
        "expected_output": {"schema": "implementation-result.v1"},
        "raw_task": {"acceptance": ["AC-OLD"]},
        "contract_snapshot": {"acceptance_criteria": ["AC-CURRENT"]},
        "instruction": "Implement current contract.",
    })

    assert compact == {
        "workflow_run_id": "run-1",
        "task_id": "T1",
        "contract_revision": "r2",
        "expected_output": {"schema": "implementation-result.v1"},
        "instruction": "Implement current contract.",
    }


def test_plan_synth_handoff_pins_requirement_and_rework_context(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    state_dir = project_root / ".zf"
    state_dir.mkdir(parents=True)
    requirement = state_dir / "requirements" / "revision-2.json"
    requirement.parent.mkdir()
    requirement.write_text(
        json.dumps({
            "objective": "Preserve behavior.",
            "acceptance": ["Canonical acceptance text."],
        }),
        encoding="utf-8",
    )

    payload = build_plan_synth_call_payload(
        state_dir=state_dir,
        project_root=project_root,
        manifest={
            "fanout_id": "fanout-replan",
            "trace_id": "run-replan",
            "stage_id": "flow-plan",
            "trigger_event_id": "evt-replan",
            "trigger_payload": {
                "requirement_spec_ref": str(requirement),
                "rework_of": "evt-package-rejected",
                "rework_attempt": 2,
                "rework_source": "plan.artifact_package.rejected",
                "rework_feedback": [
                    "Keep the canonical acceptance text unchanged.",
                ],
                "rework_categories": ["goal_claim_identity_drift"],
                "replan_classification": "design_issue",
            },
        },
        reports=[{
            "child_id": "planner",
            "report": {"status": "passed"},
        }],
        run_id="run-fanout-replan-synth",
        role_instance="plan-critic",
    )

    sources = {
        source["source_id"]: source
        for source in payload["input_refs"]
    }
    assert {"requirement", "plan-rework-context"} <= sources.keys()
    requirement_body = hydrate_sidecar_ref(
        state_dir,
        sources["requirement"],
    ).payload
    assert requirement_body["acceptance"] == ["Canonical acceptance text."]
    rework_body = hydrate_sidecar_ref(
        state_dir,
        sources["plan-rework-context"],
    ).payload
    assert rework_body == {
        "schema_version": "plan-rework-context.v1",
        "rework_of": "evt-package-rejected",
        "rework_attempt": 2,
        "rework_source": "plan.artifact_package.rejected",
        "rework_feedback": [
            "Keep the canonical acceptance text unchanged.",
        ],
        "rework_categories": ["goal_claim_identity_drift"],
        "replan_classification": "design_issue",
    }


def test_plan_rework_context_pins_current_task_and_delivery_checkpoint(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    state_dir = project_root / ".zf"
    state_dir.mkdir(parents=True)
    task_store = TaskStore(state_dir / "kanban.json")
    task_store.add(Task(
        id="GAP-RELEASE-R5",
        title="Current release successor",
        status="blocked",
        assigned_to="prd-dev-lane-1",
        blocked_reason="rework_triage:environment_issue",
        retry_count=5,
        contract=TaskContract(
            feature_id="TASK-ROOT",
            parent_task_id="GAP-RELEASE-R4",
            source_task_id="GAP-RELEASE-R5",
            source_ref="event:evt-r5",
            source_revision="source-r5",
            contract_revision="contract-r5",
            product_contract_ref="artifacts/TASK-ROOT/r5/task_map.json",
            scope=["scripts/release/**"],
            acceptance_criteria=["All release commands pass."],
            validation={"commands": [{
                "id": "release-gate",
                "command": "python scripts/release/release_gate.py",
            }]},
        ),
    ))
    event_log = EventLog(state_dir / "events.jsonl")
    event_log.append(ZfEvent(
        type="dev.build.done",
        actor="prd-dev-lane-1",
        task_id="GAP-RELEASE-R5",
        correlation_id="workflow-1",
        payload={
            "workflow_run_id": "workflow-1",
            "source_commit": "9" * 40,
            "target_commit": "9" * 40,
            "task_map_ref": "artifacts/TASK-ROOT/r5/task_map.json",
            "contract_snapshot_ref": "artifacts/contracts/r5.json",
            "contract_snapshot_digest": "a" * 64,
            "impl_self_check": {
                "command_receipts": [
                    {"status": "passed"},
                    {"status": "passed"},
                ],
                "acceptance_results": [
                    {"status": "passed"},
                ],
                "evidence_refs": [f"git:{'9' * 40}"],
                "residual_risks": [],
            },
        },
    ))
    event_log.append(ZfEvent(
        type="dev.failed",
        actor="prd-dev-lane-1",
        task_id="GAP-RELEASE-R5",
        correlation_id="workflow-1",
        payload={
            "workflow_run_id": "workflow-1",
            "failure_class": "contract_scope_base_conflict",
            "reason": "The inherited checkpoint was compared to the wrong base.",
        },
    ))

    payload = build_plan_synth_call_payload(
        state_dir=state_dir,
        project_root=project_root,
        manifest={
            "fanout_id": "fanout-replan",
            "workflow_run_id": "workflow-1",
            "stage_id": "prd-plan",
            "trigger_event_id": "evt-replan",
            "trigger_payload": {
                "pdd_id": "TASK-ROOT",
                "workflow_run_id": "workflow-1",
                "rework_of": "evt-stale-r2",
                "rework_attempt": 1,
                "failed_task_ids": ["GAP-RELEASE-R2"],
                "rework_feedback": [{
                    "severity": "high",
                    "message": "superseded_task_map",
                }],
            },
        },
        reports=[{
            "child_id": "planner",
            "report": {"status": "passed"},
        }],
        run_id="run-fanout-replan-synth",
        role_instance="plan-critic",
    )

    sources = {
        source["source_id"]: source
        for source in payload["input_refs"]
    }
    rework_body = hydrate_sidecar_ref(
        state_dir,
        sources["plan-rework-context"],
    ).payload
    snapshot = rework_body["canonical_task_snapshot"]
    assert snapshot["feature_id"] == "TASK-ROOT"
    assert snapshot["workflow_run_id"] == "workflow-1"
    assert len(snapshot["tasks"]) == 1
    current = snapshot["tasks"][0]
    assert current["task_id"] == "GAP-RELEASE-R5"
    assert current["status"] == "blocked"
    assert current["contract"]["contract_revision"] == "contract-r5"
    assert current["contract"]["product_contract_ref"].endswith("r5/task_map.json")
    assert current["latest_delivery_fact"] == {
        "event_id": current["latest_delivery_fact"]["event_id"],
        "event_type": "dev.build.done",
        "timestamp": current["latest_delivery_fact"]["timestamp"],
        "source_commit": "9" * 40,
        "target_commit": "9" * 40,
        "task_map_ref": "artifacts/TASK-ROOT/r5/task_map.json",
        "contract_snapshot_ref": "artifacts/contracts/r5.json",
        "contract_snapshot_digest": "a" * 64,
        "failure_class": "",
        "reason": "",
        "command_receipt_count": 2,
        "passed_acceptance_count": 1,
        "evidence_refs": [f"git:{'9' * 40}"],
        "residual_risks": [],
    }
    assert current["latest_blocking_fact"]["event_type"] == "dev.failed"
    assert current["latest_blocking_fact"]["failure_class"] == (
        "contract_scope_base_conflict"
    )
