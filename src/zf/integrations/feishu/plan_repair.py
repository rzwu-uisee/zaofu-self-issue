"""Bounded repair adapter for invalid Feishu Kanban Agent Plan replies."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from zf.core.events import ZfEvent
from zf.runtime.channel_sidecar import channel_message_event_payload
from zf.runtime.kanban_plan_requests import (
    PLAN_REPAIR_COMPLETED_EVENT,
    PLAN_REPAIR_EXHAUSTED_EVENT,
    PLAN_REPAIR_REQUESTED_EVENT,
)


MAX_PLAN_REPAIR_ATTEMPTS = 1


def repair_invalid_feishu_plan(
    state_dir: Path,
    writer,
    *,
    config: Any,
    channel_id: str,
    member_id: str,
    chat_id: str,
    feishu_message_id: str,
    thread_id: str,
    source: str,
    backend: str,
    permission_profile: str,
    planning_context: dict[str, Any],
    originating_message_event_id: str,
    plan_event: ZfEvent,
    plan_request: dict[str, Any],
    repair_attempt: int,
    project_root: Path | None,
    run_reply_turn: Callable[..., Any],
    emit_interaction: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Give one invalid provider Plan a bounded, auditable correction turn."""
    reason = str(plan_request.get("validation_error") or "invalid Plan")[:1600]
    if repair_attempt >= MAX_PLAN_REPAIR_ATTEMPTS:
        exhausted = writer.emit(
            PLAN_REPAIR_EXHAUSTED_EVENT,
            actor="feishu-plan-repair",
            causation_id=plan_event.id,
            correlation_id=channel_id,
            payload=_repair_event_payload(
                plan_event,
                plan_request,
                reason=reason,
                attempt=repair_attempt,
            ),
        )
        return {"status": "exhausted", "event_id": exhausted.id}

    requested = writer.emit(
        PLAN_REPAIR_REQUESTED_EVENT,
        actor="feishu-plan-repair",
        causation_id=plan_event.id,
        correlation_id=channel_id,
        payload=_repair_event_payload(
            plan_event,
            plan_request,
            reason=reason,
            attempt=repair_attempt + 1,
        ),
    )
    repair_text = (
        "The previous durable plan_request was rejected by the deterministic "
        "contract. Return exactly one corrected plan_request JSON and no "
        "action_proposal. Preserve the user's intent. Validation feedback:\n"
        f"{reason}"
    )
    repair_message = writer.emit(
        "channel.message.posted",
        actor="feishu-plan-repair",
        causation_id=requested.id,
        correlation_id=channel_id,
        payload=channel_message_event_payload(
            state_dir,
            {
                "channel_id": channel_id,
                "thread_id": thread_id,
                "message_id": f"feishu-plan-repair-{requested.id}",
                "member_id": "feishu-plan-repair",
                "role": "user",
                "source": "feishu-plan-repair",
                "text": repair_text,
                "mentions": [member_id],
                "refs": {
                    "feishu": {
                        "chat_id": chat_id,
                        "message_id": feishu_message_id,
                        "thread_id": thread_id,
                    },
                },
            },
            created_by="feishu-plan-repair",
        ),
    )
    try:
        run_reply_turn(
            state_dir,
            writer,
            config,
            message_event=repair_message,
            message_payload=repair_message.payload,
            actor=source,
            source=source,
            project_root=project_root,
            agent_context=planning_context,
        )
        interaction = emit_interaction(
            state_dir,
            writer,
            config=config,
            channel_id=channel_id,
            member_id=member_id,
            user_text=repair_text,
            trigger_event=repair_message,
            chat_id=chat_id,
            feishu_message_id=feishu_message_id,
            thread_id=thread_id,
            source=source,
            backend=backend,
            permission_profile=permission_profile,
            originating_message_event_id=originating_message_event_id,
            planning_context=planning_context,
            repair_attempt=repair_attempt + 1,
            project_root=project_root,
        )
    except Exception as exc:  # repair failure must remain owner-visible
        exhausted = writer.emit(
            PLAN_REPAIR_EXHAUSTED_EVENT,
            actor="feishu-plan-repair",
            causation_id=requested.id,
            correlation_id=channel_id,
            payload={
                **_repair_event_payload(
                    plan_event,
                    plan_request,
                    reason=reason,
                    attempt=repair_attempt + 1,
                ),
                "failure": type(exc).__name__,
            },
        )
        return {"status": "exhausted", "event_id": exhausted.id}

    replacement = interaction.get("plan_request")
    if isinstance(replacement, dict) and bool(replacement.get("valid")):
        completed = writer.emit(
            PLAN_REPAIR_COMPLETED_EVENT,
            actor="feishu-plan-repair",
            causation_id=requested.id,
            correlation_id=channel_id,
            payload={
                "request_event_id": plan_event.id,
                "request_id": str(plan_request.get("request_id") or ""),
                "replacement_request_event_id": str(
                    replacement.get("request_event_id") or ""
                ),
                "attempt": repair_attempt + 1,
                "surface": "feishu",
            },
        )
        return {"status": "repaired", "event_id": completed.id}
    return {
        "status": str(
            (interaction.get("plan_repair") or {}).get("status")
            if isinstance(interaction.get("plan_repair"), dict)
            else "exhausted"
        ),
        "event_id": requested.id,
    }


def _repair_event_payload(
    plan_event: ZfEvent,
    plan_request: dict[str, Any],
    *,
    reason: str,
    attempt: int,
) -> dict[str, Any]:
    return {
        "request_event_id": plan_event.id,
        "request_id": str(plan_request.get("request_id") or ""),
        "revision": int(plan_request.get("revision") or 1),
        "attempt": attempt,
        "max_attempts": MAX_PLAN_REPAIR_ATTEMPTS,
        "validation_error": reason,
        "surface": "feishu",
    }


__all__ = ["MAX_PLAN_REPAIR_ATTEMPTS", "repair_invalid_feishu_plan"]
