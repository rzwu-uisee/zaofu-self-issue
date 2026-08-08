"""Turnkey channel reply turn (feishu B4-core, doc 98 §9).

Replaces the Feishu sidecar's canned echo with the REAL channel agent reply path:
an inbound channel message is routed (route_channel_message → emits
channel.agent.reply.requested) and each reply request is dispatched
(dispatch_reply_request → runs the member's backend → emits the agent's
channel.message.posted + reply lifecycle). With a fake/persona backend the reply
is deterministic; with claude-code/codex it is a real LLM answer that streams
part.delta (consumed by stream_card B1-B3). No reply text is synthesized here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.channel_adapter import (
    ChannelDispatchResult,
    dispatch_pending_replies,
)
from zf.runtime.channel_router import route_channel_message


def run_channel_reply_turn(
    state_dir: Path,
    writer: EventWriter,
    config: Any | None,
    *,
    message_event: ZfEvent,
    message_payload: dict[str, Any],
    actor: str = "feishu-bridge",
    source: str = "feishu",
    project_root: Path | None = None,
    agent_context: dict[str, Any] | None = None,
    deterministic_reply: str | None = None,
    deterministic_reason: str = "",
) -> dict[str, Any]:
    """Route one inbound message and drain its channel reply queue.

    Routing and provider execution intentionally stay separate here.  A direct
    reply turn can race with a second inbound message for the same member: the
    second request is ``queued`` while the first backend run is active.  The
    queue drain owns both the initial dispatch and one terminal re-scan, so the
    newest queued request cannot remain stranded when no Orchestrator reactor
    happens to be running alongside the Feishu bridge.

    Returns ``{route, dispatched: [(request_id, result)]}``.  Each tuple uses
    the canonical reply request id, not an event id.
    """
    route = route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=message_event,
        message_payload=message_payload,
        actor=actor,
        source=source,
        config=config,
        project_root=project_root,
        agent_context=agent_context,
        deterministic_reply=deterministic_reply,
        deterministic_reason=deterministic_reason,
        dispatch_inline=False,
    )
    channel_id = str(message_payload.get("channel_id") or "")
    dispatched: list[tuple[str, Any]] = []
    if not route.reply_requests:
        return {"route": route, "dispatched": dispatched}

    def drain() -> ChannelDispatchResult:
        return dispatch_pending_replies(
            state_dir=state_dir,
            writer=writer,
            channel_id=channel_id,
            actor=actor,
            source=source,
            allow_queued=True,
            config=config,
            project_root=project_root,
            deterministic_reply=deterministic_reply,
            deterministic_reason=deterministic_reason,
            max_dispatch=max(1, len(route.reply_requests)),
        )

    def record(result: ChannelDispatchResult) -> None:
        for request_id in result.dispatched:
            dispatched.append((request_id, result))

    first = drain()
    record(first)
    # ``dispatch_pending_replies`` snapshots candidates before provider work.
    # A message that arrived while that work ran is visible only to this fresh
    # scan.  It is harmless when no new request arrived: the second scan is
    # empty and does not emit another lifecycle event.
    if first.completed or first.failed:
        record(drain())
    return {"route": route, "dispatched": dispatched}
