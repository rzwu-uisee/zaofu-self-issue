"""Validation owned by Kanban collaboration and coding actions."""

from __future__ import annotations

from zf.runtime.channel_contracts import CHANNEL_PERMISSION_PROFILES


def validate_collaboration_action_payload(
    action: str,
    payload: dict,
) -> str:
    if action in {
        "chat-orchestrator",
        "provider-dev-chat-start",
        "provider-dev-chat-send",
    }:
        profile_error = _permission_profile_error(payload)
        if profile_error:
            return profile_error
    if action == "channel-create-from-template":
        if not str(payload.get("template_id") or "").strip():
            return "template_id is required"
        if (
            payload.get("overrides") is not None
            and not isinstance(payload.get("overrides"), dict)
        ):
            return "overrides must be a mapping"
    if action == "channel-discussion-start":
        if not str(payload.get("channel_id") or "").strip():
            return "channel_id is required"
        if not any(
            str(payload.get(key) or "").strip()
            for key in ("message", "objective", "text")
        ):
            return "message or objective is required"
    if action == "research-start":
        if not str(payload.get("task_id") or "").strip():
            return "task_id is required"
        if not any(
            str(payload.get(key) or "").strip()
            for key in ("topic", "objective", "message")
        ):
            return "topic, objective, or message is required"
        template_id = str(payload.get("template_id") or "").strip()
        if (
            template_id
            and template_id != "research-fanout.fixed.v1"
        ):
            return "template_id must be research-fanout.fixed.v1"
    if action == "research-adopt":
        for key in (
            "request_id",
            "request_revision",
            "artifact_ref",
            "artifact_digest",
            "summary",
        ):
            if payload.get(key) in (None, ""):
                return f"{key} is required"
        try:
            request_revision = int(payload.get("request_revision"))
        except (TypeError, ValueError):
            request_revision = 0
        if request_revision < 1:
            return "request_revision must be a positive integer"
    return ""


def _permission_profile_error(payload: dict) -> str:
    permission_profile = str(
        payload.get("permission_profile") or ""
    ).strip()
    normalized_profile = (
        permission_profile
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )
    if (
        permission_profile
        and normalized_profile not in CHANNEL_PERMISSION_PROFILES
    ):
        return "permission_profile must be one of " + ", ".join(
            sorted(CHANNEL_PERMISSION_PROFILES)
        )
    if normalized_profile == "dangerous_full" and not _truthy(
        payload.get("dangerous_ack")
        or payload.get("permission_profile_ack")
        or payload.get("confirm_dangerous")
    ):
        return (
            "permission_profile dangerous_full requires "
            "dangerous_ack=true"
        )
    return ""


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


__all__ = ["validate_collaboration_action_payload"]
