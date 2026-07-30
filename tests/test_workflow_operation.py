from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    reduce_workflow_operations,
    stable_continuation_idempotency_key,
    stable_operation_id,
)


def _service(tmp_path: Path) -> WorkflowOperationService:
    log = EventLog(tmp_path / "events.jsonl")
    return WorkflowOperationService(
        state_dir=tmp_path,
        event_log=log,
        event_writer=EventWriter(log),
    )


def test_ensure_operation_dedupes_and_fails_closed_on_drift(tmp_path: Path) -> None:
    service = _service(tmp_path)
    operation_id = stable_operation_id(
        workflow_run_id="run-1",
        parent_stage_id="verify",
        operation_key="security",
    )
    first = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id=operation_id,
        operation_type="agent",
        request={"prompt": "review", "dispatch_id": "volatile-1"},
        parent_stage_id="verify",
        task_id="T1",
    )
    replay = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id=operation_id,
        operation_type="agent",
        request={"prompt": "review", "dispatch_id": "volatile-2"},
        parent_stage_id="verify",
        task_id="T1",
    )
    divergent = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id=operation_id,
        operation_type="agent",
        request={"prompt": "different"},
        parent_stage_id="verify",
        task_id="T1",
    )
    assert first.created is True
    assert replay.replay_hit is True
    assert replay.request_hash == first.request_hash
    assert divergent.status == "divergent"
    events = service.event_log.read_all()
    assert sum(event.type == "workflow.operation.requested" for event in events) == 1
    assert sum(event.type == "workflow.operation.blocked" for event in events) == 1
    assert all(
        event.origin == "kernel"
        for event in events
        if event.type.startswith("workflow.operation.")
    )
    view = reduce_workflow_operations(events)[operation_id]
    assert view["request_count"] == 1
    assert view["replay_count"] == 0


def test_operation_settles_even_when_product_verdict_is_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path)
    operation_id = "op-rejected"
    ensured = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id=operation_id,
        operation_type="agent",
        request={"prompt": "verify"},
        task_id="T1",
    )
    envelope_ref = {
        "ref_schema_version": "sidecar-ref.v1",
        "kind": "call_result_envelope",
        "ref": "artifacts/call-results/rejected.json",
        "sha256": "a" * 64,
    }
    service.settle(
        operation_id=operation_id,
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id="T1",
        admitted_call_result_ref=envelope_ref,
    )
    view = reduce_workflow_operations(service.event_log.read_all())[operation_id]
    assert view["status"] == "settled"
    assert view["admitted_call_result_ref"]["ref"].endswith("rejected.json")


def test_failed_operation_can_settle_after_durable_result_repair(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    ensured = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-repaired",
        operation_type="agent",
        request={"prompt": "verify"},
    )
    service.fail(
        operation_id="op-repaired",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        reason="result publication interrupted",
    )
    service.settle(
        operation_id="op-repaired",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        admitted_call_result_ref={
            "ref": "artifacts/call-results/repaired.json",
            "sha256": "b" * 64,
        },
    )

    view = reduce_workflow_operations(service.event_log.read_all())[
        "op-repaired"
    ]
    assert view["status"] == "settled"


def test_cancelled_operation_ignores_late_settlement(tmp_path: Path) -> None:
    service = _service(tmp_path)
    ensured = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-cancelled",
        operation_type="agent",
        request={"prompt": "verify"},
    )
    service.cancel(
        operation_id="op-cancelled",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        reason="operator cancelled",
    )
    service.settle(
        operation_id="op-cancelled",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        admitted_call_result_ref={
            "ref": "artifacts/call-results/late.json",
            "sha256": "c" * 64,
        },
    )

    view = reduce_workflow_operations(service.event_log.read_all())[
        "op-cancelled"
    ]
    assert view["status"] == "cancelled"
    assert view["last_event_type"] == "workflow.operation.cancelled"


def test_continuation_reservation_is_replay_safe_and_supersedable(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    ensured = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="dynamic-op-1",
        operation_type="dynamic_read_only_workflow",
        request={
            "attempt_domain": "read_only_dynamic",
            "continuation_key": "fragment-1",
        },
        parent_operation_id="parent-op",
        task_id="T1",
    )

    first = service.reserve_continuation(
        operation_id="dynamic-op-1",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        continuation_key="fragment-1",
        expected_generation="GEN-1",
        expected_package_ref="artifacts/packages/p1.json",
        expected_package_digest="a" * 64,
        pending_action_digest="b" * 64,
        budget_snapshot={"available": True},
        reservation_expires_at="2026-07-24T12:00:30+00:00",
        parent_operation_id="parent-op",
        task_id="T1",
    )
    replay = service.reserve_continuation(
        operation_id="dynamic-op-1",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        continuation_key="fragment-1",
        expected_generation="GEN-1",
        expected_package_ref="artifacts/packages/p1.json",
        expected_package_digest="a" * 64,
        pending_action_digest="b" * 64,
        budget_snapshot={"available": True},
        reservation_expires_at="2026-07-24T12:01:00+00:00",
        parent_operation_id="parent-op",
        task_id="T1",
    )

    assert first.created is True
    assert replay.replay_hit is True
    assert replay.reservation_id == first.reservation_id
    assert replay.idempotency_key == stable_continuation_idempotency_key(
        workflow_run_id="run-1",
        continuation_key="fragment-1",
        expected_generation="GEN-1",
    )
    service.supersede(
        operation_id="dynamic-op-1",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        reason="package_generation_changed",
        reservation_id=first.reservation_id,
        task_id="T1",
    )
    view = reduce_workflow_operations(service.event_log.read_all())["dynamic-op-1"]
    assert view["status"] == "superseded"
    assert view["reservation_id"] == first.reservation_id
    assert view["reason"] == "package_generation_changed"
    assert sum(
        event.type == "workflow.operation.reserved"
        for event in service.event_log.read_all()
    ) == 1
