from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    RoleLifecycleConfig,
    ZfConfig,
)
from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.task.schema import Task
from zf.runtime.flow_role_activation import (
    activate_flow_roles,
    active_flow_role_instance_ids,
    flow_role_activation_projection,
    restore_flow_role_activations,
)
from zf.runtime.flow_roles import FlowRoleBindingError
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


class _ActivationTransport:
    def __init__(self) -> None:
        self.alive: set[str] = {"controller"}

    def is_alive(self, role_name: str) -> bool:
        return role_name in self.alive

    def capture_log(self, role_name: str, lines: int = 200) -> str:
        return ""

    def poll_events(self) -> list:
        return []


class _ActivationCoordinator:
    def __init__(self, transport: _ActivationTransport) -> None:
        self.transport = transport
        self.spawned: list[tuple[str, Path | None]] = []
        self.prepared: list[str] = []

    def prepare_provider_session(self, role: RoleConfig) -> None:
        self.prepared.append(role.instance_id)

    def spawn(self, role: RoleConfig, *, cwd: Path | None = None) -> None:
        self.spawned.append((role.instance_id, cwd))
        self.transport.alive.add(role.instance_id)


def _config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="multi"),
        roles=[
            RoleConfig(
                name="controller",
                backend="mock",
                role_kind="reader",
            ),
            RoleConfig(
                name="prd-dev",
                backend="mock",
                role_kind="writer",
                flow_kind="prd",
            ),
            RoleConfig(
                name="prd-verify",
                backend="mock",
                role_kind="reader",
                flow_kind="prd",
            ),
            RoleConfig(
                name="issue-fix",
                backend="mock",
                role_kind="writer",
                flow_kind="issue",
                lifecycle=RoleLifecycleConfig(mode="on_demand"),
            ),
            RoleConfig(
                name="workflow-scoper",
                backend="mock",
                role_kind="reader",
                flow_kind="workflow",
            ),
        ],
    )


def _orchestrator(
    tmp_path: Path,
    *,
    transport: _ActivationTransport | None = None,
) -> tuple[Orchestrator, _ActivationTransport, _ActivationCoordinator]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir(exist_ok=True)
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    active_transport = transport or _ActivationTransport()
    orchestrator = Orchestrator(
        state_dir,
        _config(),
        active_transport,  # type: ignore[arg-type]
        project_root=tmp_path,
    )
    coordinator = _ActivationCoordinator(active_transport)
    orchestrator._spawn_coordinator = coordinator  # type: ignore[assignment]
    return orchestrator, active_transport, coordinator


def _payload() -> dict[str, str]:
    return {
        "workflow_operation_id": "wop-prd-1",
        "workflow_run_id": "run-prd-1",
        "flow_kind": "prd",
        "effective_config_digest": "a" * 64,
        "run_contract_digest": "b" * 64,
    }


def test_activation_manifest_spawns_only_confirmed_flow_roles(
    tmp_path: Path,
) -> None:
    orchestrator, _transport, coordinator = _orchestrator(tmp_path)

    result = activate_flow_roles(orchestrator, payload=_payload())

    assert result.status == "applied"
    assert result.role_instance_ids == ("prd-dev", "prd-verify")
    assert [item[0] for item in coordinator.spawned] == [
        "prd-dev",
        "prd-verify",
    ]
    descriptor = result.manifest_ref
    assert descriptor is not None
    manifest = hydrate_sidecar_ref(
        orchestrator.state_dir,
        descriptor,
    ).payload
    assert manifest["flow_kind"] == "prd"
    assert all(
        {
            "workflow_operation_id",
            "workflow_run_id",
            "flow_kind",
            "effective_config_digest",
            "run_contract_digest",
            "role_config_digest",
        } <= set(role)
        for role in manifest["roles"]
    )


def test_activation_supports_generic_workflow_role_closure(
    tmp_path: Path,
) -> None:
    orchestrator, _transport, coordinator = _orchestrator(tmp_path)
    payload = _payload()
    payload["flow_kind"] = "workflow"

    result = activate_flow_roles(orchestrator, payload=payload)

    assert result.status == "applied"
    assert result.role_instance_ids == ("workflow-scoper",)
    assert [item[0] for item in coordinator.spawned] == ["workflow-scoper"]


def test_on_demand_flow_activation_defers_physical_process(
    tmp_path: Path,
) -> None:
    orchestrator, transport, coordinator = _orchestrator(tmp_path)
    payload = _payload()
    payload["flow_kind"] = "issue"

    result = activate_flow_roles(orchestrator, payload=payload)

    assert result.status == "applied"
    assert result.role_instance_ids == ("issue-fix",)
    assert result.deferred_instance_ids == ("issue-fix",)
    assert coordinator.spawned == []
    assert coordinator.prepared == ["issue-fix"]
    assert "issue-fix" not in transport.alive
    registry = RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    assert registry.instance_meta()["issue-fix"]["lifecycle_state"] == "dormant"
    assert (orchestrator.state_dir / "instructions" / "issue-fix.md").exists()
    assert [
        role.instance_id
        for role in orchestrator._runtime_active_role_configs()
    ] == ["controller"]
    orchestrator._hibernate_idle_roles()
    assert not any(
        event.type == "role.lifecycle.suspend.rejected"
        for event in orchestrator.event_log.read_all()
    )


def test_on_demand_activation_ignores_terminal_task_heartbeat(
    tmp_path: Path,
) -> None:
    orchestrator, transport, coordinator = _orchestrator(tmp_path)
    orchestrator.task_store.add(Task(
        id="TASK-OLD",
        title="terminal task",
        status="blocked",
        assigned_to="issue-fix",
    ))
    RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    ).record_heartbeat("issue-fix", {
        "current_task_id": "TASK-OLD",
        "state": "busy",
    })
    payload = _payload()
    payload["flow_kind"] = "issue"

    result = activate_flow_roles(orchestrator, payload=payload)

    assert result.deferred_instance_ids == ("issue-fix",)
    assert coordinator.spawned == []
    assert "issue-fix" not in transport.alive


def test_on_demand_dispatch_activates_only_selected_role(
    tmp_path: Path,
) -> None:
    orchestrator, transport, coordinator = _orchestrator(tmp_path)
    orchestrator.config.roles.append(RoleConfig(
        name="issue-verify",
        backend="mock",
        role_kind="reader",
        flow_kind="issue",
        lifecycle=RoleLifecycleConfig(mode="on_demand"),
    ))
    payload = _payload()
    payload["flow_kind"] = "issue"
    result = activate_flow_roles(orchestrator, payload=payload)
    assert result.deferred_instance_ids == ("issue-fix", "issue-verify")

    selected = next(
        role for role in orchestrator.config.roles
        if role.instance_id == "issue-fix"
    )
    orchestrator._ensure_role_active(selected, task_id=None)

    assert [item[0] for item in coordinator.spawned] == ["issue-fix"]
    assert "issue-fix" in transport.alive
    assert "issue-verify" not in transport.alive
    assert "issue-fix" in {
        role.instance_id
        for role in orchestrator._runtime_active_role_configs()
    }


def test_restore_keeps_dormant_roles_deferred_and_recovers_active_obligation(
    tmp_path: Path,
) -> None:
    orchestrator, transport, _coordinator = _orchestrator(tmp_path)
    payload = _payload()
    payload["flow_kind"] = "issue"
    activate_flow_roles(orchestrator, payload=payload)

    dormant_restore, _transport, dormant_coordinator = _orchestrator(
        tmp_path,
        transport=transport,
    )
    dormant_results = restore_flow_role_activations(dormant_restore)
    assert dormant_results[0].status == "replay"
    assert dormant_results[0].deferred_instance_ids == ("issue-fix",)
    assert dormant_coordinator.spawned == []

    operation = {
        "workflow_run_id": "run-prd-1",
        "operation_id": "wop-issue-fix",
        "operation_type": "agent",
        "request_hash": "c" * 64,
        "role_instance": "issue-fix",
        "task_id": "TASK-ISSUE",
    }
    dormant_restore.event_writer.append(ZfEvent(
        type="workflow.operation.requested",
        actor="kernel",
        task_id="TASK-ISSUE",
        payload=operation,
    ))
    dormant_restore.event_writer.append(ZfEvent(
        type="workflow.operation.started",
        actor="kernel",
        task_id="TASK-ISSUE",
        payload=operation,
    ))
    active_restore, _transport, active_coordinator = _orchestrator(
        tmp_path,
        transport=transport,
    )

    active_results = restore_flow_role_activations(active_restore)

    assert active_results[0].status == "recovered"
    assert active_results[0].recovered_instance_ids == ("issue-fix",)
    assert [item[0] for item in active_coordinator.spawned] == ["issue-fix"]


def test_restore_ignores_nonterminal_operation_from_terminal_run(
    tmp_path: Path,
) -> None:
    orchestrator, transport, _coordinator = _orchestrator(tmp_path)
    payload = _payload()
    payload["flow_kind"] = "issue"
    payload["workflow_run_id"] = "run-current"
    payload["workflow_operation_id"] = "wop-current"
    activate_flow_roles(orchestrator, payload=payload)
    operation = {
        "workflow_run_id": "run-old",
        "operation_id": "wop-stale-issue-fix",
        "operation_type": "agent",
        "request_hash": "d" * 64,
        "role_instance": "issue-fix",
        "task_id": "TASK-STALE",
    }
    orchestrator.event_writer.append(ZfEvent(
        type="workflow.operation.requested",
        actor="kernel",
        task_id="TASK-STALE",
        payload=operation,
        correlation_id="run-old",
    ))
    orchestrator.event_writer.append(ZfEvent(
        type="workflow.operation.started",
        actor="kernel",
        task_id="TASK-STALE",
        payload=operation,
        correlation_id="run-old",
    ))
    orchestrator.event_writer.append(ZfEvent(
        type="run.goal.blocked",
        actor="kernel",
        task_id="TASK-STALE",
        payload={"run_id": "run-old"},
        correlation_id="run-old",
    ))

    restored, _transport, coordinator = _orchestrator(
        tmp_path,
        transport=transport,
    )
    results = restore_flow_role_activations(restored)

    assert results[0].status == "replay"
    assert results[0].deferred_instance_ids == ("issue-fix",)
    assert coordinator.spawned == []


def test_activation_prepares_workdir_before_materializing_role_skills(
    tmp_path: Path,
    monkeypatch,
) -> None:
    orchestrator, _transport, _coordinator = _orchestrator(tmp_path)
    role = next(
        item for item in orchestrator.config.roles
        if item.instance_id == "prd-dev"
    )
    role.skills = ["test-skill"]
    calls: list[str] = []

    def prepare_workdir(_role: RoleConfig, *, source: str) -> None:
        assert source.startswith("flow_activation:")
        calls.append("workdir")
        return None

    def materialize_skills(**_kwargs):
        calls.append("skills")
        return None

    monkeypatch.setattr(orchestrator, "_role_spawn_cwd", prepare_workdir)
    monkeypatch.setattr(
        "zf.core.skills.materialize_role_skills",
        materialize_skills,
    )

    activate_flow_roles(orchestrator, payload=_payload())

    assert calls[:2] == ["workdir", "skills"]


def test_activation_replay_and_restart_restore_are_idempotent(
    tmp_path: Path,
) -> None:
    orchestrator, transport, coordinator = _orchestrator(tmp_path)
    first = activate_flow_roles(orchestrator, payload=_payload())

    replay = activate_flow_roles(orchestrator, payload=_payload())

    assert replay.status == "replay"
    assert len(coordinator.spawned) == 2
    assert len([
        event
        for event in orchestrator.event_log.read_all()
        if event.type == "flow.roles.activation.applied"
    ]) == 1

    transport.alive.remove("prd-verify")
    restored, _transport, restored_coordinator = _orchestrator(
        tmp_path,
        transport=transport,
    )
    results = restore_flow_role_activations(restored)

    assert len(results) == 1
    assert results[0].activation_id == first.activation_id
    assert results[0].status == "recovered"
    assert restored_coordinator.spawned == [("prd-verify", None)]
    assert len([
        event
        for event in restored.event_log.read_all()
        if event.type == "flow.roles.activation.applied"
    ]) == 1


def test_activation_fails_closed_when_identity_or_flow_is_invalid(
    tmp_path: Path,
) -> None:
    orchestrator, _transport, coordinator = _orchestrator(tmp_path)
    incomplete = _payload()
    incomplete.pop("run_contract_digest")

    with pytest.raises(
        FlowRoleBindingError,
        match="flow_role_activation_identity_incomplete",
    ):
        activate_flow_roles(orchestrator, payload=incomplete)

    unknown = _payload()
    unknown["flow_kind"] = "refactor"
    with pytest.raises(
        FlowRoleBindingError,
        match="flow_role_closure_missing",
    ):
        activate_flow_roles(orchestrator, payload=unknown)

    assert coordinator.spawned == []


def test_activation_restore_fails_closed_on_role_config_drift(
    tmp_path: Path,
) -> None:
    orchestrator, transport, _coordinator = _orchestrator(tmp_path)
    first = activate_flow_roles(orchestrator, payload=_payload())
    transport.alive.remove("prd-verify")

    restored, _transport, restored_coordinator = _orchestrator(
        tmp_path,
        transport=transport,
    )
    next(
        role
        for role in restored.config.roles
        if role.instance_id == "prd-verify"
    ).backend = "codex"

    with pytest.raises(
        FlowRoleBindingError,
        match="flow_role_activation_config_drift",
    ):
        restore_flow_role_activations(restored)

    assert restored_coordinator.spawned == []
    applied = [
        event
        for event in restored.event_log.read_all()
        if event.type == "flow.roles.activation.applied"
    ]
    assert [event.payload["activation_id"] for event in applied] == [
        first.activation_id,
    ]
    failed = next(
        event
        for event in restored.event_log.read_all()
        if event.type == "flow.roles.activation.failed"
    )
    assert failed.payload["conflicting_activation_id"] == first.activation_id


@pytest.mark.parametrize(
    "terminal_type",
    ["run.goal.completed", "run.goal.blocked", "run.cancelled"],
)
def test_terminal_run_activation_is_not_restored_after_config_change(
    tmp_path: Path,
    terminal_type: str,
) -> None:
    orchestrator, _transport, _coordinator = _orchestrator(tmp_path)
    activate_flow_roles(orchestrator, payload=_payload())
    orchestrator.event_writer.append(ZfEvent(
        type=terminal_type,
        actor="kernel",
        payload={"run_id": "run-prd-1"},
        correlation_id="run-prd-1",
    ))

    restored, _transport, restored_coordinator = _orchestrator(tmp_path)
    next(
        role
        for role in restored.config.roles
        if role.instance_id == "prd-verify"
    ).backend = "codex"

    assert restore_flow_role_activations(restored) == []
    assert restored_coordinator.spawned == []
    assert active_flow_role_instance_ids(
        restored.config,
        restored.event_log.read_all(),
    ) == {"controller"}
    projected = flow_role_activation_projection(
        restored.config,
        restored.event_log.read_all(),
        active_instance_ids={"controller"},
    )
    assert projected["prd-dev"]["activation_state"] == "declared"
    assert projected["prd-verify"]["activation_state"] == "declared"


def test_activation_rejects_digest_drift_within_confirmed_flow_scope(
    tmp_path: Path,
) -> None:
    orchestrator, _transport, _coordinator = _orchestrator(tmp_path)
    first = activate_flow_roles(orchestrator, payload=_payload())
    drifted = _payload()
    drifted["effective_config_digest"] = "c" * 64

    with pytest.raises(
        FlowRoleBindingError,
        match="flow_role_activation_config_drift",
    ):
        activate_flow_roles(orchestrator, payload=drifted)

    events = orchestrator.event_log.read_all()
    applied = [
        event
        for event in events
        if event.type == "flow.roles.activation.applied"
    ]
    assert [event.payload["activation_id"] for event in applied] == [
        first.activation_id,
    ]
    assert {"prd-dev", "prd-verify"} <= active_flow_role_instance_ids(
        orchestrator.config,
        events,
    )


def test_failed_activation_does_not_deactivate_roles_used_by_another_run(
    tmp_path: Path,
) -> None:
    orchestrator, _transport, _coordinator = _orchestrator(tmp_path)
    first = activate_flow_roles(orchestrator, payload=_payload())
    orchestrator.event_writer.append(ZfEvent(
        type="flow.roles.activation.failed",
        actor="kernel",
        payload={
            **_payload(),
            "workflow_operation_id": "wop-prd-2",
            "workflow_run_id": "run-prd-2",
            "activation_id": "failed-second-run",
            "role_instance_ids": ["prd-dev", "prd-verify"],
            "failed_instance_id": "prd-verify",
            "reason": "synthetic activation failure",
        },
    ))

    events = orchestrator.event_log.read_all()
    assert {"prd-dev", "prd-verify"} <= active_flow_role_instance_ids(
        orchestrator.config,
        events,
    )
    projected = flow_role_activation_projection(
        orchestrator.config,
        events,
        active_instance_ids={"controller"},
    )
    assert projected["prd-dev"]["activation_state"] == "active"
    assert projected["prd-dev"]["activation_id"] == first.activation_id
    assert projected["prd-verify"]["activation_state"] == "active"
    assert projected["prd-verify"]["activation_id"] == first.activation_id


def test_activation_projection_distinguishes_declared_required_and_active(
    tmp_path: Path,
) -> None:
    orchestrator, _transport, _coordinator = _orchestrator(tmp_path)
    activate_flow_roles(orchestrator, payload=_payload())

    projected = flow_role_activation_projection(
        orchestrator.config,
        orchestrator.event_log.read_all(),
        active_instance_ids={"controller"},
    )

    assert projected["controller"]["activation_state"] == "active"
    assert projected["controller"]["activation_reason"] == (
        "resident_control_plane"
    )
    assert projected["prd-dev"]["activation_state"] == "active"
    assert projected["prd-dev"]["workflow_operation_id"] == "wop-prd-1"
    assert projected["prd-dev"]["workflow_run_id"] == "run-prd-1"
    assert projected["prd-dev"]["flow_kind"] == "prd"
    assert projected["issue-fix"]["activation_state"] == "declared"
    assert projected["issue-fix"]["required"] is False


def test_web_roles_expose_flow_activation_state(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from zf.web.server import create_app

    orchestrator, _transport, _coordinator = _orchestrator(tmp_path)
    activate_flow_roles(orchestrator, payload=_payload())

    roles = TestClient(create_app(
        orchestrator.state_dir,
        config=orchestrator.config,
        project_root=tmp_path,
    )).get("/api/roles").json()
    by_id = {role["instance_id"]: role for role in roles}

    assert by_id["prd-dev"]["activation"]["activation_state"] == "active"
    assert by_id["prd-dev"]["activation"]["activation_reason"] == (
        "confirmed_workflow_invoke"
    )
    assert by_id["issue-fix"]["activation"]["activation_state"] == "declared"
    assert by_id["issue-fix"]["state"] == "dormant"


def test_status_workers_exposes_flow_activation_state(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from types import SimpleNamespace

    from zf.cli import status as status_cli

    orchestrator, _transport, _coordinator = _orchestrator(tmp_path)
    activate_flow_roles(orchestrator, payload=_payload())
    config_path = tmp_path / "zf.yaml"
    config_path.write_text("project: {name: multi}\n", encoding="utf-8")
    monkeypatch.setattr(
        status_cli,
        "load_config",
        lambda _path: orchestrator.config,
    )

    exit_code = status_cli._print_workers(
        orchestrator.state_dir,
        orchestrator.event_log,
        config_path,
        context=SimpleNamespace(project_root=tmp_path),
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "ACTIVATION" in output
    assert "prd-dev" in output and "active" in output
    assert "issue-fix" in output and "declared" in output
    issue_row = next(
        line for line in output.splitlines() if line.startswith("issue-fix")
    )
    assert "dormant" in issue_row
