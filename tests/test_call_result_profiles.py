from __future__ import annotations

from pathlib import Path

import pytest

from zf.runtime.call_result_adapters import (
    ControlResultAdapterError,
    ControlResultAdapterRegistry,
    call_result_profile_identity,
    hydrate_profiled_control_result_event,
)
from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar


def test_profile_identity_distinguishes_verify_surfaces() -> None:
    assert call_result_profile_identity(
        operation_type="fanout_reader_child",
        stage_id="task-verify",
        payload={},
    ) == ("task-verify", "1")
    assert call_result_profile_identity(
        operation_type="fanout_reader_child",
        stage_id="candidate-verify",
        payload={},
    ) == ("candidate-verify", "1")
    assert call_result_profile_identity(
        operation_type="fanout_reader_child",
        stage_id="prd-lanes-verify",
        payload={
            "task_id": "TASK-1",
            "candidate_head_commit": "candidate-head",
        },
    ) == ("candidate-verify", "1")
    assert call_result_profile_identity(
        operation_type="fanout_reader_child",
        stage_id="issue-post-verify-discovery",
        payload={
            "candidate_head_commit": "candidate-head",
            "task_map_generation": "generation-1",
            "canonical_success_event": "flow.discovery.child.completed",
        },
    ) == ("workflow-read", "1")
    assert call_result_profile_identity(
        operation_type="fanout_reader_child",
        stage_id="flow-module-parity-scan",
        payload={
            "candidate_head_commit": "candidate-head",
            "canonical_success_event": "module.parity.child.completed",
        },
    ) == ("workflow-read", "1")
    assert call_result_profile_identity(
        operation_type="fanout_reader_child",
        stage_id="flow-verify-bridge",
        payload={
            "candidate_head_commit": "candidate-head",
            "canonical_success_event": "verify.bridge.child.completed",
        },
    ) == ("workflow-read", "1")
    assert call_result_profile_identity(
        operation_type="fanout_reader_child",
        stage_id="global-rescan",
        payload={},
    ) == ("global-rescan", "1")
    assert call_result_profile_identity(
        operation_type="fanout_synth",
        stage_id="plan",
        payload={},
    ) == ("plan-synth", "1")
    assert call_result_profile_identity(
        operation_type="fanout_reader_child",
        stage_id="flow-scan",
        payload={"canonical_success_event": "workflow.child.completed"},
    ) == ("workflow-read", "1")
    assert call_result_profile_identity(
        operation_type="fanout_reader_child",
        stage_id="issue-triage",
        payload={"canonical_success_event": "issue.triage.child.completed"},
    ) == ("workflow-read", "1")


def test_workflow_read_profile_preserves_complete_report(tmp_path: Path) -> None:
    event, adapted = ControlResultAdapterRegistry().adapt_semantic_result(
        tmp_path,
        profile_id="workflow-read",
        revision="1",
        event_type="workflow.child.completed",
        semantic_result={
            "status": "passed",
            "summary": "scan complete",
            "recommendation": "approve",
            "coverage_matrix": [{"scope": "simulation", "status": "covered"}],
            "findings": [],
        },
        identity={"workflow_run_id": "run-read"},
        source_event_id="evt-read",
        actor="scan-runtime",
        task_id="",
        correlation_id="run-read",
    )

    assert event.payload["report"]["coverage_matrix"][0]["scope"] == "simulation"
    assert adapted.payload["verdict"] == "passed"
    assert adapted.payload["summary"] == "scan complete"


def test_workflow_read_profile_accepts_registered_product_child_event(
    tmp_path: Path,
) -> None:
    event, adapted = ControlResultAdapterRegistry().adapt_semantic_result(
        tmp_path,
        profile_id="workflow-read",
        revision="1",
        event_type="issue.triage.child.completed",
        semantic_result={
            "status": "passed",
            "summary": "triage complete",
            "recommendation": "approve",
            "findings": [],
        },
        identity={"workflow_run_id": "run-issue"},
        source_event_id="evt-issue",
        actor="issue-triage",
        task_id="ISSUE-ANCHOR",
        correlation_id="run-issue",
    )

    assert event.type == "issue.triage.child.completed"
    assert adapted.adapter_id == "workflow-read-result-v1"
    assert adapted.payload["verdict"] == "passed"


def test_artifact_delivery_profile_overrides_agent_copied_identity(
    tmp_path: Path,
) -> None:
    claim_ref = "artifacts/goal-closure/claim-sets/current.json"
    report_ref = "artifacts/workflow/reports/current.json"
    result_ref = "artifacts/call-results/envelopes/synthesize.json"
    _, adapted = ControlResultAdapterRegistry().adapt_semantic_result(
        tmp_path,
        profile_id="artifact-delivery",
        revision="1",
        event_type="workflow.child.completed",
        semantic_result={
            "schema_version": "artifact-delivery-result.v1",
            "workflow_run_id": "run-stale",
            "goal_id": "goal-stale",
            "workflow_generation": "d" * 64,
            "request_revision": 1,
            "generic_workflow_contract_digest": "e" * 64,
            "run_contract_ref": "artifacts/run-contracts/stale.json",
            "run_contract_digest": "f" * 64,
            "completion_profile": "artifact_delivery",
            "goal_claim_set_ref": "artifacts/goal-closure/stale.json",
            "goal_claim_set_digest": "0" * 64,
            "verifier_stage_id": "verify",
            "verifier_role": "workflow-verifier",
            "verdict": "passed",
            "artifacts": [{
                "name": "report",
                "kind": "report/markdown",
                "source_ref": "synthesize.report",
                "producer_stage_id": "synthesize",
                "ref": report_ref,
                "sha256": "1" * 64,
            }],
            "goal_coverage": [{
                "goal_claim_id": "AC-1",
                "status": "closed",
                "supporting_artifact_refs": [report_ref],
            }],
            "input_result_refs": [result_ref],
            "verification_evidence_refs": [report_ref],
            "open_gap_refs": [],
            "recommended_action": "complete",
            "summary": "Verified report closes the Goal.",
        },
        identity={
            "workflow_run_id": "run-current",
            "goal_id": "goal-current",
            "workflow_generation": "a" * 64,
            "request_revision": 2,
            "generic_workflow_contract_digest": "b" * 64,
            "run_contract_ref": "artifacts/run-contracts/current.json",
            "run_contract_digest": "c" * 64,
            "completion_profile": "artifact_delivery",
            "goal_claim_set_ref": claim_ref,
            "goal_claim_set_digest": "2" * 64,
        },
        source_event_id="evt-artifact-delivery",
        actor="workflow-verifier",
        task_id="",
        correlation_id="run-current",
    )

    assert adapted.payload["workflow_run_id"] == "run-current"
    assert adapted.payload["goal_id"] == "goal-current"
    assert adapted.payload["workflow_generation"] == "a" * 64
    assert adapted.payload["request_revision"] == 2
    assert adapted.payload["goal_claim_set_ref"] == claim_ref
    assert adapted.payload["goal_claim_set_digest"] == "2" * 64


def test_workflow_read_legacy_event_requires_pinned_product_event(
    tmp_path: Path,
) -> None:
    event = ZfEvent(
        type="issue.triage.child.completed",
        payload={
            "output_profile_id": "workflow-read",
            "canonical_success_event": "issue.triage.child.completed",
            "canonical_failure_event": "issue.triage.child.failed",
            "report": {
                "status": "passed",
                "summary": "triage complete",
                "recommendation": "approve",
                "findings": [],
            },
        },
    )

    adapted = ControlResultAdapterRegistry().adapt(tmp_path, event)

    assert adapted.adapter_id == "workflow-read-result-v1"
    assert adapted.payload["schema_version"] == "workflow-read-result.v1"
    with pytest.raises(ControlResultAdapterError):
        ControlResultAdapterRegistry().adapt(
            tmp_path,
            ZfEvent(
                type="issue.triage.child.unexpected",
                payload=event.payload,
            ),
        )


def test_writer_profile_wins_over_inherited_goal_closure_fields() -> None:
    assert call_result_profile_identity(
        operation_type="fanout_writer_child",
        stage_id="prd-lanes-impl",
        payload={
            "closure_identity": "closure-current",
            "goal_claim_set_ref": "artifacts/goal-claims.json",
        },
    ) == ("implementation", "1")
    assert call_result_profile_identity(
        operation_type="fanout_reader_child",
        stage_id="task-verify",
        payload={"goal_claim_set_ref": "artifacts/goal-claims.json"},
    ) == ("task-verify", "1")
    assert call_result_profile_identity(
        operation_type="fanout_reader_child",
        stage_id="goal-closure",
        payload={"goal_claim_set_ref": "artifacts/goal-claims.json"},
    ) == ("thin-judge-goal-closure", "1")


def test_profile_revision_and_event_mapping_fail_closed(tmp_path: Path) -> None:
    registry = ControlResultAdapterRegistry()
    with pytest.raises(ControlResultAdapterError, match="unknown call-result profile"):
        registry.profile("task-verify", "99")
    with pytest.raises(ControlResultAdapterError, match="not allowed"):
        registry.adapt_semantic_result(
            tmp_path,
            profile_id="implementation",
            revision="1",
            event_type="judge.child.completed",
            semantic_result={"task_id": "T1", "target_commit": "abc"},
            identity={"workflow_run_id": "run-1"},
            source_event_id="evt-source",
            actor="dev-1",
            task_id="T1",
            correlation_id="run-1",
        )


def test_ref_backed_event_hydrates_once_and_hash_mismatch_fails(tmp_path: Path) -> None:
    descriptor = write_immutable_json_sidecar(
        tmp_path,
        {
            "schema_version": "implementation-result.v1",
            "task_id": "T1",
            "target_commit": "abc123",
        },
        root="call-results/control/implementation-result.v1",
        kind="call_control_result",
        schema_version="implementation-result.v1",
        created_by="test",
        source_event_id="evt-source",
    )
    event = ZfEvent(
        type="dev.build.done",
        payload={
            "semantic_result_profile": {
                "profile_id": "implementation",
                "revision": "1",
            },
            "control_result_ref": descriptor,
        },
    )
    hydrated = hydrate_profiled_control_result_event(tmp_path, event)
    assert hydrated.payload["implementation_result"]["target_commit"] == "abc123"
    broken = ZfEvent(
        type=event.type,
        payload={
            **event.payload,
            "control_result_ref": {**descriptor, "sha256": "0" * 64},
        },
    )
    with pytest.raises(ControlResultAdapterError, match="hydration failed"):
        hydrate_profiled_control_result_event(tmp_path, broken)
