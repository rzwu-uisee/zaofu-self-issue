"""Writer-fanout admission bridge for blocking Task Pipeline generations."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.plan_admission import emit_plan_admission_cancel
from zf.runtime.task_pipeline_reconciler import task_pipeline_blocking
from zf.runtime.task_pipeline_runtime import (
    TaskPipelineGenerationPreflight,
    admit_task_pipeline_generation,
    preflight_task_pipeline_generation,
)


def task_pipeline_enabled(runtime: Any, *, flow_kind: str = "") -> bool:
    return task_pipeline_blocking(runtime.config, flow_kind=flow_kind)


def task_pipeline_enabled_for_event(runtime: Any, event: ZfEvent) -> bool:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return task_pipeline_enabled(
        runtime,
        flow_kind=str(
            payload.get("flow_kind") or payload.get("request_kind") or ""
        ),
    )


def preflight_blocking_task_pipeline_generation(
    runtime: Any,
    *,
    trigger_event: ZfEvent,
    stage: Any,
    trace_id: str,
    pdd_id: str,
    loaded: Any,
    task_items: Iterable[Mapping[str, Any]],
) -> TaskPipelineGenerationPreflight | None:
    """Reject a broken generation before writer tasks become canonical."""

    flow_kind = str(
        getattr(loaded, "flow_kind", "")
        or (
            trigger_event.payload.get("flow_kind")
            if isinstance(trigger_event.payload, dict)
            else ""
        )
        or ""
    ).strip()
    if not task_pipeline_enabled(runtime, flow_kind=flow_kind):
        return None
    try:
        prepared = preflight_task_pipeline_generation(
            runtime,
            trigger_event=trigger_event,
            trace_id=trace_id,
            loaded=loaded,
            task_items=task_items,
        )
        if prepared is not None and suppress_admitted_blocking_task_pipeline_generation(
            runtime,
            trigger_event=trigger_event,
            stage_id=stage.id,
            preflight=prepared,
            correlation_id=trace_id,
        ):
            return None
        return prepared
    except Exception as exc:
        emit_plan_admission_cancel(
            runtime,
            trigger_event=trigger_event,
            stage_id=stage.id,
            trace_id=trace_id,
            pdd_id=loaded.pdd_id or pdd_id,
            feature_id=loaded.feature_id or loaded.pdd_id or pdd_id,
            task_map_ref=loaded.task_map_ref,
            task_map_path=loaded.task_map_path,
            source_index_ref=loaded.source_index_ref,
            task_ids=[
                str(item.get("task_id") or "")
                for item in task_items
                if str(item.get("task_id") or "")
            ],
            reason=f"task_pipeline_generation_admission_failed: {exc}",
        )
        return None


def suppress_admitted_blocking_task_pipeline_generation(
    runtime: Any,
    *,
    trigger_event: ZfEvent,
    stage_id: str,
    preflight: TaskPipelineGenerationPreflight,
    correlation_id: str,
) -> bool:
    """Consume an equivalent task-map replay before mutable writer admission."""

    events = runtime.event_log.read_all()
    admitted = next(
        (
            event
            for event in reversed(events)
            if event.type == "task.pipeline.generation.admitted"
            and isinstance(event.payload, dict)
            and str(event.payload.get("generation_id") or "")
            == preflight.generation_id
        ),
        None,
    )
    if admitted is None:
        return False
    already_recorded = any(
        event.type == "fanout.retrigger.suppressed"
        and isinstance(event.payload, dict)
        and str(event.payload.get("trigger_event_id") or "") == trigger_event.id
        and str(event.payload.get("reason") or "")
        == "task_pipeline_generation_already_admitted"
        for event in events
    )
    if not already_recorded:
        runtime.event_writer.append(ZfEvent(
            type="fanout.retrigger.suppressed",
            actor="zf-cli",
            origin="kernel",
            payload={
                "stage_id": stage_id,
                "trigger_event_id": trigger_event.id,
                "reason": "task_pipeline_generation_already_admitted",
                "generation_id": preflight.generation_id,
                "generation_admitted_event_id": admitted.id,
                "workflow_run_id": preflight.workflow_run_id,
                "task_map_generation": preflight.task_map_generation,
                "task_ids": list(preflight.task_ids),
            },
            causation_id=trigger_event.id,
            correlation_id=correlation_id,
        ))
    return True


def admit_blocking_task_pipeline_generation(
    runtime: Any,
    *,
    trigger_event: ZfEvent,
    admitted_event: ZfEvent,
    stage_id: str,
    trace_id: str,
    pdd_id: str,
    loaded: Any,
    task_items: Iterable[Mapping[str, Any]],
    preflight: TaskPipelineGenerationPreflight | None = None,
) -> bool:
    """Admit one generation and consume legacy writer-fanout dispatch."""

    flow_kind = str(
        getattr(loaded, "flow_kind", "")
        or (
            trigger_event.payload.get("flow_kind")
            if isinstance(trigger_event.payload, dict)
            else ""
        )
        or ""
    ).strip()
    if not task_pipeline_enabled(runtime, flow_kind=flow_kind):
        return False
    try:
        admit_task_pipeline_generation(
            runtime,
            trigger_event=trigger_event,
            task_map_admitted_event=admitted_event,
            stage_id=stage_id,
            trace_id=trace_id,
            loaded=loaded,
            task_items=task_items,
            preflight=preflight,
        )
    except Exception as exc:
        emit_plan_admission_cancel(
            runtime,
            trigger_event=trigger_event,
            stage_id=stage_id,
            trace_id=trace_id,
            pdd_id=loaded.pdd_id or pdd_id,
            feature_id=loaded.feature_id or loaded.pdd_id or pdd_id,
            task_map_ref=loaded.task_map_ref,
            task_map_path=loaded.task_map_path,
            source_index_ref=loaded.source_index_ref,
            task_ids=[
                str(item.get("task_id") or "")
                for item in task_items
                if str(item.get("task_id") or "")
            ],
            reason=f"task_pipeline_generation_admission_failed: {exc}",
        )
    return True


__all__ = [
    "admit_blocking_task_pipeline_generation",
    "preflight_blocking_task_pipeline_generation",
    "suppress_admitted_blocking_task_pipeline_generation",
    "task_pipeline_enabled",
]
