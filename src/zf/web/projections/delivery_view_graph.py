"""Bounded, relationship-preserving graph compaction for Delivery v2."""

from __future__ import annotations

from typing import Any

from zf.web.projections.delivery_view_wire import (
    budget_fields,
    exact_ids,
    wire_id,
    wire_node_id,
    wire_task_id,
)

_MAX_GOAL_NODES = 40
_MAX_GOAL_CLAIMS = 16
_MAX_GOAL_TASKS = 16
_MAX_GOAL_EDGES = 80
_MAX_GOAL_DIAGNOSTICS = 12
_MAX_RELATION_IDS = 4
_MAX_TEXT_CHARS = 120


def _compact_goal_coverage(graph: dict[str, Any]) -> dict[str, Any]:
    """Keep claim-to-task ownership resolvable inside a fixed node budget."""

    raw_nodes = [node for node in list(graph.get("nodes") or []) if isinstance(node, dict)]
    raw_edges = [edge for edge in list(graph.get("edges") or []) if isinstance(edge, dict)]
    by_id = {str(node.get("node_id") or ""): node for node in raw_nodes}
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()

    def include(node: dict[str, Any]) -> bool:
        node_id = str(node.get("node_id") or "")
        if not node_id or node_id in selected_ids or len(selected) >= _MAX_GOAL_NODES:
            return False
        selected.append(node)
        selected_ids.add(node_id)
        return True

    for node in raw_nodes:
        if node.get("kind") == "goal":
            include(node)
            break

    claims = [node for node in raw_nodes if node.get("kind") == "goal_claim"][:_MAX_GOAL_CLAIMS]
    related_task_ids: list[str] = []
    for claim in claims:
        include(claim)
        for task_id in list(claim.get("task_ids") or []):
            task_id = str(task_id or "")
            if task_id and task_id not in related_task_ids:
                related_task_ids.append(task_id)
    for task_id in related_task_ids[:_MAX_GOAL_TASKS]:
        task = by_id.get(f"task:{task_id}")
        if task is not None:
            include(task)

    priority = {"gap": 0, "verification_result": 1, "goal_closure": 2, "task": 3}
    remaining = [node for node in raw_nodes if str(node.get("node_id") or "") not in selected_ids]
    remaining.sort(key=lambda node: priority.get(str(node.get("kind") or ""), 4))
    for node in remaining:
        if len(selected) >= _MAX_GOAL_NODES:
            break
        include(node)

    matching_edges = [
        edge for edge in raw_edges
        if str(edge.get("from") or "") in selected_ids
        and str(edge.get("to") or "") in selected_ids
    ]
    edges = [
        {
            "from": wire_node_id(edge.get("from"))[0],
            "to": wire_node_id(edge.get("to"))[0],
            "kind": _text(edge.get("kind"), 80),
        }
        for edge in matching_edges[:_MAX_GOAL_EDGES]
    ]
    diagnostics = [
        item
        for item in list(graph.get("diagnostics") or [])
        if isinstance(item, dict)
    ]
    selected_diagnostics = diagnostics[:_MAX_GOAL_DIAGNOSTICS]
    return {
        "schema_version": "goal-coverage-graph.v2",
        "coverage_mode": _text(graph.get("coverage_mode"), 80),
        "identity": _compact_mapping(graph.get("identity"), max_items=12),
        "currentness": _compact_mapping(graph.get("currentness"), max_items=8),
        "summary": _compact_mapping(graph.get("summary"), max_items=12),
        "nodes": [_compact_goal_node(node, selected_ids=selected_ids) for node in selected],
        "edges": edges,
        "diagnostics": [
            {
                "code": _text(item.get("code") or item.get("kind"), 80),
                "message": _text(item.get("message")),
                "goal_claim_id": wire_id(
                    item.get("goal_claim_id"),
                    namespace="claim",
                )[0],
                "task_id": wire_task_id(item.get("task_id"))[0],
            }
            for item in selected_diagnostics
        ],
        "node_count": len(raw_nodes),
        **budget_fields(
            "nodes",
            total=len(raw_nodes),
            included=len(selected),
        ),
        "edge_count": len(raw_edges),
        **budget_fields(
            "edges",
            total=len(raw_edges),
            included=len(edges),
        ),
        **budget_fields(
            "diagnostics",
            total=len(diagnostics),
            included=len(selected_diagnostics),
        ),
    }


def _compact_goal_node(node: dict[str, Any], *, selected_ids: set[str]) -> dict[str, Any]:
    scalar_keys = (
        "kind", "title", "status", "owner", "source_ref",
        "result_ref", "gap_ref",
        "plan_coverage", "execution", "task_verification", "closure",
        "contract_revision",
    )
    result = {
        key: _text(node.get(key))
        for key in scalar_keys
        if node.get(key) not in (None, "")
    }
    node_id, node_id_opaque = wire_node_id(node.get("node_id"))
    result["node_id"] = node_id
    if node_id_opaque:
        result["node_id_opaque"] = True
    if node.get("goal_id") not in (None, ""):
        goal_id, goal_id_opaque = wire_id(node.get("goal_id"), namespace="goal")
        result["goal_id"] = goal_id
        if goal_id_opaque:
            result["goal_id_opaque"] = True
    if node.get("task_id") not in (None, ""):
        task_id, task_id_opaque = wire_task_id(node.get("task_id"))
        result["task_id"] = task_id
        if task_id_opaque:
            result["task_id_opaque"] = True
    if node.get("goal_claim_id") not in (None, ""):
        claim_id, claim_id_opaque = wire_id(
            node.get("goal_claim_id"),
            namespace="claim",
        )
        result["goal_claim_id"] = claim_id
        if claim_id_opaque:
            result["goal_claim_id_opaque"] = True

    raw_task_ids = [
        str(value) for value in list(node.get("task_ids") or []) if str(value)
    ]
    if raw_task_ids:
        selected_task_ids = raw_task_ids[:_MAX_RELATION_IDS]
        result["task_ids"] = [
            wire_task_id(value)[0]
            for value in selected_task_ids
        ]
        result.update(budget_fields(
            "task_ids",
            total=len(raw_task_ids),
            included=len(selected_task_ids),
        ))
    raw_claim_ids = [
        str(value) for value in list(node.get("goal_claim_ids") or []) if str(value)
    ]
    if raw_claim_ids:
        selected_claim_ids = raw_claim_ids[:_MAX_RELATION_IDS]
        result["goal_claim_ids"] = [
            wire_id(value, namespace="claim")[0]
            for value in selected_claim_ids
        ]
        result.update(budget_fields(
            "goal_claim_ids",
            total=len(raw_claim_ids),
            included=len(selected_claim_ids),
        ))
    for key in ("supporting_result_refs", "evidence_refs", "gap_refs", "stale_reasons"):
        raw_values = list(node.get(key) or [])
        values, _omitted = exact_ids(raw_values, limit=_MAX_RELATION_IDS)
        if values:
            result[key] = values
        if raw_values:
            result.update(budget_fields(
                key,
                total=len(raw_values),
                included=len(values),
            ))
    for key in ("mandatory", "current"):
        if key in node:
            result[key] = bool(node.get(key))
    if node.get("kind") == "goal_claim":
        missing = [
            task_id
            for task_id in raw_task_ids
            if f"task:{task_id}" not in selected_ids
        ]
        returned_task_ids = min(len(raw_task_ids), _MAX_RELATION_IDS)
        returned_missing_ids = missing[:_MAX_RELATION_IDS]
        result["task_details"] = {
            "total": len(raw_task_ids),
            "included": len(raw_task_ids) - len(missing),
            "missing_count": len(missing),
            "task_ids_returned": returned_task_ids,
            **budget_fields(
                "task_ids",
                total=len(raw_task_ids),
                included=returned_task_ids,
            ),
            "missing_task_ids": [
                wire_task_id(value)[0]
                for value in returned_missing_ids
            ],
            **budget_fields(
                "missing_task_ids",
                total=len(missing),
                included=len(returned_missing_ids),
            ),
        }
    return result


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


__all__ = ["_compact_goal_coverage"]
