"""Durable Workflow result availability events."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.security.redaction import redact_obj
from zf.runtime.workflow_origin import (
    WORKFLOW_ORIGIN_SCHEMA_VERSION,
    WorkflowOriginError,
    normalize_workflow_origin_binding,
)


WORKFLOW_RESULT_AVAILABLE = "workflow.result.available"
WORKFLOW_RESULT_SCHEMA_VERSION = "workflow-result.v1"


def emit_research_result_available(
    *,
    writer: EventWriter,
    terminal_event: ZfEvent,
    manifest: dict[str, Any],
    synth_event: ZfEvent | None = None,
) -> ZfEvent | None:
    """Publish one result fact after a durable Research artifact exists."""

    terminal_payload = (
        terminal_event.payload
        if isinstance(terminal_event.payload, dict)
        else {}
    )
    if str(terminal_payload.get("status") or "") != "completed":
        return None
    descriptor = _research_artifact_descriptor(terminal_payload, manifest)
    stage_id = str(
        descriptor.get("stage_id")
        or terminal_payload.get("stage_id")
        or manifest.get("stage_id")
        or ""
    ).strip()
    if "autoresearch" in stage_id.lower():
        return None
    artifact_ref = str(
        descriptor.get("ref") or descriptor.get("path") or ""
    ).strip()
    artifact_digest = str(
        descriptor.get("sha256") or descriptor.get("hash") or ""
    ).removeprefix("sha256:").strip().lower()
    if not artifact_ref or len(artifact_digest) != 64:
        return None

    prior = next(
        (
            event
            for event in reversed(writer.event_log.read_all())
            if event.type == WORKFLOW_RESULT_AVAILABLE
            and str((event.payload or {}).get("terminal_event_id") or "")
            == terminal_event.id
            and str((event.payload or {}).get("artifact_digest") or "")
            == artifact_digest
        ),
        None,
    )
    if prior is not None:
        return prior

    trigger = (
        manifest.get("trigger_payload")
        if isinstance(manifest.get("trigger_payload"), dict)
        else {}
    )
    origin_binding = _origin_binding(manifest, trigger)
    task_id = str(
        descriptor.get("task_id")
        or trigger.get("task_id")
        or manifest.get("task_id")
        or terminal_event.task_id
        or ""
    ).strip()
    request_id = str(
        descriptor.get("request_id")
        or trigger.get("request_id")
        or manifest.get("request_id")
        or ""
    ).strip()
    request_revision = _int_value(
        descriptor.get("request_revision")
        or trigger.get("request_revision")
        or manifest.get("request_revision")
    )
    if request_revision < 1:
        request_id = ""
    workflow_run_id = str(
        descriptor.get("workflow_run_id")
        or trigger.get("workflow_run_id")
        or manifest.get("workflow_run_id")
        or ""
    ).strip()
    summary = str(
        descriptor.get("summary")
        or terminal_payload.get("research_summary")
        or terminal_payload.get("summary")
        or ""
    ).strip()
    payload = {
        "schema_version": WORKFLOW_RESULT_SCHEMA_VERSION,
        "result_kind": "research_report",
        "status": "available",
        "project_id": str(origin_binding.get("project_id") or ""),
        "origin_surface": str(origin_binding.get("surface") or ""),
        "conversation_id": str(
            origin_binding.get("conversation_id") or ""
        ),
        "thread_key": str(origin_binding.get("thread_key") or ""),
        "channel_id": str(origin_binding.get("channel_id") or ""),
        "thread_id": str(origin_binding.get("thread_id") or ""),
        "request_id": request_id,
        "request_revision": request_revision,
        "task_id": task_id,
        "workflow_run_id": workflow_run_id,
        "fanout_id": str(
            descriptor.get("fanout_id")
            or terminal_payload.get("fanout_id")
            or manifest.get("fanout_id")
            or ""
        ),
        "stage_id": stage_id,
        "terminal_event_id": terminal_event.id,
        "result_source_event_id": (
            synth_event.id
            if synth_event is not None
            else str(descriptor.get("result_event_id") or "")
        ),
        "synth_event_id": (
            synth_event.id
            if (
                synth_event is not None
                and synth_event.type == "fanout.synth.completed"
            )
            else str(descriptor.get("synth_event_id") or "")
        ),
        "root_result_event_id": (
            synth_event.id
            if (
                synth_event is not None
                and synth_event.type != "fanout.synth.completed"
            )
            else str(descriptor.get("root_result_event_id") or "")
        ),
        "artifact_ref": artifact_ref,
        "artifact_digest": artifact_digest,
        "summary": summary,
        "origin_binding": origin_binding,
    }
    return writer.emit(
        WORKFLOW_RESULT_AVAILABLE,
        actor="zf-cli",
        task_id=task_id or None,
        causation_id=terminal_event.id,
        correlation_id=request_id or workflow_run_id or terminal_event.correlation_id,
        payload=redact_obj(payload),
    )


def _research_artifact_descriptor(
    terminal_payload: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    refs: list[Any] = []
    for carrier in (terminal_payload, manifest):
        items = carrier.get("artifact_refs")
        if isinstance(items, list):
            refs.extend(items)
    expected_ref = str(
        terminal_payload.get("research_artifact_ref") or ""
    ).strip()
    expected_digest = str(
        terminal_payload.get("research_artifact_digest") or ""
    ).removeprefix("sha256:").strip().lower()
    for item in reversed(refs):
        if not isinstance(item, dict):
            continue
        if str(item.get("kind") or "") != "research_report":
            continue
        ref = str(item.get("ref") or item.get("path") or "").strip()
        digest = str(
            item.get("sha256") or item.get("hash") or ""
        ).removeprefix("sha256:").strip().lower()
        if expected_ref and ref != expected_ref:
            continue
        if expected_digest and digest != expected_digest:
            continue
        return dict(item)
    return {}


def _origin_binding(
    manifest: dict[str, Any],
    trigger: dict[str, Any],
) -> dict[str, Any]:
    raw = (
        manifest.get("origin_binding")
        if isinstance(manifest.get("origin_binding"), dict)
        else trigger.get("origin_binding")
    )
    if not isinstance(raw, dict) or not raw:
        return normalize_workflow_origin_binding({
            "schema_version": WORKFLOW_ORIGIN_SCHEMA_VERSION,
            "surface": "cli",
            "source": "runtime",
            "project_id": "",
        })
    try:
        return normalize_workflow_origin_binding(
            raw,
            allow_legacy_empty_project=True,
        )
    except WorkflowOriginError:
        return {}


def _int_value(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "WORKFLOW_RESULT_AVAILABLE",
    "WORKFLOW_RESULT_SCHEMA_VERSION",
    "emit_research_result_available",
]
