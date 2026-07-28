"""Kernel reactor for a requested Channel synthesis turn."""

from __future__ import annotations

from zf.core.events import ZfEvent
from zf.runtime.channel_router import route_channel_message
from zf.runtime.channel_sidecar import channel_message_event_payload


def react_channel_synthesis_requested(
    host,
    event: ZfEvent,
) -> None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    channel_id = str(
        payload.get("channel_id") or event.correlation_id or ""
    )
    request_id = str(payload.get("request_id") or "")
    target_member_id = str(payload.get("target_member_id") or "")
    if not channel_id or not request_id or not target_member_id:
        return
    message = None
    for prior in host.event_log.read_all():
        prior_payload = (
            prior.payload if isinstance(prior.payload, dict) else {}
        )
        refs = (
            prior_payload.get("refs")
            if isinstance(prior_payload.get("refs"), dict)
            else {}
        )
        if (
            prior.type == "channel.message.posted"
            and str(refs.get("synthesis_request_id") or "") == request_id
        ):
            message = prior
            break
    thread_id = str(payload.get("thread_id") or "main")
    if message is None:
        prompt = str(
            payload.get("prompt")
            or "Synthesize this discussion into a decision, open questions, "
            "risks, and a recommended workflow."
        )
        message_payload = channel_message_event_payload(
            host.state_dir,
            {
                "channel_id": channel_id,
                "thread_id": thread_id,
                "message_id": f"msg-{request_id}",
                "member_id": "operator",
                "role": "user",
                "source": "runtime",
                "text": f"@{target_member_id} {prompt}",
                "mentions": [target_member_id],
                "refs": {"synthesis_request_id": request_id},
            },
            created_by="channel-synthesis:runtime",
            source_event_id=event.id,
        )
        message = host.event_writer.emit(
            "channel.message.posted",
            actor="orchestrator-reactor",
            task_id=event.task_id,
            causation_id=event.id,
            correlation_id=channel_id,
            payload=message_payload,
        )
    message_id = str((message.payload or {}).get("message_id") or "")
    for prior in host.event_log.read_all():
        prior_payload = (
            prior.payload if isinstance(prior.payload, dict) else {}
        )
        if (
            prior.type == "channel.agent.reply.requested"
            and str(prior_payload.get("message_id") or "") == message_id
        ):
            return
    route_channel_message(
        state_dir=host.state_dir,
        writer=host.event_writer,
        message_event=message,
        message_payload=message.payload,
        actor="orchestrator-reactor",
        source="runtime",
        project_root=getattr(host, "project_root", None),
        config=getattr(host, "config", None),
        openclaw_client=getattr(host, "openclaw_client", None),
        dispatch_inline=True,
    )


__all__ = ["react_channel_synthesis_requested"]
