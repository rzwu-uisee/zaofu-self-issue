from __future__ import annotations

from dataclasses import dataclass

from zf.runtime.channel_reply_stream import (
    CHANNEL_CONTRACT_MARKER,
    ChannelReplyStreamFilter,
    ChannelVisibleMessageEmitter,
)


def _feed(chunks: list[str]) -> tuple[str, ChannelReplyStreamFilter]:
    stream = ChannelReplyStreamFilter()
    visible = "".join(stream.feed(chunk) for chunk in chunks)
    visible += stream.finish()
    return visible, stream


def test_marker_contract_is_hidden_across_arbitrary_chunks() -> None:
    visible, stream = _feed([
        "Use one canonical state owner.\n\n<!-",
        "- zf:channel-",
        "contract -->\n{\"channel_contribution\":",
        "{\"summary\":\"ready\",\"risks\":[]}}",
    ])

    assert visible == "Use one canonical state owner.\n\n"
    assert stream.contract_hidden is True


def test_legacy_plain_and_fenced_contracts_fail_closed() -> None:
    plain, plain_stream = _feed([
        "Answer first.\n",
        '{ "channel_contribution": {"summary":"hidden"}}',
    ])
    fenced, fenced_stream = _feed([
        "Answer first.\n```json\n{\n  \"channel_synth",
        "esis\": {\"summary\": \"hidden\"}\n}\n```",
    ])
    contract_only, contract_only_stream = _feed([
        '{"channel_cross_review":{"summary":"hidden"}}',
    ])

    assert plain == "Answer first.\n"
    assert fenced == "Answer first.\n"
    assert contract_only == ""
    assert plain_stream.contract_hidden is True
    assert fenced_stream.contract_hidden is True
    assert contract_only_stream.contract_hidden is True


def test_non_contract_json_and_markdown_code_remain_visible() -> None:
    text = (
        'Inline config {"mode":"safe"} stays visible.\n'
        '{"ordinary":"json"}\n'
        "```python\nprint('visible')\n```\n"
    )
    visible, stream = _feed([text[:17], text[17:41], text[41:]])

    assert visible == text
    assert stream.contract_hidden is False


def test_finish_flushes_harmless_tail_but_drops_partial_contract() -> None:
    prose, prose_stream = _feed(["A normal reply ending with <"])
    partial, partial_stream = _feed(["Visible.\n{\"channel_contri"])

    assert prose == "A normal reply ending with <"
    assert prose_stream.contract_hidden is False
    assert partial == "Visible.\n"
    assert partial_stream.contract_hidden is True


def test_canonical_marker_constant_is_stable() -> None:
    assert CHANNEL_CONTRACT_MARKER == "<!-- zf:channel-contract -->"


def test_visible_message_emitter_forwards_non_text_and_filters_text() -> None:
    @dataclass(frozen=True)
    class Message:
        type: str
        content: str = ""

    emitted: list[Message] = []
    bridge = ChannelVisibleMessageEmitter(emitted.append)
    bridge.emit(Message(type="status", content="started"))
    bridge.emit(Message(type="text", content="Readable.\n"))
    bridge.emit(Message(type="text", content=CHANNEL_CONTRACT_MARKER[:12]))
    bridge.emit(Message(
        type="text",
        content=(
            CHANNEL_CONTRACT_MARKER[12:]
            + '\n{"channel_contribution":{"summary":"hidden"}}'
        ),
    ))
    bridge.finish()

    assert emitted == [
        Message(type="status", content="started"),
        Message(type="text", content="Readable.\n"),
    ]
