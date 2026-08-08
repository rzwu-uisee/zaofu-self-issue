from pathlib import Path

from zf.core.state.task_attempts import (
    TASK_ATTEMPT_IDENTITY_OPERATION_V2,
    TaskAttemptStore,
)
from zf.runtime.task_pipeline_identity import task_pipeline_operation_identity


def _ensure(
    store: TaskAttemptStore,
    *,
    dispatch_id: str,
    role: str,
    instance_id: str,
    operation_id: str,
    identity_version: str = TASK_ATTEMPT_IDENTITY_OPERATION_V2,
    placement_epoch: int = 1,
):
    return store.ensure_for_dispatch(
        run_id="run-1",
        task_id="TASK-A",
        dispatch_id=dispatch_id,
        role=role,
        instance_id=instance_id,
        operation_id=operation_id,
        briefing_ref="briefings/task-a.md",
        created_at=f"2026-08-03T00:00:0{placement_epoch}Z",
        lease_expires_at="2026-08-03T00:10:00Z",
        max_attempts=3,
        identity_version=identity_version,
        placement_epoch=placement_epoch,
    )


def test_task_pipeline_operation_identity_separates_rework_from_placement() -> None:
    first = task_pipeline_operation_identity(
        workflow_run_id="run-1",
        task_id="TASK-A",
        task_map_generation="map-7",
        stage="impl",
        stage_revision="contract-3",
        operation_generation=1,
    )
    replay = task_pipeline_operation_identity(
        workflow_run_id="run-1",
        task_id="TASK-A",
        task_map_generation="map-7",
        stage="impl",
        stage_revision="contract-3",
        operation_generation=1,
    )
    rework = task_pipeline_operation_identity(
        workflow_run_id="run-1",
        task_id="TASK-A",
        task_map_generation="map-7",
        stage="impl",
        stage_revision="contract-3",
        operation_generation=2,
    )

    assert replay == first
    assert rework.pipeline_key == first.pipeline_key
    assert rework.operation_key != first.operation_key
    assert rework.operation_id != first.operation_id
    assert "impl" in first.operation_key
    assert "worker" not in first.operation_key


def test_operation_scoped_attempt_relocation_supersedes_prior_placement(
    tmp_path: Path,
) -> None:
    store = TaskAttemptStore(tmp_path / "task_attempts.json")

    first = _ensure(
        store,
        dispatch_id="dispatch-1",
        role="dev-frontend",
        instance_id="dev-frontend-1",
        operation_id="wop-task-a-impl-1",
        placement_epoch=1,
    )
    relocated = _ensure(
        store,
        dispatch_id="dispatch-2",
        role="dev-backend",
        instance_id="dev-backend-1",
        operation_id="wop-task-a-impl-1",
        placement_epoch=2,
    )

    assert first.attempt["attempt_key"] == relocated.attempt["attempt_key"]
    assert relocated.superseded_attempt_id == first.attempt["attempt_id"]
    assert store.get(first.attempt["attempt_id"])["status"] == "superseded"
    assert relocated.attempt["identity_version"] == "operation-v2"
    assert relocated.attempt["placement_epoch"] == 2
    assert relocated.attempt["instance_id"] == "dev-backend-1"


def test_operation_scoped_transport_retry_and_semantic_rework_are_distinct(
    tmp_path: Path,
) -> None:
    store = TaskAttemptStore(tmp_path / "task_attempts.json")
    first = _ensure(
        store,
        dispatch_id="dispatch-1",
        role="dev",
        instance_id="dev-1",
        operation_id="wop-task-a-impl-g1",
        placement_epoch=1,
    )
    retry = _ensure(
        store,
        dispatch_id="dispatch-2",
        role="dev",
        instance_id="dev-2",
        operation_id="wop-task-a-impl-g1",
        placement_epoch=2,
    )
    rework = _ensure(
        store,
        dispatch_id="dispatch-3",
        role="dev",
        instance_id="dev-1",
        operation_id="wop-task-a-impl-g2",
        placement_epoch=3,
    )

    assert retry.attempt["attempt_key"] == first.attempt["attempt_key"]
    assert retry.attempt["ordinal"] == 2
    assert rework.attempt["attempt_key"] != retry.attempt["attempt_key"]
    assert rework.attempt["ordinal"] == 1


def test_default_v1_attempt_identity_remains_role_scoped(tmp_path: Path) -> None:
    store = TaskAttemptStore(tmp_path / "task_attempts.json")
    first = _ensure(
        store,
        dispatch_id="dispatch-1",
        role="dev-frontend",
        instance_id="dev-frontend-1",
        operation_id="wop-legacy",
        identity_version="role-v1",
        placement_epoch=0,
    )
    second = _ensure(
        store,
        dispatch_id="dispatch-2",
        role="dev-backend",
        instance_id="dev-backend-1",
        operation_id="wop-legacy",
        identity_version="role-v1",
        placement_epoch=0,
    )

    assert first.attempt["attempt_key"] != second.attempt["attempt_key"]
    assert second.superseded_attempt_id == ""
