"""Feishu-bound kanban agent: action-proposal loop parity with the Web panel.

racing-e2e P1: the Feishu specialist conversation only produced prose replies —
a Feishu user saying 创建任务 never got a create-task proposal, because
run_channel_reply_turn has no proposal extraction. These tests drive
run_specialist_conversation with a patched reply turn (standing in for the
synchronous dispatch) and assert the same extractor/gates as the Web panel:
operator.action.proposed is emitted, contract shapes are normalized
(5fca581c), agent intent evidence is source-bound, and only
agent_kind=kanban_agent gets the loop. P3b: the auto-provisioned channel
name carries a chat suffix.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.integrations.feishu import agent_conversation
from zf.integrations.feishu.kanban_proposal_card import (
    push_kanban_proposal_cards_once,
)
from zf.integrations.feishu.transport import MockFeishuTransport


CHAT_ID = "oc_feishu_chat_42"


def _writer(tmp_path: Path) -> tuple[Path, EventWriter]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir(exist_ok=True)
    return state_dir, EventWriter(EventLog(state_dir / "events.jsonl"))


def _inbound_event(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        payload={"text": text, "message_id": "om_feishu_msg_1"},
        chat_id=CHAT_ID,
        user_id="ou_operator",
    )


def _route(**overrides) -> SimpleNamespace:
    values = {"default_member": "", "channel_id": "", "backend": "fake", "cwd": ""}
    values.update(overrides)
    return SimpleNamespace(**values)


def _proposal_reply_json(source_quote: str = "创建任务") -> str:
    return json.dumps({
        "action_proposal": {
            "action": "create-task",
            "intent": {
                "decision": "propose_action",
                "source_quote": source_quote,
            },
            "payload": {
                "title": "实现赛车小游戏 MVP",
                "contract": {
                    "behavior": "交付 2D 俯视角单人赛车计时小游戏。",
                    "verification": ["打开页面 3 秒内可开始", "按 ↑ 车辆加速"],
                    "scope": ["src/**", "仅限现代桌面浏览器"],
                },
            },
            "reason": "owner 已确认澄清稿",
        }
    }, ensure_ascii=False)


def _plan_reply_json() -> str:
    return json.dumps({
        "plan_request": {
            "header": "开发路径",
            "id": "route",
            "question": "先研究还是直接拉群讨论？",
            "options": [
                {
                    "id": "research",
                    "label": "Research (Recommended)",
                    "description": "先收集证据。",
                },
                {
                    "id": "channel",
                    "label": "Channel",
                    "description": "直接邀请角色讨论。",
                },
            ],
            "allow_other": True,
        }
    }, ensure_ascii=False)


def _task_create_plan_reply_json() -> str:
    return json.dumps({
        "plan_request": {
            "subject_type": "task_create",
            "header": "Create task",
            "id": "create-task",
            "question": "Create the confirmed PRD Task?",
            "options": [
                {
                    "id": "create",
                    "label": "Create (Recommended)",
                    "recommended": True,
                    "description": "Create one workflow-managed Task.",
                    "effect": {
                        "mode": "propose",
                        "action": "create-task",
                        "payload": {
                            "title": "Implement the confirmed PRD",
                            "objective": "Deliver the exact confirmed Channel PRD.",
                        },
                    },
                },
                {
                    "id": "continue",
                    "label": "Continue discussion",
                    "description": "Do not create a Task yet.",
                    "effect": {"mode": "continue"},
                },
            ],
            "allow_other": False,
        },
    }, ensure_ascii=False)


def _patch_reply_turn(monkeypatch, state_dir: Path, writer: EventWriter, reply_text: str):
    """Stand in for the synchronous dispatch: fold the agent's reply before
    run_specialist_conversation projects the channel for extraction."""

    def fake_turn(state, w, config, *, message_event, message_payload, **kwargs):
        w.emit(
            "channel.message.posted",
            actor="kanban-agent",
            correlation_id=message_payload["channel_id"],
            payload={
                "channel_id": message_payload["channel_id"],
                "thread_id": "main",
                "message_id": "msg-agent-reply-1",
                "member_id": "kanban-agent",
                "role": "assistant",
                "source": "runtime",
                "text": reply_text,
            },
        )
        return {"route": SimpleNamespace(reply_requests=["req-1"]), "dispatched": [("req-1", None)]}

    monkeypatch.setattr(agent_conversation, "run_channel_reply_turn", fake_turn)


def test_proposal_card_uses_exact_feishu_origin_over_fallback(tmp_path: Path):
    state_dir, writer = _writer(tmp_path)
    writer.emit(
        "operator.action.proposed",
        actor="feishu-kanban-agent",
        payload={
            "source": "feishu",
            "refs": {
                "feishu": {
                    "chat_id": CHAT_ID,
                    "root_message_id": "om_exact_root",
                },
            },
            "proposal": {
                "proposal_id": "proposal-exact-origin",
                "proposal_digest": "a" * 64,
                "revision": 1,
                "valid": True,
                "action": "create-task",
                "reason": "owner requested work",
                "payload": {"title": "Exact origin task"},
            },
        },
    )
    transport = MockFeishuTransport()

    result = push_kanban_proposal_cards_once(
        state_dir,
        transport,
        receive_id="oc_wrong_fallback",
    )

    assert result["sent"] == ["proposal-exact-origin"]
    assert transport.sent_messages[0].chat_id == CHAT_ID
    assert transport.sent_messages[0].thread_id == "om_exact_root"


def _run(state_dir: Path, writer: EventWriter, *, text: str, agent_kind: str = "kanban_agent"):
    return agent_conversation.run_specialist_conversation(
        state_dir=state_dir,
        config=None,
        event=_inbound_event(text),
        writer=writer,
        route=_route(),
        agent_kind=agent_kind,
        default_member="kanban-agent" if agent_kind == "kanban_agent" else "run-manager-agent",
        display_name="Kanban Agent" if agent_kind == "kanban_agent" else "Run Manager Agent",
        source=f"feishu-{agent_kind.replace('_', '-')}",
    )


def _proposed_events(state_dir: Path):
    return [
        e for e in EventLog(state_dir / "events.jsonl").read_all()
        if e.type == "operator.action.proposed"
    ]


def test_feishu_create_task_message_emits_normalized_proposal(tmp_path, monkeypatch):
    state_dir, writer = _writer(tmp_path)
    _patch_reply_turn(monkeypatch, state_dir, writer, _proposal_reply_json())

    result = _run(state_dir, writer, text="请创建任务：把澄清稿提交为开发任务")

    proposed = _proposed_events(state_dir)
    assert len(proposed) == 1
    proposal = proposed[0].payload["proposal"]
    assert proposal["action"] == "create-task"
    assert proposal["valid"] is True
    contract = proposal["payload"]["contract"]
    # 5fca581c normalization must apply on this surface too
    assert contract["verification"] == "打开页面 3 秒内可开始\n按 ↑ 车辆加速"
    assert contract["scope"] == ["src/**"]
    assert "scope(non-path):" in proposal["payload"]["notes"]
    # provenance + surface routing fields
    payload = proposed[0].payload
    assert payload["source"] == "feishu"
    assert payload["refs"]["feishu"]["chat_id"] == CHAT_ID
    assert payload["conversation_id"] == f"feishu-kanban_agent-{CHAT_ID}"
    # caller gets the proposal for the Feishu receipt card
    assert result["action_proposal"]["action"] == "create-task"


def test_feishu_plan_reply_emits_durable_request_with_channel_context(
    tmp_path,
    monkeypatch,
):
    state_dir, writer = _writer(tmp_path)
    _patch_reply_turn(monkeypatch, state_dir, writer, _plan_reply_json())

    result = _run(state_dir, writer, text="帮我选择下一步路径")

    requested = [
        event
        for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.type == "kanban.agent.plan.requested"
    ]
    assert len(requested) == 1
    payload = requested[0].payload
    request = payload["plan_request"]
    assert request["request_event_id"] == requested[0].id
    assert request["valid"] is True
    assert payload["channel_id"] == f"feishu-kanban_agent-{CHAT_ID}"
    assert payload["thread_id"] == "main"
    assert payload["member_id"] == "kanban-agent"
    assert payload["refs"]["feishu"]["chat_id"] == CHAT_ID
    assert result["plan_request"]["request_id"] == request["request_id"]
    assert "action_proposal" not in result


def test_feishu_task_plan_uses_exact_canonical_prd_authority(
    tmp_path,
    monkeypatch,
):
    state_dir, writer = _writer(tmp_path)
    authority = {
        "channel_id": "ch-prd",
        "thread_id": "main",
        "channel_member_id": "product_pm",
        "leader_revision": 3,
        "prd_revision": 2,
        "source_ref": "channel-artifacts/ch-prd/prd-v2.md",
        "source_digest": "sha256:canonical-prd",
    }
    planning_context = {
        "schema_version": "feishu-kanban-planning-context.v1",
        "selection_status": "exact",
        "workflow_route_catalog": {"routes": []},
        "canonical_channel_prds": {"items": [{"channel_id": "ch-prd"}]},
        "workflow_parameters": authority,
    }
    monkeypatch.setattr(
        agent_conversation,
        "build_feishu_kanban_planning_context",
        lambda *args, **kwargs: planning_context,
    )
    captured = {}

    def fake_turn(state, w, config, *, message_event, message_payload, **kwargs):
        captured["agent_context"] = kwargs.get("agent_context")
        w.emit(
            "channel.message.posted",
            actor="kanban-agent",
            correlation_id=message_payload["channel_id"],
            payload={
                "channel_id": message_payload["channel_id"],
                "thread_id": "main",
                "message_id": "msg-agent-task-plan",
                "member_id": "kanban-agent",
                "role": "assistant",
                "source": "runtime",
                "text": _task_create_plan_reply_json(),
            },
        )
        return {
            "route": SimpleNamespace(reply_requests=["req-1"]),
            "dispatched": [("req-1", None)],
        }

    monkeypatch.setattr(agent_conversation, "run_channel_reply_turn", fake_turn)

    result = _run(
        state_dir,
        writer,
        text="Create a Task from the confirmed PRD",
    )

    assert captured["agent_context"] == planning_context
    request = result["plan_request"]
    assert request["valid"] is True
    create = next(
        option for option in request["options"]
        if option.get("submit_action") == "create-task"
    )
    assert create["submit_payload"]["channel_authority"] == authority
    assert create["submit_payload"]["contract"]["source_mode"] == "channel_prd"


def test_feishu_task_plan_fails_closed_without_exact_prd_authority(
    tmp_path,
    monkeypatch,
):
    state_dir, writer = _writer(tmp_path)
    monkeypatch.setattr(
        agent_conversation,
        "build_feishu_kanban_planning_context",
        lambda *args, **kwargs: {
            "schema_version": "feishu-kanban-planning-context.v1",
            "selection_status": "ambiguous",
            "workflow_route_catalog": {"routes": []},
            "canonical_channel_prds": {"items": []},
            "workflow_parameters": {},
        },
    )
    _patch_reply_turn(
        monkeypatch,
        state_dir,
        writer,
        _task_create_plan_reply_json(),
    )

    result = _run(
        state_dir,
        writer,
        text="Create a Task from the confirmed PRD",
    )

    assert result["plan_request"]["valid"] is False
    assert "missing authority field" in result["plan_request"]["validation_error"]


def test_feishu_readonly_message_keeps_invalid_agent_proposal_visible(
    tmp_path,
    monkeypatch,
):
    state_dir, writer = _writer(tmp_path)
    _patch_reply_turn(monkeypatch, state_dir, writer, _proposal_reply_json())

    result = _run(state_dir, writer, text="介绍一下当前项目进展")

    proposed = _proposed_events(state_dir)
    assert len(proposed) == 1
    proposal = proposed[0].payload["proposal"]
    assert proposal["valid"] is False
    assert "source_quote must occur verbatim" in proposal["validation_error"]
    assert result["action_proposal"]["valid"] is False


def test_run_manager_agent_kind_gets_no_proposal_loop(tmp_path, monkeypatch):
    state_dir, writer = _writer(tmp_path)
    _patch_reply_turn(monkeypatch, state_dir, writer, _proposal_reply_json())

    result = _run(state_dir, writer, text="创建任务：xxx", agent_kind="run_manager")

    assert _proposed_events(state_dir) == []
    assert "action_proposal" not in result


def test_prose_reply_yields_no_proposal(tmp_path, monkeypatch):
    state_dir, writer = _writer(tmp_path)
    _patch_reply_turn(monkeypatch, state_dir, writer, "好的，我看下需求稿再答复你。")

    result = _run(state_dir, writer, text="创建任务：把澄清稿提交为开发任务")

    assert _proposed_events(state_dir) == []
    assert "action_proposal" not in result


def test_kanban_member_invite_carries_proposal_reply_contract(tmp_path, monkeypatch):
    """Prompt-side half of the loop: without teaching the channel-dispatched
    turn the proposal-output contract, a real backend replies in prose and the
    extraction hook never fires. The invite must carry reply_contract, the
    projection must fold it, and the dispatch system prompt must append it."""
    from zf.runtime.channel_adapter import _build_channel_system_prompt
    from zf.runtime.channel_projection import project_channel
    from zf.web.operator_contract import KANBAN_AGENT_CHANNEL_PROPOSAL_CONTRACT

    state_dir, writer = _writer(tmp_path)
    _patch_reply_turn(monkeypatch, state_dir, writer, "prose only")

    _run(state_dir, writer, text="hi")

    channel = project_channel(state_dir, f"feishu-kanban_agent-{CHAT_ID}") or {}
    member = next(
        m for m in channel.get("members") or []
        if m.get("member_id") == "kanban-agent"
    )
    assert member["reply_contract"] == KANBAN_AGENT_CHANNEL_PROPOSAL_CONTRACT
    assert '"decision": "propose_action"' in member["reply_contract"]
    assert "exact verbatim user substring" in member["reply_contract"]
    assert "not through English or Chinese keyword spelling" in member["reply_contract"]
    assert "subject_type=task_create with two or three options" in member["reply_contract"]
    assert "Do not nest contract or channel_authority" in member["reply_contract"]
    assert "effect.mode=continue, not defer" in member["reply_contract"]
    prompt = _build_channel_system_prompt(member)
    assert KANBAN_AGENT_CHANNEL_PROPOSAL_CONTRACT in prompt


def test_run_manager_member_invite_has_no_reply_contract(tmp_path, monkeypatch):
    from zf.runtime.channel_projection import project_channel

    state_dir, writer = _writer(tmp_path)
    _patch_reply_turn(monkeypatch, state_dir, writer, "prose only")

    _run(state_dir, writer, text="hi", agent_kind="run_manager")

    channel = project_channel(state_dir, f"feishu-run_manager-{CHAT_ID}") or {}
    member = next(
        m for m in channel.get("members") or []
        if m.get("member_id") == "run-manager-agent"
    )
    assert not member.get("reply_contract")


def test_channel_name_carries_chat_suffix(tmp_path, monkeypatch):
    """P3b: every Feishu chat gets its own channel; a bare shared name makes
    them indistinguishable in the Web channel list."""
    state_dir, writer = _writer(tmp_path)
    _patch_reply_turn(monkeypatch, state_dir, writer, "prose only")

    _run(state_dir, writer, text="hi")

    created = [
        e for e in EventLog(state_dir / "events.jsonl").read_all()
        if e.type == "channel.created"
    ]
    assert len(created) == 1
    name = created[0].payload["name"]
    assert name.startswith("Feishu Kanban Agent · ")
    assert name.endswith(CHAT_ID.replace("oc_", "")[-8:]) or CHAT_ID[-8:] in name


def test_proposal_survives_trailing_stray_brace(tmp_path, monkeypatch):
    """combined-e2e: real codex emitted a valid action_proposal object followed
    by one stray '}' inside the fence; strict json.loads dropped the proposal.
    The extractor must recover the leading object via raw_decode."""
    state_dir, writer = _writer(tmp_path)
    _patch_reply_turn(monkeypatch, state_dir, writer, _proposal_reply_json() + "}")

    result = _run(state_dir, writer, text="创建任务：贪吃蛇 MVP")

    proposed = _proposed_events(state_dir)
    assert len(proposed) == 1
    assert proposed[0].payload["proposal"]["action"] == "create-task"
    assert result["action_proposal"]["valid"] is True
