from zf.runtime.channel_reply_parsing import (
    display_value,
    reply_question_texts,
    structured_reply_display_text,
    structured_reply_payload_with_error,
)
from zf.runtime.channel_reply_stream import CHANNEL_CONTRACT_MARKER


def test_structured_reply_parser_accepts_fenced_payload() -> None:
    payload, error = structured_reply_payload_with_error(
        'prefix\n```json\n{"channel_synthesis":{"summary":"ready"}}\n```',
        "channel_synthesis",
    )

    assert error == ""
    assert payload == {"summary": "ready"}


def test_structured_reply_helpers_keep_diagnostics_and_order() -> None:
    payload, error = structured_reply_payload_with_error(
        '{"channel_synthesis":{"summary":',
        "channel_synthesis",
    )

    assert payload == {}
    assert "invalid JSON for channel_synthesis" in error
    assert reply_question_texts({
        "questions": [{"question": "Q1"}, "Q2"],
        "open_questions": ["Q1"],
    }) == ["Q1", "Q2"]
    assert display_value({"b": 2, "a": 1}) == '{"a":1,"b":2}'


def test_structured_reply_display_text_removes_fenced_contract() -> None:
    reply = (
        "## Recommendation\n\nUse one controlled gateway.\n\n"
        "```json\n"
        '{"channel_contribution":{"summary":"ready","risks":[]}}\n'
        "```"
    )

    assert structured_reply_display_text(
        reply,
        "channel_contribution",
    ) == "## Recommendation\n\nUse one controlled gateway."


def test_structured_reply_display_text_uses_marker_as_terminal_boundary() -> None:
    reply = (
        "Readable answer.\n\n"
        f"{CHANNEL_CONTRACT_MARKER}\n"
        '{"channel_synthesis":{"summary":"ready"}}\n'
        "provider prose after the contract must stay hidden"
    )

    assert structured_reply_display_text(
        reply,
        "channel_contribution",
    ) == "Readable answer."


def test_structured_reply_display_text_hides_truncated_contract_preview() -> None:
    reply = (
        "Readable answer.\n\n"
        '{"channel_contribution":{"summary":"partial"\n'
        "[... sidecar body truncated ...]"
    )

    assert structured_reply_display_text(
        reply,
        "channel_contribution",
    ) == "Readable answer."


def test_structured_reply_display_text_hides_contract_only_truncated_preview() -> None:
    reply = (
        '```json\n{"channel_contribution":{"summary":"partial"\n'
        "[... sidecar body truncated ...]"
    )

    assert structured_reply_display_text(reply, "channel_contribution") == ""


def test_structured_reply_display_text_preserves_text_after_fenced_contract() -> None:
    reply = (
        "Readable answer.\n\n"
        "```json\n"
        '{"channel_contribution":{"summary":"ready"}}\n'
        "```\n"
        "END_MARKER"
    )

    assert structured_reply_display_text(
        reply,
        "channel_contribution",
    ) == "Readable answer.\n\nEND_MARKER"


def test_structured_reply_display_text_preserves_text_after_plain_contract() -> None:
    reply = (
        "Readable answer.\n\n"
        '{"channel_contribution":{"summary":"ready"}}\n'
        "END_MARKER"
    )

    assert structured_reply_display_text(
        reply,
        "channel_contribution",
    ) == "Readable answer.\n\nEND_MARKER"
