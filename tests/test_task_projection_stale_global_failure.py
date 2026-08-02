"""Web task projection: a global failure that predates a task must not be
attributed to it.

feishu e2e regression: a ZaoFu project reused across many rounds accumulates
stale ``prd.blocked`` / candidate failures from earlier runs (task_id=None,
matched only by a shared/empty context ref such as feature_id). Without a
temporal guard, ``_workflow_events_with_candidate_context`` injects a phantom
``review.rejected`` onto a brand-new task -> verify_state=failed -> the webkanban
card shows "blocked" while the task is actually in_progress. The guard: a global
failure whose append-order seq is BEFORE the task's first event cannot be its
failure.
"""

from __future__ import annotations

from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.web.projections.tasks import _workflow_events_with_candidate_context


def _task() -> Task:
    return Task(
        id="TASK-RACING",
        title="three.js racing demo",
        status="in_progress",
        assigned_to="prd-author",
        contract=TaskContract(feature_id="F-RACING"),
    )


def _created() -> ZfEvent:
    return ZfEvent(
        type="task.created", actor="zf-cli", task_id="TASK-RACING",
        payload={"task_id": "TASK-RACING"},
    )


def _prd_blocked(reason: str) -> ZfEvent:
    # task_id=None, matched to the task only by the shared feature_id ref.
    return ZfEvent(
        type="prd.blocked", actor="zf-cli",
        payload={"feature_id": "F-RACING", "reason": reason},
    )


def test_global_failure_before_task_creation_not_attributed(tmp_path: Path):
    stale = _prd_blocked("stale failure from an earlier round")
    created = _created()
    all_events = [(0, stale), (1, created)]  # stale precedes the task
    task_events = [(1, created)]

    out = _workflow_events_with_candidate_context(
        _task(), task_events, all_events, state_dir=tmp_path,
    )

    assert not [e for _, e in out if e.type == "review.rejected"]


def test_global_failure_during_task_lifecycle_is_attributed(tmp_path: Path):
    """Control: the temporal guard must not silence a real, current failure that
    happens after the task's first event."""
    created = _created()
    fresh = _prd_blocked("real failure in this task's own run")
    all_events = [(0, created), (1, fresh)]  # failure during the task
    task_events = [(0, created)]

    out = _workflow_events_with_candidate_context(
        _task(), task_events, all_events, state_dir=tmp_path,
    )

    assert [e for _, e in out if e.type == "review.rejected"]


def _verify_child_failed(
    *,
    event_type: str = "verify.child.failed",
    workflow_run_id: str = "workflow-current",
) -> ZfEvent:
    return ZfEvent(
        type=event_type,
        actor="verify-lane-0",
        payload={
            "stage_id": "prd-lanes-verify",
            "task_id": "TASK-RACING",
            "workflow_run_id": workflow_run_id,
            "reason": "current verification failure",
        },
        correlation_id=workflow_run_id,
    )


def _rework_child_dispatched(*, workflow_run_id: str = "workflow-current") -> ZfEvent:
    return ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-orchestrator",
        payload={
            "fanout_id": "fanout-prd-lanes-impl-rework",
            "stage_id": "prd-lanes-impl",
            "task_id": "TASK-RACING",
            "workflow_run_id": workflow_run_id,
        },
        correlation_id=workflow_run_id,
    )


def test_verify_child_failure_is_superseded_by_later_rework_attempt(tmp_path: Path):
    """A child failure is normalized to verify.failed before supersession."""
    created = _created()
    failed = _verify_child_failed()
    transport_failed = _verify_child_failed(event_type="fanout.child.failed")
    rework = _rework_child_dispatched()
    task_events = [(0, created), (1, failed), (2, transport_failed), (3, rework)]

    out = _workflow_events_with_candidate_context(
        _task(),
        task_events,
        task_events,
        state_dir=tmp_path,
    )

    assert not [event for _, event in out if event.type == "verify.failed"]


def test_verify_child_failure_is_not_superseded_by_another_workflow(tmp_path: Path):
    created = _created()
    failed = _verify_child_failed()
    unrelated = _rework_child_dispatched(workflow_run_id="workflow-other")
    task_events = [(0, created), (1, failed), (2, unrelated)]

    out = _workflow_events_with_candidate_context(
        _task(),
        task_events,
        task_events,
        state_dir=tmp_path,
    )

    assert [event for _, event in out if event.type == "verify.failed"]
