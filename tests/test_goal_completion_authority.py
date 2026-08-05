from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.goal_completion_authority import (
    active_fanout_ids_for_authority,
    scope_handoff_snapshot,
)
from zf.runtime.run_manager import run_goal_completion_gate_event


def _handoff_snapshot() -> dict:
    return {
        "delivery_phase": "feedback_resolution_claimed",
        "open_feedback_count": 3,
        "pending_handoff_count": 3,
        "open_feedback": [
            {
                "finding_id": "old-explicit",
                "request_event_id": "rework-old-explicit",
                "task_id": "TASK-CURRENT",
                "status": "acknowledged",
            },
            {
                "finding_id": "old-legacy",
                "request_event_id": "rework-old-legacy",
                "task_id": "TASK-OLD",
                "status": "acknowledged",
            },
            {
                "finding_id": "current",
                "request_event_id": "rework-current",
                "task_id": "TASK-CURRENT",
                "status": "acknowledged",
            },
        ],
        "pending_handoffs": [
            {
                "request_event_id": "rework-old-explicit",
                "task_id": "TASK-CURRENT",
                "task_map_generation": "task-map-old",
                "status": "acknowledged",
            },
            {
                "request_event_id": "rework-old-legacy",
                "task_id": "TASK-OLD",
                "task_map_generation": "",
                "status": "acknowledged",
            },
            {
                "request_event_id": "rework-current",
                "task_id": "TASK-CURRENT",
                "task_map_generation": "task-map-current",
                "status": "acknowledged",
            },
        ],
        "active_attempts": [],
        "accepted_results": [],
    }


def test_scope_handoff_snapshot_prefers_explicit_generation_over_reused_task_id() -> None:
    scoped = scope_handoff_snapshot(
        _handoff_snapshot(),
        task_map_generation="task-map-current",
        candidate_task_ids=["TASK-CURRENT"],
    )

    assert [row["request_event_id"] for row in scoped["pending_handoffs"]] == [
        "rework-current"
    ]
    assert [row["finding_id"] for row in scoped["open_feedback"]] == ["current"]
    assert scoped["historical_open_feedback_count"] == 2
    assert scoped["historical_pending_handoff_count"] == 2


def test_scope_handoff_snapshot_keeps_legacy_current_candidate_task() -> None:
    snapshot = _handoff_snapshot()
    snapshot["pending_handoffs"][-1]["task_map_generation"] = ""

    scoped = scope_handoff_snapshot(
        snapshot,
        task_map_generation="task-map-current",
        candidate_task_ids=["TASK-CURRENT"],
    )

    assert [row["request_event_id"] for row in scoped["pending_handoffs"]] == [
        "rework-current"
    ]


def test_active_fanouts_ignore_only_proven_old_generation() -> None:
    events = [
        ZfEvent(
            type="fanout.started",
            payload={
                "fanout_id": "fanout-old",
                "trigger_payload": {"task_map_generation": "task-map-old"},
            },
        ),
        ZfEvent(
            type="fanout.started",
            payload={
                "fanout_id": "fanout-current",
                "task_map_generation": "task-map-current",
            },
        ),
        ZfEvent(type="fanout.started", payload={"fanout_id": "fanout-unknown"}),
    ]

    active, historical = active_fanout_ids_for_authority(
        events,
        task_map_generation="task-map-current",
    )

    assert active == ["fanout-current", "fanout-unknown"]
    assert historical == ["fanout-old"]


def test_completion_gate_ignores_prior_generation_handoff_and_fanout() -> None:
    run_id = "RUN-CURRENT-AUTHORITY"
    target = "a" * 40
    events = [
        ZfEvent(type="run.goal.started", payload={"run_id": run_id}),
        ZfEvent(
            id="rework-old",
            type="task.rework.requested",
            task_id="TASK-OLD",
            correlation_id=run_id,
            payload={
                "workflow_run_id": run_id,
                "task_id": "TASK-OLD",
                "task_map_generation": "task-map-old",
                "finding_ids": ["finding-old"],
            },
        ),
        ZfEvent(
            type="fanout.started",
            correlation_id=run_id,
            payload={
                "fanout_id": "fanout-old",
                "trigger_payload": {"task_map_generation": "task-map-old"},
            },
        ),
        ZfEvent(
            type="candidate.ready",
            correlation_id=run_id,
            payload={
                "workflow_run_id": run_id,
                "candidate_head_commit": target,
                "completed_task_ids": ["TASK-CURRENT"],
                "task_map_generation": "task-map-current",
            },
        ),
        ZfEvent(
            type="verify.passed",
            correlation_id=run_id,
            payload={
                "workflow_run_id": run_id,
                "target_commit": target,
                "task_map_generation": "task-map-current",
            },
        ),
    ]
    claim = ZfEvent(
        id="claim-current",
        type="run.goal.completion.claimed",
        correlation_id=run_id,
        payload={
            "run_id": run_id,
            "claim_id": "claim-current",
            "target_commit": target,
            "task_map_generation": "task-map-current",
        },
    )

    outcome = run_goal_completion_gate_event([*events, claim], claim=claim)

    assert outcome is not None
    assert outcome.type == "run.goal.completed"
    assert outcome.payload["historical_open_feedback_count"] == 1
    assert outcome.payload["historical_pending_handoff_count"] == 1
    assert outcome.payload["historical_active_fanout_ids"] == ["fanout-old"]


def test_completion_gate_keeps_current_generation_handoff_blocking() -> None:
    run_id = "RUN-CURRENT-BLOCKER"
    claim = ZfEvent(
        id="claim-current",
        type="run.goal.completion.claimed",
        correlation_id=run_id,
        payload={
            "run_id": run_id,
            "claim_id": "claim-current",
            "task_map_generation": "task-map-current",
        },
    )
    events = [
        ZfEvent(type="run.goal.started", payload={"run_id": run_id}),
        ZfEvent(
            id="rework-current",
            type="task.rework.requested",
            task_id="TASK-CURRENT",
            correlation_id=run_id,
            payload={
                "workflow_run_id": run_id,
                "task_id": "TASK-CURRENT",
                "task_map_generation": "task-map-current",
                "finding_ids": ["finding-current"],
            },
        ),
        claim,
    ]

    outcome = run_goal_completion_gate_event(events, claim=claim)

    assert outcome is not None
    assert outcome.type == "run.goal.completion.blocked"
    assert outcome.payload["blockers"] == ["open_feedback", "pending_handoff"]
