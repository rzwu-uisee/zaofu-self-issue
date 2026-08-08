"""Signed Feishu option cards for durable Kanban Agent Plan requests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zf.core.events.log import EventLog
from zf.runtime.kanban_plan_requests import (
    PLAN_ANSWERED_EVENT,
    PLAN_REPAIR_COMPLETED_EVENT,
    PLAN_REPAIR_EXHAUSTED_EVENT,
    PLAN_REPAIR_REQUESTED_EVENT,
    PLAN_REQUESTED_EVENT,
    pending_kanban_plan_requests,
)

KANBAN_PLAN_ANSWER_COMMAND = "kanban-plan-answer"
KANBAN_PLAN_COMMANDS = {KANBAN_PLAN_ANSWER_COMMAND}
PLAN_FORM_OPTION_ID = "form"


def plan_answer_target(request_event_id: str, option_id: str) -> str:
    return f"{request_event_id}~{option_id}"


def parse_plan_answer_target(target: str) -> tuple[str, str]:
    request_event_id, separator, option_id = str(target or "").partition("~")
    if not separator:
        return "", ""
    return request_event_id, option_id


def build_kanban_plan_card(item: dict[str, Any]) -> dict[str, Any]:
    request_event_id = str(item.get("request_event_id") or "")
    questions = _plan_questions(item)
    primary = questions[0]
    options = primary["options"]
    multi_question = len(questions) > 1
    # Preserve the existing one-question quick actions. A Feishu form is the
    # adapter only when multiple answers must be submitted atomically.
    requires_form = multi_question
    actions = [
        _button(
            str(option.get("label") or option.get("id") or ""),
            "primary" if index == 0 or option.get("recommended") else "default",
            (
                f"{KANBAN_PLAN_ANSWER_COMMAND}:"
                f"{plan_answer_target(request_event_id, str(option.get('id') or ''))}"
            ),
        )
        for index, option in enumerate(options)
        if str(option.get("id") or "")
    ] if not requires_form else []
    if not requires_form and bool(primary.get("allow_other", True)):
        actions.append(_button(
            "Customize",
            "default",
            (
                f"{KANBAN_PLAN_ANSWER_COMMAND}:"
                f"{plan_answer_target(request_event_id, 'other')}"
            ),
        ))
    question_details = "\n\n".join(
        (
            f"**{str(question.get('question') or '')}**\n"
            + "\n".join(
                _option_detail_line(option)
                for option in question["options"]
            )
        )
        for question in questions
    )
    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": (
                    f"**{str(item.get('header') or 'Plan')}**\n"
                    f"{question_details}"
                ),
            },
        },
    ]
    if requires_form:
        elements.append(_plan_answer_form(
            request_event_id=request_event_id,
            questions=questions,
        ))
    elif actions:
        elements.append({"tag": "action", "actions": actions})
    elements.append({
        "tag": "note",
        "elements": [{
            "tag": "plain_text",
            "content": (
                f"request {str(item.get('request_id') or '')} "
                f"rev {int(item.get('revision') or 1)}"
            ),
        }],
    })
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "Kanban Agent Plan"},
            "template": "blue",
        },
        "elements": elements,
        "_card_key": f"kanban-plan-{str(item.get('request_id') or request_event_id)}",
    }


def form_answers_from_values(
    item: dict[str, Any],
    values: object,
) -> tuple[list[dict[str, str]], str]:
    """Map Feishu form values to the existing atomic Plan response contract."""
    if not isinstance(values, dict):
        return [], "plan_form_values_missing"
    answers: list[dict[str, str]] = []
    for question in _plan_questions(item):
        question_id = str(question.get("id") or "")
        option_id = _form_value(values, _option_field_name(question_id))
        if not option_id:
            return [], "plan_form_option_missing"
        answer = ""
        if option_id == "other":
            answer = _form_value(values, _answer_field_name(question_id))
            if not answer:
                return [], "plan_form_other_answer_required"
        answers.append({
            "question_id": question_id,
            "option_id": option_id,
            "answer": answer,
        })
    return answers, ""


def build_kanban_plan_result_card(
    item: dict[str, Any],
    *,
    response: dict[str, Any],
) -> dict[str, Any]:
    answers = response.get("answers")
    if not isinstance(answers, list):
        answers = [{
            "question_id": str(response.get("question_id") or ""),
            "answer": str(response.get("answer") or ""),
        }]
    answer_by_id = {
        str(answer.get("question_id") or ""): str(answer.get("answer") or "")
        for answer in answers
        if isinstance(answer, dict)
    }
    summary = "\n".join(
        (
            f"- **{str(question.get('question') or '')}:** "
            f"{answer_by_id.get(str(question.get('id') or ''), '')}"
        )
        for question in _plan_questions(item)
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "Kanban Agent Plan"},
            "template": "green",
        },
        "elements": [{
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**Plan summary**\n{summary}",
            },
        }],
        "_card_key": f"kanban-plan-{str(item.get('request_id') or '')}",
    }


def build_kanban_plan_repair_card(
    item: dict[str, Any],
    *,
    repair_state: str,
) -> dict[str, Any]:
    messages = {
        "repairing": "The Plan interaction is being corrected. A replacement card will follow.",
        "repaired": "The prior Plan interaction was corrected. Use the latest Plan card.",
        "failed": "The Plan interaction could not be corrected automatically. Continue in the ZaoFu Web dashboard.",
    }
    template = "orange" if repair_state != "failed" else "red"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "Kanban Agent Plan"},
            "template": template,
        },
        "elements": [{
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": messages[repair_state],
            },
        }],
        "_card_key": f"kanban-plan-{str(item.get('request_id') or '')}",
    }


def sync_kanban_plan_cards(
    state_dir: Path,
    *,
    send_card,
    update_card,
    ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    events = EventLog(Path(state_dir) / "events.jsonl").read_all()
    ledger = ledger if ledger is not None else {}
    all_items = _plan_items(events)
    pending_ids = {
        str(item.get("request_id") or "")
        for item in pending_kanban_plan_requests(events)
    }
    sent: list[str] = []
    updated: list[str] = []
    for request_id, item in all_items.items():
        key = f"kanban-plan-{request_id}"
        entry = ledger.get(key) if isinstance(ledger.get(key), dict) else {}
        if not bool(item.get("valid")):
            repair_state = _plan_repair_state(events, item)
            current_identity = (
                str(item.get("request_event_id") or ""),
                str(item.get("request_digest") or ""),
                int(item.get("revision") or 1),
                repair_state,
            )
            prior_identity = (
                str(entry.get("request_event_id") or ""),
                str(entry.get("request_digest") or ""),
                int(entry.get("revision") or 1),
                str(entry.get("state") or ""),
            )
            card = build_kanban_plan_repair_card(
                item,
                repair_state=repair_state,
            )
            if not entry.get("message_id"):
                target, message_id = send_card(item, card)
                ledger[key] = {
                    "message_id": str(message_id or ""),
                    "target": str(target or ""),
                    "state": repair_state,
                    "request_event_id": current_identity[0],
                    "request_digest": current_identity[1],
                    "revision": current_identity[2],
                }
                sent.append(request_id)
            elif prior_identity != current_identity:
                update_card(str(entry["message_id"]), item, card)
                ledger[key] = {
                    **entry,
                    "state": repair_state,
                    "request_event_id": current_identity[0],
                    "request_digest": current_identity[1],
                    "revision": current_identity[2],
                }
                updated.append(request_id)
            continue
        if request_id in pending_ids:
            current_identity = (
                str(item.get("request_event_id") or ""),
                str(item.get("request_digest") or ""),
                int(item.get("revision") or 1),
            )
            prior_identity = (
                str(entry.get("request_event_id") or ""),
                str(entry.get("request_digest") or ""),
                int(entry.get("revision") or 1),
            )
            if not entry.get("message_id"):
                target, message_id = send_card(item, build_kanban_plan_card(item))
                ledger[key] = {
                    "message_id": str(message_id or ""),
                    "target": str(target or ""),
                    "state": "pending",
                    "request_event_id": current_identity[0],
                    "request_digest": current_identity[1],
                    "revision": current_identity[2],
                }
                sent.append(request_id)
            elif prior_identity != current_identity:
                update_card(
                    str(entry["message_id"]),
                    item,
                    build_kanban_plan_card(item),
                )
                ledger[key] = {
                    **entry,
                    "state": "pending",
                    "request_event_id": current_identity[0],
                    "request_digest": current_identity[1],
                    "revision": current_identity[2],
                }
                updated.append(request_id)
            continue
        if entry.get("message_id") and entry.get("state") == "pending":
            response = _plan_response(events, item)
            if response is None:
                continue
            update_card(
                str(entry["message_id"]),
                item,
                build_kanban_plan_result_card(item, response=response),
            )
            ledger[key] = {**entry, "state": "answered"}
            updated.append(request_id)
    return {"sent": sent, "updated": updated, "ledger": ledger}


def push_kanban_plan_cards_once(
    state_dir: Path,
    transport,
    *,
    receive_id: str,
    receive_id_type: str = "chat_id",
    action_secret: bytes | None = None,
    action_ttl_seconds: int = 86400,
    action_key_version: str = "1",
    now: float | None = None,
) -> dict[str, Any]:
    import time

    from zf.core.state.atomic_io import atomic_write_text
    from zf.integrations.feishu.callback_token import attach_action_token
    from zf.integrations.feishu.transport import FeishuMessage

    issued_at = time.time() if now is None else now
    ledger_path = (
        Path(state_dir)
        / "integrations"
        / "feishu"
        / "kanban_plan_ledger.json"
    )
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger = {}

    def target_for(item: dict[str, Any]) -> str:
        refs = item.get("refs") if isinstance(item.get("refs"), dict) else {}
        feishu = refs.get("feishu") if isinstance(refs.get("feishu"), dict) else {}
        return str(feishu.get("chat_id") or receive_id)

    def thread_for(item: dict[str, Any]) -> str:
        refs = item.get("refs") if isinstance(item.get("refs"), dict) else {}
        feishu = refs.get("feishu") if isinstance(refs.get("feishu"), dict) else {}
        return str(
            feishu.get("root_message_id")
            or feishu.get("parent_message_id")
            or feishu.get("message_id")
            or ""
        )

    def prepare(item: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
        target = target_for(item)
        if action_secret:
            attach_action_token(
                card,
                secret=action_secret,
                chat_id=target,
                ttl_seconds=action_ttl_seconds,
                now=issued_at,
                key_version=action_key_version,
            )
        return card

    def send_card(
        item: dict[str, Any],
        card: dict[str, Any],
    ) -> tuple[str, str | None]:
        target = target_for(item)
        message_id = transport.send_card(FeishuMessage(
            chat_id=target,
            thread_id=thread_for(item),
            content=json.dumps(prepare(item, card), ensure_ascii=False),
            msg_type="interactive",
            receive_id_type=receive_id_type,
        ))
        return target, message_id

    def update_card(
        message_id: str,
        item: dict[str, Any],
        card: dict[str, Any],
    ) -> bool:
        return transport.update_card(message_id, prepare(item, card))

    result = sync_kanban_plan_cards(
        state_dir,
        send_card=send_card,
        update_card=update_card,
        ledger=ledger,
    )
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        ledger_path,
        json.dumps(result["ledger"], ensure_ascii=False, indent=2) + "\n",
    )
    return result


def _plan_items(events) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.type != PLAN_REQUESTED_EVENT:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        request = payload.get("plan_request")
        if not isinstance(request, dict):
            request = (
                payload.get("request")
                if isinstance(payload.get("request"), dict)
                else {}
            )
        request_id = str(request.get("request_id") or event.id)
        item = {
            **request,
            "request_event_id": event.id,
            "request_id": request_id,
            "refs": payload.get("refs") or {},
        }
        prior = items.get(request_id)
        if prior is None or int(item.get("revision") or 1) >= int(
            prior.get("revision") or 1
        ):
            items[request_id] = item
    return items


def _plan_repair_state(events, item: dict[str, Any]) -> str:
    request_event_id = str(item.get("request_event_id") or "")
    latest = "failed"
    for event in events:
        if event.type not in {
            PLAN_REPAIR_REQUESTED_EVENT,
            PLAN_REPAIR_COMPLETED_EVENT,
            PLAN_REPAIR_EXHAUSTED_EVENT,
        }:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("request_event_id") or "") != request_event_id:
            continue
        if event.type == PLAN_REPAIR_REQUESTED_EVENT:
            latest = "repairing"
        elif event.type == PLAN_REPAIR_COMPLETED_EVENT:
            latest = "repaired"
        elif event.type == PLAN_REPAIR_EXHAUSTED_EVENT:
            latest = "failed"
    return latest


def _plan_response(events, item: dict[str, Any]) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.type != PLAN_ANSWERED_EVENT:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            str(payload.get("request_event_id") or "")
            == str(item.get("request_event_id") or "")
            or str(payload.get("request_id") or "")
            == str(item.get("request_id") or "")
        ):
            return payload
    return None


def _plan_answer_form(
    *,
    request_event_id: str,
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    elements: list[dict[str, Any]] = []
    for question in questions:
        question_id = str(question.get("id") or "")
        options = [
            {
                "text": {
                    "tag": "plain_text",
                    "content": str(option.get("label") or option.get("id") or ""),
                },
                "value": str(option.get("id") or ""),
            }
            for option in question.get("options") or []
            if isinstance(option, dict) and str(option.get("id") or "")
        ]
        if bool(question.get("allow_other", True)):
            options.append({
                "text": {"tag": "plain_text", "content": "Other"},
                "value": "other",
            })
        elements.extend([
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**{str(question.get('question') or '')}**",
                },
            },
            {
                "tag": "select_static",
                "name": _option_field_name(question_id),
                "required": True,
                "placeholder": {
                    "tag": "plain_text",
                    "content": "Select an answer",
                },
                "options": options,
            },
        ])
        if bool(question.get("allow_other", True)):
            elements.append({
                "tag": "input",
                "name": _answer_field_name(question_id),
                "multiline": True,
                "placeholder": {
                    "tag": "plain_text",
                    "content": "Only required when Other is selected",
                },
            })
    elements.append({
        "tag": "action",
        "actions": [_button(
            "Submit answers",
            "primary",
            (
                f"{KANBAN_PLAN_ANSWER_COMMAND}:"
                f"{plan_answer_target(request_event_id, PLAN_FORM_OPTION_ID)}"
            ),
        )],
    })
    return {
        "tag": "form",
        "name": f"kanban-plan-{request_event_id}",
        "elements": elements,
    }


def _option_field_name(question_id: str) -> str:
    return f"plan_option_{question_id}"


def _answer_field_name(question_id: str) -> str:
    return f"plan_answer_{question_id}"


def _form_value(values: dict[str, Any], name: str) -> str:
    value = values.get(name)
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").strip()


def _button(text: str, button_type: str, action: str) -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": button_type,
        "value": {"action": action},
    }


def _option_detail_line(option: dict[str, Any]) -> str:
    line = f"- **{str(option.get('label') or option.get('id') or '')}**"
    description = str(option.get("description") or "").strip()
    details = (
        option.get("submit_details")
        if isinstance(option.get("submit_details"), dict)
        else {}
    )
    lane_count = _safe_int(details.get("lane_count"))
    detail_parts = [
        " / ".join(
            item for item in (
                str(details.get("family") or ""),
                str(details.get("topology") or ""),
            )
            if item
        ),
        (
            f"{lane_count} lanes"
            if lane_count
            else ""
        ),
        (
            f"mode {str(details.get('product_mode') or '')}"
            if details.get("product_mode")
            else ""
        ),
        (
            f"output {str(details.get('output_profile') or '')}"
            if details.get("output_profile")
            else ""
        ),
    ]
    suffix = "; ".join(item for item in detail_parts if item)
    if description:
        line += f": {description}"
    if suffix:
        line += f" ({suffix})"
    return line


def _safe_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _plan_questions(item: dict[str, Any]) -> list[dict[str, Any]]:
    questions = item.get("questions")
    if isinstance(questions, list):
        normalized = [
            {
                **question,
                "options": [
                    option
                    for option in question.get("options") or []
                    if isinstance(option, dict)
                ],
            }
            for question in questions
            if isinstance(question, dict)
        ]
        if normalized:
            return normalized
    return [{
        "id": str(item.get("question_id") or ""),
        "question": str(item.get("question") or ""),
        "options": [
            option
            for option in item.get("options") or []
            if isinstance(option, dict)
        ],
        "allow_other": bool(item.get("allow_other", True)),
    }]


__all__ = [
    "KANBAN_PLAN_ANSWER_COMMAND",
    "KANBAN_PLAN_COMMANDS",
    "PLAN_FORM_OPTION_ID",
    "build_kanban_plan_card",
    "build_kanban_plan_repair_card",
    "build_kanban_plan_result_card",
    "form_answers_from_values",
    "parse_plan_answer_target",
    "plan_answer_target",
    "push_kanban_plan_cards_once",
    "sync_kanban_plan_cards",
]
