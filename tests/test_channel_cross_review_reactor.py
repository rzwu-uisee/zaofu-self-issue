from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_synthesis_reactor import (
    react_channel_cross_review_requested,
)


CHANNEL_ID = "ch-cross-review"


def test_cross_review_request_routes_once_and_completes_with_fake_provider(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    writer.emit(
        "channel.member.invited",
        actor="test",
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "member_id": "arch",
            "member_type": "provider_agent",
            "provider": "fake",
            "backend": "fake",
            "channel_role": "arch",
            "permissions": ["read", "message", "summarize"],
            "source": "test",
        },
    )
    requested = writer.emit(
        "channel.cross_review.requested",
        actor="synthesizer",
        correlation_id=CHANNEL_ID,
        payload={
            "schema_version": "channel.cross_review.v1",
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "request_id": "xreview-1",
            "dedup_request_id": "dedup-1",
            "question_id": "q-fact",
            "target_member_id": "arch",
            "prompt": "Verify the implementation fact.",
            "reason": "Blind contributions conflict.",
            "source_refs": ["event:blind"],
            "source": "test",
        },
    )
    host = SimpleNamespace(
        state_dir=state_dir,
        event_log=log,
        event_writer=writer,
        project_root=tmp_path,
        config=None,
        openclaw_client=None,
    )

    react_channel_cross_review_requested(host, requested)
    react_channel_cross_review_requested(host, requested)

    events = log.read_all()
    messages = [
        event
        for event in events
        if event.type == "channel.message.posted"
        and isinstance(event.payload.get("refs"), dict)
        and event.payload["refs"].get("cross_review_request_id")
        == "xreview-1"
    ]
    assert len(messages) == 1
    assert len([
        event
        for event in events
        if event.type == "channel.cross_review.completed"
        and event.payload.get("request_id") == "xreview-1"
    ]) == 1
    projected = project_channel(state_dir, CHANNEL_ID)
    assert projected["cross_reviews"][0]["status"] == "completed"
