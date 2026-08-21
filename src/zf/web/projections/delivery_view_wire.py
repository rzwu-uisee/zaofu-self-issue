"""Bounded identity helpers shared by Delivery v2 wire projections."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any


MAX_WIRE_ID_CHARS = 96


def budget_fields(prefix: str, *, total: int, included: int) -> dict[str, Any]:
    """Return one truthful, consistently named collection budget summary."""

    safe_total = max(0, int(total))
    safe_included = min(safe_total, max(0, int(included)))
    omitted = safe_total - safe_included
    return {
        f"{prefix}_total": safe_total,
        f"{prefix}_included": safe_included,
        f"{prefix}_omitted": omitted,
        f"{prefix}_truncated": omitted > 0,
    }


def wire_id(value: object, *, namespace: str) -> tuple[str, bool]:
    """Return a bounded stable identity and whether it is an opaque handle."""

    raw = str(value or "")
    if len(raw) <= MAX_WIRE_ID_CHARS:
        return raw, False
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"{namespace}-ref:sha256:{digest}", True


def wire_task_id(value: object) -> tuple[str, bool]:
    return wire_id(value, namespace="task")


def wire_node_id(value: object) -> tuple[str, bool]:
    raw = str(value or "")
    if raw.startswith("task:"):
        task_id, opaque = wire_task_id(raw.removeprefix("task:"))
        return f"task:{task_id}", opaque
    return wire_id(raw, namespace="node")


def exact_ids(
    values: Iterable[object],
    *,
    limit: int,
    max_chars: int = MAX_WIRE_ID_CHARS,
) -> tuple[list[str], int]:
    """Keep only bounded exact refs; return rows plus total omitted count."""

    raw = list(dict.fromkeys(
        str(value or "")
        for value in values
        if str(value or "")
    ))
    selected = [value for value in raw if len(value) <= max_chars][:limit]
    return selected, len(raw) - len(selected)


__all__ = [
    "MAX_WIRE_ID_CHARS",
    "budget_fields",
    "exact_ids",
    "wire_id",
    "wire_node_id",
    "wire_task_id",
]
