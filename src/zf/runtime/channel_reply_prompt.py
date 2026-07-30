"""Provider-facing response prompts and deterministic Channel test replies."""

from __future__ import annotations

import json
from typing import Any

from zf.core.security.redaction import redact_obj


def fake_channel_reply_text(
    member: dict[str, Any],
    message: dict[str, Any],
) -> str:
    """Return a deterministic reply that still exercises the typed contract."""
    member_id = str(member.get("member_id") or "agent")
    text = str(message.get("text") or "").strip()
    if len(text) > 220:
        text = text[:217] + "..."
    summary = str(
        redact_obj(f"{member_id} received the channel request: {text}")
    )
    refs = (
        message.get("refs")
        if isinstance(message.get("refs"), dict)
        else {}
    )
    if refs.get("cross_review_request_id"):
        contract = {
            "channel_cross_review": {
                "summary": summary,
                "answer": "",
                "findings": [],
                "contradictions": [],
                "risks": [],
                "source_refs": [],
                "evidence_refs": [],
            },
        }
    elif refs.get("consensus_review_id"):
        contract = {
            "channel_consensus_review": {
                "verdict": "signed",
                "summary": summary,
                "artifact_digest": str(refs.get("artifact_digest") or ""),
                "evidence_refs": [],
            },
        }
    elif refs.get("question_dedup_request_id"):
        contract = {
            "channel_question_dedup": {
                "ledger_digest": str(
                    refs.get("question_ledger_digest") or ""
                ),
                "groups": [],
            },
        }
    elif refs.get("synthesis_request_id"):
        contract = {
            "channel_synthesis": {
                "decision": "proceed",
                "summary": summary,
                "open_questions": [],
                "risks": [],
                "recommended_workflow": {},
                "source_refs": [],
                "evidence_refs": [],
                "consumed_contribution_refs": [],
                "consumed_contribution_digests": [],
                "confidence": "deterministic-test",
            },
        }
    else:
        contract = {
            "channel_contribution": {
                "summary": summary,
                "questions": [],
                "freeze": True,
            },
        }
    return summary + "\n" + json.dumps(contract, ensure_ascii=True)


def channel_reply_response_contract(
    channel: dict[str, Any],
    request: dict[str, Any],
    message: dict[str, Any],
) -> str:
    refs = (
        message.get("refs")
        if isinstance(message.get("refs"), dict)
        else {}
    )
    if refs.get("cross_review_request_id"):
        return (
            "End with one JSON object named channel_cross_review containing "
            "summary, answer, findings, contradictions, risks, source_refs, "
            "and evidence_refs. Facts may be settled only with evidence_refs."
        )
    if refs.get("consensus_review_id"):
        return (
            "Read the exact synthesis artifact and end with one JSON object "
            "named channel_consensus_review containing verdict signed|blocked, "
            "summary, artifact_digest, evidence_refs, and for blocked verdict "
            "blocker_question plus optional blocker_question_id."
        )
    if refs.get("question_dedup_request_id"):
        return (
            "End with one JSON object named channel_question_dedup. "
            "It must contain the exact ledger_digest from the context and "
            "groups. Each group contains canonical_question_id, "
            "merge_question_ids, and reason. It may also contain "
            "question_updates and bounded cross_review_requests. Do not emit "
            "merge or cross-review events."
        )
    if refs.get("synthesis_request_id"):
        return (
            "End with one JSON object named channel_synthesis containing "
            "title, decision, summary, decisions, assumptions, out_of_scope, "
            "acceptance_criteria, open_questions, risks, "
            "recommended_workflow, source_refs, evidence_refs, "
            "consumed_contribution_refs, consumed_contribution_digests, "
            "classification, dissent, and confidence. Keep the "
            "preceding Markdown concise."
        )
    thread_id = str(request.get("thread_id") or "main")
    sessions = channel.get("discussions")
    session = sessions.get(thread_id) if isinstance(sessions, dict) else {}
    scope = (
        channel.get("scope")
        if isinstance(channel.get("scope"), dict)
        else {}
    )
    if (
        isinstance(session, dict)
        and isinstance(scope.get("template"), dict)
        and str(session.get("state") or "") == "phase1_blind"
        and str(session.get("requirement_message_id") or "")
        == str(request.get("message_id") or "")
    ):
        return (
            "End with one JSON object named channel_contribution containing "
            "summary, questions (a list of explicit clarification questions), "
            "where each question may carry kind, depends_on, priority, "
            "why_it_matters, recommended_answer, and target_member_id; "
            "findings, contradictions, risks, source_refs, evidence_refs, "
            "and freeze=true when your contribution is complete."
        )
    return ""


__all__ = [
    "channel_reply_response_contract",
    "fake_channel_reply_text",
]
