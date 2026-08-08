"""Parse typed Channel reply payloads and render compact display values."""

from __future__ import annotations

import json
import re
from typing import Any

from zf.runtime.channel_contract_artifacts import typed_items


def structured_reply_payload_with_error(
    reply: str,
    key: str,
) -> tuple[dict[str, Any], str]:
    candidates = [str(reply or "").strip()]
    candidates.extend(
        match.group(1).strip()
        for match in re.finditer(
            r"```(?:json)?\s*([\s\S]*?)```",
            reply,
            re.IGNORECASE,
        )
    )
    decoder = json.JSONDecoder()
    errors: list[tuple[int, str]] = []
    saw_object = False
    for candidate in candidates:
        positions = [0, *[index for index, char in enumerate(candidate) if char == "{"]]
        for position in dict.fromkeys(positions):
            try:
                decoded, _ = decoder.raw_decode(candidate[position:].lstrip())
            except json.JSONDecodeError as exc:
                errors.append((
                    position + int(exc.pos or 0),
                    (
                        f"invalid JSON for {key}: {exc.msg} "
                        f"at line {exc.lineno} column {exc.colno}"
                    ),
                ))
                continue
            if not isinstance(decoded, dict):
                continue
            saw_object = True
            value = decoded.get(key)
            if isinstance(value, dict):
                return value, ""
            if key in decoded:
                return {}, f"{key} must be an object"
    if errors:
        return {}, max(errors, key=lambda item: item[0])[1]
    if saw_object:
        return {}, f"reply JSON must contain an object named {key}"
    return {}, f"reply must contain a JSON object named {key}"


def structured_reply_payload(reply: str, key: str) -> dict[str, Any]:
    payload, _error = structured_reply_payload_with_error(reply, key)
    return payload


def reply_question_texts(contribution: dict[str, Any]) -> list[str]:
    questions: list[str] = []
    for key in ("questions", "open_questions"):
        for raw in contribution.get(key) or []:
            if isinstance(raw, dict):
                text = str(raw.get("question") or raw.get("text") or "").strip()
            else:
                text = str(raw or "").strip()
            if text and text not in questions:
                questions.append(text)
    return questions[:16]


def string_items(value: object) -> list[str]:
    return [
        display_value(item)
        for item in typed_items(value)
        if display_value(item)
    ][:32]


def display_value(value: object) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return str(value).strip()


__all__ = [
    "display_value",
    "reply_question_texts",
    "string_items",
    "structured_reply_payload",
    "structured_reply_payload_with_error",
]
