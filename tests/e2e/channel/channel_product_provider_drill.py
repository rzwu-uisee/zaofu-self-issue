"""Run one real provider through the Channel product reply contract."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from fastapi.testclient import TestClient
from zf.core.config.schema import (
    ChannelAgentProfileConfig,
    ChannelConfig,
    ZfConfig,
)
from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_result_receipts import (
    reconcile_channel_result_receipts,
)
from zf.runtime.control_actions import ControlledActionService
from zf.web.server import create_app


CHANNEL_ID = "ch-real-provider-product"
MEMBER_ID = "product-pm"
MARKER = "ZF_CHANNEL_REAL_PROVIDER_OK"
OWNER_ACTOR = "owner:provider-drill"


def _execute(
    service: ControlledActionService,
    writer: EventWriter,
    action: str,
    payload: dict,
) -> dict:
    requested = writer.emit(
        "runtime.action.requested",
        actor="provider-drill",
        correlation_id=CHANNEL_ID,
        payload={"action": action, "request": payload},
    )
    result = service.execute(
        action=action,
        requested_action=action,
        payload=payload,
        requested=requested,
    )
    if not result.get("ok"):
        raise RuntimeError(f"{action} failed: {result}")
    return result


def _wait_for_event(
    log: EventLog,
    event_type: str,
    predicate: Callable[[Any], bool],
    *,
    timeout_seconds: float = 180,
) -> Any:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for event in reversed(log.read_all()):
            if event.type == event_type and predicate(event):
                return event
        time.sleep(0.1)
    raise RuntimeError(f"timed out waiting for {event_type}")


def _complete_reply_requests(
    *,
    log: EventLog,
    request_event_ids: list[str],
) -> list[str]:
    request_ids = {
        str(event.payload.get("request_id") or "")
        for event in log.read_all()
        if event.id in set(request_event_ids)
        and event.type == "channel.agent.reply.requested"
    }
    request_ids.discard("")
    if not request_ids:
        raise RuntimeError(
            "Channel action did not create a provider reply request"
        )
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        completed = {
            str(event.payload.get("request_id") or "")
            for event in log.read_all()
            if event.type == "channel.agent.reply.completed"
        }
        if request_ids.issubset(completed):
            return sorted(request_ids)
        time.sleep(0.1)
    raise RuntimeError(
        "timed out waiting for provider request(s): "
        + ", ".join(sorted(request_ids))
    )


def _event_ids(log: EventLog) -> set[str]:
    return {event.id for event in log.read_all()}


def _new_events(
    log: EventLog,
    *,
    before: set[str],
    event_type: str,
) -> list[Any]:
    return [
        event
        for event in log.read_all()
        if event.id not in before and event.type == event_type
    ]


def _wait_for_turn_terminal(
    log: EventLog,
    turn_id: str,
    *,
    timeout_seconds: float = 180,
) -> Any:
    terminal_types = {
        "kanban.agent.turn.completed",
        "kanban.agent.turn.failed",
    }
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for event in reversed(log.read_all()):
            if (
                event.type in terminal_types
                and str(event.payload.get("turn_id") or "") == turn_id
            ):
                return event
        time.sleep(0.1)
    raise RuntimeError(f"timed out waiting for Kanban Agent turn {turn_id}")


def _real_plan_to_proposal(
    *,
    state_dir: Path,
    project_root: Path,
    config: ZfConfig,
    backend: str,
    authority: dict[str, Any],
) -> dict[str, Any]:
    token = os.environ.setdefault(
        "ZF_WEB_ACTION_TOKEN",
        "channel-real-provider-drill-token",
    )
    headless_backend = (
        "codex-headless"
        if backend == "codex"
        else "claude-headless"
    )
    with TestClient(
        create_app(
            state_dir,
            config=config,
            project_root=project_root,
        )
    ) as client:
        headers = {"x-zf-web-token": token}
        accepted_at = time.monotonic()
        response = client.post(
            "/api/actions/chat-orchestrator",
            headers=headers,
            json={
                "backend": headless_backend,
                "permission_profile": "read_only",
                "project_id": "channel-real-provider-drill",
                "conversation_id": f"channel:{CHANNEL_ID}",
                "thread_key": f"channel-plan:{CHANNEL_ID}:main",
                "source": "web-channel-workflow-plan",
                "message": (
                    "Propose creating one Task from the exact confirmed "
                    "Channel PRD. Return a task_create Plan with exactly two "
                    "options: one recommended create-task proposal and one "
                    "continue/defer alternative. The create-task submit payload "
                    "must be flat and use only title, objective, acceptance, "
                    "acceptance_criteria, scope, explicit_non_goals, "
                    "skills_required, priority, and optional task_id; priority "
                    "must be an integer from 1 through 5. Put mode, action, and "
                    "payload inside each option's effect object. The second "
                    "option must use effect.mode=continue with no action. Do not "
                    "nest contract or channel_authority. Do not create the "
                    "Task, approve anything, or invoke a workflow."
                ),
                "workflow_context": authority,
            },
        )
        accept_duration_ms = int((time.monotonic() - accepted_at) * 1000)
        if response.status_code != 202:
            raise RuntimeError(
                f"Kanban Agent Plan failed: {response.status_code} "
                f"{response.text}"
            )
        body = response.json()
        turn_id = str(body.get("turn_id") or "")
        if not turn_id:
            raise RuntimeError(f"Kanban Agent did not return turn_id: {body}")
        if accept_duration_ms > 2_000:
            raise RuntimeError(
                "Kanban Agent async admission exceeded 2s: "
                f"{accept_duration_ms}ms"
            )

        snapshot_started_at = time.monotonic()
        snapshot = client.get("/api/snapshot/light")
        snapshot_duration_ms = int(
            (time.monotonic() - snapshot_started_at) * 1000
        )
        if snapshot.status_code != 200 or snapshot_duration_ms > 2_000:
            raise RuntimeError(
                "Web became unresponsive while Provider turn was active: "
                f"status={snapshot.status_code} duration={snapshot_duration_ms}ms"
            )

        terminal = _wait_for_turn_terminal(
            EventLog(state_dir / "events.jsonl"),
            turn_id,
        )
        if terminal.type == "kanban.agent.turn.failed":
            raise RuntimeError(
                "Kanban Agent Plan failed asynchronously: "
                f"{terminal.payload.get('reason') or terminal.payload}"
            )
        reply_event_id = str(terminal.payload.get("reply_event_id") or "")
        reply_event = next(
            (
                event
                for event in EventLog(state_dir / "events.jsonl").read_all()
                if event.id == reply_event_id
                and event.type == "kanban.agent.reply"
            ),
            None,
        )
        if reply_event is None:
            raise RuntimeError(
                f"Kanban Agent terminal has no durable reply: {terminal.payload}"
            )
        plan = (
            reply_event.payload.get("plan_request")
            if isinstance(reply_event.payload, dict)
            else None
        )
        if not isinstance(plan, dict) or not plan.get("valid"):
            raise RuntimeError(f"Kanban Agent returned invalid Plan: {body}")
        option = next(
            (
                item
                for item in plan.get("options") or []
                if isinstance(item, dict)
                and item.get("submit_action") == "create-task"
            ),
            None,
        )
        if option is None:
            raise RuntimeError(
                f"Kanban Agent Plan has no create-task option: {plan}"
            )
        applied = client.post(
            "/api/actions/kanban-plan-apply",
            headers=headers,
            json={
                "plan_response": {
                    "request_event_id": plan["request_event_id"],
                    "request_id": plan["request_id"],
                    "revision": plan["revision"],
                    "question_id": plan["question_id"],
                    "option_id": option["id"],
                    "answer": "Create the exact PRD-bound Task proposal.",
                },
            },
        )
        if applied.status_code != 202:
            raise RuntimeError(
                f"Kanban Agent proposal failed: {applied.status_code} "
                f"{applied.text}"
            )
        result = applied.json()
        if (
            result.get("status") != "proposal_ready"
            or result.get("proposed_action") != "create-task"
        ):
            raise RuntimeError(
                f"Kanban Agent did not stop at Task proposal: {result}"
            )
        return {
            "plan_request_id": plan["request_id"],
            "plan_option_id": option["id"],
            "proposal_id": result.get("proposal_id"),
            "turn_id": turn_id,
            "accept_duration_ms": accept_duration_ms,
            "snapshot_duration_ms": snapshot_duration_ms,
            "turn_duration_ms": int(terminal.payload.get("duration_ms") or 0),
            "turn_timing": terminal.payload.get("timing") or {},
        }


def run_drill(
    *,
    project_root: Path,
    state_dir: Path,
    backend: str,
) -> dict:
    project_root.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    config = ZfConfig(
        channel=ChannelConfig(
            agent_profiles={
                MEMBER_ID: ChannelAgentProfileConfig(
                    revision=1,
                    persona="Product requirement facilitator",
                    display_name="Product PM",
                    channel_role="product_pm",
                    provider=backend,
                    backend=backend,
                    visibility_ceiling="planner",
                    permission_ceiling="read_only",
                    lifecycle="persistent",
                )
            }
        )
    )
    service = ControlledActionService(
        state_dir,
        writer,
        config=config,
        actor=OWNER_ACTOR,
        source="channel-real-provider-drill",
        surface="cli",
        project_root=project_root,
    )
    _execute(
        service,
        writer,
        "channel-create",
        {
            "channel_id": CHANNEL_ID,
            "name": "Real provider product drill",
            "owner_actor_ref": OWNER_ACTOR,
        },
    )
    _execute(
        service,
        writer,
        "channel-invite-member",
        {
            "channel_id": CHANNEL_ID,
            "member_id": MEMBER_ID,
            "profile_id": MEMBER_ID,
            "member_type": "provider_agent",
            "permission_profile": "read_only",
            "permissions": [
                "read",
                "message",
                "summarize",
                "propose_workflow",
            ],
        },
    )
    before_post = _event_ids(log)
    _execute(
        service,
        writer,
        "channel-set-leader",
        {
            "channel_id": CHANNEL_ID,
            "leader_member_id": MEMBER_ID,
            "expected_revision": 0,
            "idempotency_key": "real-provider-product-leader-r1",
        },
    )
    _execute(
        service,
        writer,
        "channel-discussion-mode",
        {
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "mode": "conversation",
            "default_responder_id": MEMBER_ID,
            "max_rounds": 2,
        },
    )
    _execute(
        service,
        writer,
        "channel-post-message",
        {
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "client_message_id": "real-provider-product-message",
            "member_id": "operator",
            "role": "user",
            "text": (
                f"@{MEMBER_ID} Reply with exactly {MARKER} followed by a "
                "single sentence confirming the Channel requirement is "
                "understood. Do not modify files or run commands."
            ),
        },
    )
    request_ids = _complete_reply_requests(
        log=log,
        request_event_ids=[
            event.id
            for event in _new_events(
                log,
                before=before_post,
                event_type="channel.agent.reply.requested",
            )
        ],
    )
    before_discuss = _event_ids(log)
    _execute(
        service,
        writer,
        "channel-discussion-start",
        {
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "mode": "conversation",
            "objective": (
                "Discuss the smallest acceptance criteria for a durable "
                "Channel requirement. Do not modify files or run commands."
            ),
        },
    )
    discussion_request_ids = _complete_reply_requests(
        log=log,
        request_event_ids=[
            event.id
            for event in _new_events(
                log,
                before=before_discuss,
                event_type="channel.agent.reply.requested",
            )
        ],
    )
    before_synthesis = _event_ids(log)
    _execute(
        service,
        writer,
        "channel-synthesis-request",
        {
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "target_member_id": MEMBER_ID,
            "reason": (
                "Finalize the current discussion into a concise PRD. Include "
                "a title, decisions, assumptions, out_of_scope, at least one "
                "acceptance criterion, no open questions, risks, and "
                "confidence. Do not modify files or run commands."
            ),
        },
    )
    synthesis_request = _new_events(
        log,
        before=before_synthesis,
        event_type="channel.synthesis.requested",
    )
    if len(synthesis_request) != 1:
        raise RuntimeError(
            "synthesis action did not create one canonical request"
        )
    proposed = _wait_for_event(
        log,
        "channel.synthesis.proposed",
        lambda event: str(event.payload.get("request_id") or "")
        == str(synthesis_request[0].payload.get("request_id") or ""),
    )
    synthesis_reply_requests = _new_events(
        log,
        before=before_synthesis,
        event_type="channel.agent.reply.requested",
    )
    if len(synthesis_reply_requests) != 1:
        raise RuntimeError(
            "synthesis action did not dispatch exactly one provider reply"
        )
    synthesis_reply_request_ids = {
        str(synthesis_reply_requests[0].payload.get("request_id") or "")
    }
    detail = project_channel(state_dir, CHANNEL_ID) or {}
    requests = [
        item
        for item in detail.get("reply_requests") or []
        if item.get("target_member_id") == MEMBER_ID
    ]
    replies = [
        item
        for item in detail.get("messages") or []
        if item.get("member_id") == MEMBER_ID
    ]
    events = log.read_all()
    failures = [
        event
        for event in events
        if event.type == "channel.agent.reply.failed"
    ]
    marker_reply = next(
        (
            item
            for item in replies
            if MARKER in str(item.get("text") or "")
        ),
        None,
    )
    if not requests or requests[-1].get("status") != "completed":
        raise RuntimeError(f"provider reply did not complete: {requests[-1:]}")
    if marker_reply is None:
        raise RuntimeError(f"provider marker missing: {replies[-1:]}")
    if failures:
        raise RuntimeError(
            "provider reply emitted failure events: "
            + ", ".join(event.id for event in failures)
        )
    event_types = [event.type for event in events]
    for required in (
        "agent.session.run.started",
        "agent.session.run.completed",
        "provider.permission.snapshot.recorded",
        "channel.agent.reply.completed",
    ):
        if required not in event_types:
            raise RuntimeError(f"required event missing: {required}")
    consensus = (
        detail.get("consensus", {}).get("main")
        if isinstance(detail.get("consensus"), dict)
        else None
    )
    if not isinstance(consensus, dict):
        raise RuntimeError("real provider did not produce a PRD proposal")
    confirmed = _execute(
        service,
        writer,
        "channel-consensus-confirm",
        {
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "artifact_ref": consensus["artifact_ref"],
            "artifact_digest": consensus["artifact_digest"],
            "prd_revision": consensus["prd_revision"],
            "accept_readiness_risk": True,
            "idempotency_key": "real-provider-product-confirm-r1",
        },
    )
    if confirmed.get("status") != "confirmed":
        raise RuntimeError(f"Owner PRD confirmation failed: {confirmed}")
    receipts = reconcile_channel_result_receipts(
        state_dir=state_dir,
        event_log=log,
        writer=writer,
    )
    if receipts.recorded != 1:
        raise RuntimeError(
            f"exact-origin PRD receipt was not recorded once: {receipts}"
        )
    authority = {
        "channel_id": CHANNEL_ID,
        "thread_id": "main",
        "channel_member_id": MEMBER_ID,
        "leader_revision": 1,
        "prd_revision": int(consensus["prd_revision"]),
        "source_ref": str(consensus["prd_ref"]),
        "source_digest": str(consensus["prd_digest"]),
        "source_refs": {
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "channel_prd_ref": str(consensus["prd_ref"]),
            "channel_prd_digest": str(consensus["prd_digest"]),
        },
        "artifact_refs": [{
            "kind": "channel_prd",
            "ref": str(consensus["prd_ref"]),
            "digest": str(consensus["prd_digest"]),
        }],
    }
    planned = _real_plan_to_proposal(
        state_dir=state_dir,
        project_root=project_root,
        config=config,
        backend=backend,
        authority=authority,
    )
    final_events = log.read_all()
    if any(
        event.type in {"task.created", "workflow.invoke.requested"}
        for event in final_events
    ):
        raise RuntimeError(
            "Channel provider drill crossed the Owner approval boundary"
        )
    lifecycle_request_ids = {
        *request_ids,
        *discussion_request_ids,
        *synthesis_reply_request_ids,
    }
    for request_id in lifecycle_request_ids:
        started_count = sum(
            event.type == "channel.agent.reply.started"
            and str(event.payload.get("request_id") or "") == request_id
            for event in final_events
        )
        completed_count = sum(
            event.type == "channel.agent.reply.completed"
            and str(event.payload.get("request_id") or "") == request_id
            for event in final_events
        )
        if (started_count, completed_count) != (1, 1):
            raise RuntimeError(
                "provider lifecycle was not exactly-once for "
                f"{request_id}: started={started_count}, "
                f"completed={completed_count}"
            )
    profile = next(
        item
        for item in detail.get("members") or []
        if item.get("member_id") == MEMBER_ID
    )
    return {
        "ok": True,
        "backend": backend,
        "channel_id": CHANNEL_ID,
        "request_id": requests[-1].get("request_id"),
        "request_ids": request_ids,
        "discussion_request_ids": discussion_request_ids,
        "provider_session_id": requests[-1].get("provider_session_id"),
        "reply": marker_reply.get("text"),
        "profile_digest": profile.get("profile_digest"),
        "profile_snapshot_ref": profile.get("profile_snapshot_ref"),
        "discussion_id": next(
            (
                str(event.payload.get("discussion_id") or "")
                for event in _new_events(
                    log,
                    before=before_discuss,
                    event_type="channel.discussion.started",
                )
            ),
            "",
        ),
        "prd_ref": proposed.payload.get("prd_ref"),
        "prd_digest": proposed.payload.get("prd_digest"),
        "prd_revision": proposed.payload.get("prd_revision"),
        "receipt_count": receipts.recorded,
        **planned,
        "events": len(final_events),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument(
        "--backend",
        choices=("codex", "claude-code"),
        default="codex",
    )
    parser.add_argument("--confirm-real", action="store_true")
    args = parser.parse_args()
    if not args.confirm_real:
        raise SystemExit("pass --confirm-real to invoke a real provider")
    result = run_drill(
        project_root=args.project_root.resolve(),
        state_dir=args.state_dir.resolve(),
        backend=args.backend,
    )
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
