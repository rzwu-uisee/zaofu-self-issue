from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    WorkflowConfig,
    WorkflowOrchestrationConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.artifact_read_capability import (
    provision_role_artifact_read_credential,
)
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.call_result_adapters import hydrate_profiled_control_result_event
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.orchestrator_agent_briefing import (
    build_orchestrator_agent_operation_briefing,
)
from zf.runtime.orchestrator_agent_operations import (
    activate_orchestrator_agent_operation,
)
from zf.runtime.owner_delivery_narrative import (
    apply_owner_delivery_narrative,
    prepare_owner_delivery_narrative_operation,
)
from zf.runtime.result_submit import (
    SemanticResultSubmitService,
    provision_role_submit_credential,
)


RUN_ID = "run-owner-narrative"
DOSSIER_FINGERPRINT = "a" * 64
RECEIPT_FINGERPRINT = "b" * 64


def _config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="owner-narrative", workspace="."),
        workflow=WorkflowConfig(orchestration=WorkflowOrchestrationConfig(
            mode="semantic_control",
            checkpoints=["owner_delivery"],
            checkpoint_policies={"owner_delivery": "shadow"},
        )),
        roles=[RoleConfig(
            name="orchestrator",
            instance_id="orchestrator",
            role_kind="reader",
            backend="mock",
        )],
    )


def _runtime(tmp_path: Path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    terminal = writer.append(ZfEvent(
        id="evt-owner-terminal",
        type="run.goal.completed",
        actor="zf-cli",
        correlation_id=RUN_ID,
        payload={"run_id": RUN_ID, "goal_id": "GOAL-1"},
    ))
    result_ref = write_immutable_json_sidecar(
        state_dir,
        {"schema_version": "implementation-result.v1", "status": "completed"},
        root="fixtures/owner-results",
        kind="implementation_result",
        schema_version="implementation-result.v1",
        created_by="test",
    )
    evidence_ref = write_immutable_json_sidecar(
        state_dir,
        {"schema_version": "evidence.v1", "status": "passed"},
        root="fixtures/owner-evidence",
        kind="evidence",
        schema_version="evidence.v1",
        created_by="test",
    )
    other_result_ref = write_immutable_json_sidecar(
        state_dir,
        {
            "schema_version": "implementation-result.v1",
            "status": "completed",
            "task_id": "TASK-2",
        },
        root="fixtures/owner-results",
        kind="implementation_result",
        schema_version="implementation-result.v1",
        created_by="test",
    )
    other_evidence_ref = write_immutable_json_sidecar(
        state_dir,
        {
            "schema_version": "evidence.v1",
            "status": "passed",
            "task_id": "TASK-2",
        },
        root="fixtures/owner-evidence",
        kind="evidence",
        schema_version="evidence.v1",
        created_by="test",
    )
    dossier = {
        "schema_version": "goal-dossier.v1",
        "run_id": RUN_ID,
        "goal_id": "GOAL-1",
        "source_fingerprint": DOSSIER_FINGERPRINT,
        "terminal": {"event_id": terminal.id, "status": "completed"},
        "claim_to_evidence": {
            "claims": [
                {"claim_id": "CLAIM-1", "status": "closed"},
                {"claim_id": "CLAIM-2", "status": "closed"},
            ],
            "rows": [
                {
                    "goal_claim_id": "CLAIM-1",
                    "task_ids": ["TASK-1"],
                    "result_refs": [result_ref["ref"]],
                    "evidence_refs": [evidence_ref["ref"]],
                },
                {
                    "goal_claim_id": "CLAIM-2",
                    "task_ids": ["TASK-2"],
                    "result_refs": [other_result_ref["ref"]],
                    "evidence_refs": [other_evidence_ref["ref"]],
                },
            ],
        },
        "task_contracts": [{"task_id": "TASK-1"}, {"task_id": "TASK-2"}],
        "gaps": [{"gap_id": "GAP-RESIDUAL", "status": "accepted"}],
        "results": [result_ref, other_result_ref],
        "evidence_index": [evidence_ref, other_evidence_ref],
    }
    projection_dir = state_dir / "projections" / "goals" / RUN_ID
    projection_dir.mkdir(parents=True)
    dossier_path = projection_dir / "goal-dossier.v1.json"
    dossier_path.write_text(
        json.dumps(dossier, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    receipt = {
        "schema_version": "goal-completion-receipt.v1",
        "run_id": RUN_ID,
        "source_fingerprint": RECEIPT_FINGERPRINT,
        "terminal_event_id": terminal.id,
    }
    receipt_path = projection_dir / "goal-completion-receipt.v1.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    config = _config()
    runtime = SimpleNamespace(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        event_log=log,
        event_writer=writer,
    )
    provision_role_submit_credential(state_dir, "orchestrator")
    provision_role_artifact_read_credential(
        state_dir,
        "orchestrator",
        role_name="orchestrator",
        provider="mock",
    )
    prepared = prepare_owner_delivery_narrative_operation(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        event_log=log,
        writer=writer,
        terminal=terminal,
        dossier=dossier,
        dossier_path=dossier_path,
        receipt=receipt,
        receipt_path=receipt_path,
    )
    assert prepared is not None
    writer.append(ZfEvent(
        type="owner.visible_message.requested",
        actor="zf-goal-dossier-delivery",
        correlation_id=RUN_ID,
        payload={
            "schema_version": "goal-dossier-delivery.v1",
            "message_id": "goal-factual-1",
            "terminal_event_id": terminal.id,
            "title": "目标已完成",
            "summary": "deterministic factual fallback",
            "dossier_ref": dossier_path.relative_to(state_dir).as_posix(),
            "completion_receipt_ref": receipt_path.relative_to(
                state_dir
            ).as_posix(),
        },
    ))
    return runtime, prepared, result_ref, evidence_ref


def _submit(runtime, prepared, result_ref, evidence_ref, *, claim_id: str):
    activate_orchestrator_agent_operation(
        runtime,
        prepared,
        dispatch_id="dispatch-owner-narrative",
        causation_id="evt-owner-terminal",
    )
    for source in prepared.context.source_manifest["sources"]:
        read_attempt_artifact(
            runtime.state_dir,
            manifest=prepared.context.source_manifest,
            source_id=source["source_id"],
            artifact_id=source["artifact_id"],
            actor="orchestrator",
            role="orchestrator",
            provider="mock",
        )
    context = prepared.context.input_body["checkpoint_context"]
    narrative = {
        "schema_version": "owner-delivery-narrative.v1",
        "execution_status": "completed",
        "identity": {
            "operation_id": prepared.operation_id,
            "workflow_run_id": RUN_ID,
            "terminal_event_id": context["terminal_event_id"],
            "terminal_event_type": context["terminal_event_type"],
            "dossier_ref": context["dossier_ref"],
            "dossier_source_fingerprint": context[
                "dossier_source_fingerprint"
            ],
            "completion_receipt_ref": context["completion_receipt_ref"],
            "completion_receipt_fingerprint": context[
                "completion_receipt_fingerprint"
            ],
        },
        "status": "completed",
        "executive_summary": "The requested behavior is delivered with cited evidence.",
        "delivered_outcomes": [{
            "claim_ids": [claim_id],
            "task_ids": ["TASK-1"],
            "gap_ids": ["GAP-RESIDUAL"],
            "result_refs": [result_ref],
            "evidence_refs": [evidence_ref],
            "narrative": "TASK-1 closed CLAIM-1 with implementation and evidence.",
        }],
        "decisions_and_tradeoffs": ["Kept the bounded implementation scope."],
        "remaining_risks": ["GAP-RESIDUAL remains explicitly accepted."],
        "recommended_next_actions": ["Monitor the accepted residual gap."],
    }
    token = (
        runtime.state_dir / "private/result-submit/roles/orchestrator.token"
    ).read_text(encoding="utf-8").strip()
    submitted = SemanticResultSubmitService(
        state_dir=runtime.state_dir,
        event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    ).submit(
        operation_id=prepared.operation_id,
        semantic_result=narrative,
        role_instance="orchestrator",
        credential=token,
    )
    event = next(
        item for item in runtime.event_log.read_all()
        if item.id == submitted.canonical_event_id
    )
    return hydrate_profiled_control_result_event(runtime.state_dir, event)


def test_owner_delivery_operation_admits_current_citations_and_updates_composite(
    tmp_path: Path,
) -> None:
    runtime, prepared, result_ref, evidence_ref = _runtime(tmp_path)
    briefing = build_orchestrator_agent_operation_briefing(
        state_dir=runtime.state_dir,
        prepared=prepared,
    )
    assert "owner-delivery-narrative.v1" in briefing
    assert "goal-dossier" in {
        source["source_id"] for source in prepared.context.source_manifest["sources"]
    }

    outcome = apply_owner_delivery_narrative(
        runtime,
        _submit(
            runtime,
            prepared,
            result_ref,
            evidence_ref,
            claim_id="CLAIM-1",
        ),
    )

    assert outcome["status"] == "admitted"
    composite = json.loads((
        runtime.state_dir / outcome["composite_ref"]
    ).read_text(encoding="utf-8"))
    assert composite["narrative_status"] == "admitted"
    assert composite["factual"]["dossier_ref"].endswith("goal-dossier.v1.json")
    assert composite["factual"]["completion_receipt_ref"].endswith(
        "goal-completion-receipt.v1.json"
    )
    assert composite["narrative"]["executive_summary"].startswith(
        "The requested behavior"
    )
    owner_messages = [
        event for event in runtime.event_log.read_all()
        if event.type == "owner.visible_message.requested"
    ]
    assert len(owner_messages) == 2
    assert owner_messages[-1].payload["narrative_status"] == "admitted"
    assert owner_messages[-1].payload["summary"].startswith(
        "The requested behavior"
    )
    assert len([
        event for event in runtime.event_log.read_all()
        if event.type == "run.goal.completed"
    ]) == 1


def test_unknown_citation_degrades_without_changing_terminal_truth(
    tmp_path: Path,
) -> None:
    runtime, prepared, result_ref, evidence_ref = _runtime(tmp_path)
    outcome = apply_owner_delivery_narrative(
        runtime,
        _submit(
            runtime,
            prepared,
            result_ref,
            evidence_ref,
            claim_id="CLAIM-INVENTED",
        ),
    )

    assert outcome["status"] == "degraded"
    assert "unknown claim citation" in outcome["reason"]
    composite = json.loads((
        runtime.state_dir
        / "projections/goals"
        / RUN_ID
        / "owner-delivery-composite.v1.json"
    ).read_text(encoding="utf-8"))
    assert composite["narrative_status"] == "degraded"
    assert not composite["narrative"]
    assert any(
        event.type == "owner.delivery.narrative.rejected"
        for event in runtime.event_log.read_all()
    )
    assert len([
        event for event in runtime.event_log.read_all()
        if event.type == "run.goal.completed"
    ]) == 1


def test_cross_claim_result_citation_is_rejected(tmp_path: Path) -> None:
    runtime, prepared, _result_ref, _evidence_ref = _runtime(tmp_path)
    dossier = json.loads((
        runtime.state_dir / "projections/goals" / RUN_ID / "goal-dossier.v1.json"
    ).read_text(encoding="utf-8"))

    outcome = apply_owner_delivery_narrative(
        runtime,
        _submit(
            runtime,
            prepared,
            dossier["results"][1],
            dossier["evidence_index"][1],
            claim_id="CLAIM-1",
        ),
    )

    assert outcome["status"] == "degraded"
    assert "claim-specific result_refs citation mismatch" in outcome["reason"]
