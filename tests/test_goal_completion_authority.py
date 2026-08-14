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


def test_provider_qualification_wait_is_idempotent_and_not_semantic_rework() -> None:
    run_id = "RUN-PROVIDER-WAIT"
    target = "c" * 40
    candidate_ref = "candidate/provider-wait"
    generation = "generation-provider-wait"
    closure_fields = {
        "workflow_run_id": run_id,
        "goal_id": "GOAL-PROVIDER-WAIT",
        "task_map_generation": generation,
        "candidate_head_commit": target,
        "goal_claim_set_digest": "a" * 64,
        "closure_fact_digest": "b" * 64,
        "product_acceptance_spec_digest": "d" * 64,
        "product_acceptance_report_digest": "e" * 64,
        "product_acceptance_required": True,
        "product_acceptance_verdict": "passed",
        "provider_qualification_required": True,
        "provider_qualification_status": "waiting_external",
    }
    admitted_ref = {
        "ref": "artifacts/call-results/envelopes/" + "f" * 64 + ".json",
        "sha256": "f" * 64,
    }
    events = [
        ZfEvent(type="run.goal.started", payload={"run_id": run_id}),
        ZfEvent(
            type="candidate.ready",
            correlation_id=run_id,
            payload={
                "workflow_run_id": run_id,
                "candidate_ref": candidate_ref,
                "candidate_head_commit": target,
                "task_map_generation": generation,
                "completed_task_ids": ["TASK-1"],
            },
        ),
        ZfEvent(
            type="fanout.child.completed",
            correlation_id=run_id,
            payload={
                "workflow_run_id": run_id,
                "candidate_ref": candidate_ref,
                "target_commit": target,
                "task_map_generation": generation,
                "control_result_schema": "verification-result.v1",
                "semantic_verdict": "passed",
                "admitted_call_result_ref": admitted_ref,
            },
        ),
        ZfEvent(type="flow.goal.closed", correlation_id=run_id, payload=closure_fields),
    ]
    claim = ZfEvent(
        id="claim-provider-wait",
        type="run.goal.completion.claimed",
        correlation_id=run_id,
        payload={
            "run_id": run_id,
            "goal_id": "GOAL-PROVIDER-WAIT",
            "claim_id": "claim-provider-wait",
            "claim_type": "admitted_goal_closure_result",
            "target_commit": target,
            "candidate_ref": candidate_ref,
            "task_map_generation": generation,
            "goal_claim_set_ref": "artifacts/goal-claims/current.json",
            "goal_claim_set_digest": "a" * 64,
            "closure_fact_ref": "artifacts/goal-closure/current.json",
            "closure_fact_digest": "b" * 64,
            "admitted_call_result_ref": admitted_ref,
            "product_acceptance_required": True,
            "product_acceptance_spec_ref": "artifacts/product/spec.json",
            "product_acceptance_spec_digest": "d" * 64,
            "product_acceptance_report_ref": "artifacts/product/report.json",
            "product_acceptance_report_digest": "e" * 64,
            "product_acceptance_verdict": "passed",
            "provider_qualification_required": True,
            "provider_qualification_status": "waiting_external",
        },
    )

    first = run_goal_completion_gate_event([*events, claim], claim=claim)

    assert first is not None
    assert first.type == "run.goal.completion.blocked"
    assert first.payload["blockers"] == ["waiting_external_provider"]
    assert first.payload["external_wait"] == {
        "kind": "provider_qualification",
        "status": "waiting_external",
        "semantic_attempt_incremented": False,
    }
    assert run_goal_completion_gate_event(
        [*events, claim, first],
        claim=claim,
    ) is None

    stale_claim = ZfEvent(
        id="claim-provider-stale-report",
        type="run.goal.completion.claimed",
        correlation_id=run_id,
        payload={
            **claim.payload,
            "claim_id": "claim-provider-stale-report",
            "product_acceptance_report_digest": "9" * 64,
        },
    )
    stale = run_goal_completion_gate_event(
        [*events, stale_claim],
        claim=stale_claim,
    )

    assert stale is not None
    assert stale.type == "run.goal.completion.rejected"
    assert "stale_product_acceptance_report_digest" in stale.payload[
        "invalid_reasons"
    ]
