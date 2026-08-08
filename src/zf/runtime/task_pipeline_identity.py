"""Stable logical identity for Task Pipeline stage operations.

Worker, pane, provider-session, and worktree placement are deliberately absent
from these keys.  A transport retry keeps the operation identity; a semantic
rework increments ``operation_generation``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from zf.runtime.workflow_operation import stable_operation_id


TASK_PIPELINE_OPERATION_TYPE = "task-stage"


class TaskPipelineIdentityError(ValueError):
    """A Task Pipeline identity is incomplete or invalid."""


@dataclass(frozen=True)
class TaskPipelineOperationIdentity:
    workflow_run_id: str
    task_id: str
    task_map_generation: str
    stage: str
    stage_revision: str
    operation_generation: int
    pipeline_key: str
    operation_key: str
    operation_id: str


def task_pipeline_operation_identity(
    *,
    workflow_run_id: str,
    task_id: str,
    task_map_generation: str,
    stage: str,
    stage_revision: str,
    operation_generation: int,
) -> TaskPipelineOperationIdentity:
    """Build an immutable stage-operation identity independent of placement."""

    values = {
        "workflow_run_id": str(workflow_run_id or "").strip(),
        "task_id": str(task_id or "").strip(),
        "task_map_generation": str(task_map_generation or "").strip(),
        "stage": str(stage or "").strip(),
        "stage_revision": str(stage_revision or "").strip(),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise TaskPipelineIdentityError(
            "Task Pipeline identity requires " + ", ".join(missing)
        )
    try:
        generation = int(operation_generation)
    except (TypeError, ValueError) as exc:
        raise TaskPipelineIdentityError(
            "operation_generation must be a positive integer"
        ) from exc
    if generation < 1:
        raise TaskPipelineIdentityError(
            "operation_generation must be a positive integer"
        )

    pipeline_digest = _digest(
        values["workflow_run_id"],
        values["task_id"],
        values["task_map_generation"],
    )
    pipeline_key = f"tp-{_safe(values['task_id'])}-{pipeline_digest}"
    revision_digest = _digest(values["stage_revision"])
    operation_key = (
        f"{pipeline_key}:{_safe(values['stage'])}:"
        f"revision:{revision_digest}:generation:{generation}"
    )
    operation_id = stable_operation_id(
        workflow_run_id=values["workflow_run_id"],
        parent_stage_id=values["stage"],
        operation_key=operation_key,
        operation_type=TASK_PIPELINE_OPERATION_TYPE,
    )
    return TaskPipelineOperationIdentity(
        **values,
        operation_generation=generation,
        pipeline_key=pipeline_key,
        operation_key=operation_key,
        operation_id=operation_id,
    )


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def _safe(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in {"-", "_"} else "-"
        for character in value
    ).strip("-")
    return normalized[:48] or "stage"


__all__ = [
    "TASK_PIPELINE_OPERATION_TYPE",
    "TaskPipelineIdentityError",
    "TaskPipelineOperationIdentity",
    "task_pipeline_operation_identity",
]
