"""Provider dispatch identity derived from a Channel member profile."""

from __future__ import annotations

from zf.core.events import EventWriter
from zf.runtime.channel_run_owner import provider_run_fields_for_request


_INACTIVE_MEMBER_STATUSES = {
    "failed",
    "rejected",
    "removed",
    "suspended",
}


def reject_inactive_channel_member(
    *,
    writer: EventWriter,
    member: dict,
    request: dict,
    request_id: str,
    channel_id: str,
    target_member_id: str,
    actor: str,
    source: str,
) -> dict[str, str] | None:
    status = str(member.get("status") or "active").strip().lower()
    if member and status not in _INACTIVE_MEMBER_STATUSES:
        return None
    writer.emit(
        "channel.agent.reply.failed",
        actor=actor,
        task_id=str(request.get("task_id") or "") or None,
        causation_id=str(request.get("event_id") or "") or None,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": str(request.get("thread_id") or "main"),
            "request_id": request_id,
            "message_id": str(request.get("message_id") or ""),
            "target_member_id": target_member_id,
            "context_pack_id": str(request.get("context_pack_id") or ""),
            "reason": "target_member_inactive",
            **provider_run_fields_for_request(channel_id, request),
            "source": source,
        },
    )
    return {
        "request_id": request_id,
        "reason": "target_member_inactive",
    }


def channel_profile_permission_fields(member: dict) -> dict:
    return {
        "skills": [
            str(item)
            for item in member.get("skill_refs") or []
            if isinstance(item, str)
        ],
        "profile_id": str(member.get("profile_id") or ""),
        "profile_revision": int(member.get("profile_revision") or 0),
        "profile_digest": str(member.get("profile_digest") or ""),
        "config_digest": str(member.get("config_digest") or ""),
        "skill_set_digest": str(member.get("skill_set_digest") or ""),
        "permission_digest": str(member.get("permission_digest") or ""),
        "profile_snapshot_ref": str(
            member.get("profile_snapshot_ref") or ""
        ),
        "profile_snapshot_sha256": str(
            member.get("profile_snapshot_sha256") or ""
        ),
    }
