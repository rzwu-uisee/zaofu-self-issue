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
    build_kanban_plan_card,
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


def test_multi_question_plan_card_fails_safe_to_web() -> None:
    card = build_kanban_plan_card({
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
    })

    assert not [
        element for element in card["elements"]
        if element.get("tag") == "action"
    ]
    encoded = json.dumps(card, ensure_ascii=False)
    assert "Which route?" in encoded
    assert "Which evidence depth?" in encoded
    assert "ZaoFu Web dashboard" in encoded


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
                "allow_other": True,
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
