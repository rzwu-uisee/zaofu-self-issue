"""Bounded repair and stale-result fencing for Channel synthesis replies."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Any

from zf.core.events import EventWriter
from zf.runtime.sidecar_refs import write_sidecar_text


MAX_SYNTHESIS_REPAIR_ATTEMPTS = 2


def emit_invalid_contract_finding(
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
    status: str,
    reason: str = "",
) -> None:
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
            "evidence_refs": [],
            "contract_status": status,
            "contract_error": reason,
            "request_id": str(request.get("request_id") or ""),
            "message_id": str(request.get("message_id") or ""),
            "run_generation": int(request.get("run_generation") or 1),
            "source_reply_event_id": reply_event_id,
            "source": source,
        },
    )


def reject_synthesis_contract(
    *,
    state_dir: Path,
    writer: EventWriter,
    channel_id: str,
    thread_id: str,
    member_id: str,
    request: dict[str, Any],
    reply: str,
    reply_event_id: str,
    actor: str,
    source: str,
    status: str,
    reason: str,
    synthesis_request_id: str,
    synthesis_repair_revision: int,
) -> None:
    emit_invalid_contract_finding(
        writer=writer,
        channel_id=channel_id,
        thread_id=thread_id,
        member_id=member_id,
        request=request,
        reply=reply,
        reply_event_id=reply_event_id,
        actor=actor,
        source=source,
        status=status,
        reason=reason,
    )
    _request_synthesis_repair(
        state_dir=state_dir,
        writer=writer,
        channel_id=channel_id,
        thread_id=thread_id,
        member_id=member_id,
        request=request,
        reply=reply,
        reply_event_id=reply_event_id,
        source=source,
        status=status,
        reason=reason,
        synthesis_request_id=synthesis_request_id,
        synthesis_repair_revision=synthesis_repair_revision,
    )


def ignore_stale_synthesis_repair(
    *,
    writer: EventWriter,
    channel_id: str,
    thread_id: str,
    synthesis_request_id: str,
    synthesis_repair_id: str,
    synthesis_repair_revision: int,
    reply_event_id: str,
    task_id: str,
) -> bool:
    requests = [
        event
        for event in writer.event_log.read_all()
        if event.type == "channel.synthesis.repair.requested"
        and isinstance(event.payload, dict)
        and str(event.payload.get("channel_id") or "") == channel_id
        and str(event.payload.get("thread_id") or "main") == thread_id
        and str(event.payload.get("request_id") or "") == synthesis_request_id
    ]
    latest_revision = max(
        (int(event.payload.get("repair_revision") or 0) for event in requests),
        default=0,
    )
    expected = next(
        (
            str(event.payload.get("repair_id") or "")
            for event in requests
            if int(event.payload.get("repair_revision") or 0)
            == synthesis_repair_revision
        ),
        "",
    )
    stale_reason = ""
    if latest_revision and synthesis_repair_revision < latest_revision:
        stale_reason = "older_repair_revision"
    elif synthesis_repair_revision and not expected:
        stale_reason = "repair_revision_not_requested"
    elif expected and synthesis_repair_id != expected:
        stale_reason = "repair_identity_mismatch"
    if not stale_reason:
        return False
    if not any(
        event.type == "channel.synthesis.repair.stale_ignored"
        and isinstance(event.payload, dict)
        and str(event.payload.get("source_reply_event_id") or "") == reply_event_id
        for event in writer.event_log.read_all()
    ):
        writer.emit(
            "channel.synthesis.repair.stale_ignored",
            actor="zf-kernel",
            task_id=task_id or None,
            causation_id=reply_event_id,
            correlation_id=channel_id,
            payload={
                "schema_version": "channel.synthesis.repair.v1",
                "channel_id": channel_id,
                "thread_id": thread_id,
                "request_id": synthesis_request_id,
                "repair_id": synthesis_repair_id,
                "repair_revision": synthesis_repair_revision,
                "latest_repair_revision": latest_revision,
                "source_reply_event_id": reply_event_id,
                "reason": stale_reason,
            },
        )
    return True


def _request_synthesis_repair(
    *,
    state_dir: Path,
    writer: EventWriter,
    channel_id: str,
    thread_id: str,
    member_id: str,
    request: dict[str, Any],
    reply: str,
    reply_event_id: str,
    source: str,
    status: str,
    reason: str,
    synthesis_request_id: str,
    synthesis_repair_revision: int,
) -> None:
    if not synthesis_request_id:
        return
    encoded = str(reply or "").encode("utf-8", errors="replace")
    reply_digest = hashlib.sha256(encoded).hexdigest()
    safe_channel = _safe_sidecar_segment(channel_id)
    safe_request = _safe_sidecar_segment(synthesis_request_id)
    invalid_reply_ref = write_sidecar_text(
        Path(state_dir),
        (
            PurePosixPath("channels")
            / safe_channel
            / "synthesis-repairs"
            / safe_request
            / f"invalid-r{synthesis_repair_revision}-{reply_digest[:16]}.txt"
        ),
        str(reply or ""),
        kind="channel_synthesis_invalid_reply",
        schema_version="channel.synthesis.invalid-reply.v1",
        created_by=member_id or "channel-synthesis",
        source_event_id=reply_event_id,
        access_scope={
            "visibility": "project",
            "channel_id": channel_id,
            "thread_id": thread_id,
        },
        retention={"class": "audit_required"},
        required=True,
        preview=str(reply or "")[:240],
    )
    if synthesis_repair_revision >= MAX_SYNTHESIS_REPAIR_ATTEMPTS:
        if _has_synthesis_event(
            writer,
            "channel.synthesis.blocked",
            channel_id=channel_id,
            thread_id=thread_id,
            request_id=synthesis_request_id,
        ):
            return
        writer.emit(
            "channel.synthesis.blocked",
            actor="zf-kernel",
            task_id=str(request.get("task_id") or "") or None,
            causation_id=reply_event_id,
            correlation_id=channel_id,
            payload={
                "schema_version": "channel.synthesis.repair.v1",
                "channel_id": channel_id,
                "thread_id": thread_id,
                "request_id": synthesis_request_id,
                "target_member_id": member_id,
                "repair_revision": synthesis_repair_revision,
                "max_repair_attempts": MAX_SYNTHESIS_REPAIR_ATTEMPTS,
                "contract_status": status,
                "contract_error": reason,
                "invalid_reply_ref": invalid_reply_ref,
                "source_reply_event_id": reply_event_id,
                "source": source,
            },
        )
        return

    next_revision = synthesis_repair_revision + 1
    repair_id = _synthesis_repair_id(
        channel_id,
        thread_id,
        synthesis_request_id,
        next_revision,
    )
    if _has_synthesis_repair_request(writer, repair_id):
        return
    writer.emit(
        "channel.synthesis.repair.requested",
        actor="zf-kernel",
        task_id=str(request.get("task_id") or "") or None,
        causation_id=reply_event_id,
        correlation_id=channel_id,
        payload={
            "schema_version": "channel.synthesis.repair.v1",
            "channel_id": channel_id,
            "thread_id": thread_id,
            "request_id": synthesis_request_id,
            "repair_id": repair_id,
            "repair_revision": next_revision,
            "max_repair_attempts": MAX_SYNTHESIS_REPAIR_ATTEMPTS,
            "target_member_id": member_id,
            "contract_status": status,
            "contract_error": reason,
            "diagnostics": [{
                "path": "channel_synthesis",
                "code": status,
                "message": reason,
            }],
            "invalid_reply_ref": invalid_reply_ref,
            "source_reply_event_id": reply_event_id,
            "source": source,
        },
    )


def _has_synthesis_event(
    writer: EventWriter,
    event_type: str,
    *,
    channel_id: str,
    thread_id: str,
    request_id: str,
) -> bool:
    return any(
        event.type == event_type
        and isinstance(event.payload, dict)
        and str(event.payload.get("channel_id") or "") == channel_id
        and str(event.payload.get("thread_id") or "main") == thread_id
        and str(event.payload.get("request_id") or "") == request_id
        for event in writer.event_log.read_all()
    )


def _has_synthesis_repair_request(writer: EventWriter, repair_id: str) -> bool:
    return any(
        event.type == "channel.synthesis.repair.requested"
        and isinstance(event.payload, dict)
        and str(event.payload.get("repair_id") or "") == repair_id
        for event in writer.event_log.read_all()
    )


def _synthesis_repair_id(
    channel_id: str,
    thread_id: str,
    request_id: str,
    revision: int,
) -> str:
    digest = hashlib.sha256(
        (
            f"channel-synthesis-repair:{channel_id}:{thread_id}:"
            f"{request_id}:{revision}"
        ).encode("utf-8")
    ).hexdigest()[:20]
    return f"synth-repair-{digest}"


def _safe_sidecar_segment(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "")).strip(".-")
    return safe or "unknown"


__all__ = [
    "MAX_SYNTHESIS_REPAIR_ATTEMPTS",
    "emit_invalid_contract_finding",
    "ignore_stale_synthesis_repair",
    "reject_synthesis_contract",
]
