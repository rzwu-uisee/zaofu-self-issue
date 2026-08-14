"""Read-only current-authority projection for long-running workflows."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.candidate_result_binding import same_task_map_generation
from zf.runtime.plan_artifact_package import (
    PlanArtifactPackageError,
    reduce_plan_artifact_packages,
)
from zf.runtime.run_manager_router import build_no_progress_projection
from zf.runtime.run_scope import event_run_id, events_for_run, run_aliases
from zf.runtime.workflow_operation import reduce_workflow_operations


SCHEMA_VERSION = "long-run-truth.v1"

_RUN_TERMINALS = frozenset({
    "run.completed",
    "run.cancelled",
    "run.abandoned",
    "run.goal.completed",
    "run.goal.blocked",
})
_RUN_LIFECYCLE_EVENTS = frozenset({
    "run.started",
    "run.goal.started",
    "run.goal.updated",
}) | _RUN_TERMINALS
_VERIFIED_EVENTS = frozenset({
    "verify.passed",
    "test.passed",
    "judge.passed",
    "task.pipeline.verify.completed",
})
_LANDED_EVENTS = frozenset({"ship.completed", "run.delivery.settled"})
_OWNER_NOTIFIED_EVENTS = frozenset({"owner.visible_message.delivered"})
_BROWSER_TIERS = frozenset({"browser", "e2e", "real_e2e"})
_PENDING_OPERATION_STATUSES = frozenset({
    "requested",
    "reserved",
    "running",
    "suspended",
})


def project_long_run_truth(events: Iterable[ZfEvent]) -> dict[str, Any]:
    """Project one current run without creating another mutable authority."""

    rows = list(events)
    aliases = run_aliases(rows)
    run_id = _select_current_run(rows, aliases)
    if not run_id:
        return _empty_projection(raw_event_count=len(rows))

    scoped = events_for_run(rows, run_id=run_id)
    package, package_error = _current_package(scoped, run_id)
    package_event_id = str(package.get("event_id") or "")
    authority_rows = _authority_rows(scoped, package_event_id)
    generation = str(package.get("task_map_generation") or "")
    if not generation:
        generation = _latest_payload_value(authority_rows, "task_map_generation")
    candidate = _current_candidate(authority_rows, generation=generation)
    candidate_ref = str(candidate.get("candidate_ref") or "")
    candidate_digest = str(candidate.get("candidate_digest") or "")

    operations = reduce_workflow_operations(scoped)
    authority_event_ids = {event.id for event in authority_rows}
    authority_operations = {
        operation_id: row
        for operation_id, row in operations.items()
        if authority_event_ids.intersection(row.get("source_event_ids") or [])
    }
    current_operations = {
        operation_id: row
        for operation_id, row in authority_operations.items()
        if str(row.get("status") or "") not in {"superseded", "cancelled"}
    }

    no_progress = build_no_progress_projection(authority_rows)
    run_status, run_terminal = _run_status(scoped)
    gate = _current_gate(
        authority_rows,
        generation=generation,
        run_status=run_status,
    )
    verified = _event_milestone(
        authority_rows,
        event_types=_VERIFIED_EVENTS,
        generation=generation,
        candidate_ref=candidate_ref,
        candidate_digest=candidate_digest,
    )
    landed = _event_milestone(
        authority_rows,
        event_types=_LANDED_EVENTS,
        generation=generation,
        candidate_ref=candidate_ref,
        candidate_digest=candidate_digest,
    )
    reachable = _reachable_milestone(
        authority_rows,
        generation=generation,
        candidate_ref=candidate_ref,
        candidate_digest=candidate_digest,
    )
    owner_notified = _owner_notified_milestone(authority_rows)
    issues = []
    if package_error:
        issues.append(package_error)
    if not package:
        issues.append("current_plan_artifact_package_unobserved")
    if package and not generation:
        issues.append("current_task_map_generation_unobserved")

    return {
        "schema_version": SCHEMA_VERSION,
        "is_derived_projection": True,
        "status": "degraded" if issues else "ready",
        "issues": issues,
        "current": {
            "run_id": run_id,
            "run_status": run_status,
            "run_terminal_event_id": str(getattr(run_terminal, "id", "") or ""),
            "task_map_generation": generation,
            "plan_package_ref": str(package.get("package_ref") or ""),
            "plan_package_digest": str(
                package.get("package_digest")
                or package.get("package_sha256")
                or ""
            ),
            "candidate_ref": candidate_ref,
            "candidate_digest": candidate_digest,
            "candidate_event_id": str(candidate.get("event_id") or ""),
            "candidate_status": str(candidate.get("status") or "unobserved"),
        },
        "counts": {
            "raw_events": len(scoped),
            "authority_events": len(authority_rows),
            "unique_operations": len(operations),
            "authority_operations": len(authority_operations),
            "current_operations": len(current_operations),
            "pending_operations": sum(
                1
                for row in current_operations.values()
                if str(row.get("status") or "") in _PENDING_OPERATION_STATUSES
            ),
            "superseded_operations": sum(
                1
                for row in operations.values()
                if str(row.get("status") or "") == "superseded"
            ),
        },
        "gate": gate,
        "no_progress": {
            "status": str(no_progress.get("status") or "clear"),
            "threshold": int(no_progress.get("threshold") or 0),
            "items": list(no_progress.get("items") or []),
        },
        "milestones": {
            "verified": verified,
            "landed": landed,
            "reachable": reachable,
            "owner_notified": owner_notified,
        },
        "debug": {
            "operation_history": _operation_debug_rows(operations),
            "authority_start_event_id": package_event_id,
        },
    }


def _empty_projection(*, raw_event_count: int) -> dict[str, Any]:
    unproven = _unproven_milestone()
    return {
        "schema_version": SCHEMA_VERSION,
        "is_derived_projection": True,
        "status": "empty",
        "issues": [],
        "current": {
            "run_id": "",
            "run_status": "unobserved",
            "run_terminal_event_id": "",
            "task_map_generation": "",
            "plan_package_ref": "",
            "plan_package_digest": "",
            "candidate_ref": "",
            "candidate_digest": "",
            "candidate_event_id": "",
            "candidate_status": "unobserved",
        },
        "counts": {
            "raw_events": raw_event_count,
            "authority_events": 0,
            "unique_operations": 0,
            "authority_operations": 0,
            "current_operations": 0,
            "pending_operations": 0,
            "superseded_operations": 0,
        },
        "gate": {
            "status": "unobserved",
            "kind": "",
            "owner": "",
            "reason": "",
            "resume_condition": "",
            "event_id": "",
            "task_id": "",
        },
        "no_progress": {"status": "clear", "threshold": 3, "items": []},
        "milestones": {
            "verified": dict(unproven),
            "landed": dict(unproven),
            "reachable": dict(unproven),
            "owner_notified": dict(unproven),
        },
        "debug": {"operation_history": [], "authority_start_event_id": ""},
    }


def _select_current_run(rows: list[ZfEvent], aliases: Mapping[str, str]) -> str:
    last_index: dict[str, int] = {}
    terminal: set[str] = set()
    for index, event in enumerate(rows):
        if event.type not in _RUN_LIFECYCLE_EVENTS:
            continue
        run_id = event_run_id(event, aliases=dict(aliases))
        if not run_id:
            continue
        last_index[run_id] = index
        if event.type in _RUN_TERMINALS:
            terminal.add(run_id)
    if not last_index:
        return ""
    active = [run_id for run_id in last_index if run_id not in terminal]
    candidates = active or list(last_index)
    return max(candidates, key=lambda item: last_index[item])


def _current_package(
    scoped: list[ZfEvent],
    run_id: str,
) -> tuple[dict[str, Any], str]:
    package_run_id = next((
        str(_payload(event).get("workflow_run_id") or "")
        for event in reversed(scoped)
        if event.type == "plan.artifact_package.admitted"
        and str(_payload(event).get("workflow_run_id") or "")
    ), run_id)
    try:
        projection = reduce_plan_artifact_packages(
            scoped,
            workflow_run_id=package_run_id,
        )
    except PlanArtifactPackageError:
        return {}, "plan_artifact_package_projection_invalid"
    current = projection.get("current")
    return (dict(current), "") if isinstance(current, Mapping) else ({}, "")


def _authority_rows(rows: list[ZfEvent], event_id: str) -> list[ZfEvent]:
    if not event_id:
        return rows
    for index, event in enumerate(rows):
        if event.id == event_id:
            return rows[index:]
    return rows


def _latest_payload_value(rows: list[ZfEvent], key: str) -> str:
    return next((
        str(_payload(event).get(key) or "")
        for event in reversed(rows)
        if str(_payload(event).get(key) or "")
    ), "")


def _current_candidate(
    rows: list[ZfEvent],
    *,
    generation: str,
) -> dict[str, Any]:
    candidate_types = {
        "candidate.ready": "ready",
        "candidate.integration.completed": "integrated",
        "candidate.updated": "updated",
    }
    for event in reversed(rows):
        status = candidate_types.get(event.type)
        if status is None:
            continue
        payload = _payload(event)
        event_generation = str(payload.get("task_map_generation") or "")
        if generation and event_generation and not same_task_map_generation(
            event_generation,
            generation,
        ):
            continue
        candidate_ref = str(
            payload.get("candidate_ref")
            or payload.get("target_ref")
            or payload.get("branch")
            or payload.get("candidate_branch")
            or ""
        )
        digest = str(
            payload.get("candidate_digest")
            or payload.get("candidate_head_commit")
            or payload.get("target_commit")
            or payload.get("commit")
            or ""
        )
        return {
            "event_id": event.id,
            "status": status,
            "candidate_ref": candidate_ref,
            "candidate_digest": digest,
        }
    return {}


def _current_gate(
    rows: list[ZfEvent],
    *,
    generation: str,
    run_status: str,
) -> dict[str, Any]:
    if run_status in {"completed", "cancelled", "abandoned"}:
        return _clear_gate()
    for event in reversed(rows):
        if event.type not in {"human.escalate", "approval.requested", "plan.approval.requested"}:
            continue
        payload = _payload(event)
        event_generation = str(payload.get("task_map_generation") or "")
        if generation and event_generation and not same_task_map_generation(
            event_generation,
            generation,
        ):
            continue
        if _gate_resolved(rows, event):
            continue
        kind = str(payload.get("blocker_kind") or payload.get("kind") or "")
        if not kind:
            kind = "plan_approval" if event.type == "plan.approval.requested" else "human_decision"
        return {
            "status": "blocked",
            "kind": kind,
            "owner": str(payload.get("owner_route") or payload.get("owner") or "human"),
            "reason": str(payload.get("reason") or payload.get("summary") or "approval_pending"),
            "resume_condition": str(
                payload.get("resolution_event_type")
                or payload.get("verify_condition")
                or "human.resolved"
            ),
            "event_id": event.id,
            "task_id": str(event.task_id or payload.get("task_id") or ""),
        }
    return _clear_gate()


def _clear_gate() -> dict[str, Any]:
    return {
        "status": "clear",
        "kind": "",
        "owner": "",
        "reason": "",
        "resume_condition": "",
        "event_id": "",
        "task_id": "",
    }


def _gate_resolved(rows: list[ZfEvent], requested: ZfEvent) -> bool:
    payload = _payload(requested)
    token = str(
        payload.get("decision_token")
        or payload.get("approval_ref")
        or payload.get("approval_id")
        or payload.get("plan_id")
        or ""
    )
    try:
        start = rows.index(requested) + 1
    except ValueError:
        start = 0
    for event in rows[start:]:
        current = _payload(event)
        if (
            event.type == "task.pipeline.external_gate.satisfied"
            and str(current.get("escalation_event_id") or "") == requested.id
        ):
            return True
        if event.type not in {
            "human.resolved",
            "human.escalation.acknowledged",
            "run.manager.human_decision.applied",
            "run.manager.human_decision.rejected",
            "approval.resolved",
            "approval.expired",
            "approval.rejected_by_policy",
            "plan.approved",
            "plan.rejected",
        }:
            continue
        current_token = str(
            current.get("decision_token")
            or current.get("approval_ref")
            or current.get("approval_id")
            or current.get("plan_id")
            or ""
        )
        if token and current_token == token:
            return True
        if not token and event.causation_id == requested.id:
            return True
    return False


def _event_milestone(
    rows: list[ZfEvent],
    *,
    event_types: frozenset[str],
    generation: str,
    candidate_ref: str,
    candidate_digest: str,
    require_candidate: bool = True,
) -> dict[str, Any]:
    if require_candidate and not (candidate_ref or candidate_digest):
        return _unproven_milestone()
    for event in reversed(rows):
        if event.type not in event_types:
            continue
        if not _matches_authority(
            event,
            generation=generation,
            candidate_ref=candidate_ref,
            candidate_digest=candidate_digest,
            require_candidate=require_candidate,
        ):
            continue
        return {
            "status": "proven",
            "event_id": event.id,
            "event_type": event.type,
            "at": event.ts,
            "evidence": "event",
        }
    return _unproven_milestone()


def _reachable_milestone(
    rows: list[ZfEvent],
    *,
    generation: str,
    candidate_ref: str,
    candidate_digest: str,
) -> dict[str, Any]:
    if not (candidate_ref or candidate_digest):
        return _unproven_milestone()
    for event in reversed(rows):
        if event.type not in _VERIFIED_EVENTS:
            continue
        if not _matches_authority(
            event,
            generation=generation,
            candidate_ref=candidate_ref,
            candidate_digest=candidate_digest,
            require_candidate=True,
        ):
            continue
        for command in _command_rows(_payload(event)):
            tier = str(command.get("tier") or command.get("verification_tier") or "").lower()
            command_text = str(command.get("command") or "").strip()
            exit_code = command.get("exit_code", command.get("returncode"))
            status = str(command.get("status") or "").lower()
            passed = exit_code == 0 or status in {"passed", "success", "ok"}
            if command_text and tier in _BROWSER_TIERS and passed:
                return {
                    "status": "proven",
                    "event_id": event.id,
                    "event_type": event.type,
                    "at": event.ts,
                    "evidence": command_text[:240],
                }
    return _unproven_milestone()


def _owner_notified_milestone(rows: list[ZfEvent]) -> dict[str, Any]:
    by_id = {event.id: event for event in rows}
    for event in reversed(rows):
        if event.type not in _OWNER_NOTIFIED_EVENTS:
            continue
        payload = _payload(event)
        requested = by_id.get(str(payload.get("source_event_id") or ""))
        if requested is None:
            attempted = by_id.get(str(event.causation_id or ""))
            if attempted is not None:
                attempted_payload = _payload(attempted)
                requested = by_id.get(str(attempted_payload.get("source_event_id") or ""))
        requested_payload = _payload(requested) if requested is not None else {}
        if not (
            requested is not None
            and requested.type == "owner.visible_message.requested"
            and (
                str(requested_payload.get("message_kind") or "")
                == "run_terminal_delivery"
                or str(requested_payload.get("delivery_class") or "")
                == "run_terminal"
            )
        ):
            continue
        return {
            "status": "proven",
            "event_id": event.id,
            "event_type": event.type,
            "at": event.ts,
            "evidence": str(payload.get("delivery_id") or "event"),
        }
    return _unproven_milestone()


def _command_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    for owner in (payload, payload.get("evidence")):
        if not isinstance(owner, Mapping):
            continue
        for key in ("checks", "commands", "command_evidence"):
            value = owner.get(key)
            if isinstance(value, list):
                rows.extend(item for item in value if isinstance(item, Mapping))
            elif isinstance(value, Mapping):
                for tier, item in value.items():
                    if isinstance(item, Mapping):
                        rows.append({"tier": str(tier), **dict(item)})
    return rows


def _matches_authority(
    event: ZfEvent,
    *,
    generation: str,
    candidate_ref: str,
    candidate_digest: str,
    require_candidate: bool,
) -> bool:
    payload = _payload(event)
    event_generation = str(payload.get("task_map_generation") or "")
    if generation and event_generation and not same_task_map_generation(
        event_generation,
        generation,
    ):
        return False
    if not require_candidate:
        return True
    refs = {
        str(payload.get(key) or "")
        for key in ("candidate_ref", "target_ref", "branch", "candidate_branch")
        if str(payload.get(key) or "")
    }
    digests = {
        str(payload.get(key) or "")
        for key in (
            "candidate_digest",
            "candidate_head_commit",
            "target_commit",
            "commit",
        )
        if str(payload.get(key) or "")
    }
    if not refs and not digests:
        return False
    return bool(
        (candidate_ref and candidate_ref in refs)
        or (candidate_digest and candidate_digest in digests)
    )


def _unproven_milestone() -> dict[str, Any]:
    return {
        "status": "unproven",
        "event_id": "",
        "event_type": "",
        "at": "",
        "evidence": "",
    }


def _run_status(rows: list[ZfEvent]) -> tuple[str, ZfEvent | None]:
    terminal = next((event for event in reversed(rows) if event.type in _RUN_TERMINALS), None)
    if terminal is None:
        return "running", None
    status = {
        "run.goal.completed": "completed",
        "run.completed": "completed",
        "run.goal.blocked": "blocked",
        "run.cancelled": "cancelled",
        "run.abandoned": "abandoned",
    }.get(terminal.type, "terminal")
    return status, terminal


def _operation_debug_rows(operations: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        {
            "operation_id": operation_id,
            "operation_type": str(item.get("operation_type") or ""),
            "status": str(item.get("status") or ""),
            "task_id": str(item.get("task_id") or ""),
            "last_event_id": str(item.get("last_event_id") or ""),
            "last_event_at": str(item.get("last_event_at") or ""),
            "request_count": int(item.get("request_count") or 0),
            "replay_count": int(item.get("replay_count") or 0),
        }
        for operation_id, item in operations.items()
    ]
    rows.sort(key=lambda item: (item["last_event_at"], item["operation_id"]), reverse=True)
    return rows[:50]


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


__all__ = ["SCHEMA_VERSION", "project_long_run_truth"]
