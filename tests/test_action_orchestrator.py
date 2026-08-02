from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.action_orchestrator import ControlledActionOrchestrator


def test_controlled_action_attempt_and_result_bind_stable_operation(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    requested = ZfEvent(
        type="runtime.action.requested",
        actor="operator",
        task_id="TASK-1",
        correlation_id="run-1",
        payload={"action": "resume"},
    )

    result = ControlledActionOrchestrator(
        writer=writer,
        actor="zf-cli",
        surface="test",
    ).run(
        action="workflow-resume",
        requested_action="resume",
        payload={
            "operation_id": "wop-resume-TASK-1",
            "request_hash": "a" * 64,
        },
        requested=requested,
        task_id="TASK-1",
        handler=lambda: {"ok": True, "status": "completed", "event_id": "evt-result"},
    )

    events = log.read_all()
    assert [event.type for event in events] == [
        "runtime.action.attempt.started",
        "runtime.action.attempt.completed",
    ]
    assert all(event.payload["operation_id"] == "wop-resume-TASK-1" for event in events)
    assert all(event.payload["request_hash"] == "a" * 64 for event in events)
    assert result["action_result"]["operation_id"] == "wop-resume-TASK-1"
    assert result["action_result"]["request_hash"] == "a" * 64


def test_failed_attempt_preserves_recovery_actionability(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    requested = ZfEvent(
        type="runtime.action.requested",
        actor="operator",
        task_id="TASK-1",
        payload={"action": "kanban-plan-apply"},
    )

    ControlledActionOrchestrator(
        writer=writer,
        actor="zf-cli",
        surface="test",
    ).run(
        action="kanban-plan-apply",
        requested_action="apply",
        payload={},
        requested=requested,
        task_id="TASK-1",
        handler=lambda: {
            "ok": False,
            "status": "workflow_task_stale",
            "actionability": "observation",
            "failure_class": "workflow_task_stale",
            "replacement_plan_event_id": "evt-remint",
            "replacement_revision": 2,
        },
    )

    failed = log.read_all()[-1]
    assert failed.type == "runtime.action.attempt.failed"
    assert failed.payload["actionability"] == "observation"
    assert failed.payload["replacement_plan_event_id"] == "evt-remint"
    assert failed.payload["replacement_revision"] == 2

    from zf.runtime.run_manager import build_run_manager_projection

    projection = build_run_manager_projection(tmp_path, events=log.read_all())
    assert not any(
        action.get("failure_class") == "unknown_actionable_event"
        for action in projection["pending_actions"]
    )
