"""Feishu-bound specialist agent conversation helpers.

Feishu stays a transport bridge: bot/chat routing decides whether a message goes
to the Kanban Agent or Run Manager Agent, then the selected agent may answer via
the normal channel reply path instead of a fixed template.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_reply_turn import run_channel_reply_turn
from zf.runtime.channel_sidecar import channel_message_event_payload
from zf.runtime.channel_contracts import (
    normalize_permission_profile,
    permission_profile_write_policy,
)
from zf.integrations.feishu.thread_scope import feishu_thread_id
from zf.runtime.kanban_proposals import PROPOSAL_EVENT


def run_specialist_conversation(
    *,
    state_dir,
    config,
    event,
    writer,
    route,
    agent_kind: str,
    default_member: str,
    display_name: str,
    source: str,
) -> dict[str, Any]:
    """Route one Feishu message to the selected specialist agent.

    This provisions a stable channel/member per Feishu chat and reuses
    ``run_channel_reply_turn``. With real headless backends the reply streams
    through the existing Feishu stream-card path; tests can use ``backend=fake``.
    """

    state = Path(state_dir)
    payload = getattr(event, "payload", None) or {}
    text = str(payload.get("text") or "")
    message_id = str(payload.get("message_id") or "") or f"feishu-{agent_kind}"
    chat_id = str(getattr(event, "chat_id", "") or "")
    user_id = str(getattr(event, "user_id", "") or payload.get("member_id") or "feishu")
    member_id = str(getattr(route, "default_member", "") or default_member)
    channel_id = str(getattr(route, "channel_id", "") or "") or _stable_channel_id(
        agent_kind,
        chat_id,
    )
    backend = _conversation_backend(config, route)
    project_root = _project_root(route)
    thread_id = feishu_thread_id(payload)
    permission_profile = normalize_permission_profile(
        getattr(route, "permission_profile", "read_only")
    )
    dangerous_ack = bool(getattr(route, "dangerous_ack", False))

    _ensure_channel(
        state,
        writer,
        channel_id=channel_id,
        member_id=member_id,
        display_name=display_name,
        backend=backend,
        source=source,
        agent_kind=agent_kind,
        chat_id=chat_id,
        permission_profile=permission_profile,
        dangerous_ack=dangerous_ack,
    )
    msg = writer.emit(
        "channel.message.posted",
        actor=f"feishu:{user_id or 'unknown'}",
        correlation_id=channel_id,
        payload=channel_message_event_payload(
            state,
            {
                "channel_id": channel_id,
                "thread_id": thread_id,
                "message_id": message_id,
                "member_id": user_id or "feishu",
                "role": "user",
                "source": "feishu",
                "text": text,
                "mentions": [member_id],
                "refs": {
                    "feishu": {
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "agent_kind": agent_kind,
                        "thread_id": thread_id,
                    },
                },
            },
            created_by=f"feishu:{agent_kind}",
        ),
    )
    turn = run_channel_reply_turn(
        state,
        writer,
        config,
        message_event=msg,
        message_payload=msg.payload,
        actor=source,
        source=source,
        project_root=project_root,
    )
    result = {
        "status": "replied",
        "kind": f"{agent_kind}_conversation",
        "target": agent_kind,
        "channel_id": channel_id,
        "member_id": member_id,
        "backend": backend,
        "thread_id": thread_id,
        "permission_profile": permission_profile,
        "reply_requests": list(turn["route"].reply_requests),
        "dispatched": len(turn["dispatched"]),
    }
    if agent_kind == "kanban_agent":
        interaction = _emit_kanban_interaction(
            state,
            writer,
            config=config,
            channel_id=channel_id,
            member_id=member_id,
            user_text=text,
            trigger_event=msg,
            chat_id=chat_id,
            feishu_message_id=message_id,
            thread_id=thread_id,
            source=source,
            backend=backend,
            permission_profile=permission_profile,
        )
        result.update(interaction)
    return result


def continue_kanban_plan_response(
    *,
    state_dir,
    config,
    writer,
    request_event_id: str,
    option_id: str,
    answer: str = "",
    user_id: str = "",
    chat_id: str = "",
    message_id: str = "",
    project_root: Path | None = None,
    source: str = "feishu-kanban-agent",
) -> dict[str, Any]:
    """Record one signed Plan answer and continue the original channel turn."""
    from zf.runtime.kanban_plan_requests import (
        PLAN_ANSWERED_EVENT,
        PLAN_RESPONSE_SCHEMA_VERSION,
        plan_response_gate,
    )

    events = writer.event_log.read_all()
    request_event = next(
        (
            item
            for item in events
            if item.id == request_event_id
            and item.type == "kanban.agent.plan.requested"
        ),
        None,
    )
    if request_event is None:
        return {"ok": False, "status": "plan_request_not_found"}
    source_payload = (
        request_event.payload
        if isinstance(request_event.payload, dict)
        else {}
    )
    request = source_payload.get("plan_request")
    if not isinstance(request, dict):
        request = (
            source_payload.get("request")
            if isinstance(source_payload.get("request"), dict)
            else {}
        )
    refs = (
        source_payload.get("refs")
        if isinstance(source_payload.get("refs"), dict)
        else {}
    )
    feishu_refs = (
        refs.get("feishu")
        if isinstance(refs.get("feishu"), dict)
        else {}
    )
    expected_chat_id = str(feishu_refs.get("chat_id") or "")
    if expected_chat_id and chat_id != expected_chat_id:
        return {"ok": False, "status": "plan_chat_mismatch"}
    channel_id = str(source_payload.get("channel_id") or "")
    thread_id = str(
        source_payload.get("thread_id")
        or source_payload.get("thread_key")
        or "main"
    )
    member_id = str(source_payload.get("member_id") or "")
    if not channel_id or not member_id:
        return {"ok": False, "status": "plan_continuation_context_missing"}

    gate = plan_response_gate(
        events,
        request_event_id=request_event_id,
        request_id=str(request.get("request_id") or ""),
        revision=request.get("revision"),
        question_id=str(request.get("question_id") or ""),
        option_id=option_id,
        answer=answer,
    )
    if not gate.get("ok"):
        return {
            "ok": False,
            "status": str(gate.get("status") or "plan_response_rejected"),
        }
    if gate.get("status") == "already_answered":
        return {
            "ok": True,
            "status": "already_answered",
            "answer_event_id": str(gate.get("answer_event_id") or ""),
        }

    actor = f"feishu:{user_id or 'owner'}"
    if str(gate.get("submit_action") or ""):
        from zf.runtime.control_actions import ControlledActionService

        plan_response = {
            "request_event_id": request_event_id,
            "request_id": str(gate.get("request_id") or ""),
            "revision": int(gate.get("revision") or 1),
            "question_id": str(gate.get("question_id") or ""),
            "option_id": str(gate.get("option_id") or ""),
            "answer": str(gate.get("answer") or ""),
        }
        requested = writer.emit(
            "runtime.action.requested",
            actor=actor,
            causation_id=request_event.id,
            correlation_id=request_event.correlation_id or channel_id,
            payload={
                "action": "kanban-plan-apply",
                "request": {
                    "plan_response": plan_response,
                    "source": source,
                },
            },
        )
        return ControlledActionService(
            Path(state_dir),
            writer,
            config=config,
            project_root=project_root,
            actor=actor,
            source=source,
            surface="feishu",
        ).execute(
            action="kanban-plan-apply",
            requested_action="kanban-plan-apply",
            payload={
                "plan_response": plan_response,
                "created_by": actor,
            },
            requested=requested,
        )

    response_payload = {
        "schema_version": PLAN_RESPONSE_SCHEMA_VERSION,
        "request_event_id": request_event_id,
        "request_id": str(gate.get("request_id") or ""),
        "request_digest": str(gate.get("request_digest") or ""),
        "revision": int(gate.get("revision") or 1),
        "question_id": str(gate.get("question_id") or ""),
        "option_id": str(gate.get("option_id") or ""),
        "answer": str(gate.get("answer") or ""),
        "source": "feishu",
        "channel_id": channel_id,
        "conversation_id": str(
            source_payload.get("conversation_id") or channel_id
        ),
        "thread_id": thread_id,
        "thread_key": str(source_payload.get("thread_key") or thread_id),
        "member_id": member_id,
        "refs": {
            "feishu": {
                "chat_id": chat_id or expected_chat_id,
                "message_id": message_id,
            },
        },
    }
    answered = writer.emit(
        PLAN_ANSWERED_EVENT,
        actor=actor,
        causation_id=request_event.id,
        correlation_id=request_event.correlation_id or channel_id,
        payload=response_payload,
    )
    continuation_text = (
        f"Plan: {str(request.get('question') or '').strip()}\n"
        f"Answer: {str(gate.get('answer') or '').strip()}"
    )
    msg = writer.emit(
        "channel.message.posted",
        actor=actor,
        causation_id=answered.id,
        correlation_id=channel_id,
        payload=channel_message_event_payload(
            Path(state_dir),
            {
                "channel_id": channel_id,
                "thread_id": thread_id,
                "message_id": message_id or f"feishu-plan-{answered.id}",
                "member_id": user_id or "feishu",
                "role": "user",
                "source": "feishu",
                "text": continuation_text,
                "mentions": [member_id],
                "refs": response_payload["refs"],
            },
            created_by=actor,
        ),
    )
    turn = run_channel_reply_turn(
        Path(state_dir),
        writer,
        config,
        message_event=msg,
        message_payload=msg.payload,
        actor=source,
        source=source,
        project_root=project_root,
    )
    interaction = _emit_kanban_interaction(
        Path(state_dir),
        writer,
        config=config,
        channel_id=channel_id,
        member_id=member_id,
        user_text=continuation_text,
        trigger_event=msg,
        chat_id=chat_id or expected_chat_id,
        feishu_message_id=message_id,
        thread_id=thread_id,
        source=source,
        backend=str(source_payload.get("backend") or ""),
        permission_profile=str(
            source_payload.get("permission_profile") or "read_only"
        ),
        originating_message_event_id=str(
            request.get("originating_message_event_id") or ""
        ),
        proposal_user_text=_plan_proposal_user_text(
            events,
            request,
            continuation_text,
        ),
    )
    return {
        "ok": True,
        "status": "continued",
        "answer_event_id": answered.id,
        "message_event_id": msg.id,
        "channel_id": channel_id,
        "thread_id": thread_id,
        "reply_requests": list(turn["route"].reply_requests),
        "dispatched": len(turn["dispatched"]),
        **interaction,
    }


def _emit_kanban_interaction(
    state_dir: Path,
    writer,
    *,
    config,
    channel_id: str,
    member_id: str,
    user_text: str,
    trigger_event,
    chat_id: str,
    feishu_message_id: str,
    thread_id: str,
    source: str,
    backend: str = "",
    permission_profile: str = "read_only",
    originating_message_event_id: str = "",
    proposal_user_text: str = "",
) -> dict[str, Any]:
    from zf.runtime.kanban_plan_requests import (
        PLAN_REQUESTED_EVENT,
        PLAN_REQUEST_SCHEMA_VERSION,
        normalize_plan_request_revision,
    )
    from zf.core.task.store import TaskStore
    from zf.runtime.task_workflow_plans import (
        task_workflow_binding_digest,
    )
    from zf.web.plan_extraction import extract_plan_request
    from zf.web.proposal_extraction import extract_action_proposal

    reply_text = _latest_assistant_reply_text(
        state_dir,
        channel_id=channel_id,
        member_id=member_id,
        thread_id=thread_id,
        after_ts=str(getattr(trigger_event, "ts", "") or ""),
    )
    if not reply_text:
        return {}
    thread_key = f"channel:{channel_id}:{thread_id}:{member_id}"
    origin_event_id = originating_message_event_id or trigger_event.id
    task_binding_digests = {
        task.id: task_workflow_binding_digest(task)
        for task in TaskStore(state_dir / "kanban.json").list_all()
    }
    plan_request = extract_plan_request(
        reply_text,
        plan_context={
            "project_id": "",
            "conversation_id": channel_id,
            "thread_key": thread_key,
            "turn_id": trigger_event.id,
            "backend": backend,
            "originating_message_event_id": origin_event_id,
            "task_binding_digests": task_binding_digests,
        },
        config=config,
    )
    if plan_request is not None:
        plan_request = normalize_plan_request_revision(
            writer.event_log.read_all(),
            plan_request,
        )
    proposal = extract_action_proposal(
        reply_text,
        user_message=proposal_user_text or user_text,
    )
    if plan_request is not None and proposal is not None:
        plan_request = {
            **plan_request,
            "valid": False,
            "validation_error": (
                "plan_request and action_proposal are mutually exclusive"
            ),
        }
        proposal = None
    refs = {
        "feishu": {
            "chat_id": chat_id,
            "message_id": feishu_message_id,
            "thread_id": thread_id,
        },
    }
    result: dict[str, Any] = {}
    if plan_request is not None:
        plan_event = ZfEvent(
            type=PLAN_REQUESTED_EVENT,
            actor=source,
            causation_id=trigger_event.id,
            correlation_id=channel_id,
        )
        plan_request["request_event_id"] = plan_event.id
        plan_event.payload = {
            "schema_version": PLAN_REQUEST_SCHEMA_VERSION,
            "source": "feishu",
            "turn_id": trigger_event.id,
            "thread_key": thread_key,
            "thread_id": thread_id,
            "project_id": "",
            "conversation_id": channel_id,
            "reply_event_id": "",
            "channel_id": channel_id,
            "member_id": member_id,
            "backend": backend,
            "permission_profile": permission_profile,
            "originating_message_event_id": origin_event_id,
            "plan_request": plan_request,
            "request": plan_request,
            "refs": refs,
        }
        writer.append(plan_event)
        result["plan_request"] = plan_request
    if proposal is not None:
        proposal_event = writer.emit(
            PROPOSAL_EVENT,
            actor=source,
            causation_id=trigger_event.id,
            correlation_id=channel_id,
            payload={
                "turn_id": trigger_event.id,
                "thread_key": thread_key,
                "project_id": "",
                "conversation_id": channel_id,
                "reply_event_id": "",
                "proposal": proposal,
                "source": "feishu",
                "refs": refs,
            },
        )
        proposal["proposal_event_id"] = proposal_event.id
        result["action_proposal"] = proposal
    return result


def _plan_proposal_user_text(
    events: list,
    request: dict[str, Any],
    continuation_text: str,
) -> str:
    origin_id = str(request.get("originating_message_event_id") or "")
    origin = next(
        (
            event
            for event in events
            if event.id == origin_id
            and event.type == "channel.message.posted"
        ),
        None,
    )
    payload = (
        origin.payload
        if origin is not None and isinstance(origin.payload, dict)
        else {}
    )
    original_text = str(
        payload.get("text")
        or payload.get("message")
        or payload.get("text_preview")
        or ""
    ).strip()
    if not original_text:
        return continuation_text
    return f"{original_text}\n{continuation_text}"


def _emit_kanban_action_proposal(
    state_dir: Path,
    writer,
    *,
    channel_id: str,
    member_id: str,
    user_text: str,
    trigger_event,
    chat_id: str,
    feishu_message_id: str,
    thread_id: str,
    source: str,
) -> dict[str, Any] | None:
    """Extract an action proposal from the kanban agent's channel reply.

    Same extractor and gates as the Web panel headless turn (racing-e2e P1:
    without this, a Feishu user saying 创建任务 only ever got prose back and no
    task-creation loop existed on this surface). dispatch runs synchronously in
    run_channel_reply_turn, so the assistant reply is already folded when we
    project here. Emits the same operator.action.proposed event the Web
    triage list renders with an Accept action — approval stays a controlled
    action; nothing executes from Feishu without the operator accepting it.
    """
    return _emit_kanban_interaction(
        state_dir,
        writer,
        channel_id=channel_id,
        member_id=member_id,
        user_text=user_text,
        trigger_event=trigger_event,
        chat_id=chat_id,
        feishu_message_id=feishu_message_id,
        thread_id=thread_id,
        source=source,
    ).get("action_proposal")


def _latest_assistant_reply_text(
    state_dir: Path,
    *,
    channel_id: str,
    member_id: str,
    thread_id: str,
    after_ts: str,
) -> str:
    channel = project_channel(state_dir, channel_id) or {}
    messages = channel.get("messages")
    if isinstance(messages, dict):
        messages = list(messages.values())
    replies = [
        m for m in (messages or [])
        if isinstance(m, dict)
        and str(m.get("member_id") or "") == member_id
        and str(m.get("role") or "") == "assistant"
        and str(m.get("thread_id") or "main") == thread_id
        and str(m.get("ts") or "") >= after_ts
    ]
    if not replies:
        return ""
    replies.sort(key=lambda m: str(m.get("ts") or ""))
    return str(replies[-1].get("text") or "")


def _ensure_channel(
    state_dir: Path,
    writer,
    *,
    channel_id: str,
    member_id: str,
    display_name: str,
    backend: str,
    source: str,
    agent_kind: str,
    chat_id: str = "",
    permission_profile: str = "read_only",
    dangerous_ack: bool = False,
) -> None:
    existing = project_channel(state_dir, channel_id) or {}
    if not existing or not existing.get("created_by_event"):
        # One channel per Feishu chat — a bare "Feishu Kanban Agent" name makes
        # every p2p chat's channel indistinguishable in the Web list (racing-e2e
        # P3b), so suffix the chat identity.
        suffix = re.sub(r"[^A-Za-z0-9_.-]+", "-", chat_id or "").strip("-")[-8:]
        name = f"Feishu {display_name} · {suffix}" if suffix else f"Feishu {display_name}"
        writer.emit(
            "channel.created",
            actor=source,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "name": name,
                "created_by": source,
                "scope": {"source": "feishu", "agent_kind": agent_kind},
            },
        )
    members = existing.get("members") if isinstance(existing, dict) else []
    if not any(
        isinstance(member, dict) and str(member.get("member_id") or "") == member_id
        for member in (members or [])
    ):
        member_payload = {
            "channel_id": channel_id,
            "member_id": member_id,
            "display_name": display_name,
            "member_type": "provider_agent",
            "provider": backend,
            "backend": backend,
            "channel_role": "owner_delegate",
            "permission_profile": permission_profile,
            "permission_profile_ack": dangerous_ack,
            "dangerous_ack": dangerous_ack,
            "permissions": ["read", "message"],
            "source": source,
        }
        if agent_kind == "kanban_agent":
            # Teach the channel-dispatched turn the same proposal-output
            # contract the Web panel bakes into its system prompt — without
            # it, a real backend replies in prose and the extraction hook in
            # run_specialist_conversation never fires.
            from zf.web.operator_contract import (
                KANBAN_AGENT_CHANNEL_PROPOSAL_CONTRACT,
            )

            member_payload["reply_contract"] = KANBAN_AGENT_CHANNEL_PROPOSAL_CONTRACT
        writer.emit(
            "channel.member.invited",
            actor=source,
            correlation_id=channel_id,
            payload=member_payload,
        )
    else:
        member = next(
            (
                item for item in (members or [])
                if isinstance(item, dict)
                and str(item.get("member_id") or "") == member_id
            ),
            {},
        )
        if str(member.get("permission_profile") or "read_only") != permission_profile:
            update = {
                "channel_id": channel_id,
                "thread_id": "main",
                "member_id": member_id,
                "member_type": str(member.get("member_type") or "provider_agent"),
                "provider": str(member.get("provider") or backend),
                "backend": str(member.get("backend") or backend),
                "channel_role": str(member.get("channel_role") or "owner_delegate"),
                "visibility_profile": str(member.get("visibility_profile") or ""),
                "permission_profile": permission_profile,
                "write_policy": permission_profile_write_policy(permission_profile),
                "permissions": list(member.get("permissions") or ["read", "message"]),
                "reason": "synchronized from Feishu route",
                "source": source,
            }
            changed = writer.emit(
                "channel.member.permissions.updated",
                actor=source,
                correlation_id=channel_id,
                payload=update,
            )
            writer.emit(
                "channel.member.permission_profile.audit",
                actor=source,
                causation_id=changed.id,
                correlation_id=channel_id,
                payload={
                    **update,
                    "dangerous_ack": dangerous_ack,
                },
            )
    discussion = existing.get("discussion") if isinstance(existing, dict) else {}
    if str((discussion or {}).get("default_responder_id") or "") != member_id:
        writer.emit(
            "channel.discussion.mode.set",
            actor=source,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "mode": "manual_mention",
                "default_responder_id": member_id,
                "source": source,
            },
        )


def _conversation_backend(config: object | None, route: object | None) -> str:
    route_backend = str(getattr(route, "backend", "") or "").strip()
    if route_backend:
        return route_backend
    runtime = getattr(config, "runtime", None)
    run_manager = getattr(runtime, "run_manager", None)
    backend = str(getattr(run_manager, "backend", "") or "").strip()
    if backend:
        return backend
    autoresearch = getattr(config, "autoresearch", None)
    trigger_policy = getattr(autoresearch, "trigger_policy", None)
    backend = str(getattr(trigger_policy, "self_repair_backend", "") or "").strip()
    return backend or "codex"


def _project_root(route: object | None) -> Path | None:
    cwd = str(getattr(route, "cwd", "") or "").strip()
    if cwd:
        return Path(cwd)
    return Path.cwd()


def _stable_channel_id(agent_kind: str, chat_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", chat_id or "unknown").strip("-")
    return f"feishu-{agent_kind}-{safe or 'unknown'}"
