from __future__ import annotations

from zf.runtime.channel_question_graph import (
    normalize_question_payload,
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


def test_question_payload_normalizes_enumerable_owner_options() -> None:
    normalized, error = normalize_question_payload(
        {
            "kind": "tradeoff",
            "options": [
                {
                    "id": "safe",
                    "label": "Safe rollout",
                    "description": "Keep the compatibility window.",
                },
                {
                    "id": "fast",
                    "label": "Fast rollout (Recommended)",
                    "description": "Prefer speed over compatibility.",
                },
            ],
            "allow_other": False,
        },
        question_id="q-rollout",
        question="Which rollout should ship?",
        asked_by="arch",
    )

    assert error == ""
    assert [item["id"] for item in normalized["options"]] == [
        "fast",
        "safe",
    ]
    assert normalized["options"][0]["recommended"] is True
    assert normalized["allow_other"] is False


def test_question_payload_rejects_ambiguous_option_contract() -> None:
    _normalized, error = normalize_question_payload(
        {
            "options": [
                {"id": "a", "label": "A", "recommended": True},
                {"id": "b", "label": "B", "recommended": True},
            ],
        },
        question_id="q-ambiguous",
        question="Which answer?",
        asked_by="arch",
    )

    assert error == "question_options_allow_one_recommendation"


def test_owner_aliases_do_not_turn_fact_questions_into_owner_decisions() -> None:
    owner, owner_error = normalize_question_payload(
        {"kind": "owner_decision", "target_member_id": "operator"},
        question_id="q-owner",
        question="Should this ship?",
        asked_by="pm",
        member_ids={"arch"},
    )
    _fact, fact_error = normalize_question_payload(
        {"kind": "fact", "target_member_id": "operator"},
        question_id="q-fact",
        question="Which API exists?",
        asked_by="pm",
        member_ids={"arch"},
    )

    assert owner_error == ""
    assert owner["target_member_id"] == "owner"
    assert fact_error == "unknown_question_target:operator"
