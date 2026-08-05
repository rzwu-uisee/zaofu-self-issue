"""Optimistic concurrency contracts for durable workflow requests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class WorkflowRequestError(ValueError):
    pass


class WorkflowRequestConflict(WorkflowRequestError):
    pass


def check_workflow_request_preconditions(
    prior: Mapping[str, Any],
    *,
    expected_revision: int | None,
    expected_requirement_digest: str,
) -> None:
    current_revision = int(prior.get("revision") or 0)
    current_digest = str(prior.get("requirement_spec_digest") or "").strip()
    if expected_revision is not None and expected_revision != current_revision:
        raise WorkflowRequestConflict(
            "stale workflow request revision: "
            f"expected {expected_revision}, current {current_revision}"
        )
    expected_digest = str(expected_requirement_digest or "").strip()
    if expected_digest and expected_digest != current_digest:
        raise WorkflowRequestConflict(
            "stale workflow requirement digest: "
            f"expected {expected_digest}, current {current_digest}"
        )


__all__ = [
    "WorkflowRequestConflict",
    "WorkflowRequestError",
    "check_workflow_request_preconditions",
]
