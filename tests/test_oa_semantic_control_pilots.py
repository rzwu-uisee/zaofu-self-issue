from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.e2e.oa_multiflow_mock_pilot import (
    FLOW_CASES,
    build_multiflow_report,
    source_identity,
)
from tests.e2e.oa_provider_ab_pilot import (
    KNOWN_GAPS,
    PLAN_FIXTURE,
    _identity,
    _provider_command,
    _sha,
    _validate_provider_result,
    blocked_report,
    completed_report,
    provider_usage,
)


def _source_identity() -> dict:
    return {
        "head_commit": "a" * 40,
        "dirty": False,
        "status_sha256": "b" * 64,
        "tracked_diff_sha256": "c" * 64,
        "untracked_sha256": "d" * 64,
        "untracked_file_count": 0,
        "worktree_sha256": "e" * 64,
    }


def _candidate_result() -> dict:
    identity = _identity(_sha(PLAN_FIXTURE))
    descriptor = {
        "ref": identity["plan_artifact_package_ref"],
        "sha256": identity["plan_artifact_package_digest"],
    }
    return {
        "schema_version": "orchestration-decision.v1",
        "execution_status": "completed",
        "identity": identity,
        "decision": "revise",
        "reason_codes": ["mandatory_claim_unmapped", "audit_evidence_missing"],
        "detected_gap_ids": sorted(KNOWN_GAPS),
        "affected_work_units": ["TASK-AUTO-API", "TASK-AUTO-WEB"],
        "required_followup": "revise the Plan Package",
        "expected_outcome": "all mandatory claims have evidence-bearing work",
        "confidence": 0.95,
        "delta": {
            "schema_version": "orchestration-delta.v1",
            "identity": {
                key: identity[key]
                for key in (
                    "operation_id",
                    "workflow_run_id",
                    "checkpoint",
                    "input_digest",
                )
            },
            "directives": [{
                "directive_id": "revise-security-and-audit",
                "action": "revise",
                "basis_refs": [descriptor],
                "required_actions": [
                    "map CLAIM-SECURITY",
                    "add audit evidence contract",
                ],
                "reuse_refs": [descriptor],
                "invalidate_refs": [],
            }],
        },
    }


def test_multiflow_report_keeps_each_flow_result_independent(
    tmp_path: Path,
) -> None:
    def runner(_root: Path, case, _timeout: int):  # noqa: ANN001
        status = "failed" if case.flow_kind == "issue" else "passed"
        return {
            "flow_kind": case.flow_kind,
            "coverage": case.coverage,
            "node_id": case.node_id,
            "status": status,
            "exit_code": 1 if status == "failed" else 0,
            "duration_seconds": 0.01,
            "stdout_tail": "",
            "stderr_tail": "",
        }

    report = build_multiflow_report(
        repo_root=tmp_path,
        timeout_seconds=30,
        runner=runner,
        identity=_source_identity(),
    )

    assert [row["flow_kind"] for row in report["flows"]] == [
        case.flow_kind for case in FLOW_CASES
    ]
    assert report["status"] == "failed"
    assert report["summary"] == {"flow_count": 4, "passed": 3, "failed": 1}


def test_source_identity_hashes_untracked_file_contents(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
    )
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=tmp_path,
        check=True,
    )
    untracked = tmp_path / "candidate.txt"
    untracked.write_text("first\n", encoding="utf-8")
    first = source_identity(tmp_path)

    untracked.write_text("second\n", encoding="utf-8")
    second = source_identity(tmp_path)

    assert first["status_sha256"] == second["status_sha256"]
    assert first["untracked_sha256"] != second["untracked_sha256"]
    assert first["worktree_sha256"] != second["worktree_sha256"]


def test_provider_result_is_a_valid_bounded_revision() -> None:
    result = _candidate_result()

    evaluated = _validate_provider_result(
        result,
        identity=result["identity"],
    )

    assert evaluated["known_gap_recall"] == 1.0
    assert evaluated["intervention_valid"] is True


def test_provider_result_rejects_duplicate_gap_ids() -> None:
    result = _candidate_result()
    result["detected_gap_ids"].append(result["detected_gap_ids"][0])

    with pytest.raises(RuntimeError, match="duplicate gap ids"):
        _validate_provider_result(result, identity=result["identity"])


def test_provider_command_reads_prompt_from_closed_stdin(tmp_path: Path) -> None:
    command = _provider_command(
        model="test-model",
        reasoning_effort="low",
        sandbox="read-only",
        schema_path=tmp_path / "schema.json",
        output_path=tmp_path / "output.json",
        root=tmp_path,
    )

    assert command[-1] == "-"


def test_completed_provider_report_compares_shadow_and_blocking_only() -> None:
    result = _candidate_result()
    evaluated = _validate_provider_result(result, identity=result["identity"])
    candidate = {
        "duration_seconds": 1.2,
        "provider_session_id": "thread-1",
        "usage": {
            "input_tokens": 100,
            "cached_input_tokens": 0,
            "output_tokens": 20,
            "cost_usd": None,
            "cost_status": "provider_not_reported",
        },
        "result": result,
        "evaluation": {
            key: value
            for key, value in evaluated.items()
            if key != "normalized"
        },
    }
    report = completed_report(
        repo_identity=_source_identity(),
        model="test-model",
        budget={"max_oa_provider_turns_per_arm": 1},
        shadow=candidate,
        blocking=candidate,
    )

    assert report["status"] == "completed"
    assert all(report["fairness"].values())
    assert report["winner"] == "B"
    assert report["rollout_decision"] == "insufficient_evidence"
    assert report["scope"] == "plan_candidate_shadow_vs_blocking"
    assert report["arms"][0]["mode"] == "plan_candidate_shadow"
    assert report["arms"][0]["provider_calls"] == 1
    assert report["arms"][0]["quality"]["intervention_applied"] is False
    assert report["arms"][1]["mode"] == "plan_candidate_blocking"
    assert report["arms"][1]["provider_calls"] == 1
    assert report["arms"][1]["quality"]["intervention_applied"] is True
    assert report["enforcement_delta"] == 1


def test_blocked_provider_report_has_no_winner() -> None:
    report = blocked_report(
        reason="provider_unavailable",
        repo_identity=_source_identity(),
        provider="codex",
        model="test-model",
        budget={"max_oa_provider_turns_per_arm": 1},
    )

    assert report["status"] == "blocked"
    assert report["winner"] is None
    assert report["arms"] == []


def test_provider_usage_uses_last_reported_usage() -> None:
    usage = provider_usage([
        {"usage": {"input_tokens": 10, "output_tokens": 2}},
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 30,
                "cached_input_tokens": 7,
                "output_tokens": 5,
            },
        },
    ])

    assert usage == {
        "input_tokens": 30,
        "cached_input_tokens": 7,
        "output_tokens": 5,
        "cost_usd": None,
        "cost_status": "provider_not_reported",
    }
