"""Bounded lifecycle and run-group projections for Delivery v2.

The canonical lifecycle can grow as ``tasks * tries * gates``.  Delivery is a
dashboard projection, so it carries explicit global inclusion budgets and
reports totals for every omitted dimension instead of serializing the complete
runtime history.
"""

from __future__ import annotations

from typing import Any

from zf.core.task.schema import Task
from zf.web.projections.delivery_view_wire import (
    budget_fields,
    exact_ids,
    wire_id,
    wire_task_id,
)


_MAX_LIFECYCLE_TASKS = 16
_MAX_LIFECYCLE_STATE_ROWS = 12
_MAX_LIFECYCLE_TRIES = 8
_MAX_LIFECYCLE_GATE_RESULTS = 8
_MAX_STATE_HISTORY_PER_TASK = 8
_MAX_TRIES_PER_TASK = 4
_MAX_GATE_RESULTS_PER_TRY = 4
_MAX_RUN_GROUPS = 8
_MAX_TASK_STATUSES = 32
_MAX_TEXT_CHARS = 120


def _compact_task_lifecycle(
    lifecycle: dict[str, Any],
    *,
    allowed_task_ids: set[str],
    task_statuses: dict[str, str] | None = None,
    preferred_task_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Return a globally bounded lifecycle with truthful omission metadata."""

    raw_tasks = lifecycle.get("tasks") if isinstance(lifecycle.get("tasks"), dict) else {}
    statuses = task_statuses or {}
    task_ids = [task_id for task_id in raw_tasks if task_id in allowed_task_ids]
    task_ids.sort(key=lambda task_id: _task_priority(
        task_id,
        raw_tasks[task_id],
        status=statuses.get(task_id, ""),
    ))
    selected_ids = task_ids[:_MAX_LIFECYCLE_TASKS]

    histories = {
        task_id: list((raw_tasks[task_id] or {}).get("state_history") or [])
        for task_id in selected_ids
    }
    tries_by_task = {
        task_id: list((raw_tasks[task_id] or {}).get("tries") or [])
        for task_id in selected_ids
    }
    history_counts = _fair_tail_counts(
        histories,
        per_item_limit=_MAX_STATE_HISTORY_PER_TASK,
        total_limit=_MAX_LIFECYCLE_STATE_ROWS,
    )
    try_counts = _fair_tail_counts(
        tries_by_task,
        per_item_limit=_MAX_TRIES_PER_TASK,
        total_limit=_MAX_LIFECYCLE_TRIES,
    )
    selected_tries = {
        task_id: tries_by_task[task_id][-try_counts[task_id]:]
        if try_counts[task_id]
        else []
        for task_id in selected_ids
    }
    try_keys = [
        (task_id, index)
        for task_id in selected_ids
        for index, _attempt in enumerate(selected_tries[task_id])
    ]
    gates_by_try = {
        key: list(selected_tries[key[0]][key[1]].get("gate_results") or [])
        for key in try_keys
    }
    gate_counts = _fair_tail_counts(
        gates_by_try,
        per_item_limit=_MAX_GATE_RESULTS_PER_TRY,
        total_limit=_MAX_LIFECYCLE_GATE_RESULTS,
    )

    tasks: dict[str, Any] = {}
    for task_id in selected_ids:
        history = histories[task_id]
        raw_tries = tries_by_task[task_id]
        included_history = (
            history[-history_counts[task_id]:]
            if history_counts[task_id]
            else []
        )
        included_tries = selected_tries[task_id]
        compact_tries = [
            _compact_try(
                attempt,
                gate_limit=gate_counts[(task_id, index)],
            )
            for index, attempt in enumerate(included_tries)
        ]
        task_gate_total = sum(
            len(list(attempt.get("gate_results") or []))
            for attempt in raw_tries
        )
        task_gate_included = sum(
            len(attempt["gate_results"])
            for attempt in compact_tries
        )
        wire_task, task_id_opaque = wire_task_id(task_id)
        tasks[wire_task] = {
            "task_id_opaque": task_id_opaque,
            "state_history": [_compact_state(row) for row in included_history],
            **budget_fields(
                "state_history",
                total=len(history),
                included=len(included_history),
            ),
            "tries": compact_tries,
            **budget_fields(
                "tries",
                total=len(raw_tries),
                included=len(compact_tries),
            ),
            **budget_fields(
                "gate_results",
                total=task_gate_total,
                included=task_gate_included,
            ),
        }

    all_items = [raw_tasks[task_id] or {} for task_id in task_ids]
    state_total = sum(len(list(item.get("state_history") or [])) for item in all_items)
    tries_total = sum(len(list(item.get("tries") or [])) for item in all_items)
    gates_total = sum(
        len(list(attempt.get("gate_results") or []))
        for item in all_items
        for attempt in list(item.get("tries") or [])
    )
    state_included = sum(item["state_history_included"] for item in tasks.values())
    tries_included = sum(item["tries_included"] for item in tasks.values())
    gates_included = sum(item["gate_results_included"] for item in tasks.values())
    all_status_ids = sorted(
        (
            task_id
            for task_id in allowed_task_ids
            if task_id in statuses
        ),
        key=lambda task_id: _task_priority(
            task_id,
            raw_tasks.get(task_id),
            status=statuses[task_id],
        ),
    )
    actionable_status_ids = [
        task_id
        for task_id in all_status_ids
        if _task_priority(
            task_id,
            raw_tasks.get(task_id),
            status=statuses[task_id],
        )[0] < 3
    ]
    preferred_status_ids = [
        task_id
        for task_id in (preferred_task_ids or [])
        if task_id in statuses
    ]
    status_ids = list(dict.fromkeys([
        *actionable_status_ids,
        *preferred_status_ids,
        *(task_id for task_id in selected_ids if task_id in statuses),
        *all_status_ids,
    ]))
    included_status_ids = status_ids[:_MAX_TASK_STATUSES]
    return {
        "schema_version": "task-lifecycle.v2",
        "tasks": tasks,
        "task_count": len(task_ids),
        **budget_fields(
            "tasks",
            total=len(task_ids),
            included=len(tasks),
        ),
        "task_ids_opaque": sum(
            1 for task_id in selected_ids if wire_task_id(task_id)[1]
        ),
        "task_statuses": {
            wire_task_id(task_id)[0]: _text(statuses[task_id], 80)
            for task_id in included_status_ids
        },
        "task_status_count": len(status_ids),
        **budget_fields(
            "task_statuses",
            total=len(status_ids),
            included=len(included_status_ids),
        ),
        "task_statuses_opaque": sum(
            1 for task_id in included_status_ids if wire_task_id(task_id)[1]
        ),
        **budget_fields(
            "state_history",
            total=state_total,
            included=state_included,
        ),
        **budget_fields(
            "tries",
            total=tries_total,
            included=tries_included,
        ),
        **budget_fields(
            "gate_results",
            total=gates_total,
            included=gates_included,
        ),
    }


def _task_priority(task_id: str, item: Any, *, status: str) -> tuple[int, int, str]:
    row = item if isinstance(item, dict) else {}
    history = list(row.get("state_history") or [])
    tries = list(row.get("tries") or [])
    latest_state = str((history[-1] if history else {}).get("state") or "")
    latest_outcome = str((tries[-1] if tries else {}).get("outcome") or "")
    signals = " ".join((status, latest_state, latest_outcome)).lower()
    if any(token in signals for token in ("fail", "block", "reject", "error")):
        rank = 0
    elif any(token in signals for token in ("running", "progress", "flight", "rework", "retry", "queued")):
        rank = 1
    elif any(token in signals for token in ("ready", "todo", "pending", "waiting")):
        rank = 2
    else:
        rank = 3
    seq_last = max(
        (_safe_int(attempt.get("seq_last")) for attempt in tries),
        default=-1,
    )
    return rank, -seq_last, task_id


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _fair_tail_counts(
    rows_by_key: dict[Any, list[Any]],
    *,
    per_item_limit: int,
    total_limit: int,
) -> dict[Any, int]:
    """Allocate a stable global budget without one task consuming it all."""

    counts = {key: 0 for key in rows_by_key}
    remaining = total_limit
    while remaining:
        progressed = False
        for key, rows in rows_by_key.items():
            allowed = min(len(rows), per_item_limit)
            if counts[key] >= allowed:
                continue
            counts[key] += 1
            remaining -= 1
            progressed = True
            if not remaining:
                break
        if not progressed:
            break
    return counts


def _compact_state(row: dict[str, Any]) -> dict[str, Any]:
    event_id, event_id_opaque = wire_id(
        row.get("via_event_id"),
        namespace="event",
    )
    return {
        "state": _text(row.get("state"), 80),
        "entered_at": _text(row.get("entered_at"), 80),
        "dwell_seconds": row.get("dwell_seconds"),
        "via_event_id": event_id,
        "via_event_id_opaque": event_id_opaque,
        "try": row.get("try"),
    }


def _compact_try(item: dict[str, Any], *, gate_limit: int) -> dict[str, Any]:
    gates = list(item.get("gate_results") or [])
    selected_gates = gates[-gate_limit:] if gate_limit else []
    dispatch_id, dispatch_id_opaque = wire_id(
        item.get("dispatch_id"),
        namespace="run",
    )
    briefing_refs, briefing_omitted = exact_ids(
        [item.get("briefing_ref")],
        limit=1,
    )
    snapshot_refs, snapshot_omitted = exact_ids(
        [item.get("snapshot_ref")],
        limit=1,
    )
    return {
        "try": int(item.get("try") or 0),
        "dispatch_id": dispatch_id,
        "dispatch_id_opaque": dispatch_id_opaque,
        "dispatched_at": _text(item.get("dispatched_at"), 80),
        "first_response_seconds": item.get("first_response_seconds"),
        "outcome": _text(item.get("outcome"), 80),
        "rework_kind": _text(item.get("rework_kind"), 120),
        "briefing_ref": briefing_refs[0] if briefing_refs else "",
        "briefing_ref_omitted": briefing_omitted > 0,
        "snapshot_ref": snapshot_refs[0] if snapshot_refs else "",
        "snapshot_ref_omitted": snapshot_omitted > 0,
        "seq_first": item.get("seq_first"),
        "seq_last": item.get("seq_last"),
        "tool_calls": int(item.get("tool_calls") or 0),
        "tokens_in": int(item.get("tokens_in") or 0),
        "tokens_out": int(item.get("tokens_out") or 0),
        "gate_results": [
            {
                "type": _text(gate.get("type"), 120),
                "passed": bool(gate.get("passed")),
                "event_id": wire_id(gate.get("event_id"), namespace="event")[0],
                "event_id_opaque": wire_id(
                    gate.get("event_id"),
                    namespace="event",
                )[1],
            }
            for gate in selected_gates
        ],
        **budget_fields(
            "gate_results",
            total=len(gates),
            included=len(selected_gates),
        ),
    }


def _run_groups_from_lifecycle(
    lifecycle: dict[str, Any],
    tasks: dict[str, Task],
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for task_id, item in (lifecycle.get("tasks") or {}).items():
        history = list(item.get("state_history") or [])
        for attempt in item.get("tries") or []:
            try_number = int(attempt.get("try") or 0)
            dispatch_id = str(attempt.get("dispatch_id") or "")
            evidence_ids = [
                str(row.get("via_event_id") or "")
                for row in history
                if row.get("via_event_id") and row.get("try") in (None, try_number)
            ]
            evidence_ids.extend(
                str(gate.get("event_id") or "")
                for gate in attempt.get("gate_results") or []
                if gate.get("event_id")
            )
            source_ids = list(dict.fromkeys(evidence_ids))
            compact_source_ids = source_ids[-4:]
            artifact_refs, artifact_refs_omitted = exact_ids(
                [attempt.get("briefing_ref"), attempt.get("snapshot_ref")],
                limit=2,
            )
            artifact_refs_total = (
                len(artifact_refs)
                + int(bool(attempt.get("briefing_ref_omitted")))
                + int(bool(attempt.get("snapshot_ref_omitted")))
                + artifact_refs_omitted
            )
            wire_task = task_id
            task_id_opaque = bool(item.get("task_id_opaque"))
            groups.append({
                "schema_version": "delivery-run-group.v2",
                "group_id": dispatch_id or f"task-attempt:{task_id}:{try_number}",
                "run_id": dispatch_id,
                "run_id_verified": bool(dispatch_id) and not bool(
                    attempt.get("dispatch_id_opaque")
                ),
                "run_id_opaque": bool(attempt.get("dispatch_id_opaque")),
                "stage_id": _text(getattr(
                    getattr(tasks.get(task_id), "contract", None),
                    "phase",
                    "",
                ), 120),
                "label": _text(tasks[task_id].title if task_id in tasks else task_id),
                "kind": "task_attempt",
                "status": _text(attempt.get("outcome") or "in_flight", 80),
                "started_at": _text(attempt.get("dispatched_at"), 80),
                "ended_at": "",
                "duration_ms": None,
                "task_ids": [wire_task],
                "task_ids_opaque": task_id_opaque,
                "children": [],
                "steps": [],
                "metrics": {
                    "try": try_number,
                    "tool_calls": int(attempt.get("tool_calls") or 0),
                    "tokens_in": int(attempt.get("tokens_in") or 0),
                    "tokens_out": int(attempt.get("tokens_out") or 0),
                },
                "verdict": {},
                "artifact_refs": artifact_refs,
                **budget_fields(
                    "artifact_refs",
                    total=artifact_refs_total,
                    included=len(artifact_refs),
                ),
                "source_event_ids": compact_source_ids,
                **budget_fields(
                    "source_event_ids",
                    total=len(source_ids),
                    included=len(compact_source_ids),
                ),
            })
    return groups[-_MAX_RUN_GROUPS:]


def _text(value: Any, limit: int = _MAX_TEXT_CHARS) -> str:
    text = str(value or "")
    return text if len(text) <= limit else f"{text[: max(0, limit - 1)]}…"


__all__ = [
    "_compact_task_lifecycle",
    "_run_groups_from_lifecycle",
]
