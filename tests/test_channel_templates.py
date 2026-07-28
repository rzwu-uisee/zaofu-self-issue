from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.runtime.channel_discussion import advance_discussion
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_reply_contract import emit_structured_reply_events
from zf.runtime.channel_templates import (
    TEMPLATE_VERSION,
    materialize_channel_template,
    template_digest,
)
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.orchestrator_reactor import EventReactorMixin


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
        "skills/zf-harness-spec-bridge/SKILL.md"
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
