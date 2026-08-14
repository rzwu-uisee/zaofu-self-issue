from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.run_manager_rework_triage import (
    pending_immediate_replan_actions,
)


def _manual_task(store: TaskStore, *, status: str = "in_progress") -> None:
    store.add(Task(
        id="TASK-MANUAL",
        title="collect independent evidence",
        status=status,
        contract=TaskContract(
            validation={
                "commands": [
                    {
                        "id": "VC-AUTOMATED",
                        "owner": "task_verify",
                        "tier": "runtime",
                        "acceptance_ids": ["AC8"],
                    },
                    {
                        "id": "VC-MANUAL",
                        "owner": "human",
                        "tier": "manual_evidence",
                        "acceptance_ids": ["AC8"],
                        "producer_paths": ["artifacts/evidence/ac8.json"],
                    },
                ],
            },
            evidence_contract={"manual_evidence": "independent observer"},
        ),
    ))


def _triage_events(*, failed_command_id: str) -> list[ZfEvent]:
    blocked = ZfEvent(
        id="blocked-manual",
        type="dev.blocked",
        task_id="TASK-MANUAL",
        correlation_id="workflow-manual",
        payload={
            "workflow_run_id": "workflow-manual",
            "failure_class": "none",
            "blocker_kind": "none",
            "reason": "required evidence is unavailable in this lane",
            "impl_self_check": {
                "command_receipts": [
                    {
                        "command_id": failed_command_id,
                        "status": "failed",
                        "exit_code": 1,
                    },
                ],
                "acceptance_results": [
                    {"acceptance_id": "AC8", "status": "blocked"},
                ],
            },
        },
    )
    triage = ZfEvent(
        id="triage-manual",
        type="task.rework.triage.completed",
        task_id="TASK-MANUAL",
        correlation_id="workflow-manual",
        payload={
            "failed_event_id": blocked.id,
            "classification": "evidence_payload_gap",
            "recommended_action": "request_evidence_reissue",
            "retryable": False,
            "is_terminal": False,
        },
    )
    return [blocked, triage]


def test_structured_manual_command_failure_routes_to_human_gate(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "kanban.json")
    _manual_task(store)

    actions = pending_immediate_replan_actions(
        _triage_events(failed_command_id="VC-MANUAL"),
        task_store=store,
    )

    assert len(actions) == 1
    assert actions[0]["safe_resume_action"] == "blocked_external_gate"
    assert actions[0]["failure_class"] == "manual_evidence_required"
    assert actions[0]["owner_route"] == "human"
    assert actions[0]["required_evidence_refs"] == [
        "artifacts/evidence/ac8.json",
    ]


def test_automated_command_failure_does_not_impersonate_manual_gate(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "kanban.json")
    _manual_task(store)

    actions = pending_immediate_replan_actions(
        _triage_events(failed_command_id="VC-AUTOMATED"),
        task_store=store,
    )

    assert actions == []


def test_superseded_backlog_task_does_not_open_manual_gate(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "kanban.json")
    _manual_task(store, status="backlog")

    actions = pending_immediate_replan_actions(
        _triage_events(failed_command_id="VC-MANUAL"),
        task_store=store,
    )

    assert actions == []
