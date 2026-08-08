"""Projection helpers for agent-produced portable plan matrices."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from zf.runtime.fanout_payload_data import payload_or_report_value


def first_normalized_plan_ports(
    payloads: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    for source in payloads:
        plan_ports = payload_or_report_value(source, "plan_ports")
        if not isinstance(plan_ports, list):
            synthesis = payload_or_report_value(
                source,
                "plan_synthesis_result",
            )
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
