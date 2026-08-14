from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.web.server import create_app


def test_run_manager_and_goal_api_are_read_only_projections(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="run.goal.started",
        payload={"run_id": "R-WEB", "objective": "refactor hermes"},
    ))
    client = TestClient(create_app(state_dir, project_root=tmp_path))

    manager = client.get("/api/run-manager")
    goal = client.get("/api/run-goal")

    assert manager.status_code == 200
    assert goal.status_code == 200
    body = manager.json()
    assert body["schema_version"] == "run-manager.v1"
    assert body["goal"]["run_id"] == "R-WEB"
    assert body["monitor"]["schema_version"] == "run-manager.monitor.v1"
    assert body["status_explain"]["schema_version"] == "run-status-explain.v1"
    assert body["completion_profile"]["schema_version"] == "run-completion-profile.v1"
    assert body["repair_merge_queue"]["schema_version"] == "repair-merge-queue.v1"
    assert body["timeline"]["schema_version"] == "run-manager.timeline.v1"
    goal_body = goal.json()
    assert goal_body["objective"] == "refactor hermes"
    assert goal_body["delivery_phase"] == "not_started"
    assert goal_body["open_feedback_count"] == 0
    assert goal_body["pending_handoff_count"] == 0
    assert goal_body["attempt_handoff_schema_version"] == "attempt-handoff-snapshot.v1"


def test_project_scoped_run_manager_api(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    EventLog(state_dir / "events.jsonl").append(ZfEvent(type="loop.started"))
    client = TestClient(create_app(state_dir, project_root=tmp_path))
    project_id = "default"
    projects = client.get("/api/workspace/projects").json()
    if projects.get("projects"):
        project_id = projects["projects"][0]["project_id"]

    response = client.get(f"/api/projects/{project_id}/run-manager")

    assert response.status_code == 200
    assert response.json()["schema_version"] == "run-manager.v1"


def test_state_api_reconciles_residual_tasks_after_run_completed(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-R4-GAP",
        title="residual gap",
        status="in_progress",
        assigned_to="verify-lane-0",
    ))
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="run.completed",
        actor="run-manager",
        payload={"status": "passed", "release_status": "not_shipped"},
    ))
    client = TestClient(create_app(state_dir, project_root=tmp_path))

    response = client.get("/api/state")

    assert response.status_code == 200
    task = response.json()["tasks"][0]
    assert task["status"] == "in_progress"
    assert task["display_status"] == "done"
    assert task["kanban_column"] == "done"
    assert task["projection_reconciled"] is True
    assert task["projection_reconcile_reason"] == "run_completed"
    assert task["effective_terminal"] is True
    assert task["canonical_drift"] is True
    assert task["attention"]["required"] is False
    assert task["task_card"]["schema_version"] == "task-card.v1"
    assert task["task_card"]["lifecycle"] == {
        "canonical_status": "in_progress",
        "display_status": "done",
        "effective_terminal": True,
        "outcome": "success",
        "reconciled": True,
        "canonical_drift": True,
    }
    assert task["task_card"]["current_stage"] is None
    assert TaskStore(state_dir / "kanban.json").get("TASK-R4-GAP").status == "in_progress"


def test_state_api_reconciles_only_goal_completed_task_ids(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-GOAL-DONE",
        title="completed by goal",
        status="in_progress",
        assigned_to="verify-lane-0",
    ))
    store.add(Task(
        id="TASK-GOAL-SIBLING",
        title="not completed by goal",
        status="in_progress",
        assigned_to="dev-lane-0",
    ))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="verify.failed",
        task_id="TASK-GOAL-DONE",
        payload={"reason": "historical failure"},
    ))
    terminal = ZfEvent(
        type="run.goal.completed",
        actor="zf-cli",
        correlation_id="RUN-GOAL-WEB",
        payload={
            "run_id": "RUN-GOAL-WEB",
            "workflow_run_id": "RUN-GOAL-WEB",
            "completed_task_ids": ["TASK-GOAL-DONE"],
        },
    )
    log.append(terminal)
    client = TestClient(create_app(state_dir, project_root=tmp_path))

    response = client.get("/api/state")

    assert response.status_code == 200
    tasks = {item["id"]: item for item in response.json()["tasks"]}
    completed = tasks["TASK-GOAL-DONE"]
    sibling = tasks["TASK-GOAL-SIBLING"]
    assert completed["status"] == "in_progress"
    assert completed["display_status"] == "done"
    assert completed["kanban_column"] == "done"
    assert completed["projection_reconciled"] is True
    assert completed["projection_reconcile_reason"] == "run_goal_completed"
    assert completed["projection_reconcile_event_id"] == terminal.id
    assert sibling["display_status"] == "in_progress"
    assert sibling["projection_reconciled"] is False
    assert store.get("TASK-GOAL-DONE").status == "in_progress"


def test_state_api_resets_goal_completion_projection_for_contract_update(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-REUSED",
        title="reused task",
        status="in_progress",
        assigned_to="dev-lane-0",
    ))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="run.goal.completed",
        actor="zf-cli",
        correlation_id="RUN-OLD",
        payload={
            "run_id": "RUN-OLD",
            "workflow_run_id": "RUN-OLD",
            "completed_task_ids": ["TASK-REUSED"],
        },
    ))
    log.append(ZfEvent(
        type="task.contract.update",
        actor="zf-cli",
        task_id="TASK-REUSED",
        correlation_id="RUN-NEW",
        payload={
            "run_id": "RUN-NEW",
            "old_task_map_ref": "artifacts/task-map-g1.json",
            "new_task_map_ref": "artifacts/task-map-g2.json",
        },
    ))
    client = TestClient(create_app(state_dir, project_root=tmp_path))

    task = client.get("/api/state").json()["tasks"][0]

    assert task["status"] == "in_progress"
    assert task["display_status"] == "in_progress"
    assert task["kanban_column"] == "in_progress"
    assert task["projection_reconciled"] is False


def test_state_api_rejects_late_goal_terminal_for_replanned_task(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-REUSED",
        title="current generation",
        status="in_progress",
        assigned_to="dev-lane-0",
        contract=TaskContract(
            feature_id="RUN-REPLAN",
            evidence_contract={
                "source": "refactor_task_map",
                "source_refs": {
                    "task_map_ref": "artifacts/task-map-g2.json",
                    "task_map_generation": "G2",
                },
            },
        ),
    ))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="task.contract.update",
        actor="zf-cli",
        task_id="TASK-REUSED",
        correlation_id="RUN-REPLAN",
        payload={
            "workflow_run_id": "RUN-REPLAN",
            "new_task_map_ref": "artifacts/task-map-g2.json",
        },
    ))
    log.append(ZfEvent(
        type="run.goal.completed",
        actor="zf-cli",
        correlation_id="RUN-REPLAN",
        payload={
            "run_id": "RUN-REPLAN",
            "workflow_run_id": "RUN-REPLAN",
            "completed_task_ids": ["TASK-REUSED"],
            "task_map_ref": "artifacts/task-map-g1.json",
            "task_map_generation": "G1",
        },
    ))
    client = TestClient(create_app(state_dir, project_root=tmp_path))

    task = client.get("/api/state").json()["tasks"][0]

    assert task["status"] == "in_progress"
    assert task["display_status"] == "in_progress"
    assert task["kanban_column"] == "in_progress"
    assert task["projection_reconciled"] is False
