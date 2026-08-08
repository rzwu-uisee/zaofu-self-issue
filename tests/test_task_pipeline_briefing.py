from __future__ import annotations

from zf.runtime.task_pipeline_briefing import (
    integration_acceptance_result_template,
    verification_result_template,
)


def test_verify_template_binds_every_admitted_acceptance_criterion() -> None:
    result = verification_result_template({
        "verification_commands": [{
            "command_id": "cmd-unit",
            "command": "pytest tests/test_unit.py -q",
            "command_digest": "a" * 64,
            "owner": "task_verify",
        }, {
            "command_id": "cmd-e2e",
            "command": "npm run test:e2e",
            "command_digest": "b" * 64,
            "owner": "candidate_verify",
        }],
        "acceptance_criteria": [
            {
                "acceptance_id": "AC-1",
                "verification_owner": "task_verify",
                "verification_tier": "browser",
                "verification_command_ids": ["cmd-unit", "cmd-e2e"],
            },
            {
                "acceptance_id": "AC-2",
                "verification_owner": "task_verify",
                "verification_tier": "runtime",
            },
        ],
    })

    assert result["schema_version"] == "verification-result.v1"
    assert result["verdict"] == "passed"
    assert [
        row["acceptance_id"] for row in result["requirement_results"]
    ] == ["AC-1", "AC-2"]
    assert [
        row["verification_tier"] for row in result["requirement_results"]
    ] == ["browser", "runtime"]
    assert all(
        row["evidence_refs"] for row in result["requirement_results"]
    )
    assert result["probe_receipts"] == [{
        "probe_id": "verify-cmd-unit",
        "command_id": "cmd-unit",
        "command": "pytest tests/test_unit.py -q",
        "command_digest": "a" * 64,
        "target_commit": "<exact target_commit from Stage Context>",
        "status": "passed",
        "exit_code": 0,
        "evidence_refs": ["<durable exact command output or event ref>"],
    }]
    assert result["requirement_results"][0]["reproduction_commands"] == [
        "pytest tests/test_unit.py -q"
    ]


def test_acceptance_template_pins_independent_verify_evidence() -> None:
    result = integration_acceptance_result_template({
        "verification_result_ref": "artifacts/verify/TASK-A.json",
    })

    assert result["schema_version"] == (
        "task-integration-acceptance-result.v1"
    )
    assert result["verdict"] == "admit"
    assert result["evidence_refs"] == ["artifacts/verify/TASK-A.json"]
    assert result["feedback"] == []
    assert result["delta_intent"] == {}
