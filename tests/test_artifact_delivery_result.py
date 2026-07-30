from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from zf.core.config.loader import load_config
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.workflow.generic_workflow import build_registered_template_spec
from zf.runtime.artifact_delivery_result import (
    ArtifactDeliveryResultError,
    artifact_delivery_admission_issues,
    artifact_delivery_dossier_projection,
    artifact_delivery_success_payload,
    normalize_artifact_delivery_result,
)
from zf.runtime.artifact_query.service import ArtifactQueryService
from zf.runtime.call_result_admission import CallResultAdmissionService
from zf.runtime.call_result_envelope import (
    normalize_call_result_envelope,
    write_immutable_json_sidecar,
)
from zf.runtime.goal_claim_set import pin_goal_claim_set_from_requirement
from zf.runtime.goal_completion_receipt import build_goal_completion_receipt
from zf.runtime.goal_dossier import build_goal_dossier
from zf.runtime.generic_workflow_fanout import (
    artifact_delivery_verified_event,
)
from zf.runtime.run_contract import (
    bind_run_contract_workflow_artifacts,
    build_run_contract,
    stable_json_sha256,
    write_run_contract_snapshot,
)
from zf.runtime.run_manager import (
    run_goal_completion_claim_event,
    run_goal_completion_gate_event,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import WorkflowOperationService


RUN_ID = "run-artifact-delivery"
GOAL_ID = "GOAL-ARTIFACT-DELIVERY"


def _fixture(tmp_path: Path) -> dict:
    project_root = tmp_path / "project"
    state_dir = project_root / ".zf"
    state_dir.mkdir(parents=True)
    roles = [
        {
            "name": role,
            "instance_id": role,
            "backend": "mock",
            "role_kind": "reader",
        }
        for role in (
            "scoper",
            "collector-a",
            "collector-b",
            "synthesizer",
            "verifier",
        )
    ]
    workflow_spec = build_registered_template_spec(
        "evidence-synthesis-v1",
        {
            "scoper_role": "scoper",
            "collector_roles": ["collector-a", "collector-b"],
            "synthesizer_role": "synthesizer",
            "verifier_role": "verifier",
            "artifact_name": "report",
            "artifact_kind": "report/markdown",
        },
    )
    config_path = project_root / "zf.yaml"
    config_path.write_text(
        yaml.safe_dump_all(
            [
                {
                    "apiVersion": "zaofu.dev/v1",
                    "kind": "Workflow",
                    "metadata": {"name": "artifact-delivery-test"},
                    "spec": workflow_spec,
                },
                {
                    "apiVersion": "zaofu.dev/v1",
                    "kind": "ZfConfig",
                    "metadata": {"name": "artifact-delivery-test"},
                    "spec": {
                        "version": "1.0",
                        "project": {
                            "name": "artifact-delivery-test",
                            "state_dir": ".zf",
                        },
                        "roles": roles,
                    },
                },
            ],
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    config = load_config(config_path)
    proposal_digest = stable_json_sha256({
        "request_id": GOAL_ID,
        "revision": 1,
        "template": "evidence-synthesis-v1",
    })
    run_contract = bind_run_contract_workflow_artifacts(
        build_run_contract(
            config,
            config_path=config_path,
            project_root=project_root,
            state_dir=state_dir,
        ),
        proposal_digest=proposal_digest,
    )
    run_contract_ref = write_run_contract_snapshot(
        state_dir,
        run_contract,
        source_event_id="evt-run-start",
    )

    requirement_path = project_root / "requirements" / "research.json"
    requirement_path.parent.mkdir(parents=True)
    requirement_path.write_text(
        json.dumps({
            "schema_version": "requirement-spec.v1",
            "request_id": GOAL_ID,
            "revision": 1,
            "objective": "Deliver one verified research report.",
            "acceptance": [{
                "id": "GOAL-REPORT",
                "text": "The report answers the confirmed research question.",
            }],
            "constraints": [],
            "open_questions": [],
            "confirmed": True,
        }),
        encoding="utf-8",
    )
    claim_set, claim_ref = pin_goal_claim_set_from_requirement(
        state_dir=state_dir,
        project_root=project_root,
        requirement_ref=str(requirement_path),
        requirement_digest=hashlib.sha256(
            requirement_path.read_bytes()
        ).hexdigest(),
        workflow_run_id=RUN_ID,
        goal_id=GOAL_ID,
        workflow_generation=proposal_digest,
        source_event_id="evt-run-start",
    )
    report_ref = write_immutable_json_sidecar(
        state_dir,
        {
            "schema_version": "research-report.v1",
            "title": "Verified research report",
            "findings": ["The evidence supports the requested conclusion."],
        },
        root="workflow/artifacts/reports",
        kind="report/markdown",
        schema_version="research-report.v1",
        created_by="synthesizer",
        source_event_id="evt-synthesize",
    )
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    writer.append(ZfEvent(
        id="evt-run-start",
        type="run.goal.started",
        correlation_id=RUN_ID,
        payload={
            "run_id": RUN_ID,
            "workflow_run_id": RUN_ID,
            "goal_id": GOAL_ID,
            "objective": "Deliver one verified research report.",
            "completion_profile": "artifact_delivery",
            "workflow_generation": proposal_digest,
        },
    ))
    writer.append(ZfEvent(
        id="evt-invoke",
        type="workflow.invoke.requested",
        correlation_id=RUN_ID,
        payload={
            "run_id": RUN_ID,
            "workflow_run_id": RUN_ID,
            "goal_id": GOAL_ID,
            "flow_kind": "workflow",
            "workflow_generation": proposal_digest,
            "workflow_proposal_digest": proposal_digest,
            "request_revision": 1,
        },
    ))
    writer.append(ZfEvent(
        id="evt-claim-pin",
        type="goal.claim_set.pinned",
        correlation_id=RUN_ID,
        payload={
            "workflow_run_id": RUN_ID,
            "goal_id": GOAL_ID,
            "task_map_generation": proposal_digest,
            "goal_claim_set_ref": claim_ref["ref"],
            "goal_claim_set_digest": claim_ref["sha256"],
            "goal_claim_set_content_digest": claim_set["claim_set_digest"],
        },
    ))
    upstream_ref = _append_admitted_stage_result(
        state_dir,
        writer,
        workflow_generation=proposal_digest,
    )
    result = {
        "schema_version": "artifact-delivery-result.v1",
        "workflow_run_id": RUN_ID,
        "goal_id": GOAL_ID,
        "workflow_generation": proposal_digest,
        "request_revision": 1,
        "generic_workflow_contract_digest": run_contract["workflow"][
            "generic_workflow_contract_digest"
        ],
        "run_contract_ref": run_contract_ref["ref"],
        "run_contract_digest": run_contract["contract_digest"],
        "completion_profile": "artifact_delivery",
        "goal_claim_set_ref": claim_ref["ref"],
        "goal_claim_set_digest": claim_ref["sha256"],
        "verifier_stage_id": "verify",
        "verifier_role": "verifier",
        "artifacts": [{
            **report_ref,
            "name": "report",
            "kind": "report/markdown",
            "source_ref": "synthesize.report",
            "producer_stage_id": "synthesize",
        }],
        "goal_coverage": [
            {
                "goal_claim_id": str(claim["goal_claim_id"]),
                "status": "closed",
                "supporting_artifact_refs": [report_ref["ref"]],
            }
            for claim in claim_set["claims"]
        ],
        "input_result_refs": [upstream_ref],
        "verification_evidence_refs": [report_ref["ref"]],
        "open_gap_refs": [],
        "verdict": "passed",
        "recommended_action": "complete",
        "summary": "All mandatory Goal claims are covered and verified.",
    }
    operations = WorkflowOperationService(
        state_dir=state_dir,
        event_log=log,
        event_writer=writer,
    )
    admission = CallResultAdmissionService(
        state_dir=state_dir,
        event_log=log,
        event_writer=writer,
        operation_service=operations,
    )
    return {
        "project_root": project_root,
        "state_dir": state_dir,
        "config": config,
        "run_contract": run_contract,
        "claim_ref": claim_ref,
        "report_ref": report_ref,
        "result": result,
        "writer": writer,
        "operations": operations,
        "admission": admission,
    }


def _append_admitted_stage_result(
    state_dir: Path,
    writer: EventWriter,
    *,
    workflow_generation: str,
) -> str:
    control_ref = write_immutable_json_sidecar(
        state_dir,
        {
            "schema_version": "stage-result.v1",
            "status": "passed",
        },
        root="call-results/control/stage-result.v1",
        kind="call_control_result",
        schema_version="stage-result.v1",
        created_by="test",
    )
    envelope = normalize_call_result_envelope(
        source_payload={
            "workflow_run_id": RUN_ID,
            "workflow_generation": workflow_generation,
            "attempt_id": "attempt-synthesize",
            "stage_id": "synthesize",
            "role_instance": "synthesizer",
        },
        control_result={
            "schema_version": "stage-result.v1",
            "ref": control_ref["ref"],
            "sha256": control_ref["sha256"],
        },
        workflow_run_id=RUN_ID,
        operation_id="operation-synthesize",
        request_hash="request-synthesize",
        source_event_id="evt-synthesize",
        source_event_type="synthesize.completed",
        actor="synthesizer",
        correlation_id=RUN_ID,
    )
    envelope_ref = write_immutable_json_sidecar(
        state_dir,
        envelope,
        root="call-results/envelopes",
        kind="call_result_envelope",
        schema_version="call-result-envelope.v1",
        created_by="test",
        source_event_id="evt-synthesize",
    )
    writer.append(ZfEvent(
        id="evt-synthesize-admitted",
        type="workflow.call.result.admitted",
        correlation_id=RUN_ID,
        payload={
            "workflow_run_id": RUN_ID,
            "operation_id": "operation-synthesize",
            "request_hash": "request-synthesize",
            "envelope_ref": envelope_ref,
            "control_result_ref": control_ref,
        },
    ))
    return str(envelope_ref["ref"])


def _admit(fixture: dict, result: dict):
    operation = fixture["operations"].ensure_operation(
        workflow_run_id=RUN_ID,
        operation_id="operation-verify",
        operation_type="fanout_reader_child",
        request={
            "workflow_generation": result["workflow_generation"],
            "request_revision": result["request_revision"],
            "generic_workflow_contract_digest": result[
                "generic_workflow_contract_digest"
            ],
            "run_contract_ref": result["run_contract_ref"],
            "run_contract_digest": result["run_contract_digest"],
        },
    )
    return fixture["admission"].report_legacy_result(
        ZfEvent(
            id="evt-verify-result",
            type="verify.child.completed",
            actor="verifier",
            correlation_id=RUN_ID,
            payload={
                "workflow_run_id": RUN_ID,
                "attempt_id": "attempt-verify",
                "goal_id": GOAL_ID,
                "workflow_generation": result["workflow_generation"],
                "request_revision": result["request_revision"],
                "generic_workflow_contract_digest": result[
                    "generic_workflow_contract_digest"
                ],
                "run_contract_ref": result["run_contract_ref"],
                "run_contract_digest": result["run_contract_digest"],
                "completion_profile": "artifact_delivery",
                "stage_id": "verify",
                "role_instance": "verifier",
                "artifact_delivery_result": result,
            },
        ),
        mode="blocking",
        operation={
            "workflow_run_id": RUN_ID,
            "operation_id": "operation-verify",
            "request_hash": operation.request_hash,
        },
    )


def test_artifact_delivery_admission_closes_goal_and_projects_evidence(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    result = normalize_artifact_delivery_result(fixture["result"])

    assert artifact_delivery_admission_issues(
        fixture["state_dir"],
        result,
        events=fixture["writer"].event_log.read_all(),
    ) == []
    admitted = _admit(fixture, result)
    assert admitted.admitted is True

    verified = fixture["writer"].append(ZfEvent(
        id="evt-artifact-verified",
        type="artifact.delivery.verified",
        actor="zf-cli",
        correlation_id=RUN_ID,
        payload={
            "workflow_run_id": RUN_ID,
            "goal_id": GOAL_ID,
            "stage_id": "verify",
            "role_instance": "verifier",
            "admitted_call_result_ref": admitted.envelope_ref,
            "control_result_ref": admitted.control_result_ref,
            "artifact_delivery_result": result,
        },
    ))
    events = fixture["writer"].event_log.read_all()
    claim = run_goal_completion_claim_event(events, cause=verified)
    assert claim is not None
    claim.payload.pop("flow_kind", None)
    fixture["writer"].append(claim)
    terminal = run_goal_completion_gate_event(
        fixture["writer"].event_log.read_all(),
        claim=claim,
        run_contract=fixture["run_contract"],
    )
    assert terminal is not None
    assert terminal.type == "run.goal.completed"
    assert terminal.payload["flow_kind"] == "workflow"
    fixture["writer"].append(terminal)
    assert run_goal_completion_gate_event(
        fixture["writer"].event_log.read_all(),
        claim=claim,
        run_contract=fixture["run_contract"],
    ) is None

    final_events = fixture["writer"].event_log.read_all()
    receipt = build_goal_completion_receipt(
        final_events,
        run_id=RUN_ID,
        generated_at="2026-07-26T06:00:00+00:00",
    )
    dossier = build_goal_dossier(
        fixture["state_dir"],
        RUN_ID,
        events=final_events,
    )
    assert receipt["artifact_delivery"]["required_artifacts"][0][
        "source_ref"
    ] == "synthesize.report"
    assert receipt["candidate"]["event_id"] == ""
    assert dossier["artifact_delivery"]["status"] == "ready"
    assert dossier["delivery_readiness"]["status"] == "ready"
    assert dossier["gaps"] == []
    replay_projection = artifact_delivery_dossier_projection([
        *final_events,
        ZfEvent(
            type="verify.child.completed",
            correlation_id=RUN_ID,
            payload={"artifact_delivery_result": result},
        ),
    ])
    assert replay_projection["status"] == "ready"
    assert replay_projection["source_event_id"] == verified.id

    query = ArtifactQueryService(
        state_dir=fixture["state_dir"],
        project_root=fixture["project_root"],
        config=fixture["config"],
    )
    result_rows = query.catalog_list(
        context=query.context(mode="canonical"),
        semantic_kind="artifact_delivery_result",
        run_id=RUN_ID,
    )
    report_rows = query.catalog_list(
        context=query.context(mode="canonical"),
        kind="report/markdown",
        run_id=RUN_ID,
    )
    assert result_rows["items"]
    assert report_rows["items"][0]["latest_locator"]["ref"] == (
        fixture["report_ref"]["ref"]
    )


def test_admitted_artifact_result_is_superseded_after_generation_moves(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    result = normalize_artifact_delivery_result(fixture["result"])
    first = _admit(fixture, result)
    assert first.admitted is True
    fixture["writer"].append(ZfEvent(
        id="evt-invoke-generation-2",
        type="workflow.invoke.requested",
        correlation_id=RUN_ID,
        payload={
            "run_id": RUN_ID,
            "workflow_run_id": RUN_ID,
            "goal_id": GOAL_ID,
            "flow_kind": "workflow",
            "workflow_generation": "b" * 64,
            "workflow_proposal_digest": "b" * 64,
            "request_revision": 2,
        },
    ))

    late = _admit(fixture, result)

    assert late.status == "superseded"
    assert "stale_workflow_generation" in {
        issue["code"] for issue in late.issues
    }
    assert sum(
        event.type == "workflow.call.result.invalid"
        for event in fixture["writer"].event_log.read_all()
    ) == 1
    operation = fixture["operations"].ensure_operation(
        workflow_run_id=RUN_ID,
        operation_id="operation-verify",
        operation_type="fanout_reader_child",
        request={
            "workflow_generation": result["workflow_generation"],
            "request_revision": result["request_revision"],
            "generic_workflow_contract_digest": result[
                "generic_workflow_contract_digest"
            ],
            "run_contract_ref": result["run_contract_ref"],
            "run_contract_digest": result["run_contract_digest"],
        },
    )
    assert operation.status == "superseded"


def test_current_claim_content_digest_requests_protocol_repair(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    result = copy.deepcopy(fixture["result"])
    claim_set = hydrate_sidecar_ref(
        fixture["state_dir"],
        fixture["claim_ref"],
    ).payload
    result["goal_claim_set_digest"] = claim_set["claim_set_digest"]

    outcome = _admit(fixture, result)

    assert outcome.status == "repair_pending"
    assert outcome.repair_requested is True
    codes = {issue["code"] for issue in outcome.issues}
    assert "claim_set_digest_kind_mismatch" in codes
    assert "stale_claim_set_identity" not in codes
    event_types = [
        event.type for event in fixture["writer"].event_log.read_all()
    ]
    assert "workflow.call.result.repair.requested" in event_types
    assert "workflow.operation.superseded" not in event_types


def test_artifact_delivery_template_accepts_only_admitted_envelope_refs(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    result = fixture["result"]
    child_payload = {
        **{
            key: result[key]
            for key in (
                "workflow_run_id",
                "goal_id",
                "workflow_generation",
                "request_revision",
                "generic_workflow_contract_digest",
                "completion_profile",
                "run_contract_ref",
                "run_contract_digest",
                "goal_claim_set_ref",
                "goal_claim_set_digest",
                "input_result_refs",
            )
        },
        "workflow_intent": "research",
        "workflow_template": "evidence-synthesis-v1",
        "required_delivery_artifacts": [{
            "name": "report",
            "kind": "report/markdown",
            "source_ref": "synthesize.report",
        }],
    }

    template = artifact_delivery_success_payload(
        child_payload,
        verifier_stage_id="verify",
        verifier_role="verifier",
    )
    assert template["artifact_delivery_result"]["input_result_refs"] == (
        result["input_result_refs"]
    )

    child_payload["input_result_refs"] = [
        "fanouts/fanout-synthesize/children/synthesize/result.json"
    ]
    with pytest.raises(
        ArtifactDeliveryResultError,
        match="must be admitted call-result envelopes",
    ):
        artifact_delivery_success_payload(
            child_payload,
            verifier_stage_id="verify",
            verifier_role="verifier",
        )


def test_dossier_rejects_raw_or_unbound_artifact_delivery(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    result = normalize_artifact_delivery_result(fixture["result"])
    raw = ZfEvent(
        type="verify.child.completed",
        correlation_id=RUN_ID,
        payload={"artifact_delivery_result": result},
    )
    unbound_verified = ZfEvent(
        type="artifact.delivery.verified",
        correlation_id=RUN_ID,
        payload={"artifact_delivery_result": result},
    )

    projection = artifact_delivery_dossier_projection([
        raw,
        unbound_verified,
    ])

    assert projection == {
        "schema_version": "goal-dossier-artifact-delivery.v1",
        "status": "incomplete",
        "required_artifacts": [],
    }
    assert artifact_delivery_verified_event(
        base_payload={},
        report_payload={"artifact_delivery_result": result},
        completed_event=raw,
        correlation_id=RUN_ID,
    ) is None


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("missing_artifact", "schema_invalid"),
        ("stale_generation", "stale_workflow_generation"),
        ("self_verify", "schema_invalid"),
        ("digest_mismatch", "hash_mismatch"),
        ("unadmitted_input", "result_not_admitted"),
    ],
)
def test_artifact_delivery_admission_fails_closed(
    tmp_path: Path,
    mutation: str,
    expected_code: str,
) -> None:
    fixture = _fixture(tmp_path)
    result = copy.deepcopy(fixture["result"])
    if mutation == "missing_artifact":
        result["artifacts"] = []
    elif mutation == "stale_generation":
        result["workflow_generation"] = "b" * 64
    elif mutation == "self_verify":
        result["verifier_stage_id"] = "synthesize"
        result["verifier_role"] = "synthesizer"
    elif mutation == "digest_mismatch":
        result["artifacts"][0]["sha256"] = "f" * 64
    else:
        result["input_result_refs"] = ["call-results/envelopes/not-admitted.json"]

    outcome = _admit(fixture, result)

    assert outcome.admitted is False
    if mutation == "stale_generation":
        assert outcome.status == "superseded"
        assert outcome.repair_requested is False
    else:
        assert outcome.repair_requested is True
    assert expected_code in {issue["code"] for issue in outcome.issues}


def test_artifact_delivery_result_requires_nonempty_typed_artifacts(
    tmp_path: Path,
) -> None:
    result = _fixture(tmp_path)["result"]
    result["artifacts"] = []

    with pytest.raises(
        ArtifactDeliveryResultError,
        match="artifacts must be a non-empty list",
    ):
        normalize_artifact_delivery_result(result)
