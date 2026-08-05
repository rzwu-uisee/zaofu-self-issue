"""Replay-safe semantic question deduplication for Channel discussions."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.verification.event_schema import (
    EventSchemaRegistry,
    channel_event_schema_rules,
)
from zf.runtime.channel_discussion import advance_discussion
from zf.runtime.channel_context import build_channel_context_pack
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_question_dedup import (
    apply_question_dedup_reply,
    question_ledger_digest,
)


CHANNEL_ID = "ch-dedup"


def _writer(tmp_path: Path) -> tuple[Path, EventWriter]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    return state_dir, EventWriter(EventLog(state_dir / "events.jsonl"))


def _open(
    writer: EventWriter,
    question_id: str,
    *,
    thread_id: str = "main",
) -> None:
    writer.emit(
        "channel.question.opened",
        actor="arch",
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": thread_id,
            "question_id": question_id,
            "question": f"Question {question_id}?",
            "category": "scope",
            "asked_by": "arch",
            "source": "test",
        },
    )


def _invite(writer: EventWriter, member_id: str) -> None:
    writer.emit(
        "channel.member.invited",
        actor="test",
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "member_id": member_id,
            "member_type": "persona_agent",
            "provider": "persona",
            "backend": "persona",
            "channel_role": member_id,
            "permissions": ["read", "message", "summarize"],
            "source": "test",
        },
    )


def _apply(
    state_dir: Path,
    writer: EventWriter,
    payload: dict,
    *,
    request_id: str = "dedup-1",
) -> tuple[bool, str]:
    return apply_question_dedup_reply(
        state_dir=state_dir,
        writer=writer,
        channel_id=CHANNEL_ID,
        thread_id="main",
        request_id=request_id,
        payload=payload,
        actor="synthesizer",
        source="test",
        causation_id="reply-1",
    )


def test_question_dedup_events_have_canonical_schema_contracts() -> None:
    registry = EventSchemaRegistry.from_dict(channel_event_schema_rules())
    valid = ZfEvent(
        type="channel.question.dedup.requested",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "request_id": "dedup-1",
            "target_member_id": "synthesizer",
            "ledger_digest": "digest",
            "question_count": 12,
            "source": "test",
        },
    )
    invalid = ZfEvent(
        type="channel.question.dedup.applied",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "request_id": "dedup-1",
            "source": "test",
        },
    )

    assert registry.validate(valid) == []
    assert {
        violation.field_path for violation in registry.validate(invalid)
    } == {
        "payload.input_ledger_digest",
        "payload.output_ledger_digest",
    }
    assert registry.validate(ZfEvent(
        type="channel.question.dedup.remediation.exhausted",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "request_id": "dedup-3",
            "attempts": 3,
            "reason": "unknown_question_target:operator",
            "source": "test",
        },
    )) == []


def test_question_dedup_applies_groups_once_and_preserves_canonical_questions(
    tmp_path: Path,
) -> None:
    state_dir, writer = _writer(tmp_path)
    for index in range(12):
        _open(writer, f"q-{index}")
    channel = project_channel(state_dir, CHANNEL_ID)
    digest = question_ledger_digest(channel, thread_id="main")
    groups = [{
            "canonical_question_id": "q-0",
            "merge_question_ids": ["q-5", "q-6", "q-7"],
            "reason": "same owner decision",
        },
    ] + [
        {
            "canonical_question_id": f"q-{index}",
            "merge_question_ids": [f"q-{index + 7}"],
            "reason": "same owner decision",
        }
        for index in range(1, 5)
    ]

    assert _apply(
        state_dir,
        writer,
        {"ledger_digest": digest, "groups": groups},
    ) == (True, "applied")
    detail = project_channel(state_dir, CHANNEL_ID)
    by_id = {
        item["question_id"]: item for item in detail["open_questions"]
    }
    assert sum(item["status"] == "open" for item in by_id.values()) == 5
    assert sum(item["status"] == "merged" for item in by_id.values()) == 7
    event_count = len(writer.event_log.read_all())

    assert _apply(
        state_dir,
        writer,
        {"ledger_digest": digest, "groups": groups},
    ) == (True, "already_applied")
    assert len(writer.event_log.read_all()) == event_count

    _open(writer, "q-5")
    assert {
        item["question_id"]: item for item in project_channel(
            state_dir,
            CHANNEL_ID,
        )["open_questions"]
    }["q-5"]["status"] == "merged"


@pytest.mark.parametrize(
    ("payload_factory", "reason"),
    [
        (
            lambda digest: {
                "ledger_digest": "stale",
                "groups": [],
            },
            "stale_ledger_digest",
        ),
        (
            lambda digest: {
                "ledger_digest": digest,
                "groups": [{
                    "canonical_question_id": "q-0",
                    "merge_question_ids": ["q-unknown"],
                }],
            },
            "merge_question_not_open:q-unknown",
        ),
        (
            lambda digest: {
                "ledger_digest": digest,
                "groups": [
                    {
                        "canonical_question_id": "q-0",
                        "merge_question_ids": ["q-1"],
                    },
                    {
                        "canonical_question_id": "q-1",
                        "merge_question_ids": ["q-0"],
                    },
                ],
            },
            "merge_chain_or_cycle:q-0,q-1",
        ),
        (
            lambda digest: {
                "ledger_digest": digest,
                "groups": [{
                    "canonical_question_id": "q-0",
                    "merge_question_ids": ["q-other-thread"],
                }],
            },
            "merge_question_not_open:q-other-thread",
        ),
    ],
)
def test_question_dedup_rejects_invalid_or_stale_plans_without_merging(
    tmp_path: Path,
    payload_factory,
    reason: str,
) -> None:
    state_dir, writer = _writer(tmp_path)
    _open(writer, "q-0")
    _open(writer, "q-1")
    _open(writer, "q-other-thread", thread_id="other")
    digest = question_ledger_digest(
        project_channel(state_dir, CHANNEL_ID),
        thread_id="main",
    )

    ok, actual_reason = _apply(
        state_dir,
        writer,
        payload_factory(digest),
    )

    assert not ok
    assert actual_reason == reason
    assert not any(
        event.type == "channel.question.merged"
        for event in writer.event_log.read_all()
    )


def test_phase1_completion_requests_question_dedup_once(
    tmp_path: Path,
) -> None:
    state_dir, writer = _writer(tmp_path)
    started = writer.emit(
        "channel.discussion.started",
        actor="runtime",
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "roster": ["arch", "critic"],
            "synthesizer": "arch",
            "requirement_message_id": "msg-requirement",
            "source": "test",
        },
    )
    writer.emit(
        "channel.discussion.phase.changed",
        actor="runtime",
        causation_id=started.id,
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "phase": "phase1_blind",
            "source": "test",
        },
    )
    _open(writer, "q-0")
    _open(writer, "q-1")
    for member in ("arch", "critic"):
        request_id = f"reply-{member}"
        writer.emit(
            "channel.agent.reply.requested",
            actor="runtime",
            correlation_id=CHANNEL_ID,
            payload={
                "channel_id": CHANNEL_ID,
                "thread_id": "main",
                "request_id": request_id,
                "message_id": "msg-requirement",
                "target_member_id": member,
                "status": "pending",
                "source": "test",
            },
        )
        writer.emit(
            "channel.agent.reply.completed",
            actor="runtime",
            correlation_id=CHANNEL_ID,
            payload={
                "channel_id": CHANNEL_ID,
                "thread_id": "main",
                "request_id": request_id,
                "message_id": "msg-requirement",
                "target_member_id": member,
                "source": "test",
            },
        )

    first = advance_discussion(
        state_dir,
        writer,
        channel_id=CHANNEL_ID,
        thread_id="main",
    )
    second = advance_discussion(
        state_dir,
        writer,
        channel_id=CHANNEL_ID,
        thread_id="main",
    )

    assert first == [
        "channel.discussion.phase.changed",
        "channel.question.dedup.requested",
    ]
    assert "channel.question.dedup.requested" not in second
    requests = [
        event
        for event in writer.event_log.read_all()
        if event.type == "channel.question.dedup.requested"
    ]
    assert len(requests) == 1
    assert requests[0].payload["target_member_id"] == "arch"
    detail = project_channel(state_dir, CHANNEL_ID)
    assert detail["question_dedup_requests"][0]["status"] == "requested"


def test_dedup_context_uses_complete_canonical_ledger_and_digest(
    tmp_path: Path,
) -> None:
    state_dir, writer = _writer(tmp_path)
    for index in range(40):
        _open(writer, f"q-{index:02d}")
    digest = question_ledger_digest(
        project_channel(state_dir, CHANNEL_ID),
        thread_id="main",
    )
    writer.emit(
        "channel.message.posted",
        actor="runtime",
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "message_id": "msg-dedup",
            "member_id": "operator",
            "role": "user",
            "text": "Deduplicate.",
            "refs": {
                "question_dedup_request_id": "dedup-1",
                "question_ledger_digest": digest,
            },
            "source": "test",
        },
    )
    pack = build_channel_context_pack(
        project_channel(state_dir, CHANNEL_ID),
        channel_id=CHANNEL_ID,
        thread_id="main",
        target_member_id="arch",
        trigger_message_id="msg-dedup",
        visibility_profile="planner",
    )

    assert pack["question_ledger_complete"] is True
    assert len(pack["question_ledger"]) == 40
    assert pack["question_ledger_digest"] == digest


def test_dedup_applies_question_graph_and_targeted_cross_reviews_once(
    tmp_path: Path,
) -> None:
    state_dir, writer = _writer(tmp_path)
    _invite(writer, "arch")
    _invite(writer, "critic")
    _open(writer, "q-fact")
    digest = question_ledger_digest(
        project_channel(state_dir, CHANNEL_ID),
        thread_id="main",
    )
    payload = {
        "ledger_digest": digest,
        "groups": [],
        "question_updates": [{
            "question_id": "q-fact",
            "kind": "fact",
            "depends_on": [],
            "priority": "p0",
            "why_it_matters": "The design depends on this fact.",
            "recommended_answer": "Inspect the implementation.",
            "target_member_id": "arch",
        }],
        "cross_review_requests": [{
            "question_id": "q-fact",
            "target_member_ids": ["arch", "critic"],
            "prompt": "Verify the fact and report the strongest counterexample.",
            "reason": "The blind contributions conflict.",
            "source_refs": ["event:blind-round"],
        }],
    }

    assert _apply(state_dir, writer, payload) == (True, "applied")
    detail = project_channel(state_dir, CHANNEL_ID)
    question = {
        item["question_id"]: item for item in detail["open_questions"]
    }["q-fact"]
    assert question["kind"] == "fact"
    assert question["priority"] == "p0"
    assert question["target_member_id"] == "arch"
    assert len(detail["cross_reviews"]) == 2
    assert {
        item["target_member_id"] for item in detail["cross_reviews"]
    } == {"arch", "critic"}
    event_count = len(writer.event_log.read_all())

    assert _apply(state_dir, writer, payload) == (True, "already_applied")
    assert len(writer.event_log.read_all()) == event_count


def test_dedup_mechanically_routes_each_surviving_fact_for_review(
    tmp_path: Path,
) -> None:
    state_dir, writer = _writer(tmp_path)
    _invite(writer, "arch")
    _open(writer, "q-fact")
    digest = question_ledger_digest(
        project_channel(state_dir, CHANNEL_ID),
        thread_id="main",
    )

    assert _apply(state_dir, writer, {
        "ledger_digest": digest,
        "groups": [],
        "question_updates": [{
            "question_id": "q-fact",
            "kind": "fact",
            "target_member_id": "arch",
        }],
    }) == (True, "applied")

    detail = project_channel(state_dir, CHANNEL_ID)
    assert len(detail["cross_reviews"]) == 1
    assert detail["cross_reviews"][0]["question_id"] == "q-fact"
    assert detail["cross_reviews"][0]["target_member_id"] == "arch"


def test_rejected_dedup_retries_twice_then_emits_one_exhaustion(
    tmp_path: Path,
) -> None:
    state_dir, writer = _writer(tmp_path)
    started = writer.emit(
        "channel.discussion.started",
        actor="runtime",
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "roster": ["arch", "critic"],
            "synthesizer": "arch",
            "requirement_message_id": "msg-requirement",
            "source": "test",
        },
    )
    writer.emit(
        "channel.discussion.phase.changed",
        actor="runtime",
        causation_id=started.id,
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "phase": "phase2_relay",
            "source": "test",
        },
    )
    _open(writer, "q-0")
    digest = question_ledger_digest(
        project_channel(state_dir, CHANNEL_ID),
        thread_id="main",
    )
    writer.emit(
        "channel.question.dedup.requested",
        actor="runtime",
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "request_id": "dedup-initial",
            "target_member_id": "arch",
            "ledger_digest": digest,
            "generation": 1,
            "source": "test",
        },
    )
    writer.emit(
        "channel.question.dedup.rejected",
        actor="arch",
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "request_id": "dedup-initial",
            "reason": "unknown_question_target:operator",
            "source": "test",
        },
    )

    assert advance_discussion(
        state_dir, writer, channel_id=CHANNEL_ID, thread_id="main",
    ) == ["channel.question.dedup.requested"]
    requests = [
        event for event in writer.event_log.read_all()
        if event.type == "channel.question.dedup.requested"
    ]
    second = requests[-1]
    assert second.payload["generation"] == 2
    assert second.payload["prior_request_id"] == "dedup-initial"
    writer.emit(
        "channel.question.dedup.rejected",
        actor="arch",
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "request_id": second.payload["request_id"],
            "reason": "stale_ledger_digest",
            "source": "test",
        },
    )
    assert advance_discussion(
        state_dir, writer, channel_id=CHANNEL_ID, thread_id="main",
    ) == ["channel.question.dedup.requested"]
    third = [
        event for event in writer.event_log.read_all()
        if event.type == "channel.question.dedup.requested"
    ][-1]
    assert third.payload["generation"] == 3
    writer.emit(
        "channel.question.dedup.rejected",
        actor="arch",
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "request_id": third.payload["request_id"],
            "reason": "question_dependency_cycle:q-0",
            "source": "test",
        },
    )
    assert advance_discussion(
        state_dir, writer, channel_id=CHANNEL_ID, thread_id="main",
    ) == ["channel.question.dedup.remediation.exhausted"]
    assert advance_discussion(
        state_dir, writer, channel_id=CHANNEL_ID, thread_id="main",
    ) == []
    exhausted = [
        event for event in writer.event_log.read_all()
        if event.type == "channel.question.dedup.remediation.exhausted"
    ]
    assert len(exhausted) == 1
    assert exhausted[0].payload["attempts"] == 3


def test_dedup_rejects_invalid_question_graph_and_target_without_updates(
    tmp_path: Path,
) -> None:
    state_dir, writer = _writer(tmp_path)
    _invite(writer, "arch")
    _open(writer, "q-a")
    _open(writer, "q-b")
    digest = question_ledger_digest(
        project_channel(state_dir, CHANNEL_ID),
        thread_id="main",
    )

    ok, reason = _apply(
        state_dir,
        writer,
        {
            "ledger_digest": digest,
            "groups": [],
            "question_updates": [
                {"question_id": "q-a", "depends_on": ["q-b"]},
                {"question_id": "q-b", "depends_on": ["q-a"]},
            ],
        },
    )
    assert not ok
    assert reason.startswith("question_dependency_cycle:")
    assert not any(
        event.type == "channel.question.updated"
        for event in writer.event_log.read_all()
    )

    ok, reason = _apply(
        state_dir,
        writer,
        {
            "ledger_digest": digest,
            "groups": [],
            "question_updates": [{
                "question_id": "q-a",
                "target_member_id": "missing-member",
            }],
        },
        request_id="dedup-2",
    )
    assert not ok
    assert reason == "unknown_question_target:missing-member"
