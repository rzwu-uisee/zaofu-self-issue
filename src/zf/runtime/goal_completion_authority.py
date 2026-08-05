"""Current-authority scoping for the deterministic Goal completion gate."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.candidate_result_binding import same_task_map_generation


def scope_handoff_snapshot(
    snapshot: Mapping[str, Any],
    *,
    task_map_generation: str = "",
    candidate_task_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Keep only handoffs authorized by the current closure identity.

    Historical rows remain in the event ledger. A row with an explicit
    generation must match the current generation; task identity is only the
    fallback for legacy rows that predate generation binding.
    """

    scoped = dict(snapshot)
    current_generation = str(task_map_generation or "").strip()
    current_tasks = {
        str(task_id).strip()
        for task_id in candidate_task_ids
        if str(task_id).strip()
    }
    authority_available = bool(current_generation or current_tasks)
    raw_handoffs = _mapping_rows(snapshot.get("pending_handoffs"))
    raw_findings = _mapping_rows(snapshot.get("open_feedback"))
    raw_attempts = _mapping_rows(snapshot.get("active_attempts"))
    raw_results = _mapping_rows(snapshot.get("accepted_results"))

    if not authority_available:
        scoped["historical_open_feedback_count"] = 0
        scoped["historical_pending_handoff_count"] = 0
        return scoped

    current_handoffs = [
        row
        for row in raw_handoffs
        if _row_matches_authority(
            row,
            task_map_generation=current_generation,
            candidate_task_ids=current_tasks,
        )
    ]
    current_request_ids = {
        str(row.get("request_event_id") or "").strip()
        for row in current_handoffs
        if str(row.get("request_event_id") or "").strip()
    }
    current_findings = [
        row
        for row in raw_findings
        if str(row.get("request_event_id") or "").strip() in current_request_ids
    ]
    current_attempts = [
        row
        for row in raw_attempts
        if _row_matches_authority(
            row,
            task_map_generation=current_generation,
            candidate_task_ids=current_tasks,
        )
    ]
    current_results = [
        row
        for row in raw_results
        if _row_matches_authority(
            row,
            task_map_generation=current_generation,
            candidate_task_ids=current_tasks,
        )
    ]

    scoped.update({
        "open_feedback_count": len(current_findings),
        "pending_handoff_count": len(current_handoffs),
        "open_feedback": current_findings,
        "pending_handoffs": current_handoffs,
        "active_attempts": current_attempts,
        "accepted_results": current_results,
        "historical_open_feedback_count": len(raw_findings) - len(current_findings),
        "historical_pending_handoff_count": len(raw_handoffs) - len(current_handoffs),
    })
    if not current_findings and not current_handoffs and current_results:
        scoped["delivery_phase"] = "result_accepted"
    return scoped


def active_fanout_ids_for_authority(
    events: Sequence[ZfEvent],
    *,
    task_map_generation: str = "",
) -> tuple[list[str], list[str]]:
    """Partition active fanouts into current/unknown and proven historical.

    Unknown fanouts remain current and therefore fail closed. Only an explicit
    generation mismatch is sufficient to make an active fanout non-blocking.
    """

    started: dict[str, ZfEvent] = {}
    settled: set[str] = set()
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        fanout_id = str(payload.get("fanout_id") or "").strip()
        if not fanout_id:
            continue
        if event.type == "fanout.started":
            started[fanout_id] = event
        elif event.type in {
            "fanout.aggregate.completed",
            "fanout.cancelled",
            "fanout.timed_out",
        }:
            settled.add(fanout_id)

    current_generation = str(task_map_generation or "").strip()
    active: list[str] = []
    historical: list[str] = []
    for fanout_id in sorted(set(started) - settled):
        generations = _fanout_generations(started[fanout_id])
        if (
            current_generation
            and generations
            and not any(
                same_task_map_generation(item, current_generation)
                for item in generations
            )
        ):
            historical.append(fanout_id)
        else:
            active.append(fanout_id)
    return active, historical


def _row_matches_authority(
    row: Mapping[str, Any],
    *,
    task_map_generation: str,
    candidate_task_ids: set[str],
) -> bool:
    row_generation = str(row.get("task_map_generation") or "").strip()
    if row_generation:
        return bool(task_map_generation) and same_task_map_generation(
            row_generation,
            task_map_generation,
        )
    task_id = str(row.get("task_id") or "").strip()
    return bool(task_id and task_id in candidate_task_ids)


def _fanout_generations(event: ZfEvent) -> set[str]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    values = {str(payload.get("task_map_generation") or "").strip()}
    trigger = payload.get("trigger_payload")
    if isinstance(trigger, Mapping):
        values.add(str(trigger.get("task_map_generation") or "").strip())
    children = payload.get("expected_children")
    if isinstance(children, list):
        for child in children:
            if not isinstance(child, Mapping):
                continue
            child_payload = child.get("payload")
            if isinstance(child_payload, Mapping):
                values.add(
                    str(child_payload.get("task_map_generation") or "").strip()
                )
    return {value for value in values if value}


def _mapping_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


__all__ = ["active_fanout_ids_for_authority", "scope_handoff_snapshot"]
