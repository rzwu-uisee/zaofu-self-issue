"""Structured contribution and synthesis contracts for Channel replies."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from zf.core.events import EventWriter
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.runtime.channel_contract_artifacts import (
    persist_channel_contract,
    persist_channel_source_manifest,
    string_refs,
    typed_items,
    validate_channel_contract,
)
from zf.runtime.channel_context import channel_contribution_index
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_deliberation_contract import (
    apply_consensus_review_reply,
    apply_cross_review_reply,
    reply_question_records,
)
from zf.runtime.channel_prd_revision import (
    consensus_mode_and_required_signers,
    persist_synthesis_prd_revision,
)
from zf.runtime.channel_sidecar import hydrate_channel_message_text
from zf.runtime.channel_question_dedup import apply_question_dedup_reply
from zf.runtime.channel_reply_prompt import (
    channel_reply_response_contract,
    fake_channel_reply_text,
)
from zf.runtime.channel_reply_parsing import (
    display_value as _display_value,
    reply_question_texts as _reply_question_texts,
    string_items as _string_items,
    structured_reply_payload as _structured_reply_payload,
    structured_reply_payload_with_error as _structured_reply_payload_with_error,
)
from zf.runtime.channel_synthesis_repair import (
    emit_invalid_contract_finding as _emit_invalid_contract_finding,
    ignore_stale_synthesis_repair as _ignore_stale_synthesis_repair,
    reject_synthesis_contract as _reject_synthesis_contract,
)
from zf.runtime.channel_templates import CHANNEL_TEMPLATES


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
    synthesis_repair_id = str(refs.get("synthesis_repair_id") or "")
    try:
        synthesis_repair_revision = max(
            0,
            int(refs.get("synthesis_repair_revision") or 0),
        )
    except (TypeError, ValueError):
        synthesis_repair_revision = 0
    question_dedup_request_id = str(
        refs.get("question_dedup_request_id") or ""
    )
    cross_review_request_id = str(
        refs.get("cross_review_request_id") or ""
    )
    consensus_review_id = str(refs.get("consensus_review_id") or "")
    if cross_review_request_id:
        apply_cross_review_reply(
            state_dir=state_dir,
            writer=writer,
            channel=channel,
            channel_id=channel_id,
            thread_id=thread_id,
            member_id=member_id,
            request=request,
            refs=refs,
            review=_structured_reply_payload(
                reply,
                "channel_cross_review",
            ),
            reply_event_id=reply_event_id,
            actor=actor,
            source=source,
        )
        return
    if consensus_review_id:
        apply_consensus_review_reply(
            writer=writer,
            channel=channel,
            channel_id=channel_id,
            thread_id=thread_id,
            member_id=member_id,
            request=request,
            refs=refs,
            review=_structured_reply_payload(
                reply,
                "channel_consensus_review",
            ),
            reply_event_id=reply_event_id,
            actor=actor,
            source=source,
        )
        return
    if question_dedup_request_id:
        dedup = _structured_reply_payload(
            reply,
            "channel_question_dedup",
        )
        if not dedup:
            writer.emit(
                "channel.question.dedup.rejected",
                actor=member_id or actor,
                task_id=str(request.get("task_id") or "") or None,
                causation_id=reply_event_id,
                correlation_id=channel_id,
                payload={
                    "schema_version": "channel.question.dedup.v1",
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "request_id": question_dedup_request_id,
                    "reason": "invalid_missing_channel_question_dedup",
                    "source": source,
                },
            )
            return
        apply_question_dedup_reply(
            state_dir=state_dir,
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            request_id=question_dedup_request_id,
            payload=dedup,
            actor=member_id or actor,
            source=source,
            causation_id=reply_event_id,
            task_id=str(request.get("task_id") or "") or None,
        )
        return
    if synthesis_request_id:
        _emit_synthesis(
            state_dir=state_dir,
            writer=writer,
            channel=channel,
            channel_id=channel_id,
            thread_id=thread_id,
            member_id=member_id,
            request=request,
            reply=reply,
            reply_event_id=reply_event_id,
            synthesis_request_id=synthesis_request_id,
            synthesis_repair_id=synthesis_repair_id,
            synthesis_repair_revision=synthesis_repair_revision,
            actor=actor,
            source=source,
        )
        return
    if not channel_reply_response_contract(channel, request, message):
        return
    _emit_contribution(
        state_dir=state_dir,
        writer=writer,
        channel=channel,
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
    channel: dict[str, Any],
    channel_id: str,
    thread_id: str,
    member_id: str,
    request: dict[str, Any],
    reply: str,
    reply_event_id: str,
    synthesis_request_id: str,
    synthesis_repair_id: str,
    synthesis_repair_revision: int,
    actor: str,
    source: str,
) -> None:
    safe_channel_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", channel_id)
    safe_thread_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", thread_id)
    with locked_path(
        Path(state_dir)
        / "locks"
        / f"channel-prd-{safe_channel_id}-{safe_thread_id}"
    ):
        _emit_synthesis_locked(
            state_dir=state_dir,
            writer=writer,
            channel=project_channel(Path(state_dir), channel_id) or channel,
            channel_id=channel_id,
            thread_id=thread_id,
            member_id=member_id,
            request=request,
            reply=reply,
            reply_event_id=reply_event_id,
            synthesis_request_id=synthesis_request_id,
            synthesis_repair_id=synthesis_repair_id,
            synthesis_repair_revision=synthesis_repair_revision,
            actor=actor,
            source=source,
        )


def _emit_synthesis_locked(
    *,
    state_dir: Path,
    writer: EventWriter,
    channel: dict[str, Any],
    channel_id: str,
    thread_id: str,
    member_id: str,
    request: dict[str, Any],
    reply: str,
    reply_event_id: str,
    synthesis_request_id: str,
    synthesis_repair_id: str,
    synthesis_repair_revision: int,
    actor: str,
    source: str,
) -> None:
    if _ignore_stale_synthesis_repair(
        writer=writer,
        channel_id=channel_id,
        thread_id=thread_id,
        synthesis_request_id=synthesis_request_id,
        synthesis_repair_id=synthesis_repair_id,
        synthesis_repair_revision=synthesis_repair_revision,
        reply_event_id=reply_event_id,
        task_id=str(request.get("task_id") or ""),
    ):
        return
    synthesis, parse_error = _structured_reply_payload_with_error(
        reply,
        "channel_synthesis",
    )
    if not synthesis:
        _reject_synthesis_contract(
            state_dir=state_dir,
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            member_id=member_id,
            request=request,
            reply=reply,
            reply_event_id=reply_event_id,
            actor=actor,
            source=source,
            status="invalid_missing_channel_synthesis",
            reason=parse_error,
            synthesis_request_id=synthesis_request_id,
            synthesis_repair_revision=synthesis_repair_revision,
        )
        return
    validation_error = validate_channel_contract(
        synthesis,
        kind="synthesis",
    )
    classification = synthesis.get("classification")
    if isinstance(classification, dict):
        classified_template = str(
            classification.get("template_id") or ""
        ).strip()
        if (
            classified_template
            and classified_template not in CHANNEL_TEMPLATES
        ):
            validation_error = (
                f"unknown classification template_id: {classified_template}"
            )
    if validation_error:
        _reject_synthesis_contract(
            state_dir=state_dir,
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            member_id=member_id,
            request=request,
            reply=reply,
            reply_event_id=reply_event_id,
            actor=actor,
            source=source,
            status="invalid_channel_synthesis",
            reason=validation_error,
            synthesis_request_id=synthesis_request_id,
            synthesis_repair_revision=synthesis_repair_revision,
        )
        return
    if any(
        event.type == "channel.synthesis.proposed"
        and isinstance(event.payload, dict)
        and str(event.payload.get("channel_id") or "") == channel_id
        and str(event.payload.get("thread_id") or "main") == thread_id
        and str(event.payload.get("request_id") or "")
        == synthesis_request_id
        for event in writer.event_log.read_all()
    ):
        return
    question_records, question_error = reply_question_records(
        synthesis,
        channel=channel,
        channel_id=channel_id,
        thread_id=thread_id,
        member_id=member_id or actor,
        source_kind="synthesis",
    )
    if question_error:
        _reject_synthesis_contract(
            state_dir=state_dir,
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            member_id=member_id,
            request=request,
            reply=reply,
            reply_event_id=reply_event_id,
            actor=actor,
            source=source,
            status="invalid_channel_synthesis_question_graph",
            reason=question_error,
            synthesis_request_id=synthesis_request_id,
            synthesis_repair_revision=synthesis_repair_revision,
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
    source_refs = list(dict.fromkeys([
        *_channel_prd_event_refs(channel, thread_id),
        *string_refs(synthesis.get("source_refs")),
        f"event:{reply_event_id}",
        f"channel:{channel_id}/{thread_id}",
    ]))
    evidence_refs = string_refs(synthesis.get("evidence_refs"))
    contribution_refs, contribution_digests = _contribution_contract_refs(
        channel,
        thread_id=thread_id,
    )
    typed_synthesis = {
        **synthesis,
        "consumed_contribution_refs": list(dict.fromkeys([
            *string_refs(synthesis.get("consumed_contribution_refs")),
            *contribution_refs,
        ])),
        "consumed_contribution_digests": list(dict.fromkeys([
            *string_refs(synthesis.get("consumed_contribution_digests")),
            *contribution_digests,
        ])),
    }
    contract_descriptor = persist_channel_contract(
        state_dir,
        channel_id=channel_id,
        thread_id=thread_id,
        identity=synthesis_request_id,
        kind="synthesis",
        body=typed_synthesis,
        created_by=member_id or actor,
        source_event_id=reply_event_id,
        provenance={
            "source_refs": source_refs,
            "evidence_refs": evidence_refs,
        },
    )
    artifact_body = _render_prd_artifact(
        channel=channel,
        channel_id=channel_id,
        thread_id=thread_id,
        source_requirement=_channel_requirement_text(
            state_dir,
            channel,
            thread_id,
        ),
        synthesis=typed_synthesis,
        summary=summary,
        source_refs=source_refs,
    )
    atomic_write_text(artifact_path, artifact_body)
    revision = persist_synthesis_prd_revision(
        state_dir,
        channel=channel,
        channel_id=channel_id,
        thread_id=thread_id,
        member_id=member_id,
        actor=actor,
        reply_event_id=reply_event_id,
        synthesis=synthesis,
        typed_synthesis=typed_synthesis,
        summary=summary,
        artifact_ref=artifact_ref,
        artifact_body=artifact_body,
        source_refs=source_refs,
        evidence_refs=evidence_refs,
    )
    canonical_artifact_ref = str(revision["artifact_ref"])
    digest = str(revision["artifact_digest"])
    prd_revision = int(revision["prd_revision"])
    synthesis_event = writer.emit(
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
                _display_value(item)
                for item in typed_items(synthesis.get("risks"))
                if _display_value(item)
            ][:16],
            "recommended_workflow": (
                synthesis.get("recommended_workflow")
                if isinstance(synthesis.get("recommended_workflow"), dict)
                else {}
            ),
            **revision,
            "contract_ref": contract_descriptor["ref"],
            "contract_digest": contract_descriptor["sha256"],
            "source_refs": source_refs,
            "evidence_refs": evidence_refs,
            "consumed_contribution_refs": typed_synthesis[
                "consumed_contribution_refs"
            ],
            "consumed_contribution_digests": typed_synthesis[
                "consumed_contribution_digests"
            ],
            "confidence": _display_value(synthesis.get("confidence")),
            "dissent": typed_items(synthesis.get("dissent")),
            "source": source,
        },
    )
    if synthesis_repair_revision:
        writer.emit(
            "channel.synthesis.repair.completed",
            actor=member_id or actor,
            task_id=str(request.get("task_id") or "") or None,
            causation_id=synthesis_event.id,
            correlation_id=channel_id,
            payload={
                "schema_version": "channel.synthesis.repair.v1",
                "channel_id": channel_id,
                "thread_id": thread_id,
                "request_id": synthesis_request_id,
                "repair_id": synthesis_repair_id,
                "repair_revision": synthesis_repair_revision,
                "source_reply_event_id": reply_event_id,
                "source": source,
            },
        )
    if question_records:
        for question_record in question_records:
            writer.emit(
                "channel.question.opened",
                actor=member_id or actor,
                task_id=str(request.get("task_id") or "") or None,
                causation_id=synthesis_event.id,
                correlation_id=channel_id,
                payload={
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    **question_record,
                    "source": source,
                },
            )
        return

    if any(
        isinstance(question, dict)
        and str(question.get("thread_id") or "main") == thread_id
        and str(question.get("status") or "") == "open"
        for question in channel.get("open_questions") or []
    ):
        return

    prior_consensus = any(
        event.type == "channel.consensus.proposed"
        and isinstance(event.payload, dict)
        and str(event.payload.get("channel_id") or "") == channel_id
        and str(event.payload.get("thread_id") or "main") == thread_id
        and str(event.payload.get("artifact_digest") or "") == digest
        for event in writer.event_log.read_all()
    )
    if prior_consensus:
        return
    product_mode, required_signers = (
        consensus_mode_and_required_signers(
            channel,
            thread_id=thread_id,
        )
    )
    if member_id and member_id not in required_signers:
        required_signers.append(member_id)
    proposed = writer.emit(
        "channel.consensus.proposed",
        actor=member_id or actor,
        task_id=str(request.get("task_id") or "") or None,
        causation_id=synthesis_event.id,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": thread_id,
            **revision,
            "owner_actor_ref": str(
                channel.get("owner_actor_ref") or ""
            ),
            "product_mode": product_mode,
            "proposed_by": member_id or actor,
            "required_signers": required_signers,
            "dissent": typed_items(synthesis.get("dissent")),
            "source_refs": source_refs,
            "source": source,
        },
    )
    if member_id:
        writer.emit(
            "channel.consensus.signed",
            actor=member_id,
            task_id=str(request.get("task_id") or "") or None,
            causation_id=proposed.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "member_id": member_id,
                "artifact_ref": canonical_artifact_ref,
                "artifact_digest": digest,
                "prd_revision": prd_revision,
                "source": source,
            },
        )


def _emit_contribution(
    *,
    state_dir: Path,
    writer: EventWriter,
    channel: dict[str, Any],
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
    if not contribution:
        _emit_invalid_contract_finding(
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            member_id=member_id,
            request=request,
            reply=reply,
            reply_event_id=reply_event_id,
            actor=actor,
            source=source,
            status="invalid_missing_channel_contribution",
        )
        return
    validation_error = validate_channel_contract(
        contribution,
        kind="contribution",
    )
    if validation_error:
        _emit_invalid_contract_finding(
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            member_id=member_id,
            request=request,
            reply=reply,
            reply_event_id=reply_event_id,
            actor=actor,
            source=source,
            status="invalid_channel_contribution",
            reason=validation_error,
        )
        return
    question_records, question_error = reply_question_records(
        contribution,
        channel=channel,
        channel_id=channel_id,
        thread_id=thread_id,
        member_id=member_id or actor,
        source_kind="contribution",
    )
    if question_error:
        _emit_invalid_contract_finding(
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            member_id=member_id,
            request=request,
            reply=reply,
            reply_event_id=reply_event_id,
            actor=actor,
            source=source,
            status="invalid_channel_contribution_question_graph",
            reason=question_error,
        )
        return
    summary = str(contribution.get("summary") or "").strip()
    source_refs = list(dict.fromkeys([
        *string_refs(contribution.get("source_refs")),
        f"event:{reply_event_id}",
    ]))
    evidence_refs = string_refs(contribution.get("evidence_refs"))
    request_identity = str(request.get("request_id") or "reply")
    event_identity = str(reply_event_id or "event").removeprefix("evt-")
    identity = f"{request_identity}-{event_identity}"
    descriptor = persist_channel_contract(
        state_dir,
        channel_id=channel_id,
        thread_id=thread_id,
        identity=identity,
        kind="contribution",
        body=contribution,
        created_by=member_id or actor,
        source_event_id=reply_event_id,
        provenance={
            "template": _template_binding(channel),
            "source_refs": source_refs,
            "evidence_refs": evidence_refs,
        },
    )
    source_manifest = persist_channel_source_manifest(
        state_dir,
        channel_id=channel_id,
        thread_id=thread_id,
        identity=identity,
        source_refs=source_refs,
        evidence_refs=evidence_refs,
        created_by=member_id or actor,
        source_event_id=reply_event_id,
    )
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
            "findings": typed_items(contribution.get("findings")),
            "contradictions": typed_items(
                contribution.get("contradictions")
            ),
            "risks": typed_items(contribution.get("risks")),
            "source_refs": source_refs,
            "evidence_refs": evidence_refs,
            "artifact_ref": descriptor["ref"],
            "artifact_digest": descriptor["sha256"],
            "source_manifest_ref": source_manifest["ref"],
            "source_manifest_digest": source_manifest["sha256"],
            "contract_status": "structured",
            "source": source,
        },
    )
    for index, question_record in enumerate(question_records, 1):
        writer.emit(
            "channel.question.opened",
            actor=member_id or actor,
            task_id=str(request.get("task_id") or "") or None,
            causation_id=reply_event_id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                **question_record,
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














def _template_binding(channel: dict[str, Any]) -> dict[str, str]:
    scope = (
        channel.get("scope")
        if isinstance(channel.get("scope"), dict)
        else {}
    )
    template = (
        scope.get("template")
        if isinstance(scope.get("template"), dict)
        else {}
    )
    return {
        "id": str(template.get("id") or ""),
        "version": str(template.get("version") or ""),
        "digest": str(template.get("digest") or ""),
        "materialization_digest": str(
            template.get("materialization_digest") or ""
        ),
    }


def _contribution_contract_refs(
    channel: dict[str, Any],
    *,
    thread_id: str,
) -> tuple[list[str], list[str]]:
    refs: list[str] = []
    digests: list[str] = []
    for contribution in channel_contribution_index(
        channel,
        thread_id=thread_id,
    ):
        if str(contribution.get("contract_status") or "") != "structured":
            continue
        artifact_ref = str(
            contribution.get("artifact_ref") or ""
        ).strip()
        artifact_digest = str(
            contribution.get("artifact_digest") or ""
        ).strip()
        if artifact_ref:
            refs.append(artifact_ref)
        if artifact_digest:
            digests.append(artifact_digest)
    return list(dict.fromkeys(refs)), list(dict.fromkeys(digests))


def _render_prd_artifact(
    *,
    channel: dict[str, Any],
    channel_id: str,
    thread_id: str,
    source_requirement: str,
    synthesis: dict[str, Any],
    summary: str,
    source_refs: list[str],
) -> str:
    title = str(
        synthesis.get("title")
        or channel.get("name")
        or f"Channel requirement {channel_id}"
    ).strip()
    decisions = _string_items(synthesis.get("decisions"))
    assumptions = _string_items(synthesis.get("assumptions"))
    out_of_scope = _string_items(synthesis.get("out_of_scope"))
    acceptance = _string_items(synthesis.get("acceptance_criteria"))
    verification_commands = _string_items(synthesis.get("verification_commands"))
    risks = _string_items(synthesis.get("risks"))
    dissent = _string_items(synthesis.get("dissent"))
    open_questions = _reply_question_texts(synthesis)
    for question in channel.get("open_questions") or []:
        if not isinstance(question, dict):
            continue
        if str(question.get("thread_id") or "main") != thread_id:
            continue
        if str(question.get("status") or "") != "resolved":
            continue
        question_text = str(question.get("question") or "").strip()
        answer = str(question.get("answer") or "").strip()
        resolved_decision = (
            f"{question_text}: {answer}"
            if question_text and answer
            else ""
        )
        if resolved_decision and resolved_decision not in decisions:
            decisions.append(resolved_decision)
    workflow = (
        synthesis.get("recommended_workflow")
        if isinstance(synthesis.get("recommended_workflow"), dict)
        else {}
    )

    def section(name: str, values: list[str]) -> list[str]:
        return [
            f"## {name}",
            *([f"- {item}" for item in values] or ["- None."]),
            "",
        ]

    lines = [
        f"# {title}",
        "",
        "## Source Requirement",
        source_requirement or "No source requirement supplied.",
        "",
        "## Requirement",
        summary or "No summary supplied.",
        "",
        *section("Decisions", decisions),
        *section("Assumptions", assumptions),
        *section("Out of Scope", out_of_scope),
        *section("Acceptance Criteria", acceptance),
        *section(
            "Verification Commands",
            [f"`{command}`" for command in verification_commands],
        ),
        *section("Risks", risks),
        *section("Dissent", dissent),
        *section("Open Questions", open_questions),
        "## Recommended Workflow",
        "```json",
        json.dumps(workflow, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
        "## Provenance",
        f"- Channel: `{channel_id}`",
        f"- Thread: `{thread_id}`",
        *[f"- Source: `{ref}`" for ref in source_refs],
        "",
    ]
    return "\n".join(lines)


def _channel_requirement_text(
    state_dir: Path,
    channel: dict[str, Any],
    thread_id: str,
) -> str:
    discussions = channel.get("discussions")
    session = (
        discussions.get(thread_id)
        if isinstance(discussions, dict)
        else {}
    )
    requirement_id = (
        str(session.get("requirement_message_id") or "")
        if isinstance(session, dict)
        else ""
    )
    for message in channel.get("messages") or []:
        if not isinstance(message, dict):
            continue
        if requirement_id and str(message.get("message_id") or "") != requirement_id:
            continue
        if str(message.get("thread_id") or "main") != thread_id:
            continue
        text = hydrate_channel_message_text(
            state_dir,
            message,
            strict=False,
        ).strip()
        if text:
            return text
    return ""


def _channel_prd_event_refs(
    channel: dict[str, Any],
    thread_id: str,
) -> list[str]:
    relevant_types = {
        "channel.finding.recorded",
        "channel.message.posted",
        "channel.question.opened",
        "channel.question.resolved",
        "channel.questions.frozen",
        "channel.synthesis.requested",
    }
    refs: list[str] = []
    for event in channel.get("linked_events") or []:
        if not isinstance(event, dict):
            continue
        payload = (
            event.get("payload")
            if isinstance(event.get("payload"), dict)
            else {}
        )
        if str(payload.get("thread_id") or "main") != thread_id:
            continue
        if str(event.get("type") or "") not in relevant_types:
            continue
        event_id = str(event.get("id") or "").strip()
        if event_id:
            refs.append(f"event:{event_id}")
    return list(dict.fromkeys(refs))[-64:]


__all__ = [
    "channel_reply_response_contract",
    "emit_structured_reply_events",
]
