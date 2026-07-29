"""Surface-neutral workflow route preview and exact proposal service."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from zf.core.config.schema import ZfConfig
from zf.core.events import EventWriter, ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.task.store import TaskStore
from zf.runtime.kanban_proposals import (
    PROPOSAL_EVENT,
    PROPOSAL_EVENT_TYPES,
    canonical_proposal_action,
    proposal_payload_digest,
)
from zf.runtime.task_workflow_plans import task_workflow_binding_digest
from zf.runtime.workflow_route_catalog import (
    resolve_workflow_route,
    workflow_route_catalog,
)


WORKFLOW_START_SCHEMA_VERSION = "workflow-start.v1"
WORKFLOW_START_ACTION = "workflow-start"
LEGACY_WORKFLOW_START_ACTION = "task-workflow-start"
WORKFLOW_START_ACTIONS = frozenset({
    WORKFLOW_START_ACTION,
    LEGACY_WORKFLOW_START_ACTION,
})


def is_workflow_start_action(action: str) -> bool:
    return str(action or "").strip() in WORKFLOW_START_ACTIONS


class WorkflowStartService:
    """Build current route bindings without owning workflow execution."""

    def __init__(
        self,
        state_dir: Path,
        config: ZfConfig | None,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.config = config

    def routes(self, *, task_id: str) -> dict[str, Any]:
        task = TaskStore(self.state_dir / "kanban.json").get(task_id)
        if task is None:
            return _failure(
                "preflight_blocked",
                f"workflow task {task_id!r} does not exist",
                status_code=404,
                task_id=task_id,
            )
        catalog = workflow_route_catalog(self.config)
        if not str(catalog.get("config_digest") or ""):
            return _failure(
                "workflow_route_unavailable",
                "workflow route catalog is unavailable",
                status_code=409,
                task_id=task_id,
            )
        return {
            "ok": True,
            "status": "ready",
            "schema_version": WORKFLOW_START_SCHEMA_VERSION,
            "action": WORKFLOW_START_ACTION,
            "task_id": task.id,
            "task_title": task.title,
            "task_contract_digest": task_workflow_binding_digest(task),
            **catalog,
        }

    def preview(
        self,
        payload: dict[str, Any],
        *,
        require_bindings: bool,
        origin: str = "",
    ) -> dict[str, Any]:
        task_id = str(payload.get("task_id") or "").strip()
        route_id = str(payload.get("route_id") or "").strip()
        if not task_id:
            return _failure(
                "invalid_payload",
                "task_id is required",
                status_code=422,
            )
        if not route_id:
            return _failure(
                "invalid_payload",
                "route_id is required",
                status_code=422,
                task_id=task_id,
            )
        parameters = payload.get("parameters")
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, dict):
            return _failure(
                "invalid_payload",
                "parameters must be a mapping",
                status_code=422,
                task_id=task_id,
            )

        task = TaskStore(self.state_dir / "kanban.json").get(task_id)
        if task is None:
            return _failure(
                "preflight_blocked",
                f"workflow task {task_id!r} does not exist",
                status_code=404,
                task_id=task_id,
            )
        task_digest = task_workflow_binding_digest(task)
        expected_task_digest = str(
            payload.get("task_contract_digest") or ""
        ).strip()
        if (
            (require_bindings and not expected_task_digest)
            or (
                expected_task_digest
                and expected_task_digest != task_digest
            )
        ):
            return _failure(
                "workflow_task_stale",
                "workflow Task binding is stale or missing",
                status_code=409,
                task_id=task_id,
            )

        catalog = workflow_route_catalog(self.config)
        config_digest = str(catalog.get("config_digest") or "")
        expected_config_digest = str(
            payload.get("config_digest") or ""
        ).strip()
        if (
            (require_bindings and not expected_config_digest)
            or (
                expected_config_digest
                and expected_config_digest != config_digest
            )
        ):
            return _failure(
                "workflow_route_unavailable",
                f"workflow route {route_id!r} is stale or unavailable",
                status_code=409,
                task_id=task_id,
            )
        route = resolve_workflow_route(
            self.config,
            route_id,
            expected_config_digest=config_digest,
        )
        if route is None:
            return _failure(
                "workflow_route_unavailable",
                f"workflow route {route_id!r} is stale or unavailable",
                status_code=409,
                task_id=task_id,
            )

        objective = str(
            payload.get("objective")
            or payload.get("message")
            or task.title
        ).strip()
        if not objective:
            return _failure(
                "invalid_payload",
                "objective or message is required",
                status_code=422,
                task_id=task_id,
            )
        normalized = {
            "schema_version": WORKFLOW_START_SCHEMA_VERSION,
            "task_id": task_id,
            "route_id": route_id,
            "objective": objective,
            "parameters": {
                str(key): value
                for key, value in parameters.items()
                if value not in (None, "", [], {})
            },
            "task_contract_digest": task_digest,
            "config_digest": config_digest,
            "origin": str(payload.get("origin") or origin or ""),
        }
        for key in (
            "artifact_refs",
            "channel_id",
            "idempotency_key",
            "reason",
            "source_refs",
            "thread_id",
        ):
            value = payload.get(key)
            if value not in (None, "", [], {}):
                normalized[key] = value
        return {
            "ok": True,
            "status": "ready",
            "schema_version": WORKFLOW_START_SCHEMA_VERSION,
            "action": WORKFLOW_START_ACTION,
            "task_id": task_id,
            "route_id": route_id,
            "payload": redact_obj(normalized),
            "route": redact_obj(route),
            "task": {
                "id": task.id,
                "title": task.title,
                "contract_digest": task_digest,
            },
        }

    def propose(
        self,
        writer: EventWriter,
        payload: dict[str, Any],
        *,
        actor: str,
        origin: str,
        causation_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        preview = self.preview(
            payload,
            require_bindings=False,
            origin=origin,
        )
        if not preview.get("ok"):
            return preview
        proposed_payload = dict(preview["payload"])
        digest = proposal_payload_digest(
            WORKFLOW_START_ACTION,
            proposed_payload,
        )
        proposal_id = f"proposal-{digest[:24]}"
        prior = _proposal_by_id(
            writer.event_log.read_all(),
            proposal_id=proposal_id,
        )
        if prior is not None:
            return {
                **preview,
                "status": "proposal_ready",
                "proposal_event_id": prior.id,
                "proposal_id": proposal_id,
                "proposal_digest": digest,
                "replayed": True,
            }

        proposal_event = ZfEvent(
            type=PROPOSAL_EVENT,
            actor=actor,
            task_id=str(preview.get("task_id") or ""),
            causation_id=causation_id,
            correlation_id=correlation_id,
        )
        proposal = {
            "proposal_event_id": proposal_event.id,
            "proposal_id": proposal_id,
            "proposal_digest": digest,
            "revision": 1,
            "expires_at": "",
            "supersedes": "",
            "action": WORKFLOW_START_ACTION,
            "requested_action": WORKFLOW_START_ACTION,
            "payload": redact_obj(proposed_payload),
            "reason": str(
                proposed_payload.get("reason")
                or f"start workflow route {preview.get('route_id') or ''}"
            ),
            "confidence": "",
            "valid": True,
            "validation_error": "",
            "mutates_task_state": False,
        }
        proposal_event.payload = {
            "schema_version": "operator.action.proposal.v1",
            "proposal": redact_obj(proposal),
            "project_id": str(
                payload.get("project_id")
                or getattr(getattr(self.config, "project", None), "name", "")
                or ""
            ),
            "conversation_id": str(
                payload.get("conversation_id") or ""
            ),
            "thread_key": str(
                payload.get("thread_key")
                or payload.get("thread_id")
                or ""
            ),
            "turn_id": str(payload.get("turn_id") or ""),
            "source": origin,
            "surface": origin,
        }
        writer.append(proposal_event)
        return {
            **preview,
            "status": "proposal_ready",
            "proposal_event_id": proposal_event.id,
            "proposal_id": proposal_id,
            "proposal_digest": digest,
            "replayed": False,
        }


def workflow_start_proposal(
    events: Iterable[ZfEvent],
    proposal_event_id: str,
) -> dict[str, Any] | None:
    event = next(
        (
            item
            for item in events
            if item.id == proposal_event_id
            and item.type in PROPOSAL_EVENT_TYPES
        ),
        None,
    )
    if event is None or not isinstance(event.payload, dict):
        return None
    proposal = event.payload.get("proposal")
    if not isinstance(proposal, dict):
        return None
    if (
        canonical_proposal_action(str(proposal.get("action") or ""))
        != WORKFLOW_START_ACTION
        or not bool(proposal.get("valid"))
    ):
        return None
    return dict(proposal)


def _proposal_by_id(
    events: Iterable[ZfEvent],
    *,
    proposal_id: str,
) -> ZfEvent | None:
    for event in events:
        if event.type not in PROPOSAL_EVENT_TYPES:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        proposal = (
            payload.get("proposal")
            if isinstance(payload.get("proposal"), dict)
            else {}
        )
        if str(proposal.get("proposal_id") or "") == proposal_id:
            return event
    return None


def _failure(
    status: str,
    reason: str,
    *,
    status_code: int,
    task_id: str = "",
) -> dict[str, Any]:
    return {
        "_status_code": status_code,
        "ok": False,
        "status": status,
        "schema_version": WORKFLOW_START_SCHEMA_VERSION,
        "action": WORKFLOW_START_ACTION,
        "task_id": task_id,
        "reason": reason,
    }


__all__ = [
    "LEGACY_WORKFLOW_START_ACTION",
    "WORKFLOW_START_ACTION",
    "WORKFLOW_START_ACTIONS",
    "WORKFLOW_START_SCHEMA_VERSION",
    "WorkflowStartService",
    "is_workflow_start_action",
    "workflow_start_proposal",
]
