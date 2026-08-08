from __future__ import annotations

import pytest

from zf.core.task.schema import Task
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.call_result_adapters import ControlResultAdapterRegistry
from zf.runtime.integration_acceptance_result import (
    IntegrationAcceptanceResultError,
    bind_required_read_ledger,
    normalize_integration_acceptance_result,
)
from zf.runtime.task_contract_snapshot import (
    build_target_snapshot,
    build_task_contract_snapshot,
    write_target_snapshot,
    write_task_contract_snapshot,
)
from zf.runtime.typed_result_admission import typed_result_admission_issues


def _identity() -> dict[str, object]:
    return {
        "schema_version": "task-integration-acceptance-result.v1",
        "workflow_run_id": "run-1",
        "task_id": "TASK-1",
        "task_map_generation": "map-g1",
        "contract_revision": "contract-r1",
        "risk_class": "high",
        "integration_admission_profile": "risk_review",
        "operation_id": "op-review-1",
        "operation_generation": 1,
        "attempt_id": "attempt-1",
        "exact_task_target_commit": "abc123",
        "target_commit": "abc123",
        "verification_result_ref": "artifacts/verify/result.json",
        "verification_result_digest": "a" * 64,
        "contract_snapshot_ref": "artifacts/contracts/task.json",
        "contract_snapshot_digest": "b" * 64,
        "target_snapshot_ref": "artifacts/targets/task.json",
        "target_snapshot_digest": "c" * 64,
        "execution_profile_id": "direct-v1",
        "execution_profile_digest": "d" * 64,
        "risk_review_timeout_seconds": 180,
        "risk_review_max_turns": 1,
        "risk_review_budget_usd": 1.0,
    }


def test_risk_acceptance_result_binds_kernel_read_ledger() -> None:
    result = normalize_integration_acceptance_result({
        **_identity(),
        "execution_status": "completed",
        "verdict": "admit",
        "evidence_refs": ["artifacts/verify/result.json"],
    })

    bound = bind_required_read_ledger(result, {
        "ref": "artifacts/attempts/a/read-ledger.json",
        "sha256": "e" * 64,
    })

    assert bound["required_read_ledger_digest"] == "e" * 64
    profile = ControlResultAdapterRegistry().profile(
        "integration-acceptance-review",
        "1",
    )
    assert profile.schema_version == "task-integration-acceptance-result.v1"


@pytest.mark.parametrize(
    ("verdict", "extra"),
    [
        ("revise", {}),
        ("replan", {}),
        ("block", {}),
    ],
)
def test_risk_acceptance_verdicts_require_typed_route_body(
    verdict: str,
    extra: dict[str, object],
) -> None:
    with pytest.raises(IntegrationAcceptanceResultError):
        normalize_integration_acceptance_result({
            **_identity(),
            "execution_status": "completed",
            "verdict": verdict,
            "evidence_refs": ["evidence"],
            **extra,
        })


def test_failed_risk_reviewer_must_abstain() -> None:
    result = normalize_integration_acceptance_result({
        **_identity(),
        "execution_status": "failed",
        "verdict": "abstained",
    })

    assert result["verdict"] == "abstained"


def test_typed_admission_binds_current_contract_target_and_verify_result(
    tmp_path,
) -> None:
    task = Task(id="TASK-1", title="Risk task")
    task.contract.risk_class = "high"
    task.contract.integration_admission_profile = "risk_review"
    snapshot = build_task_contract_snapshot(
        task,
        workflow_run_id="run-1",
        task_map_generation_id="map-g1",
        base_commit="base123",
        task_ref="refs/zf/tasks/TASK-1",
    )
    contract_ref = write_task_contract_snapshot(tmp_path, snapshot)
    target = build_target_snapshot(
        contract_ref,
        target_commit="abc123",
        contract_snapshot=snapshot,
    )
    target_ref = write_target_snapshot(tmp_path, target)
    criterion = snapshot["acceptance_criteria"][0]
    verification = {
        "schema_version": "verification-result.v1",
        "execution_status": "completed",
        "verdict": "passed",
        "workflow_run_id": "run-1",
        "task_id": "TASK-1",
        "contract_revision": snapshot["contract_revision"],
        "task_map_generation": "map-g1",
        "base_commit": "base123",
        "task_ref": "refs/zf/tasks/TASK-1",
        "contract_snapshot_ref": contract_ref["ref"],
        "contract_snapshot_digest": contract_ref["sha256"],
        "target_snapshot_ref": target_ref["ref"],
        "target_snapshot_digest": target_ref["sha256"],
        "target_commit": "abc123",
        "verification_owner": "task_verify",
        "verification_tier": "runtime",
        "summary": "passed",
        "findings": [],
        "evidence_refs": ["evidence:verify"],
        "reused_command_receipt_ids": [],
        "probe_receipts": [{
            "probe_id": "task-check",
            "status": "passed",
            "evidence_refs": ["evidence:verify"],
        }],
        "rework_items": [],
        "requirement_results": [{
            "acceptance_id": criterion["acceptance_id"],
            "status": "passed",
            "verification_owner": criterion["verification_owner"],
            "verification_tier": criterion["verification_tier"],
            "evidence_refs": ["evidence:verify"],
            "findings": [],
            "reproduction_commands": [],
        }],
    }
    verify_ref = write_immutable_json_sidecar(
        tmp_path,
        verification,
        root="call-results/control/verification-result.v1",
        kind="call_control_result",
        schema_version="verification-result.v1",
        created_by="test",
    )
    result = {
        **_identity(),
        "contract_revision": snapshot["contract_revision"],
        "contract_snapshot_ref": contract_ref["ref"],
        "contract_snapshot_digest": contract_ref["sha256"],
        "target_snapshot_ref": target_ref["ref"],
        "target_snapshot_digest": target_ref["sha256"],
        "verification_result_ref": verify_ref["ref"],
        "verification_result_digest": verify_ref["sha256"],
        "required_read_ledger_ref": "artifacts/reads/ledger.json",
        "required_read_ledger_digest": "e" * 64,
        "execution_status": "completed",
        "verdict": "admit",
        "evidence_refs": [verify_ref["ref"]],
    }

    assert typed_result_admission_issues(
        tmp_path,
        schema_version="task-integration-acceptance-result.v1",
        result=result,
        semantic_submit=True,
    ) == []
