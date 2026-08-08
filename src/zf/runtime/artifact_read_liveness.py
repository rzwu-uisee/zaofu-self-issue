"""Event-derived liveness for active artifact-read ledgers."""

from __future__ import annotations

import re
from collections.abc import Iterable

from zf.core.events.model import ZfEvent


def live_attempt_ids(events: Iterable[ZfEvent]) -> set[str]:
    live: set[str] = set()
    operation_attempts: dict[str, str] = {}
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        operation_id = str(payload.get("operation_id") or "").strip()
        attempt_id = str(
            payload.get("active_attempt_id")
            or payload.get("attempt_id")
            or payload.get("run_id")
            or payload.get("dispatch_id")
            or ""
        ).strip()
        if event.type in {
            "task.dispatched",
            "fanout.child.dispatched",
            "workflow.operation.started",
        }:
            if not attempt_id:
                continue
            if operation_id:
                operation_attempts[operation_id] = attempt_id
            live.add(_safe_component(attempt_id))
        elif event.type in {
            "dev.build.done",
            "dev.failed",
            "dev.blocked",
            "fanout.child.completed",
            "fanout.child.failed",
            "workflow.operation.settled",
            "workflow.operation.failed",
            "workflow.operation.blocked",
            "workflow.operation.superseded",
            "workflow.operation.interrupted",
            "workflow.operation.cancelled",
        }:
            if operation_id:
                attempt_id = operation_attempts.get(operation_id, attempt_id)
            if attempt_id:
                live.discard(_safe_component(attempt_id))
    return live


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-._") or "attempt"


__all__ = ["live_attempt_ids"]
