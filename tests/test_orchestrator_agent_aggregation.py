from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import (
    GoalConfig,
    ProjectConfig,
    RoleConfig,
    WorkflowConfig,
    WorkflowOrchestrationConfig,
    WorkflowOrchestrationFlowPolicyConfig,
    ZfConfig,
)
from zf.core.events.model import ZfEvent
from zf.runtime.artifact_read_capability import (
    provision_role_artifact_read_credential,
)
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.call_result_adapters import hydrate_profiled_control_result_event
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.orchestrator_agent_aggregation import (
    pre_closeout_checkpoint_state,
    stage_barrier_checkpoint_state,
)
from zf.runtime.orchestrator_agent_decision_apply import (
    apply_orchestrator_agent_decision,
)
from zf.runtime.orchestrator_agent_operations import (
    activate_orchestrator_agent_operation,
    prepared_operation_from_checkpoint_event,
)
from zf.runtime.plan_artifact_package import (
    build_plan_artifact_package,
    package_event_payload,
    write_plan_artifact_package,
)
from zf.runtime.result_submit import (
    SemanticResultSubmitService,
    provision_role_submit_credential,
)
from zf.runtime.run_admission import run_dispatch_block_reason
from zf.runtime.run_contract import (
    stable_json_sha256,
    write_run_contract_snapshot,
)


class _Transport:
    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        return None

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


def _runtime(
    tmp_path: Path,
    *,
    checkpoint: str,
) -> tuple[Orchestrator, Path]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(
            name="oa-aggregation-test",
            workspace=str(tmp_path),
            state_dir=str(state_dir),
        ),
        goal=GoalConfig(enabled=True),
        roles=[RoleConfig(
            name="orchestrator",
            instance_id="orchestrator",
            backend="mock",
            role_kind="reader",
            triggers=["orchestrator.semantic.checkpoint.requested"],
        )],
        workflow=WorkflowConfig(orchestration=WorkflowOrchestrationConfig(
            mode="semantic_control",
            checkpoints=[checkpoint],
            checkpoint_policies={checkpoint: "blocking"},
        )),
    )
    runtime = Orchestrator(
        state_dir,
        config,
        _Transport(),  # type: ignore[arg-type]
        project_root=tmp_path,
    )
    provision_role_submit_credential(state_dir, "orchestrator")
    provision_role_artifact_read_credential(
        state_dir,
        "orchestrator",
        role_name="orchestrator",
        provider="mock",
    )
    return runtime, state_dir


def _port(state_dir: Path, name: str) -> dict:
    descriptor = write_immutable_json_sidecar(
        state_dir,
        {"schema_version": f"{name}.v1", "status": "ready"},
        root=f"fixtures/aggregation/{name}",
        kind=name,
        schema_version=f"{name}.v1",
        created_by="test",
    )
    return {
        "logical_name": name,
        "artifact_kind": name,
        "schema_version": f"{name}.v1",
        "producer_stage_id": "plan",
        "ref": descriptor["ref"],
        "sha256": descriptor["sha256"],
    }


def _seed_run(runtime: Orchestrator, *, run_id: str = "run-aggregation") -> dict:
    contract = {
        "schema_version": "run-contract.v1",
        "workflow": {"kind": "prd"},
    }
    contract["contract_digest"] = stable_json_sha256(contract)
    contract_ref = write_run_contract_snapshot(runtime.state_dir, contract)
    ports = [
        _port(runtime.state_dir, name)
        for name in ("goal_claim_set", "task_map", "planning_result")
    ]
    package = build_plan_artifact_package(
        workflow_run_id=run_id,
        flow_kind="prd",
        producer_stage_id="plan",
        run_contract=contract_ref,
        plan_revision="r1",
        task_map_generation="g1",
        produced=ports,
        required_ports=[port["logical_name"] for port in ports],
    )
    descriptor = write_plan_artifact_package(runtime.state_dir, package)
    runtime.event_writer.append(ZfEvent(
        type="run.goal.started",
        correlation_id=run_id,
        payload={"run_id": run_id, "goal_id": "GOAL-1"},
    ))
    runtime.event_writer.append(ZfEvent(
        type="plan.artifact_package.admitted",
        correlation_id=run_id,
        payload=package_event_payload(package, descriptor, status="admitted"),
    ))
    return {
        "workflow_run_id": run_id,
        "goal_id": "GOAL-1",
        "task_map_generation": "g1",
        "plan_artifact_package_id": descriptor["package_id"],
        "plan_artifact_package_ref": descriptor["ref"],
        "plan_artifact_package_digest": descriptor["sha256"],
    }


def _result(runtime: Orchestrator, name: str = "verification") -> dict:
    return write_immutable_json_sidecar(
        runtime.state_dir,
        {"schema_version": f"{name}-result.v1", "status": "passed"},
        root=f"fixtures/aggregation/{name}-result",
        kind=f"{name}_result",
        schema_version=f"{name}-result.v1",
        created_by="test",
    )


def _prepared(runtime: Orchestrator):  # noqa: ANN001
    checkpoint = next(
        event
        for event in reversed(runtime.event_log.read_all())
        if event.type == "orchestrator.semantic.checkpoint.requested"
    )
    prepared = prepared_operation_from_checkpoint_event(runtime, checkpoint)
    assert prepared is not None
    activate_orchestrator_agent_operation(
        runtime,
        prepared,
        dispatch_id=f"dispatch-{prepared.operation_id}",
        causation_id=checkpoint.id,
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
    return prepared


def _apply(
    runtime: Orchestrator,
    prepared,  # noqa: ANN001
    *,
    recommendation: str,
    unclosed_claim_ids: list[str] | None = None,
) -> dict:
    identity = prepared.context.input_body["identity"]
    result_refs = [
        {
            "ref": str(item.get("ref") or ""),
            "sha256": str(item.get("sha256") or ""),
        }
        for item in prepared.context.input_body["aggregation_input_refs"]
    ]
    decision = {
        "schema_version": "orchestration-decision.v1",
        "execution_status": "completed",
        "identity": {
            "operation_id": prepared.operation_id,
            "workflow_run_id": prepared.workflow_run_id,
            "checkpoint": prepared.checkpoint,
            "input_digest": prepared.context.input_ref["sha256"],
            "effective_config_digest": prepared.context.effective_config_ref[
                "sha256"
            ],
            "plan_artifact_package_ref": identity[
                "plan_artifact_package_ref"
            ],
            "plan_artifact_package_digest": identity[
                "plan_artifact_package_digest"
            ],
            "task_map_generation": identity["task_map_generation"],
        },
        "decision": recommendation,
        "reason_codes": [f"recommend_{recommendation}"],
        "affected_work_units": [],
        "required_followup": recommendation,
        "expected_outcome": "Kernel evaluates the admitted recommendation",
        "confidence": 0.9,
        "aggregation_result": {
            "schema_version": "orchestration-result.v1",
            "identity": {
                "operation_id": prepared.operation_id,
                "workflow_run_id": prepared.workflow_run_id,
                "checkpoint": prepared.checkpoint,
            },
            "input_result_refs": result_refs,
            "selected_result_refs": result_refs,
            "rejected_result_refs": [],
            "unclosed_claim_ids": list(unclosed_claim_ids or []),
            "provenance_map": [],
            "remaining_uncertainty": [],
            "recommendation": recommendation,
        },
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
        semantic_result=decision,
        role_instance="orchestrator",
        credential=token,
    )
    canonical = next(
        event
        for event in runtime.event_log.read_all()
        if event.id == submitted.canonical_event_id
    )
    return apply_orchestrator_agent_decision(
        runtime,
        hydrate_profiled_control_result_event(runtime.state_dir, canonical),
    )


def test_stage_barrier_is_explicit_and_admission_is_restart_safe(
    tmp_path: Path,
) -> None:
    runtime, _state_dir = _runtime(tmp_path, checkpoint="stage_barrier")
    package = _seed_run(runtime)
    result = _result(runtime)
    feedback = write_immutable_json_sidecar(
        runtime.state_dir,
        {"schema_version": "rework-feedback.v1", "task_id": "TASK-1"},
        root="fixtures/aggregation/rework-feedback",
        kind="rework_feedback",
        schema_version="rework-feedback.v1",
        created_by="test",
    )
    lane = ZfEvent(
        type="lane.stage.completed",
        correlation_id=package["workflow_run_id"],
        payload={**package, "result_refs": [result]},
    )
    assert stage_barrier_checkpoint_state(runtime, lane).enabled is False
    aggregate = runtime.event_writer.append(ZfEvent(
        type="fanout.aggregate.completed",
        correlation_id=package["workflow_run_id"],
        payload={
            **package,
            "status": "completed",
            "result_refs": [result],
            "rework_feedback_ref": feedback["ref"],
            "rework_feedback_digest": feedback["sha256"],
        },
    ))

    pending = stage_barrier_checkpoint_state(runtime, aggregate)
    prepared = _prepared(runtime)
    assert prepared.context.input_body["identity"]["feedback_revision"] == (
        feedback["sha256"]
    )
    assert prepared.context.source_manifest["feedback_revision"] == (
        feedback["sha256"]
    )
    assert "rework-feedback" in {
        source["source_id"]
        for source in prepared.context.source_manifest["sources"]
    }
    outcome = _apply(runtime, prepared, recommendation="aggregate")
    admitted = stage_barrier_checkpoint_state(runtime, aggregate)

    assert pending.blocking is True
    assert pending.satisfied is False
    assert outcome["status"] == "applied"
    assert admitted.satisfied is True
    assert sum(
        event.type == "orchestrator.stage_barrier.admitted"
        for event in runtime.event_log.read_all()
    ) == 1


def test_fixed_research_fanout_bypasses_product_stage_barrier(
    tmp_path: Path,
) -> None:
    runtime, _state_dir = _runtime(tmp_path, checkpoint="stage_barrier")
    runtime.config.workflow.orchestration.flow_policies["research"] = (
        WorkflowOrchestrationFlowPolicyConfig(mode="exception_advisor")
    )
    aggregate = runtime.event_writer.append(ZfEvent(
        type="fanout.aggregate.completed",
        correlation_id="research-run",
        payload={
            "flow_kind": "workflow",
            "stage_id": "research-fanout",
            "pattern_id": "research-fanout",
            "status": "completed",
        },
    ))

    state = stage_barrier_checkpoint_state(runtime, aggregate)

    assert state.enabled is False
    assert not any(
        event.type.startswith("orchestrator.semantic.checkpoint")
        for event in runtime.event_log.read_all()
    )


def test_pre_closeout_precedes_claim_and_keeps_kernel_gate_authority(
    tmp_path: Path,
) -> None:
    runtime, _state_dir = _runtime(tmp_path, checkpoint="pre_closeout")
    package = _seed_run(runtime)
    result = _result(runtime, "goal-closure")
    judge = runtime.event_writer.append(ZfEvent(
        type="judge.passed",
        correlation_id=package["workflow_run_id"],
        payload={**package, "result_refs": [result]},
    ))

    runtime._maybe_complete_run_goal(judge)
    runtime._reconcile_run_goal_completion()

    assert not any(
        event.type == "run.goal.completion.claimed"
        for event in runtime.event_log.read_all()
    )
    prepared = _prepared(runtime)
    _apply(runtime, prepared, recommendation="continue")
    runtime._reconcile_run_goal_completion()

    types = [event.type for event in runtime.event_log.read_all()]
    assert types.index("orchestrator.pre_closeout.admitted") < types.index(
        "run.goal.completion.claimed"
    )
    assert types.count("run.goal.completed") == 1


def test_partial_recommendation_cannot_close_an_open_kernel_gap(
    tmp_path: Path,
) -> None:
    runtime, _state_dir = _runtime(tmp_path, checkpoint="pre_closeout")
    package = _seed_run(runtime)
    result = _result(runtime, "partial")
    runtime.event_writer.append(ZfEvent(
        id="rework-open",
        type="task.rework.requested",
        task_id="TASK-OPEN",
        correlation_id=package["workflow_run_id"],
        payload={
            "workflow_run_id": package["workflow_run_id"],
            "task_id": "TASK-OPEN",
            "finding_ids": ["finding-open"],
        },
    ))
    judge = runtime.event_writer.append(ZfEvent(
        type="judge.passed",
        correlation_id=package["workflow_run_id"],
        payload={**package, "result_refs": [result]},
    ))
    runtime._maybe_complete_run_goal(judge)
    prepared = _prepared(runtime)

    _apply(
        runtime,
        prepared,
        recommendation="partial",
        unclosed_claim_ids=["finding-open"],
    )
    runtime._maybe_complete_run_goal(judge)

    types = [event.type for event in runtime.event_log.read_all()]
    assert "run.goal.completion.claimed" in types
    assert "run.goal.completion.blocked" in types
    assert "run.goal.completed" not in types


def test_semantic_halt_pauses_without_terminal_and_resume_clears_fence(
    tmp_path: Path,
) -> None:
    runtime, _state_dir = _runtime(tmp_path, checkpoint="stage_barrier")
    package = _seed_run(runtime)
    result = _result(runtime, "halt")
    aggregate = runtime.event_writer.append(ZfEvent(
        type="fanout.aggregate.completed",
        correlation_id=package["workflow_run_id"],
        payload={**package, "status": "completed", "result_refs": [result]},
    ))
    stage_barrier_checkpoint_state(runtime, aggregate)
    prepared = _prepared(runtime)

    outcome = _apply(runtime, prepared, recommendation="halt")

    events = runtime.event_log.read_all()
    assert outcome["status"] == "paused"
    assert sum(event.type == "run.paused" for event in events) == 1
    assert sum(event.type == "human.escalate" for event in events) == 1
    assert not any(event.type == "run.goal.completed" for event in events)
    assert run_dispatch_block_reason(
        runtime,
        run_id=package["workflow_run_id"],
    ) == "run_paused"

    runtime.event_writer.append(ZfEvent(
        type="run.resumed",
        correlation_id=package["workflow_run_id"],
        payload={
            "workflow_run_id": package["workflow_run_id"],
            "run_id": package["workflow_run_id"],
        },
    ))

    assert run_dispatch_block_reason(
        runtime,
        run_id=package["workflow_run_id"],
    ) == ""
