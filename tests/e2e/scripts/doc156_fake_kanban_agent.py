#!/usr/bin/env python3
"""Deterministic stream-json provider for the Doc 156 browser scenario."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from typing import Any


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _user_message(raw: str) -> str:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    prompt_boundary = "\nUser message:\n"
    for text in _strings(payload):
        if prompt_boundary in text:
            return text.rsplit(prompt_boundary, 1)[1].strip()
    candidates = [
        text
        for text in _strings(payload)
        if "DOC156_" in text
    ]
    return min(candidates, key=len) if candidates else raw


def _session_id(args: list[str]) -> str:
    for flag in ("--resume", "--session-id"):
        if flag not in args:
            continue
        index = args.index(flag)
        if index + 1 < len(args):
            return args[index + 1]
    return "doc156-kanban-agent-session"


def _adoption_payload(message: str) -> dict[str, Any]:
    marker = "DOC156_ADOPT "
    start = message.find(marker)
    if start < 0:
        raise ValueError("DOC156_ADOPT payload is missing")
    body = message[start + len(marker):].strip()
    payload, _end = json.JSONDecoder().raw_decode(body)
    if not isinstance(payload, dict):
        raise ValueError("DOC156_ADOPT payload must be an object")
    return payload


def _workflow_plan_request() -> dict[str, Any]:
    return {
        "plan_request": {
            "header": "Workflow",
            "id": "workflow_pattern",
            "question": (
                "Which configured workflow should run for "
                "DOC156_WORKFLOW_PLAN?"
            ),
            "options": [
                {
                    "id": "delivery-smoke",
                    "label": "Delivery smoke (Recommended)",
                    "description": (
                        "Start one bounded delivery worker and verify dispatch."
                    ),
                },
                {
                    "id": "research-fanout",
                    "label": "Research fanout",
                    "description": (
                        "Collect more evidence before delivery."
                    ),
                },
            ],
            "allow_other": True,
            "reason": "The workflow choice changes the controlled action.",
        },
    }


def _workflow_input_plan_request() -> dict[str, Any]:
    return {
        "plan_request": {
            "header": "Workflow inputs",
            "id": "workflow_inputs",
            "question": (
                "Which input package should DOC156_WORKFLOW_INPUT pass "
                "to delivery-smoke?"
            ),
            "options": [
                {
                    "id": "channel-synthesis",
                    "label": "Channel synthesis (Recommended)",
                    "description": (
                        "Pass the Channel, thread, and expected output."
                    ),
                },
                {
                    "id": "task-only",
                    "label": "Task only",
                    "description": (
                        "Run from the task contract without Channel context."
                    ),
                },
            ],
            "allow_other": True,
            "reason": "The worker input package must be fixed before approval.",
        },
    }


def _channel_plan_request() -> dict[str, Any]:
    task_id = os.environ["ZF_DOC156_TASK_ID"]
    channel_id = os.environ["ZF_DOC156_CHANNEL_ID"]
    return {
        "plan_request": {
            "header": "Channel setup",
            "id": "channel_setup",
            "question": (
                "Which member set and turn budget should handle "
                "DOC156_CHANNEL_SETUP?"
            ),
            "submit_action": "channel-create-and-start",
            "submit_label": "Create & start",
            "options": [
                {
                    "id": "quick-change",
                    "label": "Quick change (Recommended)",
                    "description": "Focused implementation and validation.",
                    "recommended": True,
                    "submit_payload": {
                        "template_id": "quick-change",
                        "channel_id": channel_id,
                        "name": "Doc 156 live review",
                        "task_id": task_id,
                        "thread_id": "main",
                        "overrides": {
                            "backend": "fake",
                            "budget": {"max_rounds": 8},
                        },
                    },
                },
                {
                    "id": "architecture-review",
                    "label": "Architecture review",
                    "description": "Broader architecture and security scrutiny.",
                    "submit_payload": {
                        "template_id": "architecture-review",
                        "channel_id": channel_id,
                        "name": "Doc 156 architecture review",
                        "task_id": task_id,
                        "thread_id": "main",
                        "overrides": {
                            "backend": "fake",
                            "budget": {"max_rounds": 12},
                        },
                    },
                },
            ],
            "allow_other": False,
            "reason": (
                "The selected roles and round budget determine the "
                "collaboration cost."
            ),
        },
    }


def _proposal(message: str) -> dict[str, Any]:
    task_id = os.environ["ZF_DOC156_TASK_ID"]
    channel_id = os.environ["ZF_DOC156_CHANNEL_ID"]
    request_id = os.environ["ZF_DOC156_REQUEST_ID"]
    if "DOC156_RESEARCH_START" in message:
        return {
            "action": "research-start",
            "payload": {
                "task_id": task_id,
                "topic": "Collect evidence for the Doc 156 delivery decision.",
                "channel_id": channel_id,
                "thread_id": "main",
                "request_id": request_id,
                "request_revision": 1,
            },
            "reason": "Run the registered fixed-role research fanout.",
        }
    if "DOC156_ADOPT " in message:
        return {
            "action": "research-adopt",
            "payload": _adoption_payload(message),
            "reason": "Adopt the exact research artifact into the current request revision.",
        }
    if (
        "DOC156_WORKFLOW_PLAN" in message
        or "DOC156_WORKFLOW_INPUT" in message
    ):
        return {
            "action": "workflow-invoke",
            "payload": {
                "task_id": task_id,
                "pattern_id": "delivery-smoke",
                "channel_id": channel_id,
                "thread_id": "main",
                "request_id": request_id,
                "request_revision": 1,
                "reason": "Start the post-research delivery smoke workflow.",
                "expected_output": "A live Codex worker accepts the dispatched task.",
            },
            "reason": "Start the registered delivery workflow after explicit approval.",
        }
    raise ValueError("unknown Doc 156 browser marker")


def main() -> int:
    raw = sys.stdin.read()
    message = _user_message(raw)
    session_id = _session_id(sys.argv[1:])
    _emit({"type": "system", "session_id": session_id})
    _emit({
        "type": "assistant",
        "session_id": session_id,
        "message": {
            "content": [{
                "type": "text",
                "text": f"Preparing controlled action for {message.split()[0]}.",
            }],
        },
    })
    try:
        if "DOC156_CHANNEL_SETUP" in raw and "Answer:" not in raw:
            result = json.dumps(_channel_plan_request(), ensure_ascii=False)
        elif "DOC156_WORKFLOW_PLAN" in raw and "Answer:" not in message:
            result = json.dumps(_workflow_plan_request(), ensure_ascii=False)
        elif (
            "DOC156_WORKFLOW_PLAN" in raw
            and "Answer: Delivery smoke" in message
        ):
            result = json.dumps(
                _workflow_input_plan_request(),
                ensure_ascii=False,
            )
        else:
            proposal = _proposal(message)
            result = json.dumps(
                {"action_proposal": proposal},
                ensure_ascii=False,
            )
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        result = f"DOC156_PROVIDER_ERROR: {exc}"
    _emit({
        "type": "result",
        "session_id": session_id,
        "result": result,
        "usage": {"input_tokens": 16, "output_tokens": 24},
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
