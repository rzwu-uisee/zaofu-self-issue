"""Provider usage keeps raw cumulative counters and sums turn deltas."""

from __future__ import annotations

from pathlib import Path

from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.runtime.agent_session_stream import (
    AgentSessionIdentity,
    AgentSessionStreamEmitter,
)
from zf.runtime.channel_projection import project_channel
from zf.runtime.provider_usage import normalize_provider_usage


def test_codex_cumulative_usage_uses_last_as_turn_delta() -> None:
    raw = {
        "tokenUsage": {
            "total": {
                "inputTokens": 180,
                "cachedInputTokens": 140,
                "outputTokens": 18,
            },
            "last": {
                "inputTokens": 80,
                "cachedInputTokens": 60,
                "outputTokens": 8,
            },
        },
    }

    accounting = normalize_provider_usage(raw, backend="codex-headless")

    assert accounting["mode"] == "provider_cumulative_with_turn_delta"
    assert accounting["cumulative"]["input_tokens"] == 180
    assert accounting["turn"] == {
        "input_tokens": 80,
        "output_tokens": 8,
        "cached_input_tokens": 60,
        "total_tokens": 88,
    }
    assert accounting["budget_usage"] == accounting["turn"]


def test_channel_projection_sums_turn_usage_without_recounting_total(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    for index, (total_input, turn_input) in enumerate(
        ((100, 50), (180, 80)),
        1,
    ):
        stream = AgentSessionStreamEmitter(
            writer=writer,
            identity=AgentSessionIdentity(
                run_id=f"run-{index}",
                thread_id="provider-thread",
                source="channel.headless",
                actor="channel-adapter",
                correlation_id="ch-usage",
                channel_id="ch-usage",
                backend="codex-headless",
            ),
            commit_final_text=False,
        )
        stream.start()
        stream.complete(
            usage={
                "tokenUsage": {
                    "total": {
                        "inputTokens": total_input,
                        "outputTokens": index * 10,
                    },
                    "last": {
                        "inputTokens": turn_input,
                        "outputTokens": index * 5,
                    },
                },
            },
        )

    detail = project_channel(state_dir, "ch-usage")

    assert detail["usage_summary"] == {
        "input_tokens": 130,
        "output_tokens": 15,
        "total_tokens": 145,
    }
    assert detail["agent_session_runs"][1]["usage"]["tokenUsage"][
        "total"
    ]["inputTokens"] == 180
    assert detail["agent_session_runs"][1]["usage_accounting"]["turn"][
        "input_tokens"
    ] == 80


def test_flat_claude_usage_is_one_turn() -> None:
    accounting = normalize_provider_usage(
        {
            "input_tokens": 12,
            "output_tokens": 4,
            "cache_read_input_tokens": 9,
        },
        backend="claude-headless",
    )
    assert accounting["mode"] == "per_turn"
    assert accounting["turn"]["input_tokens"] == 12
    assert accounting["turn"]["output_tokens"] == 4
    assert accounting["turn"]["cached_input_tokens"] == 9
