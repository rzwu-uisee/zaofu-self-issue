from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import (
    FanoutAggregateConfig,
    ProjectConfig,
    RoleConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.sidecar_refs import write_sidecar_text
from zf.runtime.workflow_anchor import (
    bind_workflow_request_to_task,
    mark_workflow_managed_task,
)
from zf.runtime.workflow_task_lifecycle import (
    RESEARCH_TASK_COMPLETION_SOURCE,
    WORKFLOW_TASK_ACTIVATION_SOURCE,
    complete_standalone_research_task,
)


class _RecordingTransport:
    def send_task(
        self,
        role_name,
        briefing_path,
        prompt,
        *,
        context=None,
    ):  # noqa: ANN001
        return None

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


def _config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="workflow-task-lifecycle"),
        roles=[
            RoleConfig(
                name="research-reader",
                backend="mock",
                role_kind="reader",
            ),
        ],
        workflow=WorkflowConfig(
            stages=[
                WorkflowStageConfig(
                    id="research-fanout",
                    trigger="workflow.invoke.requested",
                    topology="fanout_reader",
                    roles=["research-reader"],
                    aggregate=FanoutAggregateConfig(
                        mode="wait_for_all",
                        success_event="research.approved",
                        failure_event="research.rejected",
                    ),
                ),
            ],
        ),
    )


def _runtime(
    tmp_path: Path,
    *,
    workflow_managed: bool,
) -> tuple[Orchestrator, EventLog, TaskStore]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    store = TaskStore(state_dir / "kanban.json")
    task = Task(id="TASK-RESEARCH", title="Research the delivery decision")
    if workflow_managed:
        mark_workflow_managed_task(task)
    store.add(task)
    log = EventLog(state_dir / "events.jsonl")
    runtime = Orchestrator(
        state_dir,
        _config(),
        _RecordingTransport(),  # type: ignore[arg-type]
        project_root=tmp_path,
    )
    return runtime, log, store


def _invoke() -> ZfEvent:
    return ZfEvent(
        type="workflow.invoke.requested",
        actor="web",
        task_id="TASK-RESEARCH",
        correlation_id="run-research-1",
        payload={
            "task_id": "TASK-RESEARCH",
            "pattern_id": "research-fanout",
            "workflow_run_id": "run-research-1",
            "requested_by": "kanban-agent",
            "reason": "approved research start",
        },
    )


def test_accepted_workflow_activates_managed_task_once(
    tmp_path: Path,
) -> None:
    runtime, log, store = _runtime(tmp_path, workflow_managed=True)
    invoke = _invoke()

    runtime.run_once(events=[invoke])

    task = store.get("TASK-RESEARCH")
    assert task is not None
    assert task.status == "in_progress"
    events = log.read_all()
    accepted = next(
        event
        for event in events
        if event.type == "workflow.invoke.accepted"
    )
    status_events = [
        event
        for event in events
        if event.type == "task.status_changed"
        and event.payload.get("source")
        == WORKFLOW_TASK_ACTIVATION_SOURCE
    ]
    assert len(status_events) == 1
    assert status_events[0].causation_id == accepted.id

    store.update("TASK-RESEARCH", status="backlog")
    restarted = Orchestrator(
        runtime.state_dir,
        _config(),
        _RecordingTransport(),  # type: ignore[arg-type]
        project_root=tmp_path,
    )
    restarted.run_once(events=[invoke])

    assert store.get("TASK-RESEARCH").status == "in_progress"  # type: ignore[union-attr]
    assert sum(
        event.type == "task.status_changed"
        and event.payload.get("source")
        == WORKFLOW_TASK_ACTIVATION_SOURCE
        for event in log.read_all()
    ) == 1


def test_accepted_workflow_does_not_take_over_ordinary_task(
    tmp_path: Path,
) -> None:
    runtime, log, store = _runtime(tmp_path, workflow_managed=False)

    runtime.run_once(events=[_invoke()])

    assert store.get("TASK-RESEARCH").status == "in_progress"  # type: ignore[union-attr]
    assert not any(
        event.type == "task.status_changed"
        and event.payload.get("source")
        == WORKFLOW_TASK_ACTIVATION_SOURCE
        for event in log.read_all()
    )


def _research_result(
    tmp_path: Path,
    *,
    request_bound: bool = False,
) -> tuple[TaskStore, EventWriter, ZfEvent]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    store = TaskStore(state_dir / "kanban.json")
    task = mark_workflow_managed_task(Task(
        id="TASK-RESEARCH",
        title="Standalone research",
        status="in_progress",
    ))
    if request_bound:
        task = bind_workflow_request_to_task(
            task,
            request_id="REQ-1",
            request_revision=1,
            origin_binding_digest="a" * 64,
        )
    store.add(task)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    descriptor = write_sidecar_text(
        state_dir,
        "artifacts/research/TASK-RESEARCH/result.md",
        "# Research\n\nComplete.\n",
        kind="research_report",
        schema_version="research-report.v1",
        created_by="test",
        required=True,
    )
    descriptor.update({
        "task_id": task.id,
        "stage_id": "research-adaptive",
        "fanout_id": "fanout-1",
        "workflow_run_id": "run-1",
    })
    terminal = writer.emit(
        "fanout.aggregate.completed",
        actor="zf-cli",
        task_id=task.id,
        correlation_id="run-1",
        payload={
            "fanout_id": "fanout-1",
            "stage_id": "research-adaptive",
            "status": "completed",
            "artifact_refs": [descriptor],
        },
    )
    result = writer.emit(
        "workflow.result.available",
        actor="zf-cli",
        task_id=task.id,
        causation_id=terminal.id,
        correlation_id="run-1",
        payload={
            "schema_version": "workflow-result.v1",
            "result_kind": "research_report",
            "status": "available",
            "task_id": task.id,
            "workflow_run_id": "run-1",
            "terminal_event_id": terminal.id,
            "artifact_ref": descriptor["ref"],
            "artifact_digest": descriptor["sha256"],
        },
    )
    return store, writer, result


def test_standalone_research_closes_once_after_bound_artifact(
    tmp_path: Path,
) -> None:
    store, writer, result = _research_result(tmp_path)

    first = complete_standalone_research_task(
        task_store=store,
        event_writer=writer,
        result_event=result,
    )
    replay = complete_standalone_research_task(
        task_store=store,
        event_writer=writer,
        result_event=result,
    )

    assert first is not None
    assert first.status == "done"
    assert replay is None
    assert store.get("TASK-RESEARCH").status == "done"  # type: ignore[union-attr]
    events = writer.event_log.read_all()
    assert sum(
        event.type == "task.status_changed"
        and event.payload.get("source")
        == RESEARCH_TASK_COMPLETION_SOURCE
        for event in events
    ) == 1
    assert sum(
        event.type == "task.done.evidence"
        and event.payload.get("source")
        == RESEARCH_TASK_COMPLETION_SOURCE
        for event in events
    ) == 1


def test_request_bound_research_keeps_parent_task_active(
    tmp_path: Path,
) -> None:
    store, writer, result = _research_result(
        tmp_path,
        request_bound=True,
    )

    completed = complete_standalone_research_task(
        task_store=store,
        event_writer=writer,
        result_event=result,
    )

    assert completed is None
    assert store.get("TASK-RESEARCH").status == "in_progress"  # type: ignore[union-attr]
    assert not any(
        event.type == "task.done.evidence"
        for event in writer.event_log.read_all()
    )
