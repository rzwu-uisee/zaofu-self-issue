"""Restart reconciliation for terminal Goal task settlement."""

from __future__ import annotations

from typing import Any

from zf.runtime.orchestrator_types import OrchestratorDecision


def reconcile_goal_terminal_task_settlement(
    runtime: Any,
) -> list[OrchestratorDecision]:
    """Replay terminal task settlement once after a runtime restart."""

    if getattr(runtime, "_goal_terminal_task_settlement_reconciled", False):
        return []
    try:
        events = runtime.event_log.read_all()
    except Exception:
        return []
    runtime._goal_terminal_task_settlement_reconciled = True
    decisions: list[OrchestratorDecision] = []
    for event in reversed(events):
        if event.type != "run.goal.completed":
            continue
        decision = runtime._settle_candidate_tasks_done(event)
        if decision is not None:
            decisions.append(decision)
    return decisions


__all__ = ["reconcile_goal_terminal_task_settlement"]
