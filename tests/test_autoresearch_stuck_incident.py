from __future__ import annotations

from zf.autoresearch.stuck_incident import audit_stuck_incident
from zf.core.events.model import ZfEvent


def _incident_events() -> list[ZfEvent]:
    dispatch = ZfEvent(
        id="evt-dispatch",
        type="task.dispatched",
        task_id="TASK-1",
        correlation_id="corr-run",
        payload={
            "assignee": "dev-1",
            "role": "dev",
            "dispatch_id": "disp-1",
        },
    )
    injection = ZfEvent(
        id="evt-injection",
        type="autoresearch.inject.worker_stuck",
        origin="external",
        task_id="TASK-1",
        causation_id=dispatch.id,
        correlation_id="corr-run",
        payload={
            "instance_id": "dev-1",
            "role": "dev",
            "dispatch_id": "disp-1",
            "trigger_event_id": dispatch.id,
        },
    )
    stuck = ZfEvent(
        id="evt-stuck",
        type="worker.stuck",
        origin="kernel",
        task_id="TASK-1",
        causation_id=injection.id,
        correlation_id="corr-run",
        payload={
            "instance_id": "dev-1",
            "role": "dev",
            "dispatch_id": "disp-1",
            "trigger_event_id": injection.id,
        },
    )
    recovered = ZfEvent(
        id="evt-recovered",
        type="worker.stuck.recovered",
        origin="kernel",
        task_id="TASK-1",
        causation_id=stuck.id,
        correlation_id="corr-run",
        payload={
            "instance_id": "dev-1",
            "role": "dev",
            "dispatch_id": "disp-1",
        },
    )
    return [dispatch, injection, stuck, recovered]


def test_exact_stuck_incident_chain_passes() -> None:
    audit = audit_stuck_incident(_incident_events(), required=True)

    assert audit.ok
    assert audit.status == "passed"
    assert audit.dispatch_event_id == "evt-dispatch"
    assert audit.injection_event_id == "evt-injection"
    assert audit.stuck_event_id == "evt-stuck"
    assert audit.recovery_event_id == "evt-recovered"
    assert audit.task_id == "TASK-1"
    assert audit.instance_id == "dev-1"
    assert audit.dispatch_id == "disp-1"


def test_unrelated_stuck_counts_cannot_satisfy_incident() -> None:
    events = _incident_events()
    events[2].causation_id = "evt-unrelated-injection"

    audit = audit_stuck_incident(events, required=True)

    assert not audit.ok
    assert audit.status == "failed"
    assert "stuck event caused by the injection was not found" in audit.failure_reasons


def test_stuck_incident_requires_external_injection_origin() -> None:
    events = _incident_events()
    events[1].origin = "worker"

    audit = audit_stuck_incident(events, required=True)

    assert not audit.ok
    assert "injection origin must be external" in audit.failure_reasons


def test_stuck_incident_is_explicitly_not_required() -> None:
    audit = audit_stuck_incident([], required=False)

    assert audit.ok
    assert audit.status == "not_required"
    assert audit.failure_reasons == ()
