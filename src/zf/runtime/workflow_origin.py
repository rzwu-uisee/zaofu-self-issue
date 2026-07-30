"""Canonical return binding for Workflow Request results."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from zf.core.security.redaction import redact_obj


WORKFLOW_ORIGIN_SCHEMA_VERSION = "workflow-origin-binding.v1"
_RETURN_SURFACES = {"channel", "kanban_agent", "cli"}


class WorkflowOriginError(ValueError):
    pass


def build_workflow_origin_binding(
    *,
    source: str,
    project_id: str,
    channel_id: str = "",
    thread_id: str = "",
    conversation_id: str = "",
    thread_key: str = "",
) -> dict[str, str]:
    """Build one primary return target from a controlled request surface."""

    if str(channel_id or "").strip():
        surface = "channel"
    elif str(conversation_id or "").strip():
        surface = "kanban_agent"
    else:
        surface = "cli"
    return normalize_workflow_origin_binding({
        "schema_version": WORKFLOW_ORIGIN_SCHEMA_VERSION,
        "surface": surface,
        "source": str(source or "").strip(),
        "project_id": str(project_id or "").strip(),
        "channel_id": str(channel_id or "").strip(),
        "thread_id": str(thread_id or "").strip(),
        "conversation_id": str(conversation_id or "").strip(),
        "thread_key": str(thread_key or thread_id or "").strip(),
    })


def normalize_workflow_origin_binding(
    raw: object,
    *,
    allow_legacy_empty_project: bool = False,
) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise WorkflowOriginError("workflow origin binding must be a mapping")
    schema_version = str(
        raw.get("schema_version") or WORKFLOW_ORIGIN_SCHEMA_VERSION
    ).strip()
    if schema_version != WORKFLOW_ORIGIN_SCHEMA_VERSION:
        raise WorkflowOriginError(
            "workflow origin binding schema_version must be "
            f"{WORKFLOW_ORIGIN_SCHEMA_VERSION}"
        )
    surface = str(raw.get("surface") or "").strip().lower().replace("-", "_")
    if surface not in _RETURN_SURFACES:
        raise WorkflowOriginError(
            "workflow origin surface must be channel, kanban_agent, or cli"
        )
    project_id = str(raw.get("project_id") or "").strip()
    if (
        not project_id
        and surface != "cli"
        and not allow_legacy_empty_project
    ):
        raise WorkflowOriginError("workflow origin binding requires project_id")
    channel_id = str(raw.get("channel_id") or "").strip()
    thread_id = str(raw.get("thread_id") or "").strip()
    conversation_id = str(raw.get("conversation_id") or "").strip()
    thread_key = str(raw.get("thread_key") or "").strip()
    if surface == "channel":
        if not channel_id:
            raise WorkflowOriginError(
                "channel workflow origin binding requires channel_id"
            )
        thread_id = thread_id or "main"
        conversation_id = ""
        thread_key = ""
    elif surface == "kanban_agent":
        if not conversation_id:
            raise WorkflowOriginError(
                "kanban_agent workflow origin binding requires conversation_id"
            )
        thread_key = thread_key or thread_id or "main"
        channel_id = ""
        thread_id = ""
    else:
        channel_id = ""
        thread_id = ""
        conversation_id = ""
        thread_key = ""
    return redact_obj({
        "schema_version": WORKFLOW_ORIGIN_SCHEMA_VERSION,
        "surface": surface,
        "source": str(raw.get("source") or "").strip(),
        "project_id": project_id,
        "channel_id": channel_id,
        "thread_id": thread_id,
        "conversation_id": conversation_id,
        "thread_key": thread_key,
    })


def workflow_origin_from_request(request: dict[str, Any]) -> dict[str, str]:
    raw = request.get("origin_binding")
    if isinstance(raw, dict):
        return normalize_workflow_origin_binding(
            raw,
            allow_legacy_empty_project=True,
        )
    surface = (
        "channel"
        if str(request.get("channel_id") or "").strip()
        else "cli"
    )
    return normalize_workflow_origin_binding(
        {
            "schema_version": WORKFLOW_ORIGIN_SCHEMA_VERSION,
            "surface": surface,
            "source": str(request.get("source") or "legacy"),
            "project_id": str(request.get("project_id") or ""),
            "channel_id": str(request.get("channel_id") or ""),
            "thread_id": str(request.get("thread_id") or ""),
        },
        allow_legacy_empty_project=True,
    )


def workflow_origin_from_manifest(
    manifest: dict[str, Any],
) -> dict[str, str]:
    raw = manifest.get("origin_binding")
    if isinstance(raw, dict):
        return normalize_workflow_origin_binding(
            raw,
            allow_legacy_empty_project=True,
        )
    channel_id = str(manifest.get("channel_id") or "").strip()
    conversation_id = str(
        manifest.get("conversation_id") or ""
    ).strip()
    surface = (
        "channel"
        if channel_id
        else "kanban_agent"
        if conversation_id
        else "cli"
    )
    return normalize_workflow_origin_binding(
        {
            "schema_version": WORKFLOW_ORIGIN_SCHEMA_VERSION,
            "surface": surface,
            "source": str(manifest.get("source") or "legacy"),
            "project_id": str(manifest.get("project_id") or ""),
            "channel_id": channel_id,
            "thread_id": str(manifest.get("thread_id") or ""),
            "conversation_id": conversation_id,
            "thread_key": str(manifest.get("thread_key") or ""),
        },
        allow_legacy_empty_project=True,
    )


def workflow_origin_digest(binding: dict[str, Any]) -> str:
    normalized = normalize_workflow_origin_binding(
        binding,
        allow_legacy_empty_project=True,
    )
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def assert_same_workflow_origin(
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> None:
    if workflow_origin_digest(expected) != workflow_origin_digest(actual):
        raise WorkflowOriginError(
            "workflow origin binding does not match the canonical request origin"
        )


__all__ = [
    "WORKFLOW_ORIGIN_SCHEMA_VERSION",
    "WorkflowOriginError",
    "assert_same_workflow_origin",
    "build_workflow_origin_binding",
    "normalize_workflow_origin_binding",
    "workflow_origin_digest",
    "workflow_origin_from_manifest",
    "workflow_origin_from_request",
]
