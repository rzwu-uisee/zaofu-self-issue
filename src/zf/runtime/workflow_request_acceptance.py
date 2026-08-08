"""Canonical Task acceptance inheritance for Workflow requests."""

from __future__ import annotations

from typing import Any

from zf.runtime.task_contract_snapshot import criterion_text


def inherit_task_acceptance(
    parameters: dict[str, Any],
    workflow_task: Any | None,
) -> dict[str, Any]:
    if workflow_task is None or "acceptance" in parameters:
        return parameters
    acceptance = [
        criterion_text(item)
        for item in workflow_task.contract.acceptance_criteria
    ]
    acceptance = [item for item in acceptance if item]
    if acceptance:
        parameters["acceptance"] = acceptance
    return parameters


__all__ = ["inherit_task_acceptance"]
