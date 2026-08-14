"""Suppress recovery fanout triggers for an already verified generation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.task_pipeline_contexts import (
    CANDIDATE_FREEZE_RECEIPT_SCHEMA,
    latest_task_pipeline_generation_context,
)
from zf.runtime.writer_fanout_generation import completed_writer_generation


def suppress_stale_task_pipeline_generation(
    runtime: Any,
    *,
    trigger_event: ZfEvent,
    stage_ids: Sequence[str],
    correlation_id: str,
) -> bool:
    """Reject replay of a frozen Candidate from an older v4 generation."""

    payload = (
        trigger_event.payload
        if isinstance(trigger_event.payload, dict)
        else {}
    )
    if (
        trigger_event.type != "candidate.ready"
        or str(payload.get("schema_version") or "")
        != CANDIDATE_FREEZE_RECEIPT_SCHEMA
    ):
        return False
    workflow_run_id = str(
        payload.get("workflow_run_id")
        or trigger_event.correlation_id
        or ""
    ).strip()
    claimed_generation_id = str(payload.get("generation_id") or "").strip()
    if not workflow_run_id or not claimed_generation_id:
        return False
    try:
        events = runtime.event_log.read_all()
    except Exception:
        return False
    current = latest_task_pipeline_generation_context(
        events,
        workflow_run_id=workflow_run_id,
    )
    current_generation_id = str(
        (current or {}).get("generation_id") or ""
    ).strip()
    if (
        not current_generation_id
        or current_generation_id == claimed_generation_id
    ):
        return False
    already_suppressed = any(
        event.type == "fanout.retrigger.suppressed"
        and isinstance(event.payload, dict)
        and event.payload.get("trigger_event_id") == trigger_event.id
        and event.payload.get("reason")
        == "candidate_ready_stale_task_pipeline_generation"
        for event in events
    )
    if not already_suppressed:
        runtime.event_writer.append(ZfEvent(
            type="fanout.retrigger.suppressed",
            actor="zf-cli",
            origin="kernel",
            payload={
                "stage_ids": sorted({str(item) for item in stage_ids if item}),
                "trigger_event_id": trigger_event.id,
                "reason": "candidate_ready_stale_task_pipeline_generation",
                "workflow_run_id": workflow_run_id,
                "claimed_generation_id": claimed_generation_id,
                "current_generation_id": current_generation_id,
                "current_generation_admitted_event_id": str(
                    (current or {}).get("generation_admitted_event_id") or ""
                ),
                "task_map_generation": str(
                    payload.get("task_map_generation") or ""
                ),
            },
            causation_id=trigger_event.id,
            correlation_id=correlation_id,
        ))
    return True


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
