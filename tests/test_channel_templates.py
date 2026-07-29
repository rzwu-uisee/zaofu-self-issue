from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from zf.core.events import EventWriter, ZfEvent
from zf.core.events.log import EventLog
from zf.runtime.channel_discussion import advance_discussion
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_reply_contract import emit_structured_reply_events
from zf.runtime.channel_sidecar import hydrate_channel_message_text
from zf.runtime.channel_templates import (
    TEMPLATE_VERSION,
    materialize_channel_template,
    template_digest,
)
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.kanban_plan_requests import (
    PLAN_ANSWERED_EVENT,
    PLAN_REQUESTED_EVENT,
    plan_requirement_digest,
)
from zf.runtime.orchestrator_reactor import EventReactorMixin
from zf.web.plan_extraction import extract_plan_request


def _runtime(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    service = ControlledActionService(
        state_dir,
        writer,
        project_root=tmp_path,
        actor="web",
        source="kanban-agent",
        surface="web",
    )
    return state_dir, log, writer, service


def _execute(service, writer, action: str, payload: dict):
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        payload={"action": action, "request": payload},
    )
    return service.execute(
        action=action,
        requested_action=action,
        payload=payload,
        requested=requested,
    )


def test_prd_template_persists_version_digest_roles_and_discussion(
    tmp_path: Path,
):
    state_dir, _, writer, service = _runtime(tmp_path)

    result = _execute(
        service,
        writer,
        "channel-create-from-template",
        {
            "template_id": "prd-clarification",
            "channel_id": "ch-prd",
            "overrides": {
                "backend": "fake",
                "role_overrides": {
                    "security_reviewer": {"enabled": False},
                },
            },
        },
    )

    assert result["ok"] is True
    assert result["template_version"] == TEMPLATE_VERSION
    assert result["template_digest"] == template_digest("prd-clarification")
    assert len(result["materialization_digest"]) == 64
    channel = project_channel(state_dir, "ch-prd") or {}
    template = channel["scope"]["template"]
    assert template == {
        "id": "prd-clarification",
        "version": TEMPLATE_VERSION,
        "digest": template_digest("prd-clarification"),
        "materialization_digest": result["materialization_digest"],
        "writer_role": "product_pm",
        "writer_scope": ["docs/design/**", "docs/impl/**"],
    }
    members = {member["channel_role"]: member for member in channel["members"]}
    assert set(members) == {"product_pm", "arch", "critic", "synthesizer"}
    assert members["product_pm"]["permission_profile"] == "project_writer"
    assert members["arch"]["permission_profile"] == "read_only"
    assert members["synthesizer"]["skill_refs"] == [
        "skills/zf-channel-discussion-synthesizer/SKILL.md"
    ]
    assert channel["discussion"]["mode"] == "fanout_then_synthesis"
    assert channel["discussion"]["synthesizer"] == "synthesizer"

    conflict = _execute(
        service,
        writer,
        "channel-create-from-template",
        {
            "template_id": "prd-clarification",
            "channel_id": "ch-prd",
            "overrides": {
                "backend": "fake",
                "model": "different-model",
                "role_overrides": {
                    "security_reviewer": {"enabled": False},
                },
            },
        },
    )
    assert conflict["status"] == "conflict"


def test_template_preflight_rejects_unknown_override_without_partial_channel(
    tmp_path: Path,
):
    state_dir, log, writer, service = _runtime(tmp_path)

    result = _execute(
        service,
        writer,
        "channel-create-from-template",
        {
            "template_id": "quick-change",
            "channel_id": "ch-invalid",
            "overrides": {"arbitrary_yaml": {"roles": ["root"]}},
        },
    )

    assert result["ok"] is False
    assert result["status"] == "invalid_template"
    assert project_channel(state_dir, "ch-invalid") is None
    assert not [
        event for event in log.read_all()
        if event.type == "channel.created"
        and event.payload.get("channel_id") == "ch-invalid"
    ]

    stale = _execute(
        service,
        writer,
        "channel-create-and-start",
        {
            "template_id": "quick-change",
            "channel_id": "ch-stale-plan",
            "message": "Do not run a changed template.",
            "expected_materialization_digest": "0" * 64,
            "overrides": {"backend": "fake"},
        },
    )
    assert stale["status"] == "template_superseded"
    assert project_channel(state_dir, "ch-stale-plan") is None


def test_quick_change_grants_at_most_one_writer():
    materialized, error = materialize_channel_template(
        "quick-change",
        overrides={"backend": "fake"},
    )

    assert error == ""
    assert materialized is not None
    writers = [
        member for member in materialized["members"]
        if member["permission_profile"] != "read_only"
    ]
    assert [(member["channel_role"], member["permission_profile"]) for member in writers] == [
        ("tech_leader", "workspace_writer")
    ]

    invalid, invalid_error = materialize_channel_template(
        "quick-change",
        overrides={"writer_scope": []},
    )
    assert invalid is None
    assert "non-empty string list" in invalid_error

    invalid, invalid_error = materialize_channel_template(
        "quick-change",
        overrides={"budget": {"max_rounds": "unbounded"}},
    )
    assert invalid is None
    assert invalid_error == "budget.max_rounds must be an integer"

    invalid, invalid_error = materialize_channel_template(
        "quick-change",
        overrides={
            "budget": {
                "phase_deadline_seconds": {"unknown_phase": 60},
            },
        },
    )
    assert invalid is None
    assert invalid_error == "unsupported phase deadline: unknown_phase"


def test_create_and_start_is_one_action_with_seeded_requirement_and_followup(
    tmp_path: Path,
):
    state_dir, log, writer, service = _runtime(tmp_path)

    result = _execute(
        service,
        writer,
        "channel-create-and-start",
        {
            "template_id": "quick-change",
            "channel_id": "ch-auto",
            "thread_id": "main",
            "message": "Review and implement the requested API change.",
            "overrides": {
                "backend": "fake",
                "budget": {"max_rounds": 4},
            },
        },
    )

    assert result["ok"] is True
    assert result["status"] == "started"
    assert result["channel_id"] == "ch-auto"
    assert result["member_count"] == 3
    assert result["participants"] == [
        "tech_leader",
        "dev_reviewer",
        "qa_analyst",
    ]
    assert result["max_rounds"] == 4
    channel = project_channel(state_dir, "ch-auto") or {}
    assert channel["discussion"]["default_responder_id"] == "tech_leader"
    assert channel["discussions"]["main"]["state"] == "phase1_blind"
    requirement = next(
        event
        for event in log.read_all()
        if event.type == "channel.message.posted"
        and event.payload.get("message_id") == result["message_id"]
    )
    assert hydrate_channel_message_text(
        state_dir,
        requirement.payload,
        strict=True,
    ) == "@all Review and implement the requested API change."
    completions = [
        event
        for event in log.read_all()
        if event.type == "runtime.action.completed"
        and event.payload.get("action") == "channel-create-and-start"
    ]
    assert len(completions) == 1

    followup = _execute(
        service,
        writer,
        "channel-post-message",
        {
            "channel_id": "ch-auto",
            "thread_id": "main",
            "message": "Please narrow the implementation scope.",
        },
    )
    assert followup["ok"] is True
    assert followup["route"]["targets"] == ["tech_leader"]


def test_controlled_question_resolution_and_consensus_confirmation(
    tmp_path: Path,
) -> None:
    state_dir, log, writer, service = _runtime(tmp_path)
    created = _execute(service, writer, "channel-create-from-template", {
        "template_id": "quick-change",
        "channel_id": "ch-owner-controls",
        "overrides": {"backend": "fake"},
    })
    assert created["ok"] is True
    writer.emit(
        "channel.question.opened",
        actor="tech_leader",
        correlation_id="ch-owner-controls",
        payload={
            "channel_id": "ch-owner-controls",
            "thread_id": "main",
            "question_id": "q-scope",
            "question": "Should the API remain backward compatible?",
            "category": "clarification",
            "asked_by": "tech_leader",
            "source": "test",
        },
    )

    resolved = _execute(service, writer, "channel-question-resolve", {
        "channel_id": "ch-owner-controls",
        "thread_id": "main",
        "question_id": "q-scope",
        "resolution": "answered",
        "answer": "Yes, preserve the current API.",
    })

    assert resolved["ok"] is True
    question = project_channel(
        state_dir,
        "ch-owner-controls",
    )["open_questions"][0]
    assert question["status"] == "resolved"
    assert question["answer"] == "Yes, preserve the current API."

    writer.emit(
        "channel.consensus.proposed",
        actor="tech_leader",
        correlation_id="ch-owner-controls",
        payload={
            "channel_id": "ch-owner-controls",
            "thread_id": "main",
            "artifact_ref": "channel-artifacts/ch-owner-controls/prd.md",
            "artifact_digest": "b" * 64,
            "proposed_by": "tech_leader",
            "required_signers": ["tech_leader"],
            "source": "test",
        },
    )
    stale = _execute(service, writer, "channel-consensus-confirm", {
        "channel_id": "ch-owner-controls",
        "thread_id": "main",
        "artifact_digest": "c" * 64,
    })
    confirmed = _execute(service, writer, "channel-consensus-confirm", {
        "channel_id": "ch-owner-controls",
        "thread_id": "main",
        "artifact_digest": "b" * 64,
    })

    assert stale["status"] == "consensus_stale"
    assert confirmed["status"] == "confirmed"
    signed = [
        event
        for event in log.read_all()
        if event.type == "channel.consensus.signed"
        and event.payload.get("member_id") == "owner:operator"
    ]
    assert len(signed) == 1


def test_action_bound_plan_selection_creates_channel_members_and_starts(
    tmp_path: Path,
):
    state_dir, log, writer, service = _runtime(tmp_path)
    requirement_seed = writer.emit(
        "user.message",
        actor="web",
        task_id="TASK-PLAN",
        payload={
            "message": (
                "The migration must preserve Task contract refs and "
                "provider session continuity."
            ),
            "project_id": "zaofu",
            "conversation_id": "kanban:zaofu",
            "thread_key": "main",
        },
    )
    origin = writer.emit(
        "user.message",
        actor="web",
        task_id="TASK-PLAN",
        payload={
            "message": "基于上面的需求创建评审 Channel。",
            "project_id": "zaofu",
            "conversation_id": "kanban:zaofu",
            "thread_key": "main",
        },
    )
    request = extract_plan_request(
        """
        {
          "plan_request": {
            "header": "Channel setup",
            "id": "channel-setup",
            "question": "Which team should collaborate?",
            "submit_action": "channel-create-and-start",
            "submit_label": "Create & start",
            "options": [
              {
                "id": "quick",
                "label": "Quick change (Recommended)",
                "recommended": true,
                "description": "Three implementation roles and four rounds.",
                "submit_payload": {
                  "template_id": "quick-change",
                  "name": "Session registry migration",
                  "overrides": {
                    "backend": "fake",
                    "budget": {"max_rounds": 4}
                  }
                }
              },
              {
                "id": "architecture",
                "label": "Architecture review",
                "description": "Four review roles and six rounds.",
                "submit_payload": {
                  "template_id": "architecture-review",
                  "overrides": {
                    "backend": "fake",
                    "budget": {"max_rounds": 6}
                  }
                }
              }
            ],
            "allow_other": false,
            "reason": "The role mix changes the review."
          }
        }
        """,
        plan_context={
            "project_id": "zaofu",
            "task_id": "TASK-PLAN",
            "conversation_id": "kanban:zaofu",
            "thread_key": "main",
            "originating_message_event_id": origin.id,
            "originating_message_event_ids": [
                requirement_seed.id,
                origin.id,
            ],
            "requirement_digest": plan_requirement_digest([
                (
                    requirement_seed.id,
                    requirement_seed.payload["message"],
                ),
                (origin.id, origin.payload["message"]),
            ]),
        },
    )
    assert request is not None and request["valid"] is True
    plan_event = ZfEvent(
        type=PLAN_REQUESTED_EVENT,
        actor="web",
        task_id="TASK-PLAN",
        correlation_id="kanban:zaofu",
    )
    request["request_event_id"] = plan_event.id
    plan_event.payload = {
        "plan_request": request,
        "request": request,
    }
    writer.append(plan_event)

    result = _execute(
        service,
        writer,
        "kanban-plan-apply",
        {
            "plan_response": {
                "request_event_id": plan_event.id,
                "request_id": request["request_id"],
                "revision": request["revision"],
                "question_id": request["question_id"],
                "option_id": "quick",
                "answer": "Quick change (Recommended)",
            },
        },
    )

    assert result["ok"] is True
    assert result["status"] == "applied"
    assert result["applied_action"] == "channel-create-and-start"
    assert result["member_count"] == 3
    assert result["max_rounds"] == 4
    channel = project_channel(state_dir, result["channel_id"]) or {}
    assert channel["name"] == "Session registry migration"
    assert {member["channel_role"] for member in channel["members"]} == {
        "tech_leader",
        "dev_reviewer",
        "qa_analyst",
    }
    requirement = next(
        event
        for event in log.read_all()
        if event.type == "channel.message.posted"
        and event.payload.get("channel_id") == result["channel_id"]
    )
    assert hydrate_channel_message_text(
        state_dir,
        requirement.payload,
        strict=True,
    ) == (
        "@all The migration must preserve Task contract refs and "
        "provider session continuity.\n\n基于上面的需求创建评审 Channel。"
    )
    assert requirement.payload["refs"]["plan_requirement_event_ids"] == [
        requirement_seed.id,
        origin.id,
    ]
    assert requirement.payload["refs"]["plan_requirement_digest"] == (
        request["requirement_digest"]
    )
    assert [
        event
        for event in log.read_all()
        if event.type == PLAN_ANSWERED_EVENT
        and event.payload.get("request_event_id") == plan_event.id
    ]

    repeated = _execute(
        service,
        writer,
        "kanban-plan-apply",
        {
            "plan_response": {
                "request_event_id": plan_event.id,
                "request_id": request["request_id"],
                "revision": request["revision"],
                "question_id": request["question_id"],
                "option_id": "quick",
                "answer": "Quick change (Recommended)",
            },
        },
    )
    assert repeated["status"] == "already_applied"
    assert len([
        event
        for event in log.read_all()
        if event.type == "channel.created"
        and event.payload.get("channel_id") == result["channel_id"]
    ]) == 1


def test_unstructured_channel_reply_cannot_freeze_or_propose_synthesis(
    tmp_path: Path,
):
    state_dir, log, writer, _ = _runtime(tmp_path)
    channel = {
        "channel_id": "ch-strict",
        "scope": {"template": {"id": "quick-change"}},
        "discussions": {
            "main": {
                "state": "phase1_blind",
                "requirement_message_id": "msg-requirement",
            },
        },
    }
    request = {
        "thread_id": "main",
        "message_id": "msg-requirement",
        "target_member_id": "tech_leader",
    }
    emit_structured_reply_events(
        state_dir=state_dir,
        writer=writer,
        channel=channel,
        request=request,
        message={"message_id": "msg-requirement", "refs": {}},
        reply="Plain prose without the required JSON contract.",
        reply_event_id="evt-reply-plain",
        actor="test",
        source="test",
    )
    emit_structured_reply_events(
        state_dir=state_dir,
        writer=writer,
        channel=channel,
        request={
            **request,
            "message_id": "msg-synthesis",
            "target_member_id": "tech_leader",
        },
        message={
            "message_id": "msg-synthesis",
            "refs": {"synthesis_request_id": "synth-strict"},
        },
        reply="Plain synthesis prose without the required JSON contract.",
        reply_event_id="evt-synth-plain",
        actor="test",
        source="test",
    )

    events = log.read_all()
    assert not [
        event for event in events
        if event.type in {
            "channel.questions.frozen",
            "channel.synthesis.proposed",
        }
    ]
    assert {
        event.payload.get("contract_status")
        for event in events
        if event.type == "channel.finding.recorded"
    } == {
        "invalid_missing_channel_contribution",
        "invalid_missing_channel_synthesis",
    }


def test_synthesis_open_questions_block_consensus_and_preserve_requirement(
    tmp_path: Path,
) -> None:
    state_dir, log, writer, _ = _runtime(tmp_path)
    channel = {
        "channel_id": "ch-open-prd",
        "name": "Open PRD",
        "messages": [{
            "message_id": "msg-requirement",
            "thread_id": "main",
            "text": "SCENARIO-WITNESS original requirement",
        }],
        "discussions": {
            "main": {
                "requirement_message_id": "msg-requirement",
            },
        },
        "open_questions": [],
        "linked_events": [],
    }

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
            "refs": {"synthesis_request_id": "synth-open"},
        },
        reply=(
            '{"channel_synthesis":{"title":"Draft PRD",'
            '"summary":"A draft with an owner gap.",'
            '"open_questions":["Which launch threshold is approved?"],'
            '"risks":[],"recommended_workflow":{},"confidence":"medium"}}'
        ),
        reply_event_id="evt-synth-open",
        actor="test",
        source="test",
    )

    events = log.read_all()
    synthesis = next(
        event
        for event in events
        if event.type == "channel.synthesis.proposed"
    )
    assert any(
        event.type == "channel.question.opened"
        and event.payload["question"]
        == "Which launch threshold is approved?"
        for event in events
    )
    assert not any(
        event.type == "channel.consensus.proposed"
        for event in events
    )
    artifact = (
        state_dir / synthesis.payload["artifact_ref"]
    ).read_text(encoding="utf-8")
    assert "SCENARIO-WITNESS original requirement" in artifact
    assert "- Which launch threshold is approved?" in artifact


def test_discussion_start_dispatches_participants_and_synthesis_once(
    tmp_path: Path,
):
    state_dir, log, writer, service = _runtime(tmp_path)
    created = _execute(
        service,
        writer,
        "channel-create-from-template",
        {
            "template_id": "quick-change",
            "channel_id": "ch-quick",
            "overrides": {"backend": "fake"},
        },
    )
    assert created["ok"] is True

    started = _execute(
        service,
        writer,
        "channel-discussion-start",
        {
            "channel_id": "ch-quick",
            "thread_id": "review-1",
            "message": "Review the requested change.",
        },
    )

    assert started["status"] == "started"
    assert started["participants"] == ["tech_leader", "dev_reviewer", "qa_analyst"]
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        completed = [
            event for event in log.read_all()
            if event.type == "channel.agent.reply.completed"
            and event.payload.get("thread_id") == "review-1"
        ]
        if len(completed) == 3:
            break
        time.sleep(0.02)
    assert len(completed) == 3
    assert {
        event.payload["target_member_id"] for event in completed
    } == {"tech_leader", "dev_reviewer", "qa_analyst"}

    advance_discussion(
        state_dir,
        writer,
        channel_id="ch-quick",
        thread_id="review-1",
        project_root=tmp_path,
    )
    advance_discussion(
        state_dir,
        writer,
        channel_id="ch-quick",
        thread_id="review-1",
        project_root=tmp_path,
    )
    synthesis_request = [
        event for event in log.read_all()
        if event.type == "channel.synthesis.requested"
        and event.payload.get("thread_id") == "review-1"
    ][-1]
    host = SimpleNamespace(
        state_dir=state_dir,
        event_log=log,
        event_writer=writer,
        project_root=tmp_path,
        config=None,
        openclaw_client=None,
    )
    EventReactorMixin._on_channel_synthesis_requested(host, synthesis_request)
    EventReactorMixin._on_channel_synthesis_requested(host, synthesis_request)

    events = log.read_all()
    synthesis_messages = [
        event for event in events
        if event.type == "channel.message.posted"
        and isinstance(event.payload.get("refs"), dict)
        and event.payload["refs"].get("synthesis_request_id")
        == synthesis_request.payload["request_id"]
    ]
    assert len(synthesis_messages) == 1
    syntheses = [
        event for event in events
        if event.type == "channel.synthesis.proposed"
        and event.payload.get("request_id") == synthesis_request.payload["request_id"]
    ]
    assert len(syntheses) == 1
    assert (
        state_dir / syntheses[0].payload["artifact_ref"]
    ).is_file()
    assert syntheses[0].payload["artifact_digest"]
    artifact = (
        state_dir / syntheses[0].payload["artifact_ref"]
    ).read_text(encoding="utf-8")
    for heading in (
        "## Source Requirement",
        "## Requirement",
        "## Decisions",
        "## Assumptions",
        "## Out of Scope",
        "## Acceptance Criteria",
        "## Risks",
        "## Open Questions",
        "## Recommended Workflow",
        "## Provenance",
    ):
        assert heading in artifact
    assert "Review the requested change." in artifact
    consensus = [
        event for event in events
        if event.type == "channel.consensus.proposed"
        and event.payload.get("artifact_digest")
        == syntheses[0].payload["artifact_digest"]
    ]
    assert len(consensus) == 1
    assert consensus[0].payload["required_signers"] == ["tech_leader"]
    assert any(
        event.type == "channel.consensus.signed"
        and event.payload.get("member_id") == "tech_leader"
        for event in events
    )
