"""Three-flow regression for v4 trigger ownership and runtime observation noise."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.drift import DriftDetector
from zf.runtime.goal_idle_driver import maybe_emit_goal_idle_rescan
from zf.runtime.stall_detector import (
    detect_structural_stalls,
    emit_stall_recoveries,
)
from zf.runtime.stillness_auditor import audit_stillness


pytestmark = pytest.mark.mock_e2e
_NOW = datetime(2026, 8, 4, 6, 30, tzinfo=timezone.utc)


class _Writer:
    def __init__(self) -> None:
        self.appended: list[ZfEvent] = []

    def append(self, event: ZfEvent) -> ZfEvent:
        self.appended.append(event)
        return event


def _event(
    event_type: str,
    *,
    event_id: str,
    minutes_ago: float,
    actor: str = "zf-cli",
    causation_id: str | None = None,
    payload: dict | None = None,
) -> ZfEvent:
    return ZfEvent(
        id=event_id,
        type=event_type,
        actor=actor,
        ts=(_NOW - timedelta(minutes=minutes_ago)).isoformat(),
        causation_id=causation_id,
        payload=payload or {},
    )


@pytest.mark.parametrize("flow_kind", ["issue", "prd", "refactor"])
def test_active_operation_owns_trigger_without_recovery_noise(
    flow_kind: str,
) -> None:
    run_id = f"run-{flow_kind}-v4"
    stage_id = f"{flow_kind}-lanes-impl"
    trigger_id = f"evt-{flow_kind}-task-map"
    operation_id = f"wop-{flow_kind}-task-map-admission"
    stages = [(stage_id, "task_map.ready", "lane.stage.completed", flow_kind)]
    events = [
        _event(
            "run.goal.started",
            event_id=f"evt-{flow_kind}-goal",
            minutes_ago=12,
            payload={"run_id": run_id, "objective": f"deliver {flow_kind}"},
        ),
        _event(
            "task_map.ready",
            event_id=trigger_id,
            minutes_ago=10,
            payload={
                "workflow_run_id": run_id,
                "flow_kind": flow_kind,
                "task_map_generation": "map-g1",
            },
        ),
        _event(
            "workflow.operation.requested",
            event_id=f"evt-{flow_kind}-operation",
            minutes_ago=9,
            causation_id=trigger_id,
            payload={
                "workflow_run_id": run_id,
                "operation_id": operation_id,
                "operation_type": "agent",
                "request_hash": "a" * 64,
                "role_instance": "orchestrator",
            },
        ),
    ]
    events.extend(
        _event(
            "role.lifecycle.suspend.rejected",
            event_id=f"evt-{flow_kind}-lifecycle-{index}",
            minutes_ago=8 - index / 10,
            actor="orchestrator",
            payload={
                "role": "impl",
                "instance_id": "impl-1",
                "reason": "provider_operation_active",
            },
        )
        for index in range(8)
    )

    assert detect_structural_stalls(events, stages=stages) == []
    recovery_writer = _Writer()
    assert (
        emit_stall_recoveries(
            events,
            recovery_writer,
            stages=stages,
        )
        == 0
    )
    assert recovery_writer.appended == []

    stillness = audit_stillness(events, now_epoch=_NOW.timestamp())
    assert stillness.state == "active"
    assert stillness.reason == "inflight_workflow_operations"

    goal_state = SimpleNamespace(
        goal_idle_ticks=0,
        goal_last_progress_event_id="",
    )
    goal_config = SimpleNamespace(
        goal=SimpleNamespace(
            enabled=True,
            idle_progress_ticks=2,
            max_rescans=3,
        )
    )
    goal_writer = _Writer()
    for _ in range(5):
        assert (
            maybe_emit_goal_idle_rescan(
                events,
                config=goal_config,
                state=goal_state,
                event_writer=goal_writer,
            )
            == ""
        )
    assert goal_writer.appended == []

    drift_events = [
        {"type": event.type, "actor": event.actor or ""} for event in events
    ]
    assert not any(
        signal.signal == "repeat_decisions"
        for signal in DriftDetector(repeat_threshold=3).check(drift_events)
    )

    settled_events = [
        *events,
        _event(
            "workflow.operation.settled",
            event_id=f"evt-{flow_kind}-settled",
            minutes_ago=1,
            payload={
                "workflow_run_id": run_id,
                "operation_id": operation_id,
                "request_hash": "a" * 64,
            },
        ),
        _event(
            "task.pipeline.generation.admitted",
            event_id=f"evt-{flow_kind}-admitted",
            minutes_ago=0.5,
            payload={
                "workflow_run_id": run_id,
                "trigger_event_id": trigger_id,
                "task_map_generation": "map-g1",
            },
        ),
    ]
    settled_stillness = audit_stillness(
        settled_events,
        now_epoch=_NOW.timestamp(),
    )
    assert settled_stillness.state == "active"
    assert settled_stillness.reason == "no_pending_work"
    assert settled_stillness.breakpoints == []
