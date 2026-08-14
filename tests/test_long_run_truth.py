from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.long_run_truth import project_long_run_truth


def _event(
    event_type: str,
    event_id: str,
    *,
    run_id: str = "run-airport",
    payload: dict | None = None,
    task_id: str | None = None,
    causation_id: str | None = None,
) -> ZfEvent:
    body = dict(payload or {})
    if run_id:
        body.setdefault("workflow_run_id", run_id)
    return ZfEvent(
        type=event_type,
        id=event_id,
        task_id=task_id,
        payload=body,
        causation_id=causation_id,
        correlation_id=run_id or None,
    )


def _package(event_id: str, generation: str, digest: str) -> ZfEvent:
    return _event(
        "plan.artifact_package.admitted",
        event_id,
        payload={
            "package_slot": "execution_plan",
            "package_ref": f"plan-packages/{digest}.json",
            "package_digest": digest,
            "plan_revision": generation,
            "task_map_generation": generation,
        },
    )


def test_airport_shape_separates_raw_events_from_unique_current_operations() -> None:
    rows = [
        _event("run.started", "evt-run", payload={"run_id": "run-airport"}),
        _package("evt-old-plan", "map-old", "a" * 64),
        _event(
            "candidate.ready",
            "evt-old-candidate",
            payload={
                "task_map_generation": "map-old",
                "candidate_ref": "candidate/old",
                "candidate_head_commit": "1" * 40,
            },
        ),
        _package("evt-current-plan", "map-current", "b" * 64),
    ]
    rows.extend(
        _event("worker.heartbeat", f"evt-heartbeat-{index}")
        for index in range(42_750)
    )
    rows.extend(
        _event(
            "workflow.operation.requested",
            f"evt-op-replay-{index}",
            payload={
                "operation_id": "wop-verify",
                "operation_type": "agent",
                "request_hash": "c" * 64,
            },
        )
        for index in range(100)
    )
    rows.extend([
        _event(
            "workflow.operation.requested",
            "evt-op-integrate",
            payload={
                "operation_id": "wop-integrate",
                "operation_type": "kernel",
                "request_hash": "d" * 64,
            },
        ),
        _event(
            "workflow.operation.settled",
            "evt-op-integrate-settled",
            payload={
                "operation_id": "wop-integrate",
                "operation_type": "kernel",
                "request_hash": "d" * 64,
            },
        ),
        _event(
            "candidate.ready",
            "evt-current-candidate",
            payload={
                "task_map_generation": "map-current",
                "candidate_ref": "candidate/airport",
                "candidate_head_commit": "2" * 40,
            },
        ),
        _event(
            "judge.passed",
            "evt-judge",
            payload={
                "task_map_generation": "map-current",
                "target_ref": "candidate/airport",
                "target_commit": "2" * 40,
                "commands": [{
                    "tier": "e2e",
                    "command": "docker run mcp/playwright:latest",
                    "exit_code": 0,
                }],
            },
        ),
        _event(
            "ship.completed",
            "evt-ship",
            payload={
                "task_map_generation": "map-current",
                "target_ref": "candidate/airport",
                "target_commit": "2" * 40,
            },
        ),
        _event(
            "owner.visible_message.requested",
            "evt-owner-request",
            payload={
                "message_id": "airport-terminal",
                "message_kind": "run_terminal_delivery",
                "delivery_class": "run_terminal",
            },
        ),
        _event(
            "owner.visible_message.delivered",
            "evt-owner-delivered",
            payload={
                "message_id": "airport-terminal",
                "source_event_id": "evt-owner-request",
                "delivery_id": "delivery-airport",
            },
        ),
    ])

    projection = project_long_run_truth(rows)

    assert projection["status"] == "ready"
    assert projection["current"]["run_id"] == "run-airport"
    assert projection["current"]["task_map_generation"] == "map-current"
    assert projection["current"]["candidate_ref"] == "candidate/airport"
    assert projection["counts"]["raw_events"] > 42_800
    assert projection["counts"]["unique_operations"] == 2
    assert projection["counts"]["raw_events"] > 10_000 * projection["counts"]["unique_operations"]
    assert projection["milestones"]["verified"]["status"] == "proven"
    assert projection["milestones"]["landed"]["status"] == "proven"
    assert projection["milestones"]["reachable"]["status"] == "proven"
    assert projection["milestones"]["owner_notified"]["status"] == "proven"


def test_external_gate_and_no_progress_are_current_authority_only() -> None:
    rows = [
        _event("run.started", "evt-run", payload={"run_id": "run-airport"}),
        _package("evt-plan", "map-current", "b" * 64),
        _event(
            "human.escalate",
            "evt-gate",
            task_id="TASK-MANUAL",
            payload={
                "task_map_generation": "map-current",
                "decision_token": "manual-token",
                "blocker_kind": "external_gate",
                "owner_route": "human",
                "reason": "required_manual_evidence_pending",
                "resolution_event_type": "human.resolved",
            },
        ),
        *[
            _event(
                "run.manager.action.blocked",
                f"evt-blocked-{index}",
                payload={
                    "checkpoint_id": "ck-airport",
                    "reason": "required_manual_evidence_pending",
                },
            )
            for index in range(3)
        ],
    ]

    projection = project_long_run_truth(rows)

    assert projection["gate"] == {
        "status": "blocked",
        "kind": "external_gate",
        "owner": "human",
        "reason": "required_manual_evidence_pending",
        "resume_condition": "human.resolved",
        "event_id": "evt-gate",
        "task_id": "TASK-MANUAL",
    }
    assert projection["no_progress"]["status"] == "tripped"
    assert projection["no_progress"]["items"][0]["fingerprint"] == "ck-airport"
    assert projection["no_progress"]["items"][0]["count"] == 3


def test_delivery_milestones_do_not_infer_other_layers() -> None:
    rows = [
        _event("run.started", "evt-run", payload={"run_id": "run-airport"}),
        _package("evt-plan", "map-current", "b" * 64),
        _event(
            "candidate.ready",
            "evt-candidate",
            payload={
                "task_map_generation": "map-current",
                "candidate_ref": "candidate/airport",
                "candidate_head_commit": "2" * 40,
            },
        ),
        _event(
            "judge.passed",
            "evt-judge",
            payload={
                "task_map_generation": "map-current",
                "target_ref": "candidate/airport",
                "target_commit": "2" * 40,
                "commands": [{
                    "tier": "runtime",
                    "command": "pytest -q",
                    "exit_code": 0,
                }],
            },
        ),
        _event(
            "owner.visible_message.requested",
            "evt-owner-request",
            payload={
                "message_kind": "run_terminal_delivery",
                "delivery_class": "run_terminal",
            },
        ),
        _event(
            "owner.visible_message.delivered",
            "evt-unrelated-delivery",
            payload={"source_event_id": "unknown-alert"},
        ),
    ]

    milestones = project_long_run_truth(rows)["milestones"]

    assert milestones["verified"]["status"] == "proven"
    assert milestones["landed"]["status"] == "unproven"
    assert milestones["reachable"]["status"] == "unproven"
    assert milestones["owner_notified"]["status"] == "unproven"


def test_empty_projection_has_stable_shape() -> None:
    projection = project_long_run_truth([])

    assert projection["schema_version"] == "long-run-truth.v1"
    assert projection["status"] == "empty"
    assert projection["current"]["run_id"] == ""
    assert projection["counts"]["raw_events"] == 0
    assert set(projection["milestones"]) == {
        "verified",
        "landed",
        "reachable",
        "owner_notified",
    }


def test_agent_session_run_cannot_replace_product_workflow_run() -> None:
    provider_run_id = "provider-turn-1"
    rows = [
        ZfEvent(
            type="agent.session.run.started",
            id="evt-provider-started",
            task_id="TASK-PRD",
            correlation_id="trace-provider",
            payload={
                "run_id": provider_run_id,
                "source": "kanban-agent.headless",
            },
        ),
        _event(
            "run.goal.started",
            "evt-goal-started",
            payload={"run_id": "run-airport"},
        ),
        _package("evt-plan", "map-current", "b" * 64),
        _event(
            "candidate.ready",
            "evt-candidate",
            payload={
                "task_map_generation": "map-current",
                "candidate_ref": "candidate/airport",
                "candidate_head_commit": "2" * 40,
            },
        ),
        ZfEvent(
            type="agent.session.run.completed",
            id="evt-provider-completed",
            task_id="TASK-PRD",
            correlation_id="trace-provider",
            payload={
                "run_id": provider_run_id,
                "source": "kanban-agent.headless",
                "status": "completed",
            },
        ),
        _event(
            "run.goal.completed",
            "evt-goal-completed",
            payload={"run_id": "run-airport"},
        ),
    ]

    projection = project_long_run_truth(rows)

    assert projection["current"]["run_id"] == "run-airport"
    assert projection["current"]["run_status"] == "completed"
    assert projection["current"]["candidate_ref"] == "candidate/airport"


def test_terminal_owner_delivery_failure_is_not_a_workflow_gate() -> None:
    rows = [
        _event(
            "run.goal.started",
            "evt-goal-started",
            payload={"run_id": "run-airport"},
        ),
        _package("evt-plan", "map-current", "b" * 64),
        _event(
            "candidate.ready",
            "evt-candidate",
            payload={
                "task_map_generation": "map-current",
                "candidate_ref": "candidate/airport",
                "candidate_head_commit": "2" * 40,
            },
        ),
        _event(
            "run.goal.completed",
            "evt-goal-completed",
            payload={"run_id": "run-airport"},
        ),
        _event(
            "owner.visible_message.requested",
            "evt-owner-request",
            payload={
                "message_kind": "run_terminal_delivery",
                "delivery_class": "run_terminal",
            },
        ),
        _event(
            "owner.visible_message.failed",
            "evt-owner-failed",
            payload={
                "source_event_id": "evt-owner-request",
                "reason": "feishu route not configured",
            },
        ),
        _event(
            "approval.requested",
            "evt-owner-route-approval",
            payload={
                "owner_route": "owner_visible_delivery",
                "source_event_id": "evt-owner-request",
                "reason": "feishu route not configured",
            },
            causation_id="evt-owner-failed",
        ),
    ]

    projection = project_long_run_truth(rows)

    assert projection["current"]["run_status"] == "completed"
    assert projection["gate"]["status"] == "clear"
    assert projection["milestones"]["owner_notified"]["status"] == "unproven"
