from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.state.role_sessions import RoleSessionRegistry


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
