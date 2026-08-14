"""Fail-closed replay helpers for active Workflow Requests."""

from __future__ import annotations

from typing import Any

from zf.core.events import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.control_actions_helpers import _required_text
from zf.runtime.workflow_anchor import workflow_task_request_binding
from zf.runtime.workflow_delivery_replay import submitted_request_replay_result
from zf.runtime.workflow_request_concurrency import WorkflowRequestError
from zf.runtime.workflow_requests import require_current_workflow_request


def replay_active_workflow_start(
    service: Any,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict,
    route_id: str,
    route: dict,
) -> dict:
    """Return the existing invocation without revising an active Request."""

    adapter = str(route.get("start_adapter") or "")
    if adapter not in {
        "delivery_request_submit",
        "light_delivery_request_submit",
        "registered_general",
    }:
        return service._failed(
            requested=requested,
            action=action,
            requested_action=requested_action,
            task_id=_required_text(payload, "task_id") or None,
            reason="active Workflow Request replay requires a delivery route",
            status_code=409,
            status="workflow_request_active",
        )
    request_id = _required_text(payload, "request_id")
    try:
        request_revision = int(payload.get("request_revision") or 0)
        projection = require_current_workflow_request(
            service.state_dir,
            request_id,
            request_revision,
        )
    except (TypeError, ValueError, WorkflowRequestError) as exc:
        return service._failed(
            requested=requested,
            action=action,
            requested_action=requested_action,
            task_id=_required_text(payload, "task_id") or None,
            reason=str(exc),
            status_code=409,
            status="workflow_task_stale",
        )
    replay = submitted_request_replay_result(
        config=service.config,
        state_dir=service.state_dir,
        projection=projection,
        events=service.writer.event_log.read_all(),
    )
    accepted = replay.get("status") != "STOP"
    return {
        "_status_code": 200 if accepted else 409,
        "ok": accepted,
        "status": str(replay.get("status") or "STOP"),
        "action": action,
        "requested_action": requested_action,
        "request_id": request_id,
        "workflow_invoke_event_id": str(
            replay.get("workflow_invoke_event_id") or ""
        ),
        "workflow_invoke_status": str(
            replay.get("workflow_invoke_status") or ""
        ),
        "next_action": str(replay.get("next_action") or ""),
        "idempotent_replay": True,
        "event_ids": list(replay.get("event_ids") or []),
        "blockers": list(replay.get("blockers") or []),
        "route_id": route_id,
        "route": route,
        "workflow_request": {
            "request_id": request_id,
            "request_revision": int(projection.get("revision") or 0),
            "intake_ref": str(projection.get("intake_ref") or ""),
            "workflow_input_manifest_ref": str(
                projection.get("workflow_input_manifest_ref") or ""
            ),
            "proposal_ref": dict(projection.get("proposal_ref") or {}),
            "proposal_digest": str(
                projection.get("proposal_digest") or ""
            ),
        },
    }


def active_request_mutation_failure(
    service: Any,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict,
    projection: dict,
) -> dict | None:
    """Reject revisions that would mutate an active or bound Request."""

    task_id = _required_text(payload, "task_id")
    if str(projection.get("status") or "") in {"submitted", "running"}:
        return service._failed(
            requested=requested,
            action=action,
            requested_action=requested_action,
            task_id=task_id or None,
            reason=(
                "active Workflow Request is immutable; use exact "
                "workflow-start replay or controlled recovery/replan"
            ),
            status_code=409,
            status="workflow_request_active",
        )
    task = (
        TaskStore(service.state_dir / "kanban.json").get(task_id)
        if task_id else None
    )
    binding = workflow_task_request_binding(task) if task is not None else {}
    if (
        str(binding.get("request_id") or "")
        == str(projection.get("request_id") or "")
        and int(binding.get("request_revision") or 0)
        == int(projection.get("revision") or 0)
    ):
        return service._failed(
            requested=requested,
            action=action,
            requested_action=requested_action,
            task_id=task_id,
            reason=(
                "active Workflow Request is already bound to this Task; "
                "workflow-request must not create a new revision"
            ),
            status_code=409,
            status="workflow_request_active",
        )
    return None


__all__ = [
    "active_request_mutation_failure",
    "replay_active_workflow_start",
]
