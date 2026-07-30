"""Suppress recovery fanout triggers for an already verified generation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.writer_fanout_generation import completed_writer_generation


def suppress_completed_generation(
    runtime: Any,
    *,
    trigger_event: ZfEvent,
    stage_id: str,
    task_ids: Sequence[str],
    task_map_generation: str,
    workflow_run_id: str,
    correlation_id: str,
) -> bool:
    try:
        events = runtime.event_log.read_all()
    except Exception:
        events = []
    completed = completed_writer_generation(
        events,
        trigger_event=trigger_event,
        task_ids=task_ids,
        task_map_generation=task_map_generation,
        workflow_run_id=workflow_run_id,
    )
    if completed is None:
        return False
    already_suppressed = any(
        event.type == "fanout.retrigger.suppressed"
        and isinstance(event.payload, dict)
        and event.payload.get("stage_id") == stage_id
        and event.payload.get("trigger_event_id") == trigger_event.id
        and event.payload.get("reason") == "generation_already_verified"
        for event in events
    )
    if not already_suppressed:
        runtime.event_writer.append(ZfEvent(
            type="fanout.retrigger.suppressed",
            actor="zf-cli",
            payload={
                "stage_id": stage_id,
                "trigger_event_id": trigger_event.id,
                "reason": "generation_already_verified",
                "task_ids": completed.task_ids,
                "task_map_generation": task_map_generation,
                "candidate_event_id": completed.candidate_event_id,
                "candidate_head_commit": completed.candidate_head_commit,
                "candidate_ref": completed.candidate_ref,
                "verification_event_id": completed.verification_event_id,
            },
            causation_id=trigger_event.id,
            correlation_id=correlation_id,
        ))
    return True
