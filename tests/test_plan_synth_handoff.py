from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest

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
