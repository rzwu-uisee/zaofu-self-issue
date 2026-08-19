"""Read-only operator attention derived from Channel discussion facts."""

from __future__ import annotations

from typing import Any


_ACTIVE_REPLY_STATUSES = {"pending", "queued", "running", "started"}
_QUEUED_REPLY_STATUSES = {"pending", "queued"}
_RUNNING_REPLY_STATUSES = {"running", "started"}
_FAILED_REPLY_STATUSES = {"failed", "rejected", "escalated"}


def project_discussion_attention(
    channel: dict[str, Any],
    reply_requests: list[dict[str, Any]],
    owner_questionnaires: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Derive actionable UI state without becoming discussion truth."""
    thread_ids = {
        str(thread_id)
        for thread_id in channel["discussions"]
    } | {
        str(thread_id)
        for thread_id in channel["consensus"]
    } | {
        str(item.get("thread_id") or "main")
        for key in ("open_questions", "synthesis_requests", "syntheses")
        for item in (
            channel[key].values()
            if isinstance(channel[key], dict)
            else channel[key]
        )
        if isinstance(item, dict)
    }
    projection: dict[str, dict[str, Any]] = {}

    for thread_id in sorted(thread_ids):
        session = channel["discussions"].get(thread_id)
        if not isinstance(session, dict):
            session = {}
        started_at = str(session.get("started_at") or "")

        def current_session_item(
            item: dict[str, Any],
            *,
            time_keys: tuple[str, ...],
        ) -> bool:
            if str(item.get("thread_id") or "main") != thread_id:
                return False
            item_time = next(
                (str(item.get(key) or "") for key in time_keys if item.get(key)),
                "",
            )
            return not started_at or not item_time or item_time >= started_at

        replies = [
            item for item in reply_requests
            if current_session_item(
                item,
                time_keys=("created_at", "updated_at", "ts"),
            )
        ]
        questions = [
            item for item in channel["open_questions"].values()
            if isinstance(item, dict)
            and str(item.get("thread_id") or "main") == thread_id
        ]
        open_questions = [
            item for item in questions
            if str(item.get("status") or "") == "open"
        ]
        owner_questions = owner_questionnaires.get(thread_id, [])
        synthesis_requests = [
            item for item in channel["synthesis_requests"]
            if isinstance(item, dict)
            and current_session_item(item, time_keys=("ts",))
        ]
        syntheses = [
            item for item in channel["syntheses"]
            if isinstance(item, dict)
            and current_session_item(item, time_keys=("ts",))
        ]
        consensus = channel["consensus"].get(thread_id)
        if not isinstance(consensus, dict):
            consensus = {}

        statuses = [str(item.get("status") or "") for item in replies]
        active_replies = [
            item for item in replies
            if str(item.get("status") or "") in _ACTIVE_REPLY_STATUSES
        ]
        active_agents = {
            str(item.get("target_member_id") or item.get("member_id") or "")
            for item in active_replies
        } - {""}
        failed_count = sum(
            status in _FAILED_REPLY_STATUSES for status in statuses
        )
        phase = str(session.get("state") or "idle")
        outcome = str(session.get("last_outcome") or "")
        latest_synthesis_status = str(
            synthesis_requests[-1].get("status") or ""
        ) if synthesis_requests else ""
        consensus_time = str(consensus.get("ts") or "")
        consensus_is_current = bool(consensus) and (
            not started_at
            or not consensus_time
            or consensus_time >= started_at
        )
        consensus_reached = (
            consensus_is_current and bool(consensus.get("reached_event_id"))
        )
        owner_confirmed = (
            consensus_is_current and bool(consensus.get("human_confirmed"))
        )
        owner_confirmation_required = bool(
            consensus_is_current
            and consensus.get("artifact_ref")
            and not consensus_reached
            and not owner_confirmed
        )

        state, reason = _attention_state(
            phase=phase,
            outcome=outcome,
            owner_questions=bool(owner_questions),
            owner_confirmation_required=owner_confirmation_required,
            active_replies=bool(active_replies),
            failed_replies=failed_count,
            synthesis_status=latest_synthesis_status,
            has_synthesis=bool(syntheses),
            consensus_reached=consensus_reached,
            owner_confirmed=owner_confirmed,
            open_questions=bool(open_questions),
        )
        next_action = _next_action(
            state,
            reason=reason,
            has_result=bool(syntheses or consensus),
        )
        activity_times = [
            str(session.get(key) or "")
            for key in ("started_at", "phase_changed_at", "closed_at")
        ]
        if consensus_is_current:
            activity_times.append(consensus_time)
        for item in [*replies, *questions, *synthesis_requests, *syntheses]:
            activity_times.extend(
                str(item.get(key) or "")
                for key in ("updated_at", "resolved_at", "created_at", "ts")
            )
        activity_times.extend(
            str(item.get("ts") or "")
            for item in channel["question_activity"]
            if isinstance(item, dict)
            and str(item.get("thread_id") or "main") == thread_id
        )

        projection[thread_id] = {
            "schema_version": "channel.discussion-attention.v1",
            "is_derived_projection": True,
            "thread_id": thread_id,
            "state": state,
            "reason": reason,
            "next_action": next_action,
            "kernel_phase": phase,
            "last_outcome": outcome,
            "participant_count": len(session.get("roster") or []),
            "active_agent_count": len(active_agents),
            "active_reply_count": len(active_replies),
            "queued_reply_count": sum(
                status in _QUEUED_REPLY_STATUSES for status in statuses
            ),
            "running_reply_count": sum(
                status in _RUNNING_REPLY_STATUSES for status in statuses
            ),
            "completed_reply_count": sum(
                status == "completed" for status in statuses
            ),
            "failed_reply_count": failed_count,
            "open_question_count": len(open_questions),
            "owner_question_count": len(owner_questions),
            "total_question_count": len(questions),
            "resolved_question_count": len(questions) - len(open_questions),
            "last_activity_at": max(
                (item for item in activity_times if item),
                default="",
            ),
            "can_drain_replies": bool(active_replies),
            "can_synthesize": state == "ready",
            "can_restart": state == "blocked",
            "can_review_questions": bool(owner_questions),
            "can_review_result": reason == "owner_confirmation_required",
            "can_view_activity": bool(
                session
                or replies
                or questions
                or syntheses
                or synthesis_requests
                or consensus
            ),
        }
    return projection


def _attention_state(
    *,
    phase: str,
    outcome: str,
    owner_questions: bool,
    owner_confirmation_required: bool,
    active_replies: bool,
    failed_replies: int,
    synthesis_status: str,
    has_synthesis: bool,
    consensus_reached: bool,
    owner_confirmed: bool,
    open_questions: bool,
) -> tuple[str, str]:
    if owner_questions:
        return "needs_input", "owner_questions_open"
    if owner_confirmation_required:
        return "needs_input", "owner_confirmation_required"
    if phase == "idle" and outcome == "consensus":
        return "done", "discussion_completed"
    if phase == "idle" and outcome == "stalled":
        return "blocked", "discussion_stalled"
    if active_replies:
        return "running", "replies_active"
    if failed_replies or synthesis_status == "blocked":
        return (
            "blocked",
            "reply_failures" if failed_replies else "synthesis_blocked",
        )
    if phase == "phase2_relay":
        return (
            ("blocked", "unresolved_questions")
            if open_questions
            else ("ready", "synthesis_ready")
        )
    if phase == "phase3_synthesis":
        if has_synthesis and not consensus_reached and not owner_confirmed:
            return "needs_input", "owner_confirmation_required"
        if consensus_reached:
            return "done", "discussion_completed"
        if synthesis_status in {"requested", "repair_requested"}:
            return "running", "synthesis_active"
        if open_questions:
            return "blocked", "unresolved_questions"
        return "running", "synthesis_active"
    if phase in {"active", "phase1_blind"}:
        return "running", "discussion_active"
    if open_questions:
        return "blocked", "unresolved_questions"
    return "done", "discussion_idle"


def _next_action(state: str, *, reason: str, has_result: bool) -> str:
    if state == "needs_input":
        return (
            "review_questions"
            if reason == "owner_questions_open"
            else "review_result"
        )
    return {
        "running": "view_activity",
        "ready": "synthesize",
        "blocked": "restart",
        "done": "view_result" if has_result else "none",
    }[state]
