"""Structured contribution and synthesis contracts for Channel replies."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from zf.core.events import EventWriter
from zf.core.security.redaction import redact_obj
from zf.core.state.atomic_io import atomic_write_text


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
    if refs.get("synthesis_request_id"):
        contract = {
            "channel_synthesis": {
                "decision": "proceed",
                "summary": summary,
                "open_questions": [],
                "risks": [],
                "recommended_workflow": {},
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
    if refs.get("synthesis_request_id"):
        return (
            "End with one JSON object named channel_synthesis containing "
            "decision, summary, open_questions, risks, recommended_workflow, "
            "and confidence. Keep the preceding Markdown concise."
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
            "and freeze=true when your contribution is complete."
        )
    return ""


def emit_structured_reply_events(
    *,
    state_dir: Path,
    writer: EventWriter,
    channel: dict[str, Any],
    request: dict[str, Any],
    message: dict[str, Any],
    reply: str,
    reply_event_id: str,
    actor: str,
    source: str,
) -> None:
    channel_id = str(
        channel.get("channel_id") or request.get("channel_id") or ""
    )
    thread_id = str(request.get("thread_id") or "main")
    member_id = str(request.get("target_member_id") or "")
    refs = (
        message.get("refs")
        if isinstance(message.get("refs"), dict)
        else {}
    )
    synthesis_request_id = str(refs.get("synthesis_request_id") or "")
    if synthesis_request_id:
        _emit_synthesis(
            state_dir=state_dir,
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            member_id=member_id,
            request=request,
            reply=reply,
            reply_event_id=reply_event_id,
            synthesis_request_id=synthesis_request_id,
            actor=actor,
            source=source,
        )
        return
    if not channel_reply_response_contract(channel, request, message):
        return
    _emit_contribution(
        writer=writer,
        channel_id=channel_id,
        thread_id=thread_id,
        member_id=member_id,
        request=request,
        reply=reply,
        reply_event_id=reply_event_id,
        actor=actor,
        source=source,
    )


def _emit_synthesis(
    *,
    state_dir: Path,
    writer: EventWriter,
    channel_id: str,
    thread_id: str,
    member_id: str,
    request: dict[str, Any],
    reply: str,
    reply_event_id: str,
    synthesis_request_id: str,
    actor: str,
    source: str,
) -> None:
    synthesis = _structured_reply_payload(reply, "channel_synthesis")
    if not synthesis:
        writer.emit(
            "channel.finding.recorded",
            actor=member_id or actor,
            task_id=str(request.get("task_id") or "") or None,
            causation_id=reply_event_id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "member_id": member_id,
                "summary": str(reply or "").strip(),
                "source_refs": [f"event:{reply_event_id}"],
                "contract_status": "invalid_missing_channel_synthesis",
                "source": source,
            },
        )
        return
    summary = str(synthesis.get("summary") or reply).strip()
    safe_channel_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", channel_id)
    safe_request_id = re.sub(
        r"[^A-Za-z0-9_.-]+",
        "-",
        synthesis_request_id,
    )
    artifact_ref = (
        Path("channel-artifacts")
        / safe_channel_id
        / f"{safe_request_id}.md"
    )
    artifact_path = Path(state_dir) / artifact_ref
    artifact_body = summary + "\n"
    atomic_write_text(artifact_path, artifact_body)
    digest = hashlib.sha256(artifact_body.encode("utf-8")).hexdigest()
    writer.emit(
        "channel.synthesis.proposed",
        actor=member_id or actor,
        task_id=str(request.get("task_id") or "") or None,
        causation_id=reply_event_id,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": thread_id,
            "request_id": synthesis_request_id,
            "decision": str(synthesis.get("decision") or "draft"),
            "summary": summary,
            "open_questions": _reply_question_texts(synthesis),
            "risks": [
                str(item)
                for item in (synthesis.get("risks") or [])
                if str(item)
            ][:16],
            "recommended_workflow": (
                synthesis.get("recommended_workflow")
                if isinstance(synthesis.get("recommended_workflow"), dict)
                else {}
            ),
            "artifact_ref": artifact_ref.as_posix(),
            "artifact_digest": digest,
            "source_refs": [f"event:{reply_event_id}"],
            "confidence": str(synthesis.get("confidence") or ""),
            "source": source,
        },
    )


def _emit_contribution(
    *,
    writer: EventWriter,
    channel_id: str,
    thread_id: str,
    member_id: str,
    request: dict[str, Any],
    reply: str,
    reply_event_id: str,
    actor: str,
    source: str,
) -> None:
    contribution = _structured_reply_payload(
        reply,
        "channel_contribution",
    )
    summary = str(contribution.get("summary") or reply).strip()
    writer.emit(
        "channel.finding.recorded",
        actor=member_id or actor,
        task_id=str(request.get("task_id") or "") or None,
        causation_id=reply_event_id,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": thread_id,
            "member_id": member_id,
            "summary": summary,
            "source_refs": [f"event:{reply_event_id}"],
            "contract_status": (
                "structured"
                if contribution
                else "invalid_missing_channel_contribution"
            ),
            "source": source,
        },
    )
    if not contribution:
        return
    for index, question in enumerate(
        _reply_question_texts(contribution),
        1,
    ):
        question_id = "q-" + hashlib.sha1(
            f"{channel_id}:{thread_id}:{member_id}:{question}".encode(
                "utf-8"
            )
        ).hexdigest()[:16]
        writer.emit(
            "channel.question.opened",
            actor=member_id or actor,
            task_id=str(request.get("task_id") or "") or None,
            causation_id=reply_event_id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "question_id": question_id,
                "question": question,
                "category": "clarification",
                "asked_by": member_id,
                "ordinal": index,
                "source": source,
            },
        )
    if contribution.get("freeze", True) is not False:
        writer.emit(
            "channel.questions.frozen",
            actor=member_id or actor,
            task_id=str(request.get("task_id") or "") or None,
            causation_id=reply_event_id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "member_id": member_id,
                "source": source,
            },
        )


def _structured_reply_payload(
    reply: str,
    key: str,
) -> dict[str, Any]:
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
    for candidate in candidates:
        positions = [
            0,
            *[
                index
                for index, char in enumerate(candidate)
                if char == "{"
            ],
        ]
        for position in dict.fromkeys(positions):
            try:
                decoded, _ = decoder.raw_decode(
                    candidate[position:].lstrip()
                )
            except json.JSONDecodeError:
                continue
            if not isinstance(decoded, dict):
                continue
            value = decoded.get(key)
            if isinstance(value, dict):
                return value
    return {}


def _reply_question_texts(
    contribution: dict[str, Any],
) -> list[str]:
    questions: list[str] = []
    for raw in contribution.get("questions") or []:
        if isinstance(raw, dict):
            text = str(
                raw.get("question") or raw.get("text") or ""
            ).strip()
        else:
            text = str(raw or "").strip()
        if text and text not in questions:
            questions.append(text)
    return questions[:16]


__all__ = [
    "channel_reply_response_contract",
    "emit_structured_reply_events",
]
