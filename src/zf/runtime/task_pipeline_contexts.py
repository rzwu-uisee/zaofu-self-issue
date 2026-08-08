"""Replay Task Pipeline generation ownership from canonical events."""

from __future__ import annotations

from typing import Any, Iterable

from zf.core.events.model import ZfEvent


TASK_PIPELINE_GENERATION_SCHEMA = "task-pipeline-generation.v1"
TASK_PIPELINE_GENERATION_ADMITTED = "task.pipeline.generation.admitted"


def task_pipeline_generation_contexts(
    events: Iterable[ZfEvent],
) -> dict[str, dict[str, Any]]:
    """Project the latest admitted generation context for each Task."""

    contexts: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.type != TASK_PIPELINE_GENERATION_ADMITTED:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("schema_version") or "") != TASK_PIPELINE_GENERATION_SCHEMA:
            continue
        context = {
            **payload,
            "generation_admitted_event_id": event.id,
            "generation_admitted_at": event.ts,
        }
        for raw_task_id in payload.get("task_ids") or []:
            task_id = str(raw_task_id or "").strip()
            if task_id:
                contexts[task_id] = dict(context)
    return contexts


def task_pipeline_managed_task_ids(runtime: Any) -> set[str]:
    """Return Tasks fenced away from the legacy backlog dispatcher."""

    return set(task_pipeline_generation_contexts(runtime.event_log.read_all()))


__all__ = [
    "TASK_PIPELINE_GENERATION_ADMITTED",
    "TASK_PIPELINE_GENERATION_SCHEMA",
    "task_pipeline_generation_contexts",
    "task_pipeline_managed_task_ids",
]
