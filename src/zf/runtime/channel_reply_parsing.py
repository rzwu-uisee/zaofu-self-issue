"""Parse typed Channel reply payloads and render compact display values."""

from __future__ import annotations

import json
import re
from typing import Any

from zf.runtime.channel_contract_artifacts import typed_items
from zf.runtime.channel_reply_stream import CHANNEL_CONTRACT_MARKER


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


def structured_reply_display_text(reply: str, key: str) -> str:
    """Return human prose with a typed reply contract removed.

    Channel providers intentionally return concise Markdown followed by a
    machine-readable JSON contract. The contract is persisted separately and
    must not leak into the conversation transcript. Providers can still place
    human text after that contract, so remove only the decoded contract range
    rather than trimming the whole suffix.
    """

    source = str(reply or "")
    marker_position = source.find(CHANNEL_CONTRACT_MARKER)
    if marker_position >= 0:
        # The marker defines a display/contract boundary. Everything after it
        # is machine output, even if a provider incorrectly appends prose after
        # the JSON. This keeps terminal projection identical to the live text.
        return source[:marker_position].rstrip()
    decoder = json.JSONDecoder()
    for match in re.finditer(
        r"```(?:json)?\s*([\s\S]*?)```",
        source,
        re.IGNORECASE,
    ):
        candidate = match.group(1)
        for position, char in enumerate(candidate):
            if char != "{":
                continue
            try:
                decoded, _end = decoder.raw_decode(candidate[position:])
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict) and isinstance(decoded.get(key), dict):
                return _without_display_range(source, match.start(), match.end())
    for position, char in enumerate(source):
        if char != "{":
            continue
        try:
            decoded, end = decoder.raw_decode(source[position:])
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict) and isinstance(decoded.get(key), dict):
            return _without_display_range(source, position, position + end)
    # A projected preview can end in the middle of an otherwise valid sidecar
    # body. If the stable contract marker is present, fail closed for display
    # and keep only the prose that precedes it.
    marker = re.search(
        rf"(?:^|\n)\s*(?:```(?:json)?\s*)?\{{\s*\"{re.escape(key)}\"\s*:",
        source,
        re.IGNORECASE,
    )
    if marker:
        return source[:marker.start()].rstrip()
    return source.replace("\n[... sidecar body truncated ...]", "").rstrip()


def _without_display_range(source: str, start: int, end: int) -> str:
    before = source[:start].rstrip()
    after = source[end:].lstrip()
    if before and after:
        return f"{before}\n\n{after}".rstrip()
    return (before or after).rstrip()


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
    "structured_reply_display_text",
    "structured_reply_payload_with_error",
]
