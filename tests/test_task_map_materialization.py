from __future__ import annotations

import pytest

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract, TaskExecutionBinding
from zf.core.task.store import TaskStore
from zf.runtime.task_map_materialization import (
    commit_task_map_materialization,
    prepare_task_map_materialization,
)


def test_materialization_rolls_forward_after_store_fault(tmp_path):
    state_dir = tmp_path / ".zf"
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    tasks = [
        Task(
            id="A",
            title="A",
            status="backlog",
            contract=TaskContract(behavior="a", verification="true"),
        ),
        Task(
            id="B",
            title="B",
            status="backlog",
            blocked_by=["A"],
            contract=TaskContract(behavior="b", verification="true"),
        ),
    ]
    plan, descriptor = prepare_task_map_materialization(
        state_dir=state_dir,
        tasks=tasks,
        task_map_ref="artifacts/task-map.json",
        package_id="planpkg-package-sha",
        package_ref="artifacts/plan-packages/p.json",
        package_digest="package-sha",
        writer=writer,
    )

    with pytest.raises(RuntimeError, match="injected materialization fault"):
        commit_task_map_materialization(
            state_dir=state_dir,
            plan=plan,
            descriptor=descriptor,
            writer=writer,
            fail_after_store_write=True,
        )

    result = commit_task_map_materialization(
        state_dir=state_dir,
        plan=plan,
        descriptor=descriptor,
        writer=writer,
    )
    replay = commit_task_map_materialization(
        state_dir=state_dir,
        plan=plan,
        descriptor=descriptor,
        writer=writer,
    )

    assert [task.id for task in TaskStore(state_dir / "kanban.json").list_all()] == ["A", "B"]
    assert result["status"] == "committed"
    assert result["plan_artifact_package_id"] == "planpkg-package-sha"
    assert replay == result
    events = writer.event_log.read_all()
    assert sum(event.type == "task_map.materialization.prepared" for event in events) == 1
    assert sum(event.type == "task_map.materialization.committed" for event in events) == 1
    assert sum(event.type == "task.created" for event in events) == 2


def test_materialization_replaces_existing_semantics_without_rolling_back_binding(
    tmp_path,
):
    state_dir = tmp_path / ".zf"
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-REPLAN",
        title="Replanned task",
        contract=TaskContract(
            behavior="R1 behavior",
            verification="R1 verification",
            evidence_contract={
                "contract_revision": "R1",
                "task_map_generation": "G1",
            },
        ),
        execution_binding=TaskExecutionBinding(
            owner="workflow",
            request_id="request-current",
            request_revision=2,
            workflow_run_id="run-current",
        ),
    ))
    r2_task = Task(
        id="TASK-REPLAN",
        title="Replanned task",
        contract=TaskContract(
            behavior="R2 behavior with SENTINEL-AC",
            verification="R2 verification",
            evidence_contract={
                "contract_revision": "R2",
                "task_map_generation": "G2",
            },
        ),
    )
    plan, descriptor = prepare_task_map_materialization(
        state_dir=state_dir,
        tasks=[r2_task],
        task_map_ref="artifacts/task-map-r2.json",
        writer=writer,
    )

    result = commit_task_map_materialization(
        state_dir=state_dir,
        plan=plan,
        descriptor=descriptor,
        writer=writer,
    )

    current = store.get(r2_task.id)
    assert current is not None
    assert result["created_task_ids"] == []
    assert result["skipped_task_ids"] == [r2_task.id]
    assert current.contract.behavior == "R2 behavior with SENTINEL-AC"
    assert current.contract.verification == "R2 verification"
    assert current.contract.evidence_contract["contract_revision"] == "R2"
    assert current.contract.evidence_contract["task_map_generation"] == "G2"
    assert current.execution_binding == TaskExecutionBinding(
        owner="workflow",
        request_id="request-current",
        request_revision=2,
        workflow_run_id="run-current",
    )
    assert current.contract_authority_sequence == 1
    assert current.contract_authority_revision
