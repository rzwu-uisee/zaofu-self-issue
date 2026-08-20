"""Bounded task-flow and run-chain wire projections for Delivery v2."""

from __future__ import annotations

from typing import Any

from zf.web.projections.delivery_view_wire import (
    budget_fields,
    exact_ids,
    wire_id,
    wire_task_id,
)


_MAX_STAGES = 8
_MAX_TASK_ROWS = 8
_MAX_TASK_IDS = 16
_MAX_ACTIVE_TASK_IDS = 8
_MAX_REFS = 8
_MAX_TEXT_CHARS = 120


def compact_task_flow(flow: dict[str, Any]) -> dict[str, Any]:
    raw_stages = list(flow.get("stages") or [])
    stages: list[dict[str, Any]] = []
    remaining_tasks = _MAX_TASK_ROWS
    remaining_task_ids = _MAX_TASK_IDS
    remaining_active_ids = _MAX_ACTIVE_TASK_IDS
    remaining_run_refs = _MAX_REFS
    remaining_event_refs = _MAX_REFS
    task_rows_total = sum(
        len(list(stage.get("tasks") or []))
        for stage in raw_stages
    )
    task_rows_included = 0
    task_ids_total = sum(
        len(as_list(stage.get("task_ids")))
        for stage in raw_stages
    )
    task_ids_included = 0
    refs_total = sum(
        len(as_list(stage.get("run_group_ids")))
        + len(as_list(stage.get("source_event_ids")))
        for stage in raw_stages
    )
    refs_included = 0
    for stage in raw_stages[:_MAX_STAGES]:
        raw_tasks = list(stage.get("tasks") or [])
        selected_tasks = raw_tasks[:remaining_tasks]
        stage_tasks_total = max(
            len(raw_tasks),
            int(stage.get("tasks_total") or 0),
        )
        remaining_tasks -= len(selected_tasks)
        task_rows_included += len(selected_tasks)

        raw_task_ids = as_list(stage.get("task_ids"))
        selected_task_ids = raw_task_ids[:remaining_task_ids]
        remaining_task_ids -= len(selected_task_ids)
        task_ids_included += len(selected_task_ids)

        raw_active_ids = as_list(stage.get("active_task_ids"))
        selected_active_ids = raw_active_ids[:remaining_active_ids]
        remaining_active_ids -= len(selected_active_ids)
        raw_run_refs = as_list(stage.get("run_group_ids"))
        selected_run_refs = raw_run_refs[:remaining_run_refs]
        remaining_run_refs -= len(selected_run_refs)
        raw_event_refs = as_list(stage.get("source_event_ids"))
        selected_event_refs, _event_refs_omitted = exact_ids(
            raw_event_refs,
            limit=remaining_event_refs,
        )
        remaining_event_refs -= len(selected_event_refs)
        refs_included += len(selected_run_refs) + len(selected_event_refs)
        stages.append({
            "stage_id": _text(stage.get("stage_id"), 120),
            "label": _text(stage.get("label")),
            "status": _text(stage.get("status"), 80),
            "tasks_done": int(stage.get("tasks_done") or 0),
            "tasks_running": int(stage.get("tasks_running") or 0),
            "tasks_failed": int(stage.get("tasks_failed") or 0),
            "tasks_blocked": int(stage.get("tasks_blocked") or 0),
            "active_task_ids": [
                wire_task_id(value)[0]
                for value in selected_active_ids
            ],
            "active_task_ids_opaque": sum(
                1 for value in selected_active_ids if wire_task_id(value)[1]
            ),
            **budget_fields(
                "active_task_ids",
                total=len(raw_active_ids),
                included=len(selected_active_ids),
            ),
            "task_ids": [wire_task_id(value)[0] for value in selected_task_ids],
            "task_ids_opaque": sum(
                1 for value in selected_task_ids if wire_task_id(value)[1]
            ),
            **budget_fields(
                "task_ids",
                total=len(raw_task_ids),
                included=len(selected_task_ids),
            ),
            "tasks": [compact_task_flow_task(task) for task in selected_tasks],
            **budget_fields(
                "tasks",
                total=stage_tasks_total,
                included=len(selected_tasks),
            ),
            "task_rows_total": len(raw_tasks),
            "task_rows_included": len(selected_tasks),
            "task_rows_omitted": len(raw_tasks) - len(selected_tasks),
            "task_rows_truncated": len(raw_tasks) > len(selected_tasks),
            "run_group_ids": [
                wire_id(value, namespace="run")[0]
                for value in selected_run_refs
            ],
            "run_group_ids_opaque": sum(
                1
                for value in selected_run_refs
                if wire_id(value, namespace="run")[1]
            ),
            **budget_fields(
                "run_group_ids",
                total=len(raw_run_refs),
                included=len(selected_run_refs),
            ),
            "source_event_ids": selected_event_refs,
            **budget_fields(
                "source_event_ids",
                total=len(raw_event_refs),
                included=len(selected_event_refs),
            ),
        })
    stage_order = as_list(flow.get("stage_order"))
    active_stage_ids = as_list(flow.get("active_stage_ids"))
    selected_stage_order = stage_order[:_MAX_STAGES]
    selected_active_stages = active_stage_ids[:_MAX_STAGES]
    return {
        "schema_version": "delivery-task-flow.v2",
        "stage_order": [
            wire_id(value, namespace="stage")[0]
            for value in selected_stage_order
        ],
        **budget_fields(
            "stage_order",
            total=len(stage_order),
            included=len(selected_stage_order),
        ),
        "active_stage_ids": [
            wire_id(value, namespace="stage")[0]
            for value in selected_active_stages
        ],
        **budget_fields(
            "active_stage_ids",
            total=len(active_stage_ids),
            included=len(selected_active_stages),
        ),
        "stages": stages,
        "metrics": _compact_mapping(flow.get("metrics"), max_items=12),
        "stage_count": len(raw_stages),
        **budget_fields(
            "stages",
            total=len(raw_stages),
            included=len(stages),
        ),
        **budget_fields(
            "task_rows",
            total=task_rows_total,
            included=task_rows_included,
        ),
        **budget_fields(
            "task_ids",
            total=task_ids_total,
            included=task_ids_included,
        ),
        **budget_fields(
            "refs",
            total=refs_total,
            included=refs_included,
        ),
    }


def compact_task_flow_task(task: dict[str, Any]) -> dict[str, Any]:
    task_id, task_id_opaque = wire_task_id(task.get("task_id"))
    raw_blocked = as_list(task.get("blocked_by"))
    selected_blocked = raw_blocked[:2]
    raw_events = as_list(task.get("source_event_ids"))
    source_event_ids, _source_event_ids_omitted = exact_ids(raw_events, limit=2)
    return {
        "task_id": task_id,
        "task_id_opaque": task_id_opaque,
        "title": _text(task.get("title")),
        "status": _text(task.get("status"), 80),
        "assigned_to": _text(task.get("assigned_to"), 80),
        "phase": _text(task.get("phase"), 80),
        "owner_role": _text(task.get("owner_role"), 80),
        "owner_instance": _text(task.get("owner_instance"), 80),
        "blocked_by": [wire_task_id(value)[0] for value in selected_blocked],
        **budget_fields(
            "blocked_by",
            total=len(raw_blocked),
            included=len(selected_blocked),
        ),
        "source_event_ids": source_event_ids,
        **budget_fields(
            "source_event_ids",
            total=len(raw_events),
            included=len(source_event_ids),
        ),
    }


def compact_run_chain(chain: dict[str, Any]) -> dict[str, Any]:
    raw_stages = list(chain.get("stages") or [])
    selected_stages = raw_stages[:_MAX_STAGES]
    remaining_task_ids = _MAX_TASK_IDS
    stages: list[dict[str, Any]] = []
    task_ids_total = sum(
        len(as_list(stage.get("task_ids")))
        for stage in raw_stages
    )
    task_ids_included = 0
    for stage in selected_stages:
        raw_task_ids = as_list(stage.get("task_ids"))
        selected_task_ids = raw_task_ids[:remaining_task_ids]
        remaining_task_ids -= len(selected_task_ids)
        task_ids_included += len(selected_task_ids)
        via_event_ids, via_omitted = exact_ids([stage.get("via_event_id")], limit=1)
        causation_ids, causation_omitted = exact_ids(
            [stage.get("causation_id")],
            limit=1,
        )
        stages.append({
            "stage": _text(stage.get("stage"), 120),
            "status": _text(stage.get("status"), 80),
            "entered_at": _text(stage.get("entered_at"), 80),
            "completed_at": _text(stage.get("completed_at"), 80),
            "via_event_id": via_event_ids[0] if via_event_ids else "",
            "via_event_id_omitted": via_omitted > 0,
            "causation_id": causation_ids[0] if causation_ids else "",
            "causation_id_omitted": causation_omitted > 0,
            "occurrences": int(stage.get("occurrences") or 0),
            "seq_first": stage.get("seq_first"),
            "seq_last": stage.get("seq_last"),
            "task_ids": [wire_task_id(value)[0] for value in selected_task_ids],
            "task_ids_opaque": sum(
                1 for value in selected_task_ids if wire_task_id(value)[1]
            ),
            **budget_fields(
                "task_ids",
                total=len(raw_task_ids),
                included=len(selected_task_ids),
            ),
        })
    return {
        "schema_version": "run-chain.v2",
        "status": _text(chain.get("status"), 80),
        "trigger": _compact_mapping(chain.get("trigger"), max_items=6),
        "stages": stages,
        "stage_count": len(raw_stages),
        **budget_fields(
            "stages",
            total=len(raw_stages),
            included=len(stages),
        ),
        **budget_fields(
            "task_ids",
            total=task_ids_total,
            included=task_ids_included,
        ),
    }


def as_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value] if value not in (None, "") else []


def _compact_mapping(value: Any, *, max_items: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key, item in list(value.items())[:max_items]:
        if isinstance(item, str):
            result[str(key)] = _text(item)
        elif isinstance(item, (int, float, bool)) or item is None:
            result[str(key)] = item
        elif isinstance(item, list):
            result[str(key)] = _strings(item, limit=4, chars=96)
    return result


def _strings(value: Any, *, limit: int, chars: int) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        value = [value] if value not in (None, "") else []
    return [_text(item, chars) for item in list(value)[:limit] if _text(item, chars)]


def _text(value: Any, limit: int = _MAX_TEXT_CHARS) -> str:
    text = str(value or "")
    return text if len(text) <= limit else f"{text[: max(0, limit - 1)]}…"


__all__ = ["as_list", "compact_run_chain", "compact_task_flow"]
