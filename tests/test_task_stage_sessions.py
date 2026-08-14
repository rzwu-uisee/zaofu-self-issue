from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.task_pipeline_dispatch import _next_task_stage_placement_epoch
from zf.runtime.task_pipeline_terminal import (
    archive_task_pipeline_stage_binding,
)


def _bind(
    registry: RoleSessionRegistry,
    task_id: str,
    *,
    role_instance: str,
    placement_epoch: int,
) -> dict:
    return registry.bind_task_stage_session(
        workflow_run_id="run-1",
        task_id=task_id,
        stage="impl",
        rework_affinity_id="impl-chain-1",
        role_instance=role_instance,
        role_config_digest="config-sha",
        workspace_generation=1,
        placement_epoch=placement_epoch,
        backend="mock",
    )


def test_task_stage_sessions_isolate_tasks_and_survive_relocation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "role_sessions.yaml"
    registry = RoleSessionRegistry(path, project_root="/project")
    task_a = _bind(
        registry, "TASK-A", role_instance="impl-1", placement_epoch=1
    )
    task_c = _bind(
        registry, "TASK-C", role_instance="impl-1", placement_epoch=1
    )
    relocated_a = _bind(
        registry, "TASK-A", role_instance="impl-2", placement_epoch=2
    )

    assert task_a["session_id"] != task_c["session_id"]
    assert relocated_a["session_id"] == task_a["session_id"]
    assert relocated_a["role_config_digest"] == task_a["role_config_digest"]
    assert [
        item["role_instance"]
        for item in relocated_a["placement_history"]
    ] == ["impl-1", "impl-2"]

    reloaded = RoleSessionRegistry(path, project_root="/project")
    assert reloaded.task_stage_binding(
        workflow_run_id="run-1",
        task_id="TASK-A",
        stage="impl",
        rework_affinity_id="impl-chain-1",
    ) == relocated_a


def test_next_placement_epoch_includes_pre_attempt_binding(tmp_path: Path) -> None:
    registry = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml",
        project_root="/project",
    )
    registry.bind_task_stage_session(
        workflow_run_id="run-1",
        task_id="TASK-A",
        stage="impl",
        rework_affinity_id="map-g1:impl",
        role_instance="impl-1",
        role_config_digest="config-sha",
        workspace_generation=1,
        placement_epoch=4,
        backend="codex",
    )

    placement_epoch = _next_task_stage_placement_epoch(
        SimpleNamespace(state_dir=tmp_path, project_root="/project"),
        workflow_run_id="run-1",
        task_id="TASK-A",
        task_map_generation="map-g1",
        stage="impl",
        operation_ids={"op-A-impl"},
        attempt_rows=[{
            "operation_id": "op-A-impl",
            "placement_epoch": 3,
        }],
    )

    assert placement_epoch == 5


def test_codex_task_stage_relocation_keeps_one_physical_session_owner(
    tmp_path: Path,
) -> None:
    registry = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml",
        project_root="/project",
    )
    binding = registry.bind_task_stage_session(
        workflow_run_id="run-1",
        task_id="TASK-A",
        stage="impl",
        rework_affinity_id="impl-chain-1",
        role_instance="impl-1",
        role_config_digest="config-sha",
        workspace_generation=1,
        placement_epoch=1,
        backend="codex",
    )
    registry.activate_task_stage_session(
        binding_key=binding["binding_key"],
        role_instance="impl-1",
    )
    session_id = "57575757-5757-5757-5757-575757575757"
    session_path = (
        tmp_path
        / "workdirs"
        / "impl-1"
        / "codex-home"
        / "sessions"
        / f"rollout-{session_id}.jsonl"
    )
    session_path.parent.mkdir(parents=True)
    session_path.write_text("{}\n", encoding="utf-8")
    registry.bind_codex_session(
        "impl-1",
        session_id,
        session_path=session_path,
    )
    relocated = registry.bind_task_stage_session(
        workflow_run_id="run-1",
        task_id="TASK-A",
        stage="impl",
        rework_affinity_id="impl-chain-1",
        role_instance="impl-2",
        role_config_digest="config-sha",
        workspace_generation=1,
        placement_epoch=2,
        backend="codex",
    )
    registry.activate_task_stage_session(
        binding_key=relocated["binding_key"],
        role_instance="impl-2",
    )

    reloaded = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml",
        project_root="/project",
    )
    assert reloaded.get("impl-1") is None
    assert str(reloaded.get("impl-2")) == session_id
    stored = reloaded.task_stage_binding(
        workflow_run_id="run-1",
        task_id="TASK-A",
        stage="impl",
        rework_affinity_id="impl-chain-1",
    )
    assert stored is not None
    assert stored["provider_session_role_instance"] == "impl-1"


def test_task_stage_session_rejects_config_or_workspace_drift(
    tmp_path: Path,
) -> None:
    registry = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml", project_root="/project"
    )
    _bind(registry, "TASK-A", role_instance="impl-1", placement_epoch=1)

    with pytest.raises(ValueError, match="role config digest changed"):
        registry.bind_task_stage_session(
            workflow_run_id="run-1",
            task_id="TASK-A",
            stage="impl",
            rework_affinity_id="impl-chain-1",
            role_instance="impl-2",
            role_config_digest="changed",
            workspace_generation=1,
            placement_epoch=2,
        )
    with pytest.raises(ValueError, match="workspace generation changed"):
        registry.bind_task_stage_session(
            workflow_run_id="run-1",
            task_id="TASK-A",
            stage="impl",
            rework_affinity_id="impl-chain-1",
            role_instance="impl-2",
            role_config_digest="config-sha",
            workspace_generation=2,
            placement_epoch=2,
        )


def test_task_stage_session_requires_seal_before_archive(tmp_path: Path) -> None:
    registry = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml", project_root="/project"
    )
    _bind(registry, "TASK-A", role_instance="impl-1", placement_epoch=1)
    identity = {
        "workflow_run_id": "run-1",
        "task_id": "TASK-A",
        "stage": "impl",
        "rework_affinity_id": "impl-chain-1",
    }

    with pytest.raises(ValueError, match="sealed"):
        registry.archive_task_stage_session(**identity)
    sealed = registry.seal_task_stage_session(**identity)
    archived = registry.archive_task_stage_session(**identity)

    assert sealed is not None and sealed["status"] == "sealed"
    assert archived is not None and archived["status"] == "archived"


def test_incompatible_stage_binding_archives_and_releases_live_slot(
    tmp_path: Path,
) -> None:
    registry = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml",
        project_root="/project",
    )
    binding = registry.bind_task_stage_session(
        workflow_run_id="run-1",
        task_id="TASK-A",
        stage="impl",
        rework_affinity_id="map-g1:impl",
        role_instance="impl-1",
        role_config_digest="config-sha",
        workspace_generation=1,
        placement_epoch=1,
        backend="mock",
    )
    registry.activate_task_stage_session(
        binding_key=binding["binding_key"],
        role_instance="impl-1",
    )
    event_log = EventLog(tmp_path / "events.jsonl")
    terminate = Mock()
    runtime = SimpleNamespace(
        state_dir=tmp_path,
        project_root=Path("/project"),
        event_log=event_log,
        event_writer=EventWriter(event_log),
        transport=SimpleNamespace(
            is_alive=lambda _instance: True,
            terminate=terminate,
        ),
        _find_role_by_instance=lambda _instance: SimpleNamespace(
            name="impl",
            instance_id="impl-1",
        ),
        _set_worker_state=Mock(),
        _emit_role_lifecycle_event=Mock(),
    )

    assert archive_task_pipeline_stage_binding(
        runtime,
        binding_key=binding["binding_key"],
        task_id="TASK-A",
        causation_id="evt-superseded",
        reason="task_pipeline_entry_mode_mismatch",
    ) is True
    assert archive_task_pipeline_stage_binding(
        runtime,
        binding_key=binding["binding_key"],
        task_id="TASK-A",
        causation_id="evt-superseded",
        reason="task_pipeline_entry_mode_mismatch",
    ) is False

    reloaded = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml",
        project_root="/project",
    )
    assert reloaded.task_stage_bindings()[binding["binding_key"]]["status"] == (
        "archived"
    )
    assert reloaded.instance_meta()["impl-1"][
        "active_task_stage_binding_key"
    ] == ""
    terminate.assert_called_once_with("impl-1")
    assert len([
        event for event in event_log.read_all()
        if event.type == "task.pipeline.stage.session.archived"
    ]) == 1
