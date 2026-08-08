"""doc 122 §8 P0-3 — feishu question cards: fold, card shape, callback emit."""

from __future__ import annotations

import json
import time
from pathlib import Path

from zf.cli.feishu import _handle_event_data
from zf.cli.main import main
from zf.core.config.project_context import resolve_project_context
from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.integrations.feishu.channel_question_card import (
    build_question_card,
    fold_open_questions,
    handle_question_decision,
    push_channel_question_cards_once,
    sync_channel_question_cards,
)
from zf.integrations.feishu.callback_token import verify_action
from zf.integrations.feishu.transport import MockFeishuTransport

CH = "ch-q"


def _writer(tmp_path: Path) -> tuple[Path, EventWriter]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir(exist_ok=True)
    return state_dir, EventWriter(EventLog(state_dir / "events.jsonl"))


def _open(
    writer: EventWriter,
    qid: str,
    question: str,
    *,
    recommended_answer: str = "",
) -> None:
    payload = {
        "channel_id": CH,
        "thread_id": "main",
        "question_id": qid,
        "question": question,
        "category": "scope",
        "asked_by": "arch-1",
    }
    if recommended_answer:
        payload["recommended_answer"] = recommended_answer
    writer.emit(
        "channel.question.opened",
        actor="arch-1",
        correlation_id=CH,
        payload=payload,
    )


def test_fold_and_card_shape(tmp_path: Path) -> None:
    state_dir, writer = _writer(tmp_path)
    _open(writer, "q-1", "要不要移动端?建议:MVP 不做,只键盘")
    questions = fold_open_questions(EventLog(state_dir / "events.jsonl").read_all())
    assert questions["q-1"]["status"] == "open"
    card = build_question_card(questions["q-1"])
    blob = str(card)
    assert "channel-question-adopt:q-1" in blob  # suggestion present -> adopt button
    assert "channel-question-oos:q-1" in blob
    assert card["_card_key"] == "channel-question-q-1"


def test_structured_recommendation_is_actionable_and_becomes_the_answer(
    tmp_path: Path,
) -> None:
    state_dir, writer = _writer(tmp_path)
    _open(
        writer,
        "q-structured",
        "是否采用独立审批？",
        recommended_answer="Task 与 workflow 分别审批。",
    )
    question = fold_open_questions(EventLog(state_dir / "events.jsonl").read_all())["q-structured"]
    assert question["recommended_answer"] == "Task 与 workflow 分别审批。"
    assert "采纳推荐答案" in str(build_question_card(question))

    result = handle_question_decision(
        command="channel-question-adopt",
        question_id="q-structured",
        state_dir=state_dir,
        writer=writer,
        user_id="ou_owner",
    )
    assert result["ok"] is True
    resolved = [
        event for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.type == "channel.question.resolved"
    ][-1]
    assert "Task 与 workflow 分别审批。" in resolved.payload["answer"]


def test_sync_sends_once_and_flips_receipt(tmp_path: Path) -> None:
    state_dir, writer = _writer(tmp_path)
    _open(writer, "q-1", "范围问题")
    sent_cards, updated_cards = [], []

    def send_card(card):
        sent_cards.append(card)
        return f"om-{len(sent_cards)}"

    def update_card(message_id, card):
        updated_cards.append((message_id, card))

    result = sync_channel_question_cards(
        state_dir, send_card=send_card, update_card=update_card, ledger={})
    assert result["sent"] == ["q-1"]
    # second pass: no dup send
    result2 = sync_channel_question_cards(
        state_dir, send_card=send_card, update_card=update_card,
        ledger=result["ledger"])
    assert result2["sent"] == [] and len(sent_cards) == 1
    # resolve -> receipt update
    writer.emit(
        "channel.question.resolved",
        actor="operator",
        correlation_id=CH,
        payload={"channel_id": CH, "thread_id": "main", "question_id": "q-1",
                 "resolution": "answered", "resolved_by": "operator",
                 "answer": "只键盘"},
    )
    result3 = sync_channel_question_cards(
        state_dir, send_card=send_card, update_card=update_card,
        ledger=result2["ledger"])
    assert result3["updated"] == ["q-1"] and updated_cards[0][0] == "om-1"


def test_sync_upgrades_open_legacy_card_when_presentation_changes(tmp_path: Path) -> None:
    state_dir, writer = _writer(tmp_path)
    _open(writer, "q-1", "是否采用独立审批？", recommended_answer="采用独立审批")
    updated_cards = []

    result = sync_channel_question_cards(
        state_dir,
        send_card=lambda card: "unexpected",
        update_card=lambda message_id, card: updated_cards.append((message_id, card)),
        ledger={"channel-question-q-1": {"message_id": "om-legacy", "state": "open"}},
    )

    assert result["sent"] == []
    assert result["updated"] == ["q-1"]
    assert updated_cards[0][0] == "om-legacy"
    assert "采纳推荐答案" in str(updated_cards[0][1])


def test_push_question_card_signs_actions_with_origin_chat(tmp_path: Path) -> None:
    state_dir, writer = _writer(tmp_path)
    _open(writer, "q-1", "范围问题")
    transport = MockFeishuTransport()

    result = push_channel_question_cards_once(
        state_dir,
        transport,
        receive_id="oc_owner",
        action_secret=b"question-secret",
        action_ttl_seconds=120,
    )

    assert result["sent"] == ["q-1"]
    message = transport.sent_messages[-1]
    assert message.chat_id == "oc_owner"
    assert message.msg_type == "interactive"
    card = json.loads(message.content)
    token = card["elements"][1]["actions"][0]["value"]["t"]
    ok, reason = verify_action(
        token,
        secrets_by_version={"1": b"question-secret"},
        expect_action="channel-question-oos",
        expect_target="q-1",
        expect_chat_id="oc_owner",
        now=time.time(),
    )
    assert ok, reason


def test_handle_adopt_emits_answered_with_suggestion(tmp_path: Path) -> None:
    state_dir, writer = _writer(tmp_path)
    _open(writer, "q-1", "要不要移动端?建议:MVP 不做")
    result = handle_question_decision(
        command="channel-question-adopt", question_id="q-1",
        state_dir=state_dir, writer=writer, user_id="ou_owner")
    assert result["ok"] and result["resolution"] == "answered"
    events = EventLog(state_dir / "events.jsonl").read_all()
    resolved = [e for e in events if e.type == "channel.question.resolved"][-1]
    assert "MVP 不做" in resolved.payload["answer"]
    assert resolved.payload["resolved_by"] == "feishu:ou_owner"
    # idempotent-ish: second click reports already_resolved, no second event
    again = handle_question_decision(
        command="channel-question-adopt", question_id="q-1",
        state_dir=state_dir, writer=writer, user_id="ou_owner")
    assert again["reason"] == "already_resolved"
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert len([e for e in events if e.type == "channel.question.resolved"]) == 1


def test_handle_oos_emits_out_of_scope(tmp_path: Path) -> None:
    state_dir, writer = _writer(tmp_path)
    _open(writer, "q-2", "支持手柄吗")
    result = handle_question_decision(
        command="channel-question-oos", question_id="q-2",
        state_dir=state_dir, writer=writer, user_id="ou_owner")
    assert result["ok"] and result["resolution"] == "out_of_scope"


def _button(action: str, user_id: str = "ou_owner", message_id: str = "m1") -> dict:
    return {
        "type": "button_action",
        "payload": {"action": action, "message_id": message_id},
        "user_id": user_id,
        "chat_id": "oc_owner",
    }


def test_cli_button_routes_channel_question_decision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: feishu-question-test\n"
        "  state_dir: .zf\n"
        "roles:\n"
        "  - name: dev\n"
        "    backend: mock\n"
        "integrations:\n"
        "  feishu_identity:\n"
        "    enabled: true\n"
        "    require_signed_actions: false\n"
        "    users:\n"
        "      ou_owner:\n"
        "        name: owner\n"
        "        level: operator\n",
        encoding="utf-8",
    )
    main(["init"])
    ctx = resolve_project_context()
    writer = EventWriter(EventLog(ctx.state_dir / "events.jsonl"))
    _open(writer, "q-3", "要不要移动端?建议:MVP 不做")

    result = _handle_event_data(
        _button("channel-question-adopt:q-3"),
        context=ctx,
        user_levels={},
    )

    assert result["ok"] is True
    assert result["resolution"] == "answered"
    events = EventLog(ctx.state_dir / "events.jsonl").read_all()
    resolved = [event for event in events if event.type == "channel.question.resolved"]
    assert resolved
    assert resolved[-1].payload["question_id"] == "q-3"
    assert resolved[-1].payload["resolved_by"] == "feishu:ou_owner"
