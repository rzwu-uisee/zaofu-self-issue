"""Deterministic audit for the controlled worker-stuck scenario."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from zf.core.config.loader import load_config
from zf.core.events.factory import event_log_from_project
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent


@dataclass(frozen=True)
class StuckIncidentAudit:
    status: str
    required: bool
    failure_reasons: tuple[str, ...] = ()
    dispatch_event_id: str = ""
    injection_event_id: str = ""
    stuck_event_id: str = ""
    recovery_event_id: str = ""
    task_id: str = ""
    instance_id: str = ""
    dispatch_id: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {"passed", "not_required"}

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["failure_reasons"] = list(self.failure_reasons)
        payload["ok"] = self.ok
        return payload


def event_log_for_worktree(worktree: Path) -> EventLog:
    """Build the target project's signer-aware, archive-aware event log."""
    config_path = worktree / "zf.yaml"
    config = load_config(config_path) if config_path.is_file() else None
    return event_log_from_project(worktree / ".zf", config=config, warn=False)


def read_worktree_events(worktree: Path) -> list[ZfEvent]:
    return event_log_for_worktree(worktree).read_all()


def _payload_key(event: ZfEvent) -> tuple[str, str, str]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return (
        str(payload.get("instance_id") or payload.get("assignee") or ""),
        str(payload.get("role") or ""),
        str(payload.get("dispatch_id") or ""),
    )


def _same_incident(event: ZfEvent, *, task_id: str, key: tuple[str, str, str]) -> bool:
    return str(event.task_id or "") == task_id and _payload_key(event) == key


def audit_stuck_incident(
    events: Iterable[ZfEvent],
    *,
    required: bool,
) -> StuckIncidentAudit:
    """Require one exact D -> I -> S -> R chain for a controlled incident."""
    if not required:
        return StuckIncidentAudit(status="not_required", required=False)

    ordered = list(events)
    injections = [
        event for event in ordered if event.type == "autoresearch.inject.worker_stuck"
    ]
    if not injections:
        return StuckIncidentAudit(
            status="failed",
            required=True,
            failure_reasons=("required injection event was not found",),
        )

    injection = injections[0]
    reasons: list[str] = []
    if len(injections) != 1:
        reasons.append("exactly one injection event is required")
    if injection.origin != "external":
        reasons.append("injection origin must be external")

    injection_payload = injection.payload if isinstance(injection.payload, dict) else {}
    trigger_event_id = str(injection_payload.get("trigger_event_id") or "")
    if not injection.causation_id or injection.causation_id != trigger_event_id:
        reasons.append("injection causation must equal payload.trigger_event_id")

    dispatch = next(
        (
            event
            for event in ordered
            if event.id == injection.causation_id and event.type == "task.dispatched"
        ),
        None,
    )
    task_id = str(injection.task_id or "")
    key = _payload_key(injection)
    if dispatch is None:
        reasons.append("triggering task.dispatched event was not found")
    else:
        dispatch_key = _payload_key(dispatch)
        if str(dispatch.task_id or "") != task_id or dispatch_key != key:
            reasons.append("dispatch and injection incident keys do not match")
        if (
            dispatch.correlation_id is not None
            and injection.correlation_id != dispatch.correlation_id
        ):
            reasons.append("injection did not preserve dispatch correlation")

    stuck_events = [
        event
        for event in ordered
        if event.type == "worker.stuck"
        and event.causation_id == injection.id
        and _same_incident(event, task_id=task_id, key=key)
    ]
    stuck = stuck_events[0] if stuck_events else None
    if not stuck_events:
        reasons.append("stuck event caused by the injection was not found")
    elif len(stuck_events) != 1:
        reasons.append("exactly one stuck event is required for the injection")
    if stuck is not None:
        if stuck.origin != "kernel":
            reasons.append("stuck event origin must be kernel")
        if str(stuck.payload.get("trigger_event_id") or "") != injection.id:
            reasons.append("stuck payload trigger must reference the injection")
        if stuck.correlation_id != injection.correlation_id:
            reasons.append("stuck event did not preserve injection correlation")

    recovery = None
    if stuck is not None:
        recovery_events = [
            event
            for event in ordered
            if event.type in {"worker.stuck.recovered", "worker.stuck.recovery_failed"}
            and event.causation_id == stuck.id
            and _same_incident(event, task_id=task_id, key=key)
        ]
        recovery = recovery_events[0] if recovery_events else None
        if not recovery_events:
            reasons.append("recovery event caused by the stuck event was not found")
        elif len(recovery_events) != 1:
            reasons.append("exactly one terminal recovery event is required")
        if recovery is not None:
            if recovery.origin != "kernel":
                reasons.append("recovery event origin must be kernel")
            if recovery.correlation_id != stuck.correlation_id:
                reasons.append("recovery event did not preserve stuck correlation")
            if recovery.type == "worker.stuck.recovery_failed":
                reasons.append("controlled stuck recovery reported failure")

    return StuckIncidentAudit(
        status="failed" if reasons else "passed",
        required=True,
        failure_reasons=tuple(reasons),
        dispatch_event_id=dispatch.id if dispatch is not None else "",
        injection_event_id=injection.id,
        stuck_event_id=stuck.id if stuck is not None else "",
        recovery_event_id=recovery.id if recovery is not None else "",
        task_id=task_id,
        instance_id=key[0],
        dispatch_id=key[2],
    )
