"""Typed Channel handoff, template identity, and evidence sidecars."""

from __future__ import annotations

import json
from pathlib import Path

from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.runtime.channel_context import build_channel_context_pack
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_reply_contract import emit_structured_reply_events
from zf.runtime.channel_reply_prompt import channel_reply_response_contract


CHANNEL_ID = "ch-typed"


def _writer(tmp_path: Path) -> tuple[Path, EventWriter]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    return state_dir, EventWriter(EventLog(state_dir / "events.jsonl"))


def _channel() -> dict:
    return {
        "channel_id": CHANNEL_ID,
        "name": "Typed review",
        "scope": {
            "template": {
                "id": "architecture-review",
                "version": "v1",
                "digest": "template-digest",
                "materialization_digest": "materialized-digest",
            },
        },
        "messages": [],
        "open_questions": [],
        "linked_events": [],
        "discussions": {
            "main": {
                "state": "phase1_blind",
                "started_event_id": "evt-start",
                "requirement_message_id": "msg-requirement",
            },
        },
    }


def _emit_contribution(
    state_dir: Path,
    writer: EventWriter,
    channel: dict,
    body: dict,
) -> None:
    emit_structured_reply_events(
        state_dir=state_dir,
        writer=writer,
        channel=channel,
        request={
            "request_id": "reply-researcher",
            "thread_id": "main",
            "message_id": "msg-requirement",
            "target_member_id": "researcher",
        },
        message={"message_id": "msg-requirement"},
        reply=json.dumps({"channel_contribution": body}),
        reply_event_id="evt-reply",
        actor="test",
        source="test",
    )


def test_synthesis_prompt_states_mechanical_json_types() -> None:
    prompt = channel_reply_response_contract(
        {},
        {"thread_id": "main"},
        {"refs": {"synthesis_request_id": "synth-types"}},
    )

    assert (
        "recommended_workflow and classification must each be JSON objects"
        in prompt
    )
    assert "All plural fields must be JSON arrays" in prompt


def test_contribution_prompt_states_exact_question_enums() -> None:
    prompt = channel_reply_response_contract(
        _channel(),
        {
            "thread_id": "main",
            "message_id": "msg-requirement",
        },
        {"message_id": "msg-requirement"},
    )

    assert "fact|owner_decision|tradeoff|clarification" in prompt
    assert "p0|p1|p2|p3" in prompt
    assert "critical, high, medium, or low" in prompt


def test_consensus_review_prompt_pins_canonical_artifact_digest() -> None:
    prompt = channel_reply_response_contract(
        {},
        {"thread_id": "main"},
        {
            "refs": {
                "consensus_review_id": "creview-1",
                "artifact_ref": "channels/ch-1/prd/r2.json",
                "artifact_digest": "canonical-prd-digest",
            },
        },
    )

    assert 'artifact_ref="channels/ch-1/prd/r2.json"' in prompt
    assert 'artifact_digest="canonical-prd-digest"' in prompt
    assert "MUST equal that canonical digest exactly" in prompt
    assert "do not substitute a spec_digest" in prompt


def test_typed_contribution_and_synthesis_preserve_evidence_and_objects(
    tmp_path: Path,
) -> None:
    state_dir, writer = _writer(tmp_path)
    channel = _channel()
    _emit_contribution(
        state_dir,
        writer,
        channel,
        {
            "summary": "Primary sources support the bounded change.",
            "findings": [{
                "claim": "The API is versioned.",
                "confidence": "high",
            }],
            "contradictions": [],
            "risks": [{"risk": "migration", "severity": "medium"}],
            "questions": [],
            "source_refs": ["https://example.test/spec"],
            "evidence_refs": ["artifact:api-contract"],
            "freeze": True,
        },
    )
    finding = next(
        event
        for event in writer.event_log.read_all()
        if event.type == "channel.finding.recorded"
    )
    contribution_payload = json.loads(
        (state_dir / finding.payload["artifact_ref"]).read_text(
            encoding="utf-8",
        )
    )
    assert contribution_payload["body"]["findings"][0] == {
        "claim": "The API is versioned.",
        "confidence": "high",
    }
    assert finding.payload["source_refs"] == [
        "https://example.test/spec",
        "event:evt-reply",
    ]
    assert finding.payload["evidence_refs"] == ["artifact:api-contract"]
    assert (
        state_dir / finding.payload["source_manifest_ref"]
    ).is_file()

    projected = project_channel(state_dir, CHANNEL_ID)
    projected["scope"] = channel["scope"]
    projected["discussions"] = channel["discussions"]
    pack = build_channel_context_pack(
        projected,
        channel_id=CHANNEL_ID,
        thread_id="main",
        target_member_id="arch",
        trigger_message_id="msg-synthesis",
        visibility_profile="planner",
    )
    binding = pack["collaboration_contract"]
    assert binding["selected_template"]["id"] == "architecture-review"
    assert binding["template_binding_status"] == "frozen"
    assert binding["discussion_phase"] == "phase1_blind"
    assert "quick-change" in binding["allowed_template_ids"]
    assert pack["contribution_index"][0]["artifact_ref"] == (
        finding.payload["artifact_ref"]
    )

    emit_structured_reply_events(
        state_dir=state_dir,
        writer=writer,
        channel=projected,
        request={
            "thread_id": "main",
            "message_id": "msg-synthesis",
            "target_member_id": "arch",
        },
        message={
            "message_id": "msg-synthesis",
            "refs": {"synthesis_request_id": "synth-typed"},
        },
        reply=json.dumps({
            "channel_synthesis": {
                "title": "Typed decision",
                "decision": "proceed",
                "summary": "Proceed with the bounded change.",
                "decisions": [{
                    "decision": "keep API compatibility",
                    "owner": "platform",
                }],
                "assumptions": [],
                "out_of_scope": [],
                "acceptance_criteria": [{
                    "id": "AC-1",
                    "then": "existing clients continue to work",
                }],
                "open_questions": [],
                "risks": [{"risk": "migration", "severity": "medium"}],
                "recommended_workflow": {"kind": "issue"},
                "source_refs": ["https://example.test/spec"],
                "evidence_refs": ["artifact:api-contract"],
                "confidence": {"level": "high", "basis": "primary source"},
            },
        }),
        reply_event_id="evt-synthesis",
        actor="test",
        source="test",
    )
    synthesis = next(
        event
        for event in writer.event_log.read_all()
        if event.type == "channel.synthesis.proposed"
    )
    typed = json.loads(
        (state_dir / synthesis.payload["contract_ref"]).read_text(
            encoding="utf-8",
        )
    )
    assert typed["body"]["decisions"][0]["owner"] == "platform"
    assert typed["body"]["confidence"]["level"] == "high"
    assert typed["body"]["consumed_contribution_refs"] == [
        finding.payload["artifact_ref"]
    ]
    assert synthesis.payload["evidence_refs"] == [
        "artifact:api-contract"
    ]
    canonical_prd = json.loads(
        (state_dir / synthesis.payload["artifact_ref"]).read_text(
            encoding="utf-8",
        )
    )
    assert canonical_prd["schema_version"] == "channel-prd.v1"
    assert canonical_prd["body"]["synthesis"]["decisions"][0] == {
        "decision": "keep API compatibility",
        "owner": "platform",
    }


def test_phase2_reply_can_replace_an_invalid_initial_contribution(
    tmp_path: Path,
) -> None:
    state_dir, writer = _writer(tmp_path)
    channel = _channel()
    channel["discussions"]["main"]["state"] = "phase2_relay"

    emit_structured_reply_events(
        state_dir=state_dir,
        writer=writer,
        channel=channel,
        request={
            "request_id": "reply-researcher-correction",
            "thread_id": "main",
            "message_id": "msg-owner-correction",
            "target_member_id": "researcher",
        },
        message={"message_id": "msg-owner-correction", "refs": {}},
        reply=json.dumps({
            "channel_contribution": {
                "summary": "The corrected contribution is ready.",
                "findings": [{"kind": "fact", "statement": "Bounded."}],
                "questions": [],
                "risks": [],
                "source_refs": ["event:evt-owner-correction"],
                "evidence_refs": [],
                "freeze": True,
            },
        }),
        reply_event_id="evt-reply-correction",
        actor="test",
        source="test",
    )

    events = writer.event_log.read_all()
    assert any(
        event.type == "channel.finding.recorded"
        and event.payload.get("contract_status") == "structured"
        for event in events
    )
    assert any(
        event.type == "channel.questions.frozen"
        and event.payload.get("member_id") == "researcher"
        for event in events
    )


def test_context_pack_preserves_complete_contribution_index() -> None:
    channel = _channel()
    channel["linked_events"] = [
        {
            "id": f"evt-{index}",
            "type": "channel.finding.recorded",
            "payload": {
                "thread_id": "main",
                "member_id": f"member-{index}",
                "contract_status": "structured",
                "artifact_ref": (
                    f"channels/ch-typed/contracts/{index}.json"
                ),
                "artifact_digest": f"digest-{index}",
                "source_refs": [f"source:{index}"],
                "evidence_refs": [f"evidence:{index}"],
            },
        }
        for index in range(70)
    ]

    pack = build_channel_context_pack(
        channel,
        channel_id=CHANNEL_ID,
        thread_id="main",
        target_member_id="arch",
        trigger_message_id="msg-synthesis",
        visibility_profile="planner",
    )

    assert len(pack["contribution_index"]) == 70
    assert pack["contribution_index"][0]["event_id"] == "evt-0"
    assert pack["contribution_index"][-1]["event_id"] == "evt-69"


def test_projection_keeps_contributions_beyond_recent_event_window(
    tmp_path: Path,
) -> None:
    state_dir, writer = _writer(tmp_path)
    expected_refs = []
    for index in range(5):
        artifact_ref = f"channels/{CHANNEL_ID}/contracts/{index}.json"
        expected_refs.append(artifact_ref)
        writer.emit(
            "channel.finding.recorded",
            actor=f"member-{index}",
            correlation_id=CHANNEL_ID,
            payload={
                "channel_id": CHANNEL_ID,
                "thread_id": "main",
                "member_id": f"member-{index}",
                "contract_status": "structured",
                "artifact_ref": artifact_ref,
                "artifact_digest": f"digest-{index}",
                "source_refs": [f"source:{index}"],
                "evidence_refs": [f"evidence:{index}"],
            },
        )
    for index in range(100):
        writer.emit(
            "channel.relay.suppressed",
            actor="test",
            correlation_id=CHANNEL_ID,
            payload={
                "channel_id": CHANNEL_ID,
                "thread_id": "main",
                "reason": f"noise-{index}",
            },
        )

    channel = project_channel(state_dir, CHANNEL_ID)
    assert channel is not None
    assert len(channel["linked_events"]) == 80
    assert len(channel["contributions"]) == 5
    pack = build_channel_context_pack(
        channel,
        channel_id=CHANNEL_ID,
        thread_id="main",
        target_member_id="synthesizer",
        trigger_message_id="msg-synthesis",
        visibility_profile="planner",
    )
    assert [
        item["artifact_ref"] for item in pack["contribution_index"]
    ] == expected_refs

    emit_structured_reply_events(
        state_dir=state_dir,
        writer=writer,
        channel=channel,
        request={
            "thread_id": "main",
            "message_id": "msg-synthesis",
            "target_member_id": "synthesizer",
        },
        message={
            "message_id": "msg-synthesis",
            "refs": {"synthesis_request_id": "synth-complete"},
        },
        reply=json.dumps({
            "channel_synthesis": {
                "summary": "All durable contributions were consumed.",
                "open_questions": [],
            },
        }),
        reply_event_id="evt-synthesis-complete",
        actor="test",
        source="test",
    )
    synthesis = next(
        event
        for event in writer.event_log.read_all()
        if event.type == "channel.synthesis.proposed"
    )
    assert synthesis.payload["consumed_contribution_refs"] == expected_refs


def test_malformed_contract_and_unknown_classification_fail_closed(
    tmp_path: Path,
) -> None:
    state_dir, writer = _writer(tmp_path)
    channel = _channel()
    _emit_contribution(
        state_dir,
        writer,
        channel,
        {
            "summary": "Malformed questions.",
            "questions": "not-a-list",
            "freeze": True,
        },
    )
    events = writer.event_log.read_all()
    finding = next(
        event
        for event in events
        if event.type == "channel.finding.recorded"
    )
    assert finding.payload["contract_status"] == (
        "invalid_channel_contribution"
    )
    assert finding.payload["contract_error"] == "questions must be a list"
    assert finding.payload["request_id"] == "reply-researcher"
    failed = next(
        event
        for event in events
        if event.type == "channel.agent.reply.failed"
    )
    assert failed.payload["failure_class"] == (
        "channel_contribution_contract_invalid"
    )
    assert failed.payload["retryable"] is True
    assert not any(
        event.type == "channel.questions.frozen" for event in events
    )

    emit_structured_reply_events(
        state_dir=state_dir,
        writer=writer,
        channel=channel,
        request={
            "thread_id": "main",
            "target_member_id": "tech_leader",
        },
        message={
            "refs": {"synthesis_request_id": "synth-invalid-template"},
        },
        reply=json.dumps({
            "channel_synthesis": {
                "summary": "Route it.",
                "classification": {"template_id": "missing-template"},
            },
        }),
        reply_event_id="evt-classification",
        actor="test",
        source="test",
    )
    events = writer.event_log.read_all()
    assert not any(
        event.type == "channel.synthesis.proposed" for event in events
    )
    assert any(
        event.type == "channel.finding.recorded"
        and event.payload.get("contract_status")
        == "invalid_channel_synthesis"
        and "unknown classification template_id"
        in str(event.payload.get("contract_error") or "")
        for event in events
    )


def test_question_graph_contract_failure_is_retryable(tmp_path: Path) -> None:
    state_dir, writer = _writer(tmp_path)
    _emit_contribution(
        state_dir,
        writer,
        _channel(),
        {
            "summary": "Priority alias drifted.",
            "questions": [{
                "id": "q-priority",
                "question": "Which rollout is approved?",
                "kind": "owner_decision",
                "priority": "high",
                "target_member_id": "owner",
            }],
            "freeze": True,
        },
    )

    events = writer.event_log.read_all()
    finding = next(
        event
        for event in events
        if event.type == "channel.finding.recorded"
    )
    assert finding.payload["contract_status"] == (
        "invalid_channel_contribution_question_graph"
    )
    assert finding.payload["contract_error"] == "invalid_question_priority:high"
    failed = next(
        event
        for event in events
        if event.type == "channel.agent.reply.failed"
    )
    assert failed.payload["run_generation"] == 1
    assert failed.payload["failure_status"] == "contract_invalid"
    assert not any(
        event.type == "channel.questions.frozen" for event in events
    )

def test_synthesis_malformed_json_requests_bounded_repair_and_recovers(
    tmp_path: Path,
) -> None:
    state_dir, writer = _writer(tmp_path)
    channel = _channel()
    request = {
        "thread_id": "main",
        "target_member_id": "synthesizer",
    }
    emit_structured_reply_events(
        state_dir=state_dir,
        writer=writer,
        channel=channel,
        request=request,
        message={"refs": {"synthesis_request_id": "synth-repair"}},
        reply='{"channel_synthesis":{"summary":"truncated"',
        reply_event_id="evt-synth-invalid",
        actor="test",
        source="test",
    )

    repair = next(
        event
        for event in writer.event_log.read_all()
        if event.type == "channel.synthesis.repair.requested"
    )
    descriptor = repair.payload["invalid_reply_ref"]
    assert repair.payload["repair_revision"] == 1
    assert "invalid JSON" in repair.payload["contract_error"]
    assert (state_dir / descriptor["ref"]).is_file()

    emit_structured_reply_events(
        state_dir=state_dir,
        writer=writer,
        channel=channel,
        request=request,
        message={
            "refs": {
                "synthesis_request_id": "synth-repair",
                "synthesis_repair_id": repair.payload["repair_id"],
                "synthesis_repair_revision": 1,
            },
        },
        reply=json.dumps({
            "channel_synthesis": {
                "summary": "The complete repair is valid.",
                "open_questions": [],
                "readiness": {
                    "verdict": "needs_owner",
                    "implementation_start": False,
                    "gaps": ["Owner approval"],
                    "risks": [],
                    "evidence_refs": [],
                    "reason": "Owner approval remains.",
                },
            },
        }),
        reply_event_id="evt-synth-repaired",
        actor="test",
        source="test",
    )

    events = writer.event_log.read_all()
    assert sum(
        event.type == "channel.synthesis.proposed" for event in events
    ) == 1
    completed = next(
        event
        for event in events
        if event.type == "channel.synthesis.repair.completed"
    )
    assert completed.payload["repair_id"] == repair.payload["repair_id"]


def test_synthesis_repair_exhaustion_blocks_and_ignores_late_revision(
    tmp_path: Path,
) -> None:
    state_dir, writer = _writer(tmp_path)
    channel = _channel()
    request = {
        "thread_id": "main",
        "target_member_id": "synthesizer",
    }

    def submit(reply: str, event_id: str, refs: dict) -> None:
        emit_structured_reply_events(
            state_dir=state_dir,
            writer=writer,
            channel=channel,
            request=request,
            message={"refs": refs},
            reply=reply,
            reply_event_id=event_id,
            actor="test",
            source="test",
        )

    submit(
        '{"channel_synthesis":',
        "evt-invalid-r0",
        {"synthesis_request_id": "synth-exhaust"},
    )
    repair_one = next(
        event
        for event in writer.event_log.read_all()
        if event.type == "channel.synthesis.repair.requested"
    )
    submit(
        json.dumps({"channel_synthesis": {"summary": 7}}),
        "evt-invalid-r1",
        {
            "synthesis_request_id": "synth-exhaust",
            "synthesis_repair_id": repair_one.payload["repair_id"],
            "synthesis_repair_revision": 1,
        },
    )
    repair_two = [
        event
        for event in writer.event_log.read_all()
        if event.type == "channel.synthesis.repair.requested"
    ][-1]
    submit(
        json.dumps({
            "channel_synthesis": {
                "summary": "Still invalid.",
                "readiness": {"verdict": "maybe"},
            },
        }),
        "evt-invalid-r2",
        {
            "synthesis_request_id": "synth-exhaust",
            "synthesis_repair_id": repair_two.payload["repair_id"],
            "synthesis_repair_revision": 2,
        },
    )
    submit(
        json.dumps({
            "channel_synthesis": {
                "summary": "Late but otherwise valid.",
                "open_questions": [],
            },
        }),
        "evt-late-r1",
        {
            "synthesis_request_id": "synth-exhaust",
            "synthesis_repair_id": repair_one.payload["repair_id"],
            "synthesis_repair_revision": 1,
        },
    )

    events = writer.event_log.read_all()
    assert sum(
        event.type == "channel.synthesis.repair.requested" for event in events
    ) == 2
    blocked = next(
        event for event in events if event.type == "channel.synthesis.blocked"
    )
    assert blocked.payload["repair_revision"] == 2
    assert any(
        event.type == "channel.synthesis.repair.stale_ignored"
        and event.payload["source_reply_event_id"] == "evt-late-r1"
        for event in events
    )
    assert not any(
        event.type == "channel.synthesis.proposed" for event in events
    )


def test_cross_review_can_evidence_resolve_a_targeted_fact(
    tmp_path: Path,
) -> None:
    state_dir, writer = _writer(tmp_path)
    writer.emit(
        "channel.member.invited",
        actor="test",
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "member_id": "arch",
            "member_type": "persona_agent",
            "provider": "persona",
            "backend": "persona",
            "channel_role": "arch",
            "permissions": ["read", "message", "summarize"],
            "source": "test",
        },
    )
    writer.emit(
        "channel.question.opened",
        actor="test",
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "question_id": "q-fact",
            "question": "Which protocol is implemented?",
            "category": "architecture",
            "kind": "fact",
            "depends_on": [],
            "priority": "p0",
            "target_member_id": "arch",
            "asked_by": "critic",
            "source": "test",
        },
    )
    writer.emit(
        "channel.cross_review.requested",
        actor="critic",
        correlation_id=CHANNEL_ID,
        payload={
            "schema_version": "channel.cross_review.v1",
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "request_id": "xreview-1",
            "question_id": "q-fact",
            "target_member_id": "arch",
            "prompt": "Verify the implemented protocol.",
            "reason": "The fact requires evidence.",
            "source": "test",
        },
    )
    channel = project_channel(state_dir, CHANNEL_ID)

    emit_structured_reply_events(
        state_dir=state_dir,
        writer=writer,
        channel=channel,
        request={
            "thread_id": "main",
            "target_member_id": "arch",
        },
        message={
            "refs": {
                "cross_review_request_id": "xreview-1",
                "question_id": "q-fact",
            },
        },
        reply=json.dumps({
            "channel_cross_review": {
                "summary": "The implementation uses protocol v2.",
                "answer": "Protocol v2 is implemented.",
                "findings": [{"claim": "v2", "status": "confirmed"}],
                "contradictions": [],
                "risks": [],
                "source_refs": ["src:protocol"],
                "evidence_refs": ["file:src/protocol.py"],
            },
        }),
        reply_event_id="evt-cross-review",
        actor="test",
        source="test",
    )

    events = writer.event_log.read_all()
    completed = next(
        event
        for event in events
        if event.type == "channel.cross_review.completed"
    )
    assert (state_dir / completed.payload["artifact_ref"]).is_file()
    detail = project_channel(state_dir, CHANNEL_ID)
    question = {
        item["question_id"]: item for item in detail["open_questions"]
    }["q-fact"]
    assert question["status"] == "resolved"
    assert question["resolution"] == "evidence"
    assert question["evidence_refs"] == ["file:src/protocol.py"]


def test_cross_review_rejects_a_non_target_reply(
    tmp_path: Path,
) -> None:
    state_dir, writer = _writer(tmp_path)
    for member_id in ("arch", "critic"):
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
    writer.emit(
        "channel.cross_review.requested",
        actor="critic",
        correlation_id=CHANNEL_ID,
        payload={
            "schema_version": "channel.cross_review.v1",
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "request_id": "xreview-target",
            "question_id": "q-fact",
            "target_member_id": "arch",
            "prompt": "Verify the implemented protocol.",
            "reason": "The fact requires evidence.",
            "source": "test",
        },
    )
    emit_structured_reply_events(
        state_dir=state_dir,
        writer=writer,
        channel=project_channel(state_dir, CHANNEL_ID),
        request={
            "thread_id": "main",
            "target_member_id": "critic",
        },
        message={
            "refs": {
                "cross_review_request_id": "xreview-target",
                "question_id": "q-fact",
            },
        },
        reply=json.dumps({
            "channel_cross_review": {
                "summary": "I was not the requested reviewer.",
            },
        }),
        reply_event_id="evt-cross-review-wrong-target",
        actor="test",
        source="test",
    )

    events = writer.event_log.read_all()
    assert not any(
        event.type == "channel.cross_review.completed"
        for event in events
    )
    assert any(
        event.type == "channel.cross_review.rejected"
        and event.payload.get("reason") == "cross_review_target_mismatch"
        for event in events
    )


def test_typed_contribution_preserves_question_dependencies_and_frontier(
    tmp_path: Path,
) -> None:
    state_dir, writer = _writer(tmp_path)
    channel = _channel()
    _emit_contribution(
        state_dir,
        writer,
        channel,
        {
            "summary": "Two decisions must be made in order.",
            "questions": [
                {
                    "id": "policy",
                    "question": "Which compatibility policy is required?",
                    "kind": "owner_decision",
                    "priority": "p0",
                    "why_it_matters": "It constrains the rollout.",
                    "recommended_answer": "Preserve the current API.",
                    "options": [
                        {
                            "id": "preserve",
                            "label": "Preserve API (Recommended)",
                            "description": "Retain the compatibility surface.",
                            "recommended": True,
                        },
                        {
                            "id": "break",
                            "label": "Allow breaking changes",
                            "description": "Optimize for the new contract.",
                        },
                    ],
                    "allow_other": False,
                    "target_member_id": "owner",
                },
                {
                    "id": "rollout",
                    "question": "Which rollout strategy should be used?",
                    "kind": "tradeoff",
                    "depends_on": ["policy"],
                    "priority": "p1",
                    "target_member_id": "owner",
                },
            ],
            "freeze": True,
        },
    )
    detail = project_channel(state_dir, CHANNEL_ID)
    by_text = {
        item["question"]: item for item in detail["open_questions"]
    }
    policy = by_text["Which compatibility policy is required?"]
    rollout = by_text["Which rollout strategy should be used?"]
    assert rollout["depends_on"] == [policy["question_id"]]
    assert [
        item["question_id"]
        for item in detail["owner_questionnaires"]["main"]
    ] == [policy["question_id"]]
    assert policy["options"][0]["id"] == "preserve"
    assert policy["allow_other"] is False


def test_consensus_review_is_digest_bound_and_preserves_blocker(
    tmp_path: Path,
) -> None:
    state_dir, writer = _writer(tmp_path)
    channel = _channel()
    channel["members"] = [
        {"member_id": "critic", "status": "active"},
        {"member_id": "arch", "status": "active"},
    ]
    channel["consensus"] = {
        "main": {
            "artifact_ref": "channel-artifacts/ch-typed/synth.md",
            "artifact_digest": "digest-1",
            "required_signers": ["critic", "arch"],
            "signed": {},
            "blocked": [],
        },
    }
    emit_structured_reply_events(
        state_dir=state_dir,
        writer=writer,
        channel=channel,
        request={
            "thread_id": "main",
            "target_member_id": "critic",
        },
        message={
            "refs": {
                "consensus_review_id": "creview-1",
                "artifact_ref": "channel-artifacts/ch-typed/synth.md",
                "artifact_digest": "digest-1",
            },
        },
        reply=json.dumps({
            "channel_consensus_review": {
                "verdict": "blocked",
                "summary": "The failure mode is missing.",
                "artifact_digest": "digest-1",
                "blocker_question": (
                    "What happens when the upstream provider times out?"
                ),
                "evidence_refs": ["test:timeout"],
            },
        }),
        reply_event_id="evt-consensus-review",
        actor="test",
        source="test",
    )
    blocked = next(
        event
        for event in writer.event_log.read_all()
        if event.type == "channel.consensus.blocked"
    )
    assert blocked.payload["member_id"] == "critic"
    assert blocked.payload["artifact_digest"] == "digest-1"
    assert blocked.payload["dissent"] == "The failure mode is missing."

    emit_structured_reply_events(
        state_dir=state_dir,
        writer=writer,
        channel=channel,
        request={
            "thread_id": "main",
            "target_member_id": "arch",
        },
        message={
            "refs": {
                "consensus_review_id": "creview-stale",
                "artifact_ref": "channel-artifacts/ch-typed/synth.md",
                "artifact_digest": "digest-1",
            },
        },
        reply=json.dumps({
            "channel_consensus_review": {
                "verdict": "signed",
                "summary": "Looks good.",
                "artifact_digest": "stale-digest",
            },
        }),
        reply_event_id="evt-consensus-stale",
        actor="test",
        source="test",
    )
    assert any(
        event.type == "channel.consensus.review.rejected"
        for event in writer.event_log.read_all()
    )

    emit_structured_reply_events(
        state_dir=state_dir,
        writer=writer,
        channel=channel,
        request={
            "thread_id": "main",
            "target_member_id": "intruder",
        },
        message={
            "refs": {
                "consensus_review_id": "creview-unauthorized",
                "artifact_ref": "channel-artifacts/ch-typed/synth.md",
                "artifact_digest": "digest-1",
            },
        },
        reply=json.dumps({
            "channel_consensus_review": {
                "verdict": "signed",
                "summary": "Looks good.",
                "artifact_digest": "digest-1",
            },
        }),
        reply_event_id="evt-consensus-unauthorized",
        actor="test",
        source="test",
    )
    assert any(
        event.type == "channel.consensus.review.rejected"
        and event.payload.get("member_id") == "intruder"
        for event in writer.event_log.read_all()
    )
