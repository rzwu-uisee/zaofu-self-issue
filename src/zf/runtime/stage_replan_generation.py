"""Shared policy for an owner-approved stage replan generation."""

from __future__ import annotations

from typing import Any


STAGE_REPLAN_GENERATION_EXPECTED_EVENTS = frozenset({
    "run.goal.updated",
    "run.manager.action.applied",
})


def stage_replan_generation_preflight(
    payload: dict[str, Any],
) -> dict[str, Any]:
    failures: list[str] = []
    if not str(payload.get("escalation_event_id") or ""):
        failures.append("missing_escalation_event_id")
    if not str(payload.get("approval_ref") or ""):
        failures.append("missing_approval_ref")
    expected = sorted(STAGE_REPLAN_GENERATION_EXPECTED_EVENTS)
    return {
        "schema_version": "run-manager.action-preflight.v1",
        "status": "blocked" if failures else "passed",
        "failures": failures,
        "warnings": [],
        "safe_resume_action": "stage_replan_new_generation",
        "expected_downstream_events": expected,
        "verify_condition": (
            str(payload.get("verify_condition") or "")
            or "expected_downstream_event:" + ",".join(expected)
        ),
    }


__all__ = [
    "STAGE_REPLAN_GENERATION_EXPECTED_EVENTS",
    "stage_replan_generation_preflight",
]
