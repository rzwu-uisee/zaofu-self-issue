"""Run-scoped failure incident projection shared by owner-facing views."""

from __future__ import annotations

import hashlib
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.runtime.attempt_ledger import failure_fingerprint
from zf.runtime.terminal_events import is_successful_run_terminal


FAILURE_EVENTS = frozenset({
    "candidate.failed",
    "candidate.quality.failed",
    "integration.failed",
    "judge.failed",
    "review.rejected",
    "run.delivery.blocked",
    "run.delivery.failed",
    "run.goal.blocked",
    "run.goal.completion.blocked",
    "run.goal.completion.rejected",
    "task.rework.capped",
    "test.failed",
    "verify.failed",
})

FAILURE_SETTLEMENT_EVENTS = {
    "candidate.failed": frozenset({
        "candidate.integration.completed", "candidate.ready", "candidate.updated",
    }),
    "candidate.quality.failed": frozenset({
        "candidate.integration.completed", "candidate.ready", "candidate.updated",
    }),
    "integration.failed": frozenset({
        "candidate.integration.completed", "candidate.ready", "candidate.updated",
    }),
    "judge.failed": frozenset({"judge.passed"}),
    "review.rejected": frozenset({"review.approved"}),
    "run.delivery.blocked": frozenset({
        "run.delivery.completed", "ship.completed", "ship.done",
    }),
    "run.delivery.failed": frozenset({
        "run.delivery.completed", "ship.completed", "ship.done",
    }),
    "run.goal.blocked": frozenset({"run.goal.completed", "run.completed"}),
    "run.goal.completion.blocked": frozenset({
        "run.goal.completed", "run.completed",
    }),
    "run.goal.completion.rejected": frozenset({
        "run.goal.completed", "run.completed",
    }),
    "task.rework.capped": frozenset({
        "task.done.accepted", "task.attempt.succeeded",
    }),
    "test.failed": frozenset({"test.passed"}),
    "verify.failed": frozenset({"verify.passed"}),
}


def project_run_failure_incidents(events: list[ZfEvent]) -> list[dict[str, Any]]:
    """Fold repeated failures without creating another lifecycle authority."""

    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str]] = []
    for index, event in enumerate(events):
        if event.type not in FAILURE_EVENTS:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        task_id = str(event.task_id or payload.get("task_id") or "")
        scope_id = incident_scope_id(event)
        fingerprint = failure_fingerprint(event)
        key = (scope_id, event.type, fingerprint)
        summary = str(
            payload.get("reason")
            or payload.get("message")
            or payload.get("summary")
            or event.type
        )
        incident = grouped.get(key)
        if incident is None:
            incident = {
                "incident_id": "incident-" + hashlib.sha256(
                    "\0".join(key).encode("utf-8")
                ).hexdigest()[:16],
                "status": "active",
                "event_type": event.type,
                "task_id": task_id,
                "scope_id": scope_id,
                "failure_fingerprint": fingerprint,
                "summary": summary,
                "count": 0,
                "first_event_id": event.id,
                "first_event_at": event.ts,
                "last_event_id": event.id,
                "last_event_at": event.ts,
                "resolved_by_event_id": "",
                "resolved_by_event_type": "",
                "_last_index": index,
            }
            grouped[key] = incident
            order.append(key)
        incident["count"] = int(incident["count"]) + 1
        incident["summary"] = summary
        incident["last_event_id"] = event.id
        incident["last_event_at"] = event.ts
        incident["_last_index"] = index

    for key in order:
        incident = grouped[key]
        allowed = FAILURE_SETTLEMENT_EVENTS.get(
            str(incident["event_type"]), frozenset(),
        )
        for event in events[int(incident["_last_index"]) + 1:]:
            if not is_successful_run_terminal(event) and event.type not in allowed:
                continue
            if (
                not is_successful_run_terminal(event)
                and incident_scope_id(event) != incident["scope_id"]
            ):
                continue
            incident["status"] = "resolved"
            incident["resolved_by_event_id"] = event.id
            incident["resolved_by_event_type"] = event.type
            break
        incident.pop("_last_index", None)
    return redact_obj([grouped[key] for key in order])


def incident_scope_id(event: ZfEvent) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    for value in (
        event.task_id,
        payload.get("task_id"),
        payload.get("pdd_id"),
        payload.get("feature_id"),
        payload.get("goal_id"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return "run"


__all__ = [
    "FAILURE_EVENTS",
    "FAILURE_SETTLEMENT_EVENTS",
    "incident_scope_id",
    "project_run_failure_incidents",
]
