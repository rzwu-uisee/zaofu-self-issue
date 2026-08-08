from zf.runtime.channel_reply_parsing import (
    display_value,
    reply_question_texts,
    structured_reply_payload_with_error,
)


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
