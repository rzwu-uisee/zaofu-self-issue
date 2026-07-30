from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.task_attempt_recovery import pending_task_attempt_recovery_actions


def _write_attempts(tmp_path: Path, tasks: dict) -> Path:
    projections = tmp_path / "projections"
    projections.mkdir()
    (projections / "task_attempts.json").write_text(
        json.dumps({
            "schema_version": "shadow-spine.v1",
            "tasks": tasks,
        }),
        encoding="utf-8",
    )
    return projections


def _canonical_attempt(
    store: TaskAttemptStore,
    *,
    run_id: str,
    task_id: str,
    dispatch_id: str,
    status: str,
    recovery_owner: str = "scheduler",
    retryable: bool = True,
) -> dict:
    row = store.ensure_for_dispatch(
        run_id=run_id,
        task_id=task_id,
        dispatch_id=dispatch_id,
        role="dev",
        instance_id=f"dev-{run_id.lower()}",
        operation_id=f"operation-{run_id.lower()}",
        briefing_ref=f"{run_id}.md",
        created_at="2026-07-26T10:00:00+00:00",
        lease_expires_at="2026-07-26T10:10:00+00:00",
        max_attempts=3,
    ).attempt
    updated = store.update(
        row["attempt_id"],
        status=status,
        updated_at="2026-07-26T10:15:00+00:00",
        terminal_event_id=f"terminal-{dispatch_id}",
        failure_reason="failed",
        failure_class=(
            "semantic_result_failed"
            if recovery_owner == "workflow"
            else "transport_delivery"
        ),
        retryable=retryable,
        recovery_owner=recovery_owner,
    )
    assert updated is not None
    return updated


def test_expired_open_attempt_becomes_worker_lifecycle_recover(tmp_path: Path) -> None:
    projections = _write_attempts(tmp_path, {
        "TASK-1": {
            "latest_state": "running",
            "current_owner": "dev-lane-1",
            "open_attempts": 1,
            "counted_failures": 0,
            "attempts": [{
                "attempt_key": "attempt-1",
                "state": "running",
                "role": "dev-lane-1",
                "started_ts": "2026-07-06T20:00:00+00:00",
                "last_heartbeat_ts": "2026-07-06T20:10:00+00:00",
                "source_event_id": "evt-start",
                "lease_token": "lease-1",
                "lease_state": "held",
                "terminal": None,
            }],
        },
    })

    actions = pending_task_attempt_recovery_actions(
        projections,
        now=datetime(2026, 7, 6, 20, 40, tzinfo=timezone.utc),
        lease_grace_s=900,
    )

    assert len(actions) == 1
    action = actions[0]
    assert action["action"] == "worker-lifecycle-recover"
    assert action["safe_resume_action"] == "worker_lifecycle_recover"
    assert action["task_id"] == "TASK-1"
    assert action["instance_id"] == "dev-lane-1"
    assert action["policy_decision"]["decision"] == "auto_decide"
    assert action["preflight"]["status"] == "passed"
    assert action["source_refs"] == ["projections/task_attempts.json#tasks.TASK-1"]


def test_recent_open_attempt_stays_quiet(tmp_path: Path) -> None:
    projections = _write_attempts(tmp_path, {
        "TASK-1": {
            "latest_state": "running",
            "current_owner": "dev-lane-1",
            "attempts": [{
                "attempt_key": "attempt-1",
                "state": "running",
                "role": "dev-lane-1",
                "started_ts": "2026-07-06T20:00:00+00:00",
                "last_heartbeat_ts": "2026-07-06T20:35:00+00:00",
                "source_event_id": "evt-start",
                "terminal": None,
            }],
        },
    })

    assert pending_task_attempt_recovery_actions(
        projections,
        now=datetime(2026, 7, 6, 20, 40, tzinfo=timezone.utc),
        lease_grace_s=900,
    ) == []


def test_recent_provider_activity_takes_precedence_over_old_heartbeat(
    tmp_path: Path,
) -> None:
    projections = _write_attempts(tmp_path, {
        "TASK-1": {
            "latest_state": "running",
            "current_owner": "dev-lane-1",
            "attempts": [{
                "attempt_key": "attempt-1",
                "state": "running",
                "role": "dev-lane-1",
                "started_ts": "2026-07-06T20:00:00+00:00",
                "last_heartbeat_ts": "2026-07-06T20:05:00+00:00",
                "last_activity_ts": "2026-07-06T20:35:00+00:00",
                "source_event_id": "evt-start",
                "terminal": None,
            }],
        },
    })

    assert pending_task_attempt_recovery_actions(
        projections,
        now=datetime(2026, 7, 6, 20, 40, tzinfo=timezone.utc),
        lease_grace_s=900,
    ) == []


def test_retryable_failed_attempt_routes_to_diagnosis(tmp_path: Path) -> None:
    projections = _write_attempts(tmp_path, {
        "TASK-2": {
            "latest_state": "failed",
            "counted_failures": 1,
            "attempts": [{
                "attempt_key": "attempt-2",
                "state": "failed",
                "source_event_id": "evt-start",
                "failure_signature": "task_attempt_failed",
                "retryable": True,
                "terminal": {
                    "type": "task.attempt.failed",
                    "event_id": "evt-failed",
                },
            }],
        },
    })

    actions = pending_task_attempt_recovery_actions(projections)

    assert len(actions) == 1
    action = actions[0]
    assert action["action"] == "diagnose-attention"
    assert action["task_id"] == "TASK-2"
    assert action["failure_class"] == "task_attempt_failed"
    assert action["policy_decision"]["decision"] == "needs_diagnosis"
    assert action["preflight"]["status"] == "passed"
    assert "workflow resume checkpoint is required" in action["reason"]


def test_deadletter_or_exhausted_attempt_routes_to_human(tmp_path: Path) -> None:
    projections = _write_attempts(tmp_path, {
        "TASK-3": {
            "latest_state": "deadlettered",
            "counted_failures": 3,
            "attempts": [{
                "attempt_key": "attempt-3",
                "state": "deadlettered",
                "source_event_id": "evt-start",
                "failure_signature": "task_attempt_failed",
                "retryable": False,
                "terminal": {
                    "type": "task.attempt.deadlettered",
                    "event_id": "evt-dead",
                },
            }],
        },
    })

    actions = pending_task_attempt_recovery_actions(
        projections,
        max_retry_attempts=3,
    )

    assert len(actions) == 1
    action = actions[0]
    assert action["action"] == "diagnose-attention"
    assert action["policy_decision"]["decision"] == "human_escalate"
    assert action["policy_decision"]["executable"] is False
    assert action["intervention_class"] == "safe_halt"


def test_canonical_store_wins_over_conflicting_projection(
    tmp_path: Path,
) -> None:
    projections = _write_attempts(tmp_path, {
        "LEGACY": {
            "latest_state": "failed",
            "counted_failures": 1,
            "attempts": [{
                "attempt_key": "legacy-attempt",
                "state": "failed",
                "retryable": True,
                "terminal": {"event_id": "legacy-failed"},
            }],
        },
    })
    store = TaskAttemptStore(tmp_path / "task_attempts.json")
    canonical = _canonical_attempt(
        store,
        run_id="RUN-1",
        task_id="CANONICAL",
        dispatch_id="dispatch-1",
        status="failed",
    )

    actions = pending_task_attempt_recovery_actions(
        projections,
        canonical_store_path=store.path,
    )

    assert len(actions) == 1
    assert actions[0]["task_id"] == "CANONICAL"
    assert actions[0]["workflow_run_id"] == "RUN-1"
    assert actions[0]["attempt_id"] == canonical["attempt_id"]
    assert actions[0]["source_refs"] == [
        f"task_attempts.json#attempts.{canonical['attempt_id']}"
    ]


def test_canonical_recovery_keeps_same_task_in_distinct_runs(
    tmp_path: Path,
) -> None:
    store = TaskAttemptStore(tmp_path / "task_attempts.json")
    first = _canonical_attempt(
        store,
        run_id="RUN-1",
        task_id="SHARED",
        dispatch_id="dispatch-1",
        status="failed",
    )
    second = _canonical_attempt(
        store,
        run_id="RUN-2",
        task_id="SHARED",
        dispatch_id="dispatch-2",
        status="failed",
    )

    actions = pending_task_attempt_recovery_actions(
        tmp_path / "projections",
        canonical_store_path=store.path,
    )

    assert {item["workflow_run_id"] for item in actions} == {"RUN-1", "RUN-2"}
    assert {item["attempt_id"] for item in actions} == {
        first["attempt_id"],
        second["attempt_id"],
    }
    assert len({item["checkpoint_id"] for item in actions}) == 2


def test_canonical_semantic_failure_stays_owned_by_workflow(
    tmp_path: Path,
) -> None:
    store = TaskAttemptStore(tmp_path / "task_attempts.json")
    _canonical_attempt(
        store,
        run_id="RUN-1",
        task_id="TASK-1",
        dispatch_id="dispatch-1",
        status="failed",
        recovery_owner="workflow",
        retryable=False,
    )

    assert pending_task_attempt_recovery_actions(
        tmp_path / "projections",
        canonical_store_path=store.path,
    ) == []


def test_corrupt_canonical_store_fails_closed_instead_of_projection_fallback(
    tmp_path: Path,
) -> None:
    projections = _write_attempts(tmp_path, {})
    canonical = tmp_path / "task_attempts.json"
    canonical.write_text("{broken", encoding="utf-8")

    actions = pending_task_attempt_recovery_actions(
        projections,
        canonical_store_path=canonical,
    )

    assert len(actions) == 1
    assert actions[0]["failure_class"] == "task_attempt_store_unreadable"
    assert actions[0]["policy_decision"]["decision"] == "human_escalate"
