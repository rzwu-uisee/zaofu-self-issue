"""Projection helpers for agent-produced portable plan matrices."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def first_normalized_plan_ports(
    payloads: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    for source in payloads:
        plan_ports = source.get("plan_ports")
        if not isinstance(plan_ports, list):
            synthesis = source.get("plan_synthesis_result")
            plan_ports = (
                synthesis.get("plan_ports")
                if isinstance(synthesis, dict)
                else None
            )
        if isinstance(plan_ports, list):
            normalized = [
                dict(item) for item in plan_ports if isinstance(item, dict)
            ]
            if normalized:
                return normalized
    return []


__all__ = ["first_normalized_plan_ports"]
