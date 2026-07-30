from __future__ import annotations

from zf.runtime.channel_question_graph import (
    owner_questionnaire,
    question_frontier,
    question_graph_digest,
    validate_question_graph,
)


def test_question_frontier_respects_dependencies_and_owner_routing() -> None:
    channel = {
        "open_questions": [
            {
                "question_id": "q-fact",
                "thread_id": "main",
                "question": "Which protocol is implemented?",
                "kind": "fact",
                "priority": "p0",
                "target_member_id": "arch",
                "depends_on": [],
                "status": "resolved",
            },
            {
                "question_id": "q-owner",
                "thread_id": "main",
                "question": "Which compatibility policy should ship?",
                "kind": "owner_decision",
                "priority": "p0",
                "target_member_id": "owner",
                "depends_on": ["q-fact"],
                "status": "open",
            },
            {
                "question_id": "q-later",
                "thread_id": "main",
                "question": "Which rollout follows the policy?",
                "kind": "tradeoff",
                "priority": "p1",
                "target_member_id": "owner",
                "depends_on": ["q-owner"],
                "status": "open",
            },
        ],
    }

    assert [
        item["question_id"]
        for item in question_frontier(channel, thread_id="main")
    ] == ["q-owner"]
    assert [
        item["question_id"]
        for item in owner_questionnaire(channel, thread_id="main")
    ] == ["q-owner"]
    assert len(question_graph_digest(channel, thread_id="main")) == 64


def test_question_graph_rejects_unknown_self_and_cycles() -> None:
    base = {
        "question_id": "q-a",
        "question": "A?",
        "kind": "fact",
        "priority": "p1",
        "target_member_id": "arch",
        "status": "open",
    }
    assert validate_question_graph([
        {**base, "depends_on": ["q-missing"]},
    ]) == "unknown_question_dependency:q-a:q-missing"
    assert validate_question_graph([
        {**base, "depends_on": ["q-a"]},
    ]) == "question_self_dependency:q-a"
    assert validate_question_graph([
        {**base, "depends_on": ["q-b"]},
        {
            **base,
            "question_id": "q-b",
            "depends_on": ["q-a"],
        },
    ]).startswith("question_dependency_cycle:")
