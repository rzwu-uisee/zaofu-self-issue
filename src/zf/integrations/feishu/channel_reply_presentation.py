"""Read-only presentation data for Feishu Channel reply cards.

The Channel event ledger and sidecar body remain canonical.  Feishu card
projectors use this helper only to recover the human-readable assistant reply
and the exact inbound Feishu origin for a request.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from zf.runtime.channel_sidecar import hydrate_channel_message_text


@dataclass(frozen=True)
class ChannelReplyPresentation:
    """Read-only display and origin metadata for one assistant reply."""

    request_id: str
    channel_id: str
    member_id: str
    display_name: str
    text: str
    chat_id: str
    thread_id: str


def collect_channel_reply_presentations(
    state_dir: Path,
    events: Iterable[Any],
    *,
    member: str = "",
) -> dict[str, ChannelReplyPresentation]:
    """Return the latest committed assistant reply for each request id.

    ``channel.message.posted`` owns the semantic body.  Its ``refs.feishu``
    field is copied from the original inbound message by the Channel adapter,
    so the projection can reply in the original Feishu thread without creating
    a second source of routing truth.
    """

    event_list = list(events)
    reply_events: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    channel_ids: set[str] = set()
    for event in event_list:
        if str(getattr(event, "type", "") or "") != "channel.message.posted":
            continue
        payload = getattr(event, "payload", None)
        payload = payload if isinstance(payload, dict) else {}
        if str(payload.get("role") or "") != "assistant":
            continue
        member_id = str(payload.get("member_id") or "")
        if member and member_id != member:
            continue
        refs = payload.get("refs") if isinstance(payload.get("refs"), dict) else {}
        request_id = str(refs.get("request_id") or "").strip()
        if not request_id:
            continue
        channel_id = str(payload.get("channel_id") or "").strip()
        if channel_id:
            channel_ids.add(channel_id)
        reply_events.append((request_id, payload, refs))

    display_names = _channel_member_display_names(
        event_list,
        channel_ids,
    )
    presentations: dict[str, ChannelReplyPresentation] = {}
    for request_id, payload, refs in reply_events:
        channel_id = str(payload.get("channel_id") or "").strip()
        member_id = str(payload.get("member_id") or "").strip()
        origin = refs.get("feishu") if isinstance(refs.get("feishu"), dict) else {}
        chat_id = str(origin.get("chat_id") or "").strip()
        text = hydrate_channel_message_text(
            Path(state_dir), payload, strict=False
        ).strip()
        presentations[request_id] = ChannelReplyPresentation(
            request_id=request_id,
            channel_id=channel_id,
            member_id=member_id,
            display_name=(display_names.get((channel_id, member_id)) or "ZaoFu Agent"),
            text=text,
            chat_id=chat_id,
            thread_id=_origin_thread_id(origin) if chat_id else "",
        )
    return presentations


def _channel_member_display_names(
    events: list[Any],
    channel_ids: set[str],
) -> dict[tuple[str, str], str]:
    names: dict[tuple[str, str], str] = {}
    for event in events:
        if not str(getattr(event, "type", "") or "").startswith("channel.member."):
            continue
        payload = getattr(event, "payload", None)
        payload = payload if isinstance(payload, dict) else {}
        channel_id = str(payload.get("channel_id") or "").strip()
        member_id = str(payload.get("member_id") or "").strip()
        if not channel_id or channel_id not in channel_ids or not member_id:
            continue
        display_name = str(
            payload.get("display_name") or payload.get("persona") or ""
        ).strip()
        if display_name:
            names[(channel_id, member_id)] = display_name
    return names


def _origin_thread_id(origin: dict[str, Any]) -> str:
    """Return a Feishu *message* id suitable as a reply target.

    Feishu uses ``omt_*`` for a thread identifier and ``om_*`` for a message
    identifier.  The send API accepts the latter only.  Prefer the immutable
    root/parent message references copied from the inbound event and retain
    compatibility with older payloads that stored an ``om_*`` message in the
    ``thread_id`` field.
    """
    for key in (
        "root_message_id",
        "parent_message_id",
        "message_id",
        "thread_id",
    ):
        value = str(origin.get(key) or "").strip()
        if not value or value == "main":
            continue
        if key != "thread_id" or value.startswith("om_"):
            return value
    return ""


__all__ = [
    "ChannelReplyPresentation",
    "collect_channel_reply_presentations",
]
