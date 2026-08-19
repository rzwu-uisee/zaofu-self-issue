from __future__ import annotations

from zf.runtime.channel_discussion_attention import project_discussion_attention


def _channel() -> dict[str, object]:
    return {
        "discussions": {
            "main": {
                "state": "phase1_blind",
                "started_at": "2026-08-19T02:00:00Z",
                "roster": ["pm", "architect"],
            },
        },
        "consensus": {
            "main": {
                "ts": "2026-08-19T01:00:00Z",
                "artifact_ref": "channels/ch-test/prd/stale.json",
            },
        },
        "open_questions": {
            "question-1": {
                "question_id": "question-1",
                "thread_id": "main",
                "status": "open",
                "created_at": "2026-08-19T02:01:00Z",
            },
        },
        "synthesis_requests": [],
        "syntheses": [],
        "question_activity": [],
    }


def test_attention_keeps_active_execution_separate_from_owner_question() -> None:
    projection = project_discussion_attention(
        _channel(),
        [{
            "thread_id": "main",
            "request_id": "reply-1",
            "target_member_id": "architect",
            "status": "running",
            "started_at": "2026-08-19T02:02:00Z",
        }],
        {"main": [{"question_id": "question-1"}]},
    )["main"]

    assert projection["schema_version"] == "channel.discussion-attention.v2"
    assert projection["state"] == "needs_input"
    assert projection["execution_state"] == "running"
    assert projection["attention_kind"] == "question"
    assert projection["blocking_scope"] == "phase"
    assert projection["blocks_transition"] == "synthesis"
    assert projection["active_agent_count"] == 1
    assert projection["can_review_result"] is False


def test_owner_question_becomes_waiting_gate_after_agents_finish() -> None:
    projection = project_discussion_attention(
        _channel(),
        [{
            "thread_id": "main",
            "request_id": "reply-1",
            "target_member_id": "architect",
            "status": "completed",
            "started_at": "2026-08-19T02:02:00Z",
        }],
        {"main": [{"question_id": "question-1"}]},
    )["main"]

    assert projection["state"] == "needs_input"
    assert projection["execution_state"] == "ready"
    assert projection["attention_kind"] == "question"
    assert projection["active_agent_count"] == 0
