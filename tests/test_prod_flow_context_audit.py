from __future__ import annotations

from pathlib import Path

from tests.e2e.scripts.prod_flow_context_audit import (
    audit_product_flow_context,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import write_sidecar_text


RUN_ID = "run-context-audit"
GENERATION = "generation-2"


def _sidecar(
    state_dir: Path,
    body: dict,
    *,
    root: str,
    kind: str,
    schema: str,
) -> dict:
    return write_immutable_json_sidecar(
        state_dir,
        body,
        root=root,
        kind=kind,
        schema_version=schema,
        created_by="context-audit-test",
    )


def _append(log: EventLog, event_type: str, payload: dict) -> ZfEvent:
    return log.append(ZfEvent(
        type=event_type,
        correlation_id=RUN_ID,
        payload=payload,
    ))


def _fixture(
    tmp_path: Path,
    *,
    shifted_verify: bool = False,
    typed_contract: bool = True,
    shifted_contract_snapshot: bool = False,
    task_pipeline_dispatch: bool = False,
    terminal_type: str = "run.goal.completed",
) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    task_map = _sidecar(
        state_dir,
        {"schema_version": "task-map.v1", "tasks": [{"id": "TASK-1"}]},
        root="fixtures/task-map",
        kind="task_map",
        schema="task-map.v1",
    )
    plan_body = {
        "schema_version": "plan-artifact-package.v1",
        "workflow_run_id": RUN_ID,
        "task_map_generation": GENERATION,
        "required_ports": ["task_map"],
        "produced": [{
            "logical_name": "task_map",
            "ref": task_map["ref"],
            "sha256": task_map["sha256"],
        }],
    }
    plan = _sidecar(
        state_dir,
        plan_body,
        root="fixtures/plan-package",
        kind="plan_artifact_package",
        schema="plan-artifact-package.v1",
    )
    _append(log, "plan.artifact_package.admitted", {
        "workflow_run_id": RUN_ID,
        "package_ref": plan["ref"],
        "package_digest": plan["sha256"],
        "task_map_generation": GENERATION,
    })
    _append(log, "task_map.ready", {
        "workflow_run_id": RUN_ID,
        "plan_artifact_package_ref": plan["ref"],
        "plan_artifact_package_digest": plan["sha256"],
        "task_map_generation": GENERATION,
    })
    contract = _sidecar(
        state_dir,
        {
            "schema_version": "task-contract-snapshot.v1",
            "workflow_run_id": RUN_ID,
            "task_id": "TASK-1",
            "contract_revision": "contract-r1",
            "task_map_generation": (
                "generation-old" if shifted_contract_snapshot else GENERATION
            ),
            "plan_artifact_package_ref": plan["ref"],
            "plan_artifact_package_digest": plan["sha256"],
        },
        root="fixtures/task-contract",
        kind="task_contract_snapshot",
        schema="task-contract-snapshot.v1",
    )
    contract_payload = {
        "source": "task_map_materialization",
        "task_id": "TASK-1",
        "contract": {
            "contract_revision": "contract-r1",
            "evidence_contract": {"source_refs": {
                "plan_artifact_package_ref": plan["ref"],
                "plan_artifact_package_digest": plan["sha256"],
                "task_map_generation": GENERATION,
            }},
        },
    }
    _append(log, "task.contract.update", contract_payload)
    dispatch_payload = {
        "workflow_run_id": RUN_ID,
        "task_id": "TASK-1",
        "contract_revision": "contract-r1",
        "plan_artifact_package_ref": plan["ref"],
        "plan_artifact_package_digest": plan["sha256"],
        "task_map_generation": (
            "generation-old" if shifted_contract_snapshot else GENERATION
        ),
    }
    if typed_contract:
        dispatch_payload.update({
            "task_contract_snapshot_ref": contract["ref"],
            "task_contract_snapshot_digest": contract["sha256"],
        })
    _append(
        log,
        (
            "task.pipeline.stage.dispatched"
            if task_pipeline_dispatch
            else "fanout.child.dispatched"
        ),
        dispatch_payload,
    )

    for schema in (
        "implementation-result.v1",
        "verification-result.v1",
        "goal-closure-result.v1",
    ):
        operation = schema.removesuffix("-result.v1")
        ledger = write_sidecar_text(
            state_dir,
            f"artifacts/fixtures/{operation}-read-ledger.jsonl",
            '{"status":"read"}\n',
            kind="artifact_read_ledger",
            schema_version="artifact-read-ledger.v1",
            created_by="context-audit-test",
        )
        operation_generation = (
            "generation-old"
            if shifted_verify and schema == "verification-result.v1"
            else GENERATION
        )
        envelope = _sidecar(
            state_dir,
            {
                "schema_version": "call-result-envelope.v1",
                "identity": {
                    "workflow_run_id": RUN_ID,
                    "operation_id": f"op-{operation}",
                    "task_id": "TASK-1",
                    "producer_stage_id": operation,
                    "plan_artifact_package_ref": plan["ref"],
                    "plan_artifact_package_digest": plan["sha256"],
                    "task_map_generation": operation_generation,
                    "contract_snapshot_ref": contract["ref"],
                    "contract_snapshot_digest": contract["sha256"],
                },
                "input_consumption": {
                    "status": "satisfied",
                    "read_ledger_ref": ledger["ref"],
                    "read_ledger_digest": ledger["sha256"],
                },
            },
            root=f"fixtures/envelopes/{operation}",
            kind="call_result_envelope",
            schema="call-result-envelope.v1",
        )
        control = _sidecar(
            state_dir,
            {"schema_version": schema, "status": "passed"},
            root=f"fixtures/control/{operation}",
            kind="call_control_result",
            schema=schema,
        )
        _append(log, "workflow.call.result.admitted", {
            "workflow_run_id": RUN_ID,
            "operation_id": f"op-{operation}",
            "control_result_schema": schema,
            "envelope_ref": envelope,
            "control_result_ref": control,
            "read_ledger_ref": ledger,
        })

    stage_card = _sidecar(
        state_dir,
        {"schema_version": "orchestrator-agent-stage-card.v1"},
        root="fixtures/stage-card",
        kind="orchestrator_agent_stage_card",
        schema="orchestrator-agent-stage-card.v1",
    )
    _append(log, "orchestrator.semantic.checkpoint.requested", {
        "workflow_run_id": RUN_ID,
        "operation_id": "op-oa-plan",
        "checkpoint": "plan_candidate",
        "stage_execution_card_ref": stage_card,
    })
    _append(log, terminal_type, {
        "workflow_run_id": RUN_ID,
        "flow_kind": "prd",
        "task_map_generation": GENERATION,
        "goal_coverage": [{
            "goal_claim_id": "CLAIM-1",
            "status": "closed",
            "supporting_result_refs": ["artifacts/evidence/claim-1.json"],
        }],
    })
    return state_dir


def test_context_audit_accepts_exact_product_handoff(tmp_path: Path) -> None:
    result = audit_product_flow_context(_fixture(tmp_path))

    assert result["status"] == "passed"
    assert all(result["checks"].values())
    assert result["task_contracts"]["task_ids"] == ["TASK-1"]
    assert result["schema_counts"] == {
        "implementation-result.v1": 1,
        "verification-result.v1": 1,
        "goal-closure-result.v1": 1,
    }


def test_context_audit_accepts_task_pipeline_contract_handoff(
    tmp_path: Path,
) -> None:
    result = audit_product_flow_context(
        _fixture(tmp_path, task_pipeline_dispatch=True),
    )

    assert result["status"] == "passed"
    assert result["checks"]["typed_task_contract_snapshots"] is True


def test_context_audit_rejects_verify_generation_shift(tmp_path: Path) -> None:
    result = audit_product_flow_context(
        _fixture(tmp_path, shifted_verify=True),
    )

    assert result["status"] == "failed"
    assert result["checks"]["impl_verify_exact_handoff"] is False
    assert any("shifted Plan/Task context" in row for row in result["reasons"])


def test_context_audit_requires_typed_task_snapshot(tmp_path: Path) -> None:
    result = audit_product_flow_context(
        _fixture(tmp_path, typed_contract=False),
    )

    assert result["status"] == "failed"
    assert result["checks"]["typed_task_contract_snapshots"] is False


def test_context_audit_rejects_task_snapshot_generation_shift(
    tmp_path: Path,
) -> None:
    result = audit_product_flow_context(
        _fixture(tmp_path, shifted_contract_snapshot=True),
    )

    assert result["status"] == "failed"
    assert result["checks"]["typed_task_contract_snapshots"] is False
    assert any(
        "not bound to the current Plan Package" in row
        for row in result["reasons"]
    )


def test_context_audit_keeps_blocked_terminal_failed(tmp_path: Path) -> None:
    result = audit_product_flow_context(
        _fixture(tmp_path, terminal_type="run.goal.blocked"),
    )

    assert result["status"] == "failed"
    assert "workflow reached run.goal.blocked" in result["reasons"]
