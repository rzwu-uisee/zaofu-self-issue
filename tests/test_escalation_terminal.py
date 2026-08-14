from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.escalation import EscalationManager
from zf.runtime.escalation_terminal import (
    converge_unrecoverable_escalations,
    escalation_terminal_policy,
)
from zf.runtime.goal_dossier_delivery import (
    materialize_terminal_goal_deliveries,
)
from zf.runtime.run_manager import run_manager_tick
from zf.runtime.workflow_anchor import mark_workflow_managed_task


def _state(tmp_path: Path) -> tuple[Path, EventLog, EventWriter]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    return state_dir, log, EventWriter(log)


def _start(run_id: str = "RUN-1") -> ZfEvent:
    return ZfEvent(
        id="evt-run-start",
        type="run.goal.started",
        correlation_id=run_id,
        payload={
            "run_id": run_id,
            "goal_id": "GOAL-1",
            "objective": "deliver the requested change",
        },
    )


def test_run_manager_terminalizes_legacy_replan_cap_once(tmp_path: Path) -> None:
    state_dir, log, writer = _state(tmp_path)
    log.append(_start())
    log.append(ZfEvent(
        id="evt-escalate-prd",
        type="human.escalate",
        payload={
            "reason": (
                "prd.plan.failed: stage replan cap exhausted; "
                "plan output keeps failing admission"
            ),
        },
    ))

    run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        event_log=log,
        auto_execute=False,
        spawn_repairs=False,
    )
    run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        event_log=log,
        auto_execute=False,
        spawn_repairs=False,
    )

    blocked = [event for event in log.read_all() if event.type == "run.goal.blocked"]
    assert len(blocked) == 1
    assert blocked[0].payload["run_id"] == "RUN-1"
    assert blocked[0].payload["recovery_owner"] == "operator"
    assert blocked[0].payload["max_auto_attempts"] == 0
    assert blocked[0].payload["max_rescans"] == 0
    assert blocked[0].payload["evidence_event_ids"] == ["evt-escalate-prd"]
    requested = [
        event for event in log.read_all()
        if event.type == "run.manager.autoresearch.requested"
    ]
    assert len(requested) == 1
    assert requested[0].causation_id == blocked[0].id


def test_replan_cap_orders_terminal_task_block_and_autoresearch(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    store.add(mark_workflow_managed_task(Task(
        id="TASK-PLAN",
        title="Plan workflow",
        status="in_progress",
    )))
    log.append(_start())
    log.append(ZfEvent(
        id="evt-escalate-plan",
        type="human.escalate",
        task_id="TASK-PLAN",
        correlation_id="RUN-1",
        payload={
            "reason": "issue.triage.failed: stage replan cap exhausted",
            "failure_class": "stage_replan_cap_exhausted",
            "failure_scope": "plan_admission",
            "operator_required": True,
            "recoverable": False,
            "source_event_id": "evt-plan-rejected",
        },
    ))

    assert converge_unrecoverable_escalations(
        log.read_all(),
        writer=writer,
        task_store=store,
        request_autoresearch=True,
    ) == 1

    events = log.read_all()
    terminal_index = next(
        index for index, event in enumerate(events)
        if event.type == "run.goal.blocked"
    )
    task_index = next(
        index for index, event in enumerate(events)
        if event.type == "task.status_changed"
        and event.payload.get("to") == "blocked"
    )
    autoresearch_index = next(
        index for index, event in enumerate(events)
        if event.type == "run.manager.autoresearch.requested"
    )
    assert terminal_index < task_index < autoresearch_index
    task = store.get("TASK-PLAN")
    assert task is not None and task.status == "blocked"
    request = events[autoresearch_index]
    assert request.payload["apply_policy"] == "proposal_only"
    assert request.payload["failure_scope"] == "plan_admission"
    assert converge_unrecoverable_escalations(
        log.read_all(),
        writer=writer,
        task_store=store,
        request_autoresearch=True,
    ) == 0
    assert sum(
        event.type == "run.manager.autoresearch.requested"
        for event in log.read_all()
    ) == 1


def test_escalation_manager_stamps_bounded_replan_cap_authority(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    EscalationManager(state_dir).escalate(
        "prd.plan.failed: stage replan cap exhausted",
        task_id="TASK-PLAN",
        metadata={"source_event_id": "evt-plan-rejected"},
    )

    escalation = next(
        event for event in log.read_all() if event.type == "human.escalate"
    )
    assert escalation.payload["failure_class"] == "stage_replan_cap_exhausted"
    assert escalation.payload["failure_scope"] == "plan_admission"
    assert escalation.payload["recovery_owner"] == "operator"
    assert escalation.payload["allowed_actions"] == [
        "operator_review",
        "start_new_generation",
    ]
    assert escalation.payload["max_auto_attempts"] == 0
    assert escalation.payload["max_rescans"] == 0
    assert escalation.payload["terminalization_condition"] == (
        "auto_recovery_exhausted"
    )
    assert escalation.payload["source_event_id"] == "evt-plan-rejected"


def test_invalid_parent_route_terminal_materializes_dossier_and_owner_request(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    log.append(_start("RUN-REFACTOR"))
    log.append(ZfEvent(
        id="evt-plan-cancelled",
        type="plan.admission.cancelled",
        correlation_id="RUN-REFACTOR",
        payload={"workflow_run_id": "RUN-REFACTOR"},
    ))
    log.append(ZfEvent(
        id="evt-escalate-refactor",
        type="human.escalate",
        correlation_id="RUN-REFACTOR",
        causation_id="evt-plan-cancelled",
        payload={
            "failure_class": "plan_admission_failed",
            "failure_scope": "plan_admission",
            "reason": (
                "plan admission rejected and no upstream failure route is "
                "declared; operator recovery is required"
            ),
            "source_event_id": "evt-plan-cancelled",
        },
    ))

    assert converge_unrecoverable_escalations(
        log.read_all(),
        writer=writer,
    ) == 1
    assert converge_unrecoverable_escalations(
        log.read_all(),
        writer=writer,
    ) == 0
    result = materialize_terminal_goal_deliveries(
        state_dir=state_dir,
        event_log=log,
        writer=writer,
        project_id="test-project",
    )

    assert result.materialized == 1
    assert result.requested == 1
    events = log.read_all()
    owner = [
        event
        for event in events
        if event.type == "owner.visible_message.requested"
    ]
    assert len(owner) == 1
    assert owner[0].payload["run_id"] == "RUN-REFACTOR"
    assert (
        state_dir
        / "projections"
        / "goals"
        / "RUN-REFACTOR"
        / "goal-dossier.v1.json"
    ).exists()


def test_recoverable_escalation_does_not_claim_terminal(tmp_path: Path) -> None:
    _state_dir, log, writer = _state(tmp_path)
    log.append(_start())
    log.append(ZfEvent(
        type="human.escalate",
        correlation_id="RUN-1",
        payload={
            "reason": "temporary provider outage",
            "recoverable": True,
            "operator_required": False,
            "recovery_owner": "run_manager",
            "allowed_actions": ["retry_provider"],
            "max_auto_attempts": 2,
        },
    ))

    assert escalation_terminal_policy(log.read_all()[-1].payload) == {}
    assert converge_unrecoverable_escalations(
        log.read_all(),
        writer=writer,
    ) == 0
    assert not any(
        event.type == "run.goal.blocked" for event in log.read_all()
    )


def test_human_resolution_after_escalation_prevents_late_terminal(tmp_path: Path) -> None:
    _state_dir, log, writer = _state(tmp_path)
    log.append(_start())
    log.append(ZfEvent(
        type="human.escalate",
        correlation_id="RUN-1",
        payload={
            "reason": "operator recovery is required",
            "operator_required": True,
            "recoverable": False,
        },
    ))
    log.append(ZfEvent(
        type="human.resolved",
        correlation_id="RUN-1",
        payload={"response": "start a corrected generation"},
    ))

    assert converge_unrecoverable_escalations(
        log.read_all(),
        writer=writer,
    ) == 0


def test_reopened_blocked_run_can_terminalize_a_later_recovery_epoch(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    store.add(mark_workflow_managed_task(Task(
        id="TASK-PLAN",
        title="Plan workflow",
        status="in_progress",
    )))
    log.append(_start())
    first_escalation = ZfEvent(
        id="evt-escalate-first",
        type="human.escalate",
        task_id="TASK-PLAN",
        correlation_id="RUN-1",
        payload={
            "reason": "issue.triage.failed: stage replan cap exhausted",
            "failure_class": "stage_replan_cap_exhausted",
            "operator_required": True,
            "recoverable": False,
        },
    )
    log.append(first_escalation)
    assert converge_unrecoverable_escalations(
        log.read_all(), writer=writer, task_store=store,
    ) == 1
    first_terminal = next(
        event for event in log.read_all()
        if event.type == "run.goal.blocked"
    )
    log.append(ZfEvent(
        id="evt-run-reopened",
        type="run.goal.updated",
        task_id="TASK-PLAN",
        correlation_id="RUN-1",
        payload={"run_id": "RUN-1", "status": "active"},
    ))
    store.update("TASK-PLAN", status="in_progress", blocked_reason="")
    second_escalation = ZfEvent(
        id="evt-escalate-second",
        type="human.escalate",
        task_id="TASK-PLAN",
        correlation_id="RUN-1",
        payload={
            "reason": "issue.triage.failed: stage replan cap exhausted again",
            "failure_class": "stage_replan_cap_exhausted",
            "operator_required": True,
            "recoverable": False,
        },
    )
    log.append(second_escalation)

    assert converge_unrecoverable_escalations(
        log.read_all(), writer=writer, task_store=store,
    ) == 1
    assert converge_unrecoverable_escalations(
        log.read_all(), writer=writer, task_store=store,
    ) == 0
    terminals = [
        event for event in log.read_all()
        if event.type == "run.goal.blocked"
    ]
    assert len(terminals) == 2
    assert terminals[0].id == first_terminal.id
    assert terminals[1].causation_id == second_escalation.id
    assert store.get("TASK-PLAN").status == "blocked"

def test_reopened_run_can_terminalize_a_new_escalation(tmp_path: Path) -> None:
    _state_dir, log, writer = _state(tmp_path)
    log.append(_start())
    log.append(ZfEvent(
        id="evt-old-blocked",
        type="run.goal.blocked",
        correlation_id="RUN-1",
        payload={"run_id": "RUN-1", "status": "blocked"},
    ))
    log.append(ZfEvent(
        id="evt-old-resolved",
        type="human.resolved",
        correlation_id="RUN-1",
        payload={"response": "resume with a larger budget"},
    ))
    log.append(ZfEvent(
        id="evt-new-escalation",
        type="human.escalate",
        correlation_id="RUN-1",
        payload={
            "reason": "prd.plan.failed: stage replan cap exhausted",
            "failure_class": "stage_replan_cap_exhausted",
            "operator_required": True,
            "recoverable": False,
        },
    ))

    assert converge_unrecoverable_escalations(
        log.read_all(),
        writer=writer,
        request_autoresearch=True,
    ) == 1
    assert converge_unrecoverable_escalations(
        log.read_all(),
        writer=writer,
        request_autoresearch=True,
    ) == 0

    blocked = [event for event in log.read_all() if event.type == "run.goal.blocked"]
    assert len(blocked) == 2
    assert blocked[-1].causation_id == "evt-new-escalation"
    requested = [
        event for event in log.read_all()
        if event.type == "run.manager.autoresearch.requested"
    ]
    assert len(requested) == 1
    assert requested[0].causation_id == blocked[-1].id
