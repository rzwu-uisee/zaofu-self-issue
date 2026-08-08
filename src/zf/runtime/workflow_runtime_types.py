"""Canonical types owned by the deterministic workflow runtime."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WorkflowRuntimeDecision:
    action: str  # dispatch, move, respawn, capture, skip
    task_id: str | None = None
    role: str | None = None
    target_role: str | None = None
    reason: str = ""


__all__ = ["WorkflowRuntimeDecision"]
