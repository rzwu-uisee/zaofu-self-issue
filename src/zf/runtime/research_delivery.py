"""Research fanout result, task lifecycle, and channel settlement."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.channel_workflow_bridge import emit_fanout_channel_state_update
from zf.runtime.research_fanout_artifact import (
    materialize_research_fanout_artifact,
    merge_research_artifact_payload,
)
from zf.runtime.research_templates import (
    ADAPTIVE_RESEARCH_TEMPLATE,
    RESEARCH_TEMPLATES,
)
from zf.runtime.workflow_results import emit_research_result_available
from zf.runtime.workflow_task_lifecycle import (
    complete_standalone_research_task,
)


def settle_research_delivery(
    coordinator: Any,
    aggregate_event: ZfEvent,
    manifest: dict[str, Any],
    artifact_payload: dict[str, Any],
    result_event: ZfEvent | None,
) -> None:
    """Converge durable call, result, Task, and Channel projections once."""

    coordinator._consume_durable_fanout_aggregate_result(aggregate_event)
    terminal_manifest = {
        **manifest,
        "aggregate": aggregate_event.payload,
        "artifact_refs": artifact_payload.get(
            "artifact_refs",
            manifest.get("artifact_refs", []),
        ),
    }
    available_event = None
    if _is_research_stage(manifest):
        available_event = emit_research_result_available(
            writer=coordinator.event_writer,
            terminal_event=aggregate_event,
            manifest=terminal_manifest,
            synth_event=result_event,
        )
    if available_event is not None:
        completed_task = complete_standalone_research_task(
            task_store=coordinator.task_store,
            event_writer=coordinator.event_writer,
            result_event=available_event,
        )
        if completed_task is not None:
            coordinator._refresh_task_doc_projection(
                completed_task,
                source_event=available_event.type,
            )
    emit_fanout_channel_state_update(
        writer=coordinator.event_writer,
        terminal_event=aggregate_event,
        manifest=terminal_manifest,
        synth_event=result_event,
    )


def project_direct_research_artifact(
    *,
    state_dir,
    event_log,
    manifest: dict[str, Any],
    artifact_payload: dict[str, Any],
) -> dict[str, Any]:
    """Materialize the single adaptive Root result without a second agent."""

    if (
        str(manifest.get("stage_id") or "")
        != ADAPTIVE_RESEARCH_TEMPLATE.pattern_id
    ):
        return artifact_payload
    root_event = _adaptive_root_result_event(event_log, manifest)
    if root_event is None:
        return artifact_payload
    descriptor = materialize_research_fanout_artifact(
        state_dir,
        manifest=manifest,
        synth_event=root_event,
    )
    return merge_research_artifact_payload(artifact_payload, descriptor)


def _adaptive_root_result_event(event_log, manifest: dict[str, Any]) -> ZfEvent | None:
    children = [
        child
        for child in manifest.get("children", []) or []
        if isinstance(child, dict)
        and str(child.get("status") or "") == "completed"
    ]
    if len(children) != 1:
        return None
    child = children[0]
    result_event_id = str(child.get("result_event_id") or "").strip()
    if not result_event_id:
        return None
    for event in reversed(event_log.read_all()):
        if event.id == result_event_id:
            return event
    report = (
        dict(child.get("report"))
        if isinstance(child.get("report"), dict)
        else {}
    )
    return ZfEvent(
        id=result_event_id,
        type="research.child.completed",
        actor=str(child.get("role_instance") or "research_root"),
        task_id=str(child.get("task_id") or "") or None,
        correlation_id=str(manifest.get("trace_id") or "") or None,
        payload={
            "fanout_id": str(manifest.get("fanout_id") or ""),
            "stage_id": str(manifest.get("stage_id") or ""),
            "role_instance": str(child.get("role_instance") or ""),
            "status": "completed",
            "summary": str(report.get("summary") or ""),
            "evidence_refs": report.get("evidence_refs") or [],
            "provider_operation_summary": (
                report.get("provider_operation_summary")
            ),
            "report": report,
        },
    )


def _is_research_stage(manifest: dict[str, Any]) -> bool:
    stage_id = str(manifest.get("stage_id") or "")
    return any(
        template.pattern_id == stage_id
        for template in RESEARCH_TEMPLATES
    )


__all__ = [
    "project_direct_research_artifact",
    "settle_research_delivery",
]
