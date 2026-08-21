"""Bounded Work graph projection for the on-demand Delivery Graph lens."""

from __future__ import annotations

from typing import Any

from zf.core.task.schema import Task
from zf.runtime.delivery_trace import _node_counts
from zf.web.projections.delivery_view_lifecycle import _compact_task_lifecycle
from zf.web.projections.delivery_view_wire import (
    budget_fields,
    wire_id,
    wire_task_id,
)


_MAX_WORK_NODES = 32
_MAX_NODE_RELATIONS = 4
_MAX_TEXT_CHARS = 120
_ACTIONABLE = {
    "blocked", "failed", "error", "rejected", "in_progress", "running",
    "review", "test", "judge", "dispatched", "rework",
}


def compact_work_projection(
    *,
    execution_graph: dict[str, Any],
    goal_graph: dict[str, Any],
    compact_goal_graph: dict[str, Any],
    evidence_by_task: dict[str, list[str]],
    lifecycle: dict[str, Any],
    tasks: dict[str, Task],
    goal_scoped: bool = False,
) -> dict[str, Any]:
    """Keep the original Work topology useful without restoring a full view payload."""

    execution_nodes = [
        node
        for node in list(execution_graph.get("nodes") or [])
        if isinstance(node, dict) and str(node.get("task_id") or "")
    ]
    raw_nodes = _include_canonical_tasks(
        execution_nodes,
        evidence_by_task=evidence_by_task,
        tasks=tasks,
    )
    task_claims = _task_claims(goal_graph)
    visible_claim_ids = {
        str(node.get("goal_claim_id") or "")
        for node in list(compact_goal_graph.get("nodes") or [])
        if isinstance(node, dict) and node.get("kind") == "goal_claim"
    }
    eligible_nodes = [
        node
        for node in raw_nodes
        if (
            not goal_scoped
            and not task_claims.get(str(node.get("task_id") or ""))
        )
        or (
            bool(task_claims.get(str(node.get("task_id") or "")))
            and any(
                wire_id(claim_id, namespace="claim")[0] in visible_claim_ids
                for claim_id in task_claims[str(node.get("task_id") or "")]
            )
        )
    ]
    scoped_nodes = (
        [
            node
            for node in raw_nodes
            if task_claims.get(str(node.get("task_id") or ""))
        ]
        if goal_scoped
        else raw_nodes
    )
    preferred_ids = _linked_goal_task_ids(compact_goal_graph)
    preferred = set(preferred_ids)
    ordered = sorted(
        eligible_nodes,
        key=lambda node: _node_priority(node, preferred),
    )
    selected = ordered[:_MAX_WORK_NODES]
    selected_raw_ids = [str(node.get("task_id") or "") for node in selected]
    counts = _node_counts(scoped_nodes)
    compact_lifecycle = _compact_work_lifecycle(_compact_task_lifecycle(
        lifecycle,
        allowed_task_ids=set(selected_raw_ids),
        task_statuses={
            task_id: tasks[task_id].status
            for task_id in selected_raw_ids
            if task_id in tasks
        },
        preferred_task_ids=selected_raw_ids,
    ))
    return {
        "execution_graph": {
            "schema_version": "execution-graph.v2",
            "task_count": len(scoped_nodes),
            "done_count": counts["done"],
            "in_progress_count": counts["in_progress"],
            "blocked_count": counts["blocked"],
            "waiting_count": counts["waiting"],
            "nodes": [
                _compact_work_node(
                    node,
                    claim_ids=task_claims.get(str(node.get("task_id") or ""), []),
                    visible_claim_ids=visible_claim_ids,
                )
                for node in selected
            ],
            **budget_fields(
                "nodes",
                total=len(scoped_nodes),
                included=len(selected),
            ),
            "edges": [],
            "waves": [],
            "nodes_only": True,
        },
        "task_lifecycle": compact_lifecycle,
    }


def scope_work_goal_graph(
    goal_graph: dict[str, Any],
    *,
    goal_id: str,
) -> tuple[dict[str, Any], bool]:
    """Return the Goal subgraph selected by an explicit Work expansion.

    The current feature contract normally contains one Goal.  Keeping this
    projection edge-driven makes the optional ``goal_id`` query truthful if a
    future task-map exposes more than one Goal, while preserving the legacy
    unscoped Work response when the query is absent.
    """

    requested = goal_id.strip()
    raw_nodes = [
        node for node in list(goal_graph.get("nodes") or [])
        if isinstance(node, dict)
    ]
    raw_edges = [
        edge for edge in list(goal_graph.get("edges") or [])
        if isinstance(edge, dict)
    ]
    goals = [node for node in raw_nodes if node.get("kind") == "goal"]
    goal = next(
        (
            node
            for node in goals
            if str(node.get("goal_id") or "") == requested
            or str(node.get("node_id") or "") == f"goal:{requested}"
        ),
        None,
    )
    if goal is None:
        diagnostics = [
            item for item in list(goal_graph.get("diagnostics") or [])
            if isinstance(item, dict)
        ]
        diagnostics.append({
            "kind": "work_goal_not_found",
            "message": "selected Goal is no longer available",
        })
        return {
            **goal_graph,
            "identity": {
                **dict(goal_graph.get("identity") or {}),
                "goal_id": requested,
            },
            "summary": {
                "mandatory_claims": 0,
                "planned_claims": 0,
                "claims_with_current_results": 0,
                "closed_claims": 0,
                "open_gaps": 0,
            },
            "nodes": [],
            "edges": [],
            "diagnostics": diagnostics,
        }, False

    by_id = {
        str(node.get("node_id") or ""): node
        for node in raw_nodes
        if str(node.get("node_id") or "")
    }
    goal_node_id = str(goal.get("node_id") or f"goal:{requested}")
    claim_node_ids: set[str] = set()
    for edge in raw_edges:
        source = str(edge.get("from") or "")
        target = str(edge.get("to") or "")
        candidate = target if source == goal_node_id else source if target == goal_node_id else ""
        if candidate and (by_id.get(candidate) or {}).get("kind") == "goal_claim":
            claim_node_ids.add(candidate)
    # Legacy-derived single-Goal graphs may omit topology edges.  In that
    # unambiguous case all claims still belong to the sole Goal.
    if not claim_node_ids and len(goals) == 1:
        claim_node_ids = {
            str(node.get("node_id") or "")
            for node in raw_nodes
            if node.get("kind") == "goal_claim"
            and str(node.get("node_id") or "")
        }

    claim_ids = {
        str(by_id[node_id].get("goal_claim_id") or "")
        for node_id in claim_node_ids
        if node_id in by_id
    } - {""}
    task_ids = {
        str(task_id or "")
        for node_id in claim_node_ids
        for task_id in list((by_id.get(node_id) or {}).get("task_ids") or [])
        if str(task_id or "")
    }
    task_ids.update(
        str(node.get("task_id") or "")
        for node in raw_nodes
        if node.get("kind") == "task"
        and claim_ids.intersection(
            str(value or "") for value in list(node.get("goal_claim_ids") or [])
        )
        and str(node.get("task_id") or "")
    )
    selected_node_ids = {goal_node_id, *claim_node_ids}
    for node in raw_nodes:
        node_id = str(node.get("node_id") or "")
        task_id = str(node.get("task_id") or "")
        if task_id in task_ids:
            selected_node_ids.add(node_id)
    selected_nodes = [
        node
        for node in raw_nodes
        if str(node.get("node_id") or "") in selected_node_ids
    ]
    selected_edges = [
        edge
        for edge in raw_edges
        if str(edge.get("from") or "") in selected_node_ids
        and str(edge.get("to") or "") in selected_node_ids
    ]
    selected_diagnostics = [
        item
        for item in list(goal_graph.get("diagnostics") or [])
        if isinstance(item, dict)
        and (
            not str(item.get("goal_claim_id") or "")
            or str(item.get("goal_claim_id") or "") in claim_ids
        )
        and (
            not str(item.get("task_id") or "")
            or str(item.get("task_id") or "") in task_ids
        )
    ]
    claims = [
        node for node in selected_nodes if node.get("kind") == "goal_claim"
    ]
    return {
        **goal_graph,
        "identity": {
            **dict(goal_graph.get("identity") or {}),
            "goal_id": requested,
        },
        "summary": {
            "mandatory_claims": sum(node.get("mandatory") is not False for node in claims),
            "planned_claims": sum(node.get("plan_coverage") == "covered" for node in claims),
            "claims_with_current_results": sum(
                node.get("task_verification") == "passed" for node in claims
            ),
            "closed_claims": sum(
                node.get("closure") in {"closed", "waived"} for node in claims
            ),
            "open_gaps": sum(len(list(node.get("gap_refs") or [])) for node in claims),
        },
        "nodes": selected_nodes,
        "edges": selected_edges,
        "diagnostics": selected_diagnostics,
    }, True


def _include_canonical_tasks(
    execution_nodes: list[dict[str, Any]],
    *,
    evidence_by_task: dict[str, list[str]],
    tasks: dict[str, Task],
) -> list[dict[str, Any]]:
    """Include every scoped canonical task omitted by an accepted task-map join."""

    result = list(execution_nodes)
    included = {str(node.get("task_id") or "") for node in result}
    for task_id, task in tasks.items():
        if task_id in included:
            continue
        contract = task.contract
        owner = str(
            task.assigned_to
            or task.execution_binding.owner
            or contract.owner_instance
            or contract.owner_role
            or ""
        )
        result.append({
            "task_id": task_id,
            "title": task.title or task_id,
            "planned": {
                "owner_role": contract.owner_role,
                "owner_instance": contract.owner_instance,
                "blocked_by": list(task.blocked_by),
            },
            "actual": {
                "status": task.status,
                "assigned_to": owner,
                "evidence_events": list(evidence_by_task.get(task_id, [])),
            },
            "drift": [],
        })
        included.add(task_id)
    return result


def _linked_goal_task_ids(goal_graph: dict[str, Any]) -> list[str]:
    linked: list[str] = []
    for node in list(goal_graph.get("nodes") or []):
        if not isinstance(node, dict):
            continue
        if node.get("kind") == "task" and list(node.get("goal_claim_ids") or []):
            task_id = str(node.get("task_id") or "")
            if task_id and task_id not in linked:
                linked.append(task_id)
        if node.get("kind") == "goal_claim":
            for value in list(node.get("task_ids") or []):
                task_id = str(value or "")
                if task_id and task_id not in linked:
                    linked.append(task_id)
    return linked


def _task_claims(goal_graph: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for node in list(goal_graph.get("nodes") or []):
        if not isinstance(node, dict):
            continue
        if node.get("kind") == "task":
            task_id = str(node.get("task_id") or "")
            values = [str(value or "") for value in list(node.get("goal_claim_ids") or [])]
            if task_id:
                result.setdefault(task_id, []).extend(value for value in values if value)
        elif node.get("kind") == "goal_claim":
            claim_id = str(node.get("goal_claim_id") or "")
            if not claim_id:
                continue
            for value in list(node.get("task_ids") or []):
                task_id = str(value or "")
                if task_id:
                    result.setdefault(task_id, []).append(claim_id)
    return {
        task_id: list(dict.fromkeys(claim_ids))
        for task_id, claim_ids in result.items()
    }


def _node_priority(
    node: dict[str, Any],
    preferred: set[str],
) -> tuple[int, str]:
    task_id = str(node.get("task_id") or "")
    status = str((node.get("actual") or {}).get("status") or "").lower()
    if status in _ACTIONABLE:
        rank = 0
    elif wire_task_id(task_id)[0] in preferred:
        rank = 1
    else:
        rank = 2
    return rank, task_id


def _compact_work_node(
    node: dict[str, Any],
    *,
    claim_ids: list[str],
    visible_claim_ids: set[str],
) -> dict[str, Any]:
    raw_task_id = str(node.get("task_id") or "")
    task_id, task_id_opaque = wire_task_id(raw_task_id)
    planned = node.get("planned") if isinstance(node.get("planned"), dict) else {}
    actual = node.get("actual") if isinstance(node.get("actual"), dict) else {}
    blocked_by = list(dict.fromkeys(
        str(value or "")
        for value in list(planned.get("blocked_by") or [])
        if str(value or "")
    ))
    evidence = list(dict.fromkeys(
        str(value or "")
        for value in list(actual.get("evidence_events") or [])
        if str(value or "")
    ))
    selected_blockers = blocked_by[:_MAX_NODE_RELATIONS]
    selected_evidence = evidence[:_MAX_NODE_RELATIONS]
    ordered_claim_ids = [
        *[
            value
            for value in claim_ids
            if wire_id(value, namespace="claim")[0] in visible_claim_ids
        ],
        *[
            value
            for value in claim_ids
            if wire_id(value, namespace="claim")[0] not in visible_claim_ids
        ],
    ]
    selected_claim_ids = ordered_claim_ids[:_MAX_NODE_RELATIONS]
    return {
        "task_id": task_id,
        "task_id_opaque": task_id_opaque,
        "title": _text(node.get("title")),
        "goal_claim_ids": [
            wire_id(value, namespace="claim")[0]
            for value in selected_claim_ids
        ],
        **budget_fields(
            "goal_claim_ids",
            total=len(claim_ids),
            included=len(selected_claim_ids),
        ),
        "planned": {
            "owner_role": _text(planned.get("owner_role"), 80),
            "owner_instance": _text(planned.get("owner_instance"), 96),
            "blocked_by": [wire_task_id(value)[0] for value in selected_blockers],
            **budget_fields(
                "blocked_by",
                total=len(blocked_by),
                included=len(selected_blockers),
            ),
        },
        "actual": {
            "status": _text(actual.get("status"), 80),
            "assigned_to": _text(actual.get("assigned_to"), 96),
            "evidence_events": [
                wire_id(value, namespace="event")[0]
                for value in selected_evidence
            ],
            **budget_fields(
                "evidence_events",
                total=len(evidence),
                included=len(selected_evidence),
            ),
        },
        "drift": [],
    }


def _compact_work_lifecycle(lifecycle: dict[str, Any]) -> dict[str, Any]:
    """Drop lifecycle fields with no Work UI consumer while preserving bounds."""

    tasks: dict[str, Any] = {}
    for task_id, raw in (lifecycle.get("tasks") or {}).items():
        item = raw if isinstance(raw, dict) else {}
        raw_tries = [attempt for attempt in list(item.get("tries") or []) if isinstance(attempt, dict)]
        tasks[task_id] = {
            "task_id_opaque": bool(item.get("task_id_opaque")),
            "state_history": [],
            **budget_fields(
                "state_history",
                total=int(item.get("state_history_total") or 0),
                included=0,
            ),
            "tries": [
                {
                    "try": int(attempt.get("try") or 0),
                    "outcome": _text(attempt.get("outcome"), 80),
                    "rework_kind": _text(attempt.get("rework_kind"), 120),
                    "gate_results": [
                        {
                            "type": _text(gate.get("type"), 120),
                            "passed": bool(gate.get("passed")),
                            "event_id": str(gate.get("event_id") or ""),
                            "event_id_opaque": bool(gate.get("event_id_opaque")),
                        }
                        for gate in list(attempt.get("gate_results") or [])
                        if isinstance(gate, dict)
                    ],
                    **{
                        key: attempt[key]
                        for key in (
                            "gate_results_total",
                            "gate_results_included",
                            "gate_results_omitted",
                            "gate_results_truncated",
                        )
                        if key in attempt
                    },
                }
                for attempt in raw_tries
            ],
            **{
                key: item[key]
                for key in (
                    "tries_total",
                    "tries_included",
                    "tries_omitted",
                    "tries_truncated",
                    "gate_results_total",
                    "gate_results_included",
                    "gate_results_omitted",
                    "gate_results_truncated",
                )
                if key in item
            },
        }
    state_total = int(lifecycle.get("state_history_total") or 0)
    return {
        **lifecycle,
        "tasks": tasks,
        "state_history_included": 0,
        "state_history_omitted": state_total,
        "state_history_truncated": state_total > 0,
    }


def _text(value: Any, limit: int = _MAX_TEXT_CHARS) -> str:
    text = str(value or "")
    return text if len(text) <= limit else f"{text[: max(0, limit - 1)]}…"


__all__ = ["compact_work_projection", "scope_work_goal_graph"]
