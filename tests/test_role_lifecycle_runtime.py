from __future__ import annotations

from pathlib import Path

from zf.cli.start import _record_dormant_worker_state
from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    RoleLifecycleConfig,
    SessionConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.core.events.writer import EventWriter
from zf.runtime.call_result_admission import CallResultAdmissionService
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.session_tailer import claude_session_path
from zf.runtime.spawn_coordinator import SpawnCoordinator
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    load_workflow_operation,
)
from zf.web.projections.common import _derive_lifecycle_state


class RecordingTransport:
    def __init__(self) -> None:
        self.alive: set[str] = set()
        self.spawned: list[tuple[str, list[str], Path | None]] = []
        self.sent: list[str] = []
        self.terminated: list[str] = []

    def spawn(self, role, argv, *, cwd=None) -> None:  # noqa: ANN001
        self.alive.add(role.instance_id)
        self.spawned.append((role.instance_id, list(argv), cwd))

    def is_alive(self, role_name: str) -> bool:
        return role_name in self.alive

    def wait_ready(self, role_name: str, pattern: str, timeout: float) -> bool:
        return role_name in self.alive

    def send_task(self, role_name, briefing_path, prompt, *, context=None) -> None:  # noqa: ANN001
        assert role_name in self.alive
        self.sent.append(role_name)

    def terminate(self, role_name: str) -> None:
        self.alive.discard(role_name)
        self.terminated.append(role_name)

    def capture_log(self, role_name: str, lines: int = 200) -> str:
        return ""

    def poll_events(self) -> list[ZfEvent]:
        return []


def _runtime(tmp_path: Path) -> tuple[Orchestrator, RoleConfig, RecordingTransport]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "memory").mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    SessionStore(state_dir / "session.yaml").create(project_root=str(tmp_path))
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(type="loop.started", actor="zf-cli")
    )
    role = RoleConfig(
        name="impl",
        backend="claude-code",
        instance_id="impl-1",
        lifecycle=RoleLifecycleConfig(
            mode="on_demand",
            idle_seconds=0,
            cooldown_seconds=0,
        ),
    )
    config = ZfConfig(
        project=ProjectConfig(name="lifecycle-test"),
        session=SessionConfig(tmux_session="lifecycle-test"),
        roles=[role],
    )
    transport = RecordingTransport()
    return (
        Orchestrator(
            state_dir,
            config,
            transport,
            project_root=tmp_path,
        ),
        role,
        transport,
    )


def test_start_registration_keeps_on_demand_role_dormant(
    tmp_path: Path,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    registry = RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    coordinator = SpawnCoordinator(
        state_dir=orchestrator.state_dir,
        registry=registry,
        transport=transport,
        project_root=str(tmp_path),
        event_log=orchestrator.event_log,
        config=orchestrator.config,
    )

    coordinator.prepare_provider_session(role)
    _record_dormant_worker_state(
        event_log=orchestrator.event_log,
        registry=registry,
        role=role,
    )

    meta = RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    ).instance_meta()[role.instance_id]
    assert transport.spawned == []
    assert meta["lifecycle_state"] == "dormant"
    assert meta["provider_session_config_digest"]
    assert [
        event.type for event in orchestrator.event_log.read_all()
    ][-2:] == ["role.lifecycle.dormant", "worker.state.changed"]
    assert _derive_lifecycle_state(
        "dormant",
        active_task="",
        signal={},
    ) == "dormant"


def test_dispatch_primitive_activates_on_demand_role_before_send(
    tmp_path: Path,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    briefing = orchestrator.state_dir / "briefings" / "impl.md"
    briefing.parent.mkdir(parents=True)
    briefing.write_text("work\n", encoding="utf-8")

    orchestrator._send_transport_task(
        role.instance_id,
        briefing,
        "work",
        None,
    )

    assert len(transport.spawned) == 1
    assert transport.sent == [role.instance_id]
    meta = RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    ).instance_meta()[role.instance_id]
    assert meta["lifecycle_state"] == "active"


def test_fanout_liveness_activates_on_demand_role_before_watchdog(
    tmp_path: Path,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)

    assert orchestrator._ensure_fanout_role_dispatchable(
        role=role,
        fanout_id="fanout-on-demand",
        stage_id="impl",
        child_id="child-1",
        run_id="run-child-1",
        trace_id="trace-1",
        task_id="TASK-1",
    )

    assert [item[0] for item in transport.spawned] == [role.instance_id]
    events = orchestrator.event_log.read_all()
    assert [
        event.payload["instance_id"]
        for event in events
        if event.type == "role.lifecycle.ready"
    ] == [role.instance_id]
    assert not [event for event in events if event.type == "worker.respawned"]


def test_fanout_activation_failure_does_not_bypass_lifecycle_with_respawn(
    tmp_path: Path,
    monkeypatch,
) -> None:
    orchestrator, role, _transport = _runtime(tmp_path)
    respawned: list[str] = []

    def fail_activation(*_args, **_kwargs) -> bool:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(orchestrator, "_ensure_role_active", fail_activation)
    monkeypatch.setattr(
        orchestrator,
        "_respawn_instance",
        lambda target, **_kwargs: respawned.append(target.instance_id),
    )

    assert not orchestrator._ensure_fanout_role_dispatchable(
        role=role,
        fanout_id="fanout-on-demand",
        stage_id="impl",
        child_id="child-1",
        run_id="run-child-1",
        trace_id="trace-1",
        task_id="TASK-1",
    )
    assert respawned == []
    deferred = [
        event
        for event in orchestrator.event_log.read_all()
        if event.type == "fanout.child.dispatch_deferred"
    ]
    assert "role_activation_failed:provider unavailable" in (
        deferred[-1].payload["reason"]
    )


def test_activation_failure_does_not_claim_task(
    tmp_path: Path,
    monkeypatch,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    task = Task(
        id="TASK-ACTIVATION-FAIL",
        title="activation must precede ownership",
        status="backlog",
    )
    orchestrator.task_store.add(task)

    def fail_activation(*_args, **_kwargs) -> bool:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(
        orchestrator,
        "_ensure_role_active",
        fail_activation,
    )

    assert orchestrator._dispatch_task(task, role) is False
    stored = orchestrator.task_store.get(task.id)
    assert stored is not None
    assert stored.status == "backlog"
    assert not stored.assigned_to
    assert transport.sent == []
    failed = [
        event
        for event in orchestrator.event_log.read_all()
        if event.type == "orchestrator.dispatch_failed"
    ]
    assert failed[-1].payload["stage"] == "role_activation"


def test_active_workflow_operation_prevents_hibernation(
    tmp_path: Path,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    orchestrator._ensure_role_active(role, task_id="TASK-1")
    orchestrator.event_writer.append(ZfEvent(
        type="workflow.operation.requested",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "workflow_run_id": orchestrator._current_run_id(),
            "operation_id": "wop-active",
            "operation_type": "agent",
            "request_hash": "a" * 64,
            "role_instance": role.instance_id,
        },
    ))
    registry = RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    registry.record_heartbeat(role.instance_id, {
        "state": "idle",
        "last_action_ts": 1,
    })

    orchestrator._hibernate_idle_roles()

    assert role.instance_id in transport.alive
    rejected = [
        event
        for event in orchestrator.event_log.read_all()
        if event.type == "role.lifecycle.suspend.rejected"
    ]
    assert rejected[-1].payload["reason"] == "provider_operation_active"


def test_ready_task_for_other_role_does_not_prevent_hibernation(
    tmp_path: Path,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    verify_role = RoleConfig(
        name="verify",
        backend="claude-code",
        instance_id="verify-1",
    )
    orchestrator.config.roles.append(verify_role)
    orchestrator._ensure_role_active(role, task_id="TASK-IMPL-DONE")
    orchestrator.task_store.add(Task(
        id="TASK-VERIFY-READY",
        title="ready for a different stage role",
        status="backlog",
        assigned_to=verify_role.instance_id,
    ))
    RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    ).record_heartbeat(role.instance_id, {
        "state": "idle",
        "last_action_ts": 1,
        "checkpoint_ref": "checkpoint://TASK-IMPL-DONE",
    })

    orchestrator._hibernate_idle_roles()

    assert role.instance_id not in transport.alive
    assert verify_role.instance_id not in transport.terminated
    assert (
        orchestrator.task_store.get("TASK-VERIFY-READY").status
        == "backlog"
    )


def test_hibernate_preserves_session_and_next_activation_resumes(
    tmp_path: Path,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    orchestrator._ensure_role_active(role, task_id="TASK-1")
    registry = RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    session_id = registry.get(role.instance_id)
    assert session_id is not None
    session_path = claude_session_path(str(tmp_path), str(session_id))
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text("{}\n", encoding="utf-8")
    registry.record_heartbeat(role.instance_id, {
        "state": "idle",
        "last_action_ts": 1,
        "checkpoint_ref": "checkpoint://TASK-1",
    })

    orchestrator._hibernate_idle_roles()

    after_suspend = RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    assert after_suspend.get(role.instance_id) == session_id
    assert (
        after_suspend.instance_meta()[role.instance_id]["lifecycle_state"]
        == "suspended"
    )
    assert role.instance_id not in transport.alive

    orchestrator._ensure_role_active(role, task_id="TASK-2")

    assert len(transport.spawned) == 2
    second_argv = transport.spawned[-1][1]
    assert "--resume" in second_argv
    assert str(session_id) in second_argv
    assert RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    ).get(role.instance_id) == session_id


def test_mock_e2e_dormant_operation_settlement_suspend_and_same_lane_resume(
    tmp_path: Path,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    state_dir = orchestrator.state_dir
    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    coordinator = SpawnCoordinator(
        state_dir=state_dir,
        registry=registry,
        transport=transport,
        project_root=str(tmp_path),
        event_log=orchestrator.event_log,
        config=orchestrator.config,
    )
    coordinator.prepare_provider_session(role)
    _record_dormant_worker_state(
        event_log=orchestrator.event_log,
        registry=registry,
        role=role,
    )

    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-IMPL-1",
        title="provider root operation",
        status="in_progress",
        assigned_to=role.instance_id,
    ))
    briefing = state_dir / "briefings" / "TASK-IMPL-1.md"
    briefing.parent.mkdir(parents=True, exist_ok=True)
    briefing.write_text("implement\n", encoding="utf-8")
    orchestrator._send_transport_task(
        role.instance_id,
        briefing,
        "implement",
        None,
    )
    orchestrator._set_worker_state(
        role.instance_id,
        "busy",
        reason="mock operation started",
        task_id="TASK-IMPL-1",
    )

    run_id = str(orchestrator._current_run_id() or "")
    session_id = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    ).get(role.instance_id)
    assert session_id is not None
    session_path = claude_session_path(str(tmp_path), str(session_id))
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text("{}\n", encoding="utf-8")
    writer = EventWriter(orchestrator.event_log)
    operations = WorkflowOperationService(
        state_dir=state_dir,
        event_log=orchestrator.event_log,
        event_writer=writer,
    )
    call_task_id = "CALL-RESULT-1"
    operation = operations.ensure_operation(
        workflow_run_id=run_id,
        operation_id="wop-TASK-IMPL-1",
        operation_type="agent",
        request={"prompt": "implement"},
        task_id=call_task_id,
        role_instance=role.instance_id,
    )
    operations.mark_started(
        operation_id=operation.operation_id,
        request_hash=operation.request_hash,
        workflow_run_id=run_id,
        task_id=call_task_id,
        role_instance=role.instance_id,
        provider_session_id=str(session_id),
    )
    admission = CallResultAdmissionService(
        state_dir=state_dir,
        event_log=orchestrator.event_log,
        event_writer=writer,
        operation_service=operations,
    )
    result_payload = {
        "workflow_run_id": run_id,
        "task_id": call_task_id,
        "run_id": "attempt-1",
        "stage_id": "verify",
        "role_instance": role.instance_id,
        "contract_revision": "contract-1",
        "task_map_generation": "generation-1",
        "base_commit": "a" * 40,
        "task_ref": "artifacts/task-ref.json",
        "contract_snapshot_ref": "artifacts/contract.json",
        "contract_snapshot_digest": "b" * 64,
        "target_snapshot_ref": "artifacts/target.json",
        "target_snapshot_digest": "c" * 64,
        "target_commit": "d" * 40,
        "verification_result": {
            "schema_version": "verification-result.v1",
            "execution_status": "completed",
            "verdict": "passed",
            "failure_class": "none",
            "workflow_run_id": run_id,
            "task_id": call_task_id,
            "contract_revision": "contract-1",
            "task_map_generation": "generation-1",
            "base_commit": "a" * 40,
            "task_ref": "artifacts/task-ref.json",
            "contract_snapshot_ref": "artifacts/contract.json",
            "contract_snapshot_digest": "b" * 64,
            "target_snapshot_ref": "artifacts/target.json",
            "target_snapshot_digest": "c" * 64,
            "target_commit": "d" * 40,
            "verification_owner": "task_verify",
            "verification_tier": "runtime",
            "requirement_results": [{
                "acceptance_id": "AC-1",
                "status": "passed",
                "verification_owner": "task_verify",
                "verification_tier": "runtime",
                "evidence_refs": ["test:mock-e2e"],
                "findings": [],
                "reproduction_commands": ["pytest"],
            }],
        },
        "provider_operation_summary": {
            "schema_version": "provider-operation-summary.v1",
            "workflow_run_id": run_id,
            "operation_id": operation.operation_id,
            "provider_session_id": str(session_id),
            "settlement": "settled",
            "child_count": 2,
            "child_status_counts": {"completed": 2},
            "active_child_count": 0,
            "peak_parallel_agents": 2,
            "usage": {"input_tokens": 100, "output_tokens": 20},
            "cost_usd": 0.2,
        },
    }
    outcome = admission.report_legacy_result(
        ZfEvent(
            type="verify.child.completed",
            actor=role.instance_id,
            task_id=call_task_id,
            payload=result_payload,
        ),
        mode="blocking",
        operation={
            "workflow_run_id": run_id,
            "operation_id": operation.operation_id,
            "request_hash": operation.request_hash,
            "provider_session_max_parallel_agents": 4,
            "budget_snapshot": {"remaining_usd": 1.0},
        },
    )
    assert outcome.admitted is True
    assert load_workflow_operation(
        orchestrator.event_log,
        operation.operation_id,
    )["status"] == "settled"

    store.update("TASK-IMPL-1", status="done")
    orchestrator._set_worker_state(
        role.instance_id,
        "idle",
        reason="mock operation settled",
        task_id="TASK-IMPL-1",
        force=True,
    )
    RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    ).record_heartbeat(role.instance_id, {
        "state": "idle",
        "last_action_ts": 1,
        "checkpoint_ref": "checkpoint://TASK-IMPL-1",
    })
    orchestrator._hibernate_idle_roles()
    assert role.instance_id not in transport.alive

    store.add(Task(
        id="TASK-REWORK-1",
        title="same lane continuation",
        status="in_progress",
        assigned_to=role.instance_id,
    ))
    rework_briefing = state_dir / "briefings" / "TASK-REWORK-1.md"
    rework_briefing.write_text("continue\n", encoding="utf-8")
    orchestrator._send_transport_task(
        role.instance_id,
        rework_briefing,
        "continue",
        None,
    )
    store.update("TASK-REWORK-1", status="done")
    writer.append(ZfEvent(
        type="run.goal.completed",
        actor="zf-cli",
        correlation_id=run_id,
        payload={
            "run_id": run_id,
            "completed_task_ids": ["TASK-IMPL-1", "TASK-REWORK-1"],
        },
    ))

    archived = store.list_all_with_archive()
    assert {task.id for task in archived} == {
        "TASK-IMPL-1",
        "TASK-REWORK-1",
    }
    assert all(task.assigned_to == role.instance_id for task in archived)
    assert RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    ).get(role.instance_id) == session_id
    assert "--resume" in transport.spawned[-1][1]
