"""Bounded repair entry for invalid Channel contribution contracts."""

from __future__ import annotations

from typing import Any

from zf.core.events import EventWriter
from zf.runtime.channel_run_owner import provider_run_fields_for_request
from zf.runtime.channel_synthesis_repair import emit_invalid_contract_finding


def reject_contribution_contract(
    *,
    writer: EventWriter,
    channel_id: str,
    thread_id: str,
    member_id: str,
    request: dict[str, Any],
    reply: str,
    reply_event_id: str,
    actor: str,
    source: str,
    status: str,
    reason: str = "",
) -> None:
    """Record a rejected semantic result and re-arm its provider request.

    Provider completion proves transport success only. A contribution that
    fails the typed contract is a retryable reply failure, so the existing
    generation-bounded remediation path can correct it without inventing a
    second Channel state machine.
    """

    if _already_rejected(writer, reply_event_id):
        return
    emit_invalid_contract_finding(
        writer=writer,
        channel_id=channel_id,
        thread_id=thread_id,
        member_id=member_id,
        request=request,
        reply=reply,
        reply_event_id=reply_event_id,
        actor=actor,
        source=source,
        status=status,
        reason=reason,
    )
    diagnostic = ":".join(part for part in (status, reason) if part)
    writer.emit(
        "channel.agent.reply.failed",
        actor="channel-contract",
        task_id=str(request.get("task_id") or "") or None,
        causation_id=reply_event_id,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": thread_id,
            "request_id": str(request.get("request_id") or ""),
            "message_id": str(request.get("message_id") or ""),
            "target_member_id": member_id,
            "context_pack_id": str(request.get("context_pack_id") or ""),
            "reason": f"channel contribution contract rejected: {diagnostic}",
            "failure_status": "contract_invalid",
            "failure_class": "channel_contribution_contract_invalid",
            "retryable": True,
            "terminal_disposition": "failed",
            **provider_run_fields_for_request(channel_id, request),
            "source": "runtime:channel-contract",
        },
    )


def _already_rejected(writer: EventWriter, reply_event_id: str) -> bool:
    return any(
        event.type == "channel.agent.reply.failed"
        and event.causation_id == reply_event_id
        and isinstance(event.payload, dict)
        and event.payload.get("failure_class")
        == "channel_contribution_contract_invalid"
        for event in writer.event_log.read_all()
    )


__all__ = ["reject_contribution_contract"]
