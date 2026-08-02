"""Pure lookup and normalization helpers for bound Plan application."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.channel_sidecar import (
    SidecarRefError,
    hydrate_channel_message_text,
)
from zf.runtime.kanban_plan_requests import (
    PLAN_REQUESTED_EVENT,
    plan_requirement_digest,
)


def channel_plan_discussion_seed(
    request: dict[str, Any],
    origin_message: str,
) -> tuple[str, bool, str]:
    seed = str(request.get("discussion_seed") or "").strip()
    if str(request.get("subject_type") or "") == "channel_setup":
        if seed:
            return seed, False, ""
        if origin_message:
            return origin_message, True, ""
        return "", False, "Channel Plan discussion_seed is missing"
    return origin_message, False, ""


def channel_plan_discussion_seed_digest(seed: str) -> str:
    return "sha256:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


def originating_plan_message(
    state_dir: Path,
    events: list[ZfEvent],
    request: dict[str, Any],
) -> str:
    event_ids = [
        str(item)
        for item in request.get("originating_message_event_ids", [])
        if str(item)
    ]
    legacy_id = str(request.get("originating_message_event_id") or "")
    if not event_ids and legacy_id:
        event_ids = [legacy_id]
    by_id = {event.id: event for event in events}
    rows: list[tuple[str, str]] = []
    for event_id in event_ids:
        origin = by_id.get(event_id)
        if origin is None or not isinstance(origin.payload, dict):
            return ""
        if origin.type == "user.message":
            message = str(
                origin.payload.get("message")
                or origin.payload.get("text")
                or ""
            ).strip()
        elif origin.type == "channel.message.posted":
            try:
                message = hydrate_channel_message_text(
                    state_dir,
                    origin.payload,
                    strict=True,
                ).strip()
            except SidecarRefError:
                return ""
        else:
            return ""
        if message:
            rows.append((event_id, message))
    expected_digest = str(request.get("requirement_digest") or "")
    if expected_digest and expected_digest != plan_requirement_digest(rows):
        return ""
    return "\n\n".join(message for _event_id, message in rows)


def latest_plan_revision(
    events: list[ZfEvent],
    *,
    request_id: str,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for event in events:
        if event.type != PLAN_REQUESTED_EVENT:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        request = (
            payload.get("request")
            if isinstance(payload.get("request"), dict)
            else payload.get("plan_request")
            if isinstance(payload.get("plan_request"), dict)
            else {}
        )
        if str(request.get("request_id") or event.id) != request_id:
            continue
        candidates.append({**request, "request_event_id": event.id})
    return max(
        candidates,
        key=lambda item: int(item.get("revision") or 1),
        default={},
    )


def latest_task_binding_event_id(
    events: list[ZfEvent],
    *,
    task_id: str,
    fallback: str,
) -> str:
    for event in reversed(events):
        if event.task_id == task_id and event.type in {
            "task.created",
            "task.contract.update",
            "task.updated",
        }:
            return event.id
    return fallback


def shared_workflow_parameters(value: object) -> dict[str, Any]:
    parameter_sets: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return {}
    for option in value:
        if not isinstance(option, dict):
            continue
        submit_payload = option.get("submit_payload")
        if not isinstance(submit_payload, dict):
            continue
        parameters = submit_payload.get("parameters")
        if isinstance(parameters, dict):
            parameter_sets.append(parameters)
    if not parameter_sets:
        return {}
    shared = dict(parameter_sets[0])
    for key in list(shared):
        if any(
            key not in parameters or parameters[key] != shared[key]
            for parameters in parameter_sets[1:]
        ):
            shared.pop(key)
    return shared


__all__ = [
    "channel_plan_discussion_seed",
    "channel_plan_discussion_seed_digest",
    "latest_plan_revision",
    "latest_task_binding_event_id",
    "originating_plan_message",
    "shared_workflow_parameters",
]
