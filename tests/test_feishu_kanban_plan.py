from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from zf.cli.feishu import _handle_event_data
from zf.core.config.loader import load_config
from zf.core.config.project_context import ProjectContext
from zf.core.config.schema import (
    FeishuIdentityConfig,
    FeishuIdentityUserConfig,
    IntegrationsConfig,
    ZfConfig,
)
from zf.core.events import EventWriter, ZfEvent
from zf.core.events.log import EventLog
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.integrations.feishu import agent_conversation
from zf.integrations.feishu.kanban_plan_card import (
    PLAN_FORM_OPTION_ID,
    build_kanban_plan_card,
    form_answers_from_values,
    push_kanban_plan_cards_once,
)
from zf.integrations.feishu.transport import MockFeishuTransport
from zf.web.plan_extraction import extract_plan_request
from zf.runtime.task_workflow_plans import task_workflow_binding_digest


CHAT_ID = "oc_plan_origin"
SECRET = b"kanban-plan-secret"
ROOT = Path(__file__).resolve().parents[1]


def _context(tmp_path: Path) -> ProjectContext:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        integrations=IntegrationsConfig(
            feishu_identity=FeishuIdentityConfig(
                enabled=True,
                require_signed_actions=True,
                users={
                    "ou_operator": FeishuIdentityUserConfig(
                        operator="operator",
                        level="operator",
                    ),
                },
            ),
        ),
    )
    return ProjectContext(
        project_root=tmp_path,
        config_path=tmp_path / "zf.yaml",
        config=config,
        state_dir=state_dir,
    )


def _workflow_context(tmp_path: Path) -> ProjectContext:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    root = load_config(ROOT / "zf.yaml")
    config = ZfConfig(
        project=root.project,
        roles=root.roles,
        workflow=root.workflow,
        integrations=IntegrationsConfig(
            feishu_identity=FeishuIdentityConfig(
                enabled=True,
                require_signed_actions=True,
                users={
                    "ou_operator": FeishuIdentityUserConfig(
                        operator="operator",
                        level="operator",
                    ),
                },
            ),
        ),
    )
    return ProjectContext(
        project_root=ROOT,
        config_path=ROOT / "zf.yaml",
        config=config,
        state_dir=state_dir,
    )


def test_multi_question_plan_card_renders_atomic_feishu_form() -> None:
    item = {
        "request_event_id": "evt-multi-plan",
        "request_id": "plan-multi",
        "revision": 1,
        "header": "Delivery inputs",
        "questions": [
            {
                "id": "route",
                "question": "Which route?",
                "options": [
                    {
                        "id": "direct",
                        "label": "Direct",
                        "recommended": True,
                    },
                    {"id": "research", "label": "Research"},
                ],
                "allow_other": True,
            },
            {
                "id": "evidence",
                "question": "Which evidence depth?",
                "options": [
                    {
                        "id": "focused",
                        "label": "Focused",
                        "recommended": True,
                    },
                    {"id": "broad", "label": "Broad"},
                ],
                "allow_other": True,
            },
        ],
    }
    card = build_kanban_plan_card(item)

    form = next(
        element for element in card["elements"]
        if element.get("tag") == "form"
    )
    submit = next(
        button
        for element in form["elements"]
        if element.get("tag") == "action"
        for button in element["actions"]
    )
    assert submit["value"]["action"].endswith(f"~{PLAN_FORM_OPTION_ID}")
    encoded = json.dumps(card, ensure_ascii=False)
    assert "Which route?" in encoded
    assert "Which evidence depth?" in encoded
    assert "plan_option_route" in encoded
    answers, error = form_answers_from_values(
        item,
        {
            "plan_option_route": "direct",
            "plan_option_evidence": "focused",
        },
    )
    assert error == ""
    assert [answer["option_id"] for answer in answers] == ["direct", "focused"]
    _answers, error = form_answers_from_values(
        item,
        {
            "plan_option_route": "other",
            "plan_answer_route": "",
            "plan_option_evidence": "focused",
        },
    )
    assert error == "plan_form_other_answer_required"


def _requested_event(state_dir: Path) -> ZfEvent:
    request = extract_plan_request(
        json.dumps({
            "plan_request": {
                "header": "Route",
                "id": "route",
                "question": "Which route?",
                "options": [
                    {
                        "id": "research",
                        "label": "Research (Recommended)",
                        "description": "Collect evidence.",
                    },
                    {
                        "id": "channel",
                        "label": "Channel",
                        "description": "Discuss with roles.",
                    },
                ],
                "allow_other": False,
            },
        }),
        plan_context={
            "conversation_id": "feishu-kanban-agent",
            "thread_key": "channel:feishu-kanban-agent:main:kanban-agent",
            "turn_id": "turn-plan",
        },
    )
    assert request is not None
    event = ZfEvent(
        id="evt-kanban-plan",
        type="kanban.agent.plan.requested",
        actor="feishu-kanban-agent",
        correlation_id="feishu-kanban-agent",
    )
    request["request_event_id"] = event.id
    event.payload = {
        "source": "feishu",
        "conversation_id": "feishu-kanban-agent",
        "thread_key": "channel:feishu-kanban-agent:main:kanban-agent",
        "thread_id": "main",
        "channel_id": "feishu-kanban-agent",
        "member_id": "kanban-agent",
        "backend": "fake",
        "permission_profile": "read_only",
        "plan_request": request,
        "request": request,
        "refs": {
            "feishu": {
                "chat_id": CHAT_ID,
                "message_id": "om-plan-request",
                "thread_id": "main",
            },
        },
    }
    EventLog(state_dir / "events.jsonl").append(event)
    return event


def _multi_requested_event(state_dir: Path) -> ZfEvent:
    event = ZfEvent(
        id="evt-kanban-multi-plan",
        type="kanban.agent.plan.requested",
        actor="feishu-kanban-agent",
        correlation_id="feishu-kanban-agent",
    )
    request = {
        "valid": True,
        "request_id": "plan-multi",
        "request_event_id": event.id,
        "revision": 1,
        "header": "Delivery inputs",
        "subject_type": "clarification",
        "questions": [
            {
                "id": "route",
                "question": "Which route?",
                "options": [
                    {"id": "direct", "label": "Direct"},
                    {"id": "research", "label": "Research"},
                ],
                "allow_other": True,
            },
            {
                "id": "evidence",
                "question": "Which evidence depth?",
                "options": [
                    {"id": "focused", "label": "Focused"},
                    {"id": "broad", "label": "Broad"},
                ],
                "allow_other": True,
            },
        ],
    }
    event.payload = {
        "source": "feishu",
        "conversation_id": "feishu-kanban-agent",
        "thread_key": "channel:feishu-kanban-agent:main:kanban-agent",
        "thread_id": "main",
        "channel_id": "feishu-kanban-agent",
        "member_id": "kanban-agent",
        "backend": "fake",
        "permission_profile": "read_only",
        "plan_request": request,
        "request": request,
        "refs": {
            "feishu": {
                "chat_id": CHAT_ID,
                "message_id": "om-plan-request",
                "thread_id": "main",
            },
        },
    }
    EventLog(state_dir / "events.jsonl").append(event)
    return event


def _token_from_card(
    transport: MockFeishuTransport,
    option_id: str = "research",
) -> tuple[str, str]:
    card = json.loads(transport.sent_messages[-1].content)
    button = next(
        button
        for element in card["elements"]
        if element.get("tag") == "action"
        for button in element["actions"]
        if button["value"]["action"].endswith(f"~{option_id}")
    )
    return button["value"]["action"], button["value"]["t"]


def _form_token_from_card(
    transport: MockFeishuTransport,
) -> tuple[str, str]:
    card = json.loads(transport.sent_messages[-1].content)
    form = next(
        element for element in card["elements"]
        if element.get("tag") == "form"
    )
    button = next(
        button
        for element in form["elements"]
        if element.get("tag") == "action"
        for button in element["actions"]
    )
    return button["value"]["action"], button["value"]["t"]


def test_signed_plan_option_continues_original_channel_thread(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = _context(tmp_path)
    _requested_event(context.state_dir)
    monkeypatch.setenv("ZF_FEISHU_ACTION_TOKEN_SECRET", SECRET.decode())
    turns: list[dict] = []

    def fake_turn(state, writer, config, *, message_event, message_payload, **kwargs):
        turns.append({
            "event": message_event,
            "payload": message_payload,
            "kwargs": kwargs,
        })
        return {
            "route": SimpleNamespace(reply_requests=["req-plan-continue"]),
            "dispatched": [("req-plan-continue", None)],
        }

    monkeypatch.setattr(agent_conversation, "run_channel_reply_turn", fake_turn)
    transport = MockFeishuTransport()
    pushed = push_kanban_plan_cards_once(
        context.state_dir,
        transport,
        receive_id="oc_fallback",
        action_secret=SECRET,
    )

    assert pushed["sent"]
    assert transport.sent_messages[-1].chat_id == CHAT_ID
    assert transport.sent_messages[-1].thread_id == "om-plan-request"
    action, token = _token_from_card(transport)
    callback = {
        "type": "button_action",
        "payload": {
            "action": action,
            "action_token": token,
            "message_id": "om-plan-answer",
        },
        "user_id": "ou_operator",
        "chat_id": CHAT_ID,
    }
    first = _handle_event_data(callback, context=context, user_levels={})
    duplicate = _handle_event_data(callback, context=context, user_levels={})

    assert first["ok"] is True
    assert first["status"] == "continued"
    assert duplicate["status"] == "duplicate"
    assert len(turns) == 1
    assert turns[0]["payload"]["channel_id"] == "feishu-kanban-agent"
    assert turns[0]["payload"]["thread_id"] == "main"
    assert turns[0]["payload"]["mentions"] == ["kanban-agent"]
    events = EventLog(context.state_dir / "events.jsonl").read_all()
    answered = [
        event
        for event in events
        if event.type == "kanban.agent.plan.answered"
    ]
    assert len(answered) == 1
    assert answered[0].payload["request_event_id"] == "evt-kanban-plan"
    assert answered[0].payload["option_id"] == "research"
    assert answered[0].payload["answer"] == "Research (Recommended)"
    continuation = [
        event
        for event in events
        if event.type == "channel.message.posted"
        and event.causation_id == answered[0].id
    ]
    assert len(continuation) == 1

    receipt = push_kanban_plan_cards_once(
        context.state_dir,
        transport,
        receive_id="oc_fallback",
        action_secret=SECRET,
    )
    assert receipt["updated"]
    result_card = json.loads(transport.updated_messages[-1][1])
    assert "Research (Recommended)" in json.dumps(result_card, ensure_ascii=False)


def test_signed_multi_question_form_continues_original_channel_thread(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = _context(tmp_path)
    _multi_requested_event(context.state_dir)
    monkeypatch.setenv("ZF_FEISHU_ACTION_TOKEN_SECRET", SECRET.decode())
    turns: list[dict] = []

    def fake_turn(state, writer, config, *, message_event, message_payload, **kwargs):
        turns.append({"payload": message_payload, "kwargs": kwargs})
        return {
            "route": SimpleNamespace(reply_requests=["req-plan-form"]),
            "dispatched": [("req-plan-form", None)],
        }

    monkeypatch.setattr(agent_conversation, "run_channel_reply_turn", fake_turn)
    transport = MockFeishuTransport()
    push_kanban_plan_cards_once(
        context.state_dir,
        transport,
        receive_id="oc_fallback",
        action_secret=SECRET,
    )
    action, token = _form_token_from_card(transport)
    result = _handle_event_data(
        {
            "type": "button_action",
            "payload": {
                "action": action,
                "action_token": token,
                "form_values": {
                    "plan_option_route": "direct",
                    "plan_option_evidence": "focused",
                },
                "message_id": "om-plan-form-answer",
            },
            "user_id": "ou_operator",
            "chat_id": CHAT_ID,
        },
        context=context,
        user_levels={},
    )

    assert result["ok"] is True
    assert result["status"] == "continued"
    assert len(turns) == 1
    assert "Plan answers:" in turns[0]["payload"]["text"]
    events = EventLog(context.state_dir / "events.jsonl").read_all()
    answered = [
        event for event in events
        if event.type == "kanban.agent.plan.answered"
    ]
    assert len(answered) == 1
    assert answered[0].payload["answers"] == [
        {"question_id": "route", "option_id": "direct", "answer": "Direct"},
        {
            "question_id": "evidence",
            "option_id": "focused",
            "answer": "Focused",
        },
    ]


def test_incomplete_plan_form_keeps_the_signed_card_retryable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = _context(tmp_path)
    _multi_requested_event(context.state_dir)
    monkeypatch.setenv("ZF_FEISHU_ACTION_TOKEN_SECRET", SECRET.decode())
    turns: list[dict] = []

    def fake_turn(state, writer, config, *, message_event, message_payload, **kwargs):
        turns.append({"payload": message_payload, "kwargs": kwargs})
        return {
            "route": SimpleNamespace(reply_requests=["req-plan-form"]),
            "dispatched": [("req-plan-form", None)],
        }

    monkeypatch.setattr(agent_conversation, "run_channel_reply_turn", fake_turn)
    transport = MockFeishuTransport()
    push_kanban_plan_cards_once(
        context.state_dir,
        transport,
        receive_id="oc_fallback",
        action_secret=SECRET,
    )
    action, token = _form_token_from_card(transport)
    callback = {
        "type": "button_action",
        "payload": {
            "action": action,
            "action_token": token,
            "message_id": "om-plan-form-retry",
            "form_values": {"plan_option_route": "direct"},
        },
        "user_id": "ou_operator",
        "chat_id": CHAT_ID,
    }

    incomplete = _handle_event_data(callback, context=context, user_levels={})
    callback["payload"]["form_values"] = {
        "plan_option_route": "direct",
        "plan_option_evidence": "focused",
    }
    completed = _handle_event_data(callback, context=context, user_levels={})

    assert incomplete["ok"] is False
    assert incomplete["status"] == "plan_form_option_missing"
    assert completed["ok"] is True
    assert completed["status"] == "continued"
    assert len(turns) == 1
    answered = [
        event
        for event in EventLog(context.state_dir / "events.jsonl").read_all()
        if event.type == "kanban.agent.plan.answered"
    ]
    assert len(answered) == 1


def test_invalid_plan_is_repaired_once_in_the_same_feishu_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    replies = iter([
        json.dumps({"plan_request": {
            "header": "Route",
            "id": "route",
            "question": "Which route?",
            "options": [{"label": "Only one option"}],
        }}),
        json.dumps({"plan_request": {
            "header": "Route",
            "id": "route",
            "question": "Which route?",
            "allow_other": False,
            "options": [
                {"label": "Direct (Recommended)", "recommended": True},
                {"label": "Research"},
            ],
        }}),
    ])
    turns: list[dict] = []

    monkeypatch.setattr(
        agent_conversation,
        "_latest_assistant_reply_text",
        lambda *args, **kwargs: next(replies),
    )

    def fake_turn(state, writer, config, *, message_event, message_payload, **kwargs):
        turns.append({"payload": message_payload, "kwargs": kwargs})
        return {
            "route": SimpleNamespace(reply_requests=["repair-turn"]),
            "dispatched": [("repair-turn", None)],
        }

    monkeypatch.setattr(agent_conversation, "run_channel_reply_turn", fake_turn)
    trigger = ZfEvent(
        id="evt-feishu-request",
        type="channel.message.posted",
        actor="feishu:ou_operator",
        correlation_id="feishu-kanban-agent",
        payload={
            "refs": {"feishu": {"chat_id": CHAT_ID, "message_id": "om-root"}},
        },
    )

    result = agent_conversation._emit_kanban_interaction(
        state_dir,
        writer,
        config=ZfConfig(),
        channel_id="feishu-kanban-agent",
        member_id="kanban-agent",
        user_text="Create a controlled delivery.",
        trigger_event=trigger,
        chat_id=CHAT_ID,
        feishu_message_id="om-root",
        thread_id="main",
        source="feishu-kanban-agent",
        backend="fake",
        project_root=tmp_path,
    )

    assert result["plan_request"]["valid"] is False
    assert result["plan_repair"]["status"] == "repaired"
    assert len(turns) == 1
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert len([
        event for event in events
        if event.type == "kanban.agent.plan.repair.requested"
    ]) == 1
    assert len([
        event for event in events
        if event.type == "kanban.agent.plan.repair.completed"
    ]) == 1
    repaired_plan = [
        event for event in events
        if event.type == "kanban.agent.plan.requested"
        and event.payload["plan_request"].get("valid")
    ]
    assert len(repaired_plan) == 1


def test_invalid_plan_repair_exhaustion_is_visible_to_feishu(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    invalid_reply = json.dumps({"plan_request": {
        "header": "Route",
        "id": "route",
        "question": "Which route?",
        "options": [{"label": "Only one option"}],
    }})
    monkeypatch.setattr(
        agent_conversation,
        "_latest_assistant_reply_text",
        lambda *args, **kwargs: invalid_reply,
    )
    monkeypatch.setattr(
        agent_conversation,
        "run_channel_reply_turn",
        lambda *args, **kwargs: {
            "route": SimpleNamespace(reply_requests=["repair-turn"]),
            "dispatched": [("repair-turn", None)],
        },
    )
    trigger = ZfEvent(
        id="evt-feishu-request",
        type="channel.message.posted",
        actor="feishu:ou_operator",
        correlation_id="feishu-kanban-agent",
        payload={
            "refs": {"feishu": {"chat_id": CHAT_ID, "message_id": "om-root"}},
        },
    )
    agent_conversation._emit_kanban_interaction(
        state_dir,
        writer,
        config=ZfConfig(),
        channel_id="feishu-kanban-agent",
        member_id="kanban-agent",
        user_text="Create a controlled delivery.",
        trigger_event=trigger,
        chat_id=CHAT_ID,
        feishu_message_id="om-root",
        thread_id="main",
        source="feishu-kanban-agent",
        backend="fake",
        project_root=tmp_path,
    )

    transport = MockFeishuTransport()
    pushed = push_kanban_plan_cards_once(
        state_dir,
        transport,
        receive_id=CHAT_ID,
        action_secret=SECRET,
    )
    assert pushed["sent"]
    card = json.loads(transport.sent_messages[-1].content)
    assert "could not be corrected automatically" in json.dumps(card)
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert len([
        event for event in events
        if event.type == "kanban.agent.plan.repair.exhausted"
    ]) == 1


def test_plan_option_token_cannot_be_retargeted_to_another_option(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = _context(tmp_path)
    _requested_event(context.state_dir)
    monkeypatch.setenv("ZF_FEISHU_ACTION_TOKEN_SECRET", SECRET.decode())
    transport = MockFeishuTransport()
    push_kanban_plan_cards_once(
        context.state_dir,
        transport,
        receive_id=CHAT_ID,
        action_secret=SECRET,
    )
    action, token = _token_from_card(transport)
    tampered = action.rsplit("~", 1)[0] + "~channel"

    result = _handle_event_data(
        {
            "type": "button_action",
            "payload": {
                "action": tampered,
                "action_token": token,
                "message_id": "om-plan-tampered",
            },
            "user_id": "ou_operator",
            "chat_id": CHAT_ID,
        },
        context=context,
        user_levels={},
    )

    assert result["status"] == "rejected"
    assert not [
        event
        for event in EventLog(context.state_dir / "events.jsonl").read_all()
        if event.type == "kanban.agent.plan.answered"
    ]


def test_signed_channel_setup_plan_auto_creates_and_starts_without_continuation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = _context(tmp_path)
    writer = EventWriter(EventLog(context.state_dir / "events.jsonl"))
    origin = writer.emit(
        "channel.message.posted",
        actor="feishu:ou_operator",
        correlation_id="feishu-kanban-agent",
        payload={
            "channel_id": "feishu-kanban-agent",
            "thread_id": "main",
            "message_id": "om-channel-requirement",
            "member_id": "ou_operator",
            "role": "user",
            "source": "feishu",
            "text": "Review the Feishu migration requirement.",
            "mentions": ["kanban-agent"],
            "refs": {"feishu": {"chat_id": CHAT_ID}},
        },
    )
    request = extract_plan_request(
        json.dumps({
            "plan_request": {
                "header": "Channel setup",
                "id": "channel-setup",
                "question": "Which collaboration setup?",
                "discussion_seed": "Review the Feishu migration requirement.",
                "submit_action": "channel-create-and-start",
                "submit_label": "Create & start",
                "options": [
                    {
                        "id": "quick",
                        "label": "Quick change (Recommended)",
                        "recommended": True,
                        "description": "Focused implementation review.",
                        "submit_payload": {
                            "template_id": "quick-change",
                            "mode": "conversation",
                            "channel_id": "ch-feishu-auto",
                            "overrides": {
                                "backend": "fake",
                                "budget": {"max_rounds": 4},
                            },
                        },
                    },
                    {
                        "id": "architecture",
                        "label": "Architecture review",
                        "description": "Broader architecture review.",
                        "submit_payload": {
                            "template_id": "architecture-review",
                            "mode": "multi_lens",
                            "channel_id": "ch-feishu-auto",
                            "overrides": {"backend": "fake"},
                        },
                    },
                ],
                "allow_other": False,
            },
        }),
        plan_context={
            "conversation_id": "feishu-kanban-agent",
            "thread_key": "channel:feishu-kanban-agent:main:kanban-agent",
            "turn_id": "turn-channel-plan",
            "originating_message_event_id": origin.id,
        },
    )
    assert request is not None and request["valid"] is True
    event = ZfEvent(
        id="evt-kanban-channel-plan",
        type="kanban.agent.plan.requested",
        actor="feishu-kanban-agent",
        correlation_id="feishu-kanban-agent",
    )
    request["request_event_id"] = event.id
    event.payload = {
        "source": "feishu",
        "conversation_id": "feishu-kanban-agent",
        "thread_key": "channel:feishu-kanban-agent:main:kanban-agent",
        "thread_id": "main",
        "channel_id": "feishu-kanban-agent",
        "member_id": "kanban-agent",
        "backend": "fake",
        "permission_profile": "operator",
        "plan_request": request,
        "request": request,
        "refs": {
            "feishu": {
                "chat_id": CHAT_ID,
                "message_id": "om-channel-plan",
                "thread_id": "main",
                "root_message_id": "om-channel-root",
                "parent_message_id": "om-channel-parent",
            },
        },
    }
    writer.append(event)
    monkeypatch.setenv("ZF_FEISHU_ACTION_TOKEN_SECRET", SECRET.decode())
    monkeypatch.setattr(
        agent_conversation,
        "run_channel_reply_turn",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("action-bound Plan must not continue the provider")
        ),
    )
    transport = MockFeishuTransport()
    push_kanban_plan_cards_once(
        context.state_dir,
        transport,
        receive_id=CHAT_ID,
        action_secret=SECRET,
    )
    action, token = _token_from_card(transport, "quick")

    result = _handle_event_data(
        {
            "type": "button_action",
            "payload": {
                "action": action,
                "action_token": token,
                "message_id": "om-channel-plan-answer",
            },
            "user_id": "ou_operator",
            "chat_id": CHAT_ID,
        },
        context=context,
        user_levels={},
    )

    assert result["ok"] is True
    assert result["status"] == "applied"
    assert result["applied_action"] == "channel-create-and-start"
    assert result["channel_id"] == "ch-feishu-auto"
    events = EventLog(context.state_dir / "events.jsonl").read_all()
    assert len([
        item
        for item in events
        if item.type == "channel.created"
        and item.payload.get("channel_id") == "ch-feishu-auto"
    ]) == 1
    requirement = next(
        item
        for item in events
        if item.type == "channel.message.posted"
        and item.payload.get("channel_id") == "ch-feishu-auto"
    )
    assert requirement.payload["text"] == (
        "Review the Feishu migration requirement."
    )
    assert requirement.payload["refs"]["feishu"] == {
        "chat_id": CHAT_ID,
        "message_id": "om-channel-plan",
        "thread_id": "main",
        "root_message_id": "om-channel-root",
        "parent_message_id": "om-channel-parent",
    }
    created = next(
        item for item in events
        if item.type == "channel.created"
        and item.payload.get("channel_id") == "ch-feishu-auto"
    )
    assert created.payload["origin_binding"] == {
        "schema_version": "channel-origin-binding.v1",
        "surface": "feishu",
        "channel_id": "ch-feishu-auto",
        "thread_id": "main",
        "chat_id": CHAT_ID,
        "origin_message_id": "om-channel-root",
        "root_message_id": "om-channel-root",
        "source_message_id": "om-channel-plan",
    }
    answered = next(
        item for item in events
        if item.type == "kanban.agent.plan.answered"
        and item.payload.get("request_event_id") == event.id
    )
    assert answered.payload["applied_action"] == "channel-create-and-start"


def test_signed_task_workflow_plan_creates_proposal_without_starting(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = _workflow_context(tmp_path)
    writer = EventWriter(EventLog(context.state_dir / "events.jsonl"))
    task = Task(id="TASK-FEISHU-WORKFLOW", title="Feishu workflow Plan")
    TaskStore(context.state_dir / "kanban.json").add(task)
    request = extract_plan_request(
        json.dumps({
            "plan_request": {
                "subject_type": "task_workflow",
                "header": "Workflow route",
                "id": "workflow-route",
                "question": "How should the Task run?",
                "options": [
                    {
                        "id": "research",
                        "label": "Research first (Recommended)",
                        "recommended": True,
                        "description": "Collect evidence first.",
                        "effect": {
                            "mode": "propose",
                            "action": "task-workflow-start",
                            "payload": {
                                "task_id": task.id,
                                "route_id": "research:fixed",
                                "objective": task.title,
                                "parameters": {},
                            },
                        },
                    },
                    {
                        "id": "defer",
                        "label": "No workflow yet",
                        "description": "Keep tracking only.",
                        "effect": {"mode": "continue"},
                    },
                ],
                "allow_other": True,
            },
        }),
        plan_context={
            "conversation_id": "feishu-kanban-agent",
            "thread_key": "channel:feishu-kanban-agent:main:kanban-agent",
            "turn_id": "turn-workflow-plan",
            "task_binding_digests": {
                task.id: task_workflow_binding_digest(task),
            },
        },
        config=context.config,
    )
    assert request is not None and request["valid"] is True
    event = ZfEvent(
        id="evt-kanban-workflow-plan",
        type="kanban.agent.plan.requested",
        actor="feishu-kanban-agent",
        task_id=task.id,
        correlation_id="feishu-kanban-agent",
    )
    request["request_event_id"] = event.id
    event.payload = {
        "source": "feishu",
        "conversation_id": "feishu-kanban-agent",
        "thread_key": "channel:feishu-kanban-agent:main:kanban-agent",
        "thread_id": "main",
        "channel_id": "feishu-kanban-agent",
        "member_id": "kanban-agent",
        "backend": "fake",
        "permission_profile": "operator",
        "plan_request": request,
        "request": request,
        "refs": {
            "feishu": {
                "chat_id": CHAT_ID,
                "message_id": "om-workflow-plan",
                "thread_id": "main",
            },
        },
    }
    writer.append(event)
    monkeypatch.setenv("ZF_FEISHU_ACTION_TOKEN_SECRET", SECRET.decode())
    monkeypatch.setattr(
        agent_conversation,
        "run_channel_reply_turn",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("workflow Plan selection must not continue provider")
        ),
    )
    transport = MockFeishuTransport()
    push_kanban_plan_cards_once(
        context.state_dir,
        transport,
        receive_id=CHAT_ID,
        action_secret=SECRET,
    )
    action, token = _token_from_card(transport, "research")

    result = _handle_event_data(
        {
            "type": "button_action",
            "payload": {
                "action": action,
                "action_token": token,
                "message_id": "om-workflow-plan-answer",
            },
            "user_id": "ou_operator",
            "chat_id": CHAT_ID,
        },
        context=context,
        user_levels={},
    )

    assert result["ok"] is True
    assert result["status"] == "proposal_ready"
    assert result["proposed_action"] == "workflow-start"
    events = EventLog(context.state_dir / "events.jsonl").read_all()
    proposal = next(
        item
        for item in events
        if item.type == "operator.action.proposed"
    )
    assert proposal.task_id == task.id
    assert proposal.payload["proposal"]["payload"]["route_id"] == (
        "research:fixed"
    )
    assert not any(
        item.type == "workflow.invoke.requested"
        for item in events
    )


def test_task_workflow_plan_rejects_task_ineligible_research_route(
    tmp_path: Path,
) -> None:
    context = _workflow_context(tmp_path)
    task = Task(id="TASK-INELIGIBLE-RESEARCH", title="Channel PRD Task")
    request = extract_plan_request(
        json.dumps({
            "plan_request": {
                "subject_type": "task_workflow",
                "header": "Workflow route",
                "id": "workflow-route",
                "question": "How should the Task run?",
                "options": [
                    {
                        "id": "research",
                        "label": "Research first (Recommended)",
                        "recommended": True,
                        "description": "Collect evidence first.",
                        "effect": {
                            "mode": "propose",
                            "action": "workflow-start",
                            "payload": {
                                "task_id": task.id,
                                "route_id": "research:fixed",
                                "objective": task.title,
                                "parameters": {},
                            },
                        },
                    },
                    {
                        "id": "continue",
                        "label": "Continue discussion",
                        "description": "Do not start a workflow.",
                        "effect": {"mode": "continue"},
                    },
                ],
                "allow_other": True,
            },
        }),
        plan_context={
            "task_binding_digests": {
                task.id: task_workflow_binding_digest(task),
            },
            "workflow_route_eligibility": {
                task.id: {
                    "research:fixed": (
                        "research route requires a canonical Workflow Request "
                        "binding for a Channel PRD Task"
                    ),
                },
            },
        },
        config=context.config,
    )

    assert request is not None
    assert request["valid"] is False
    assert "Workflow Request binding" in request["validation_error"]
