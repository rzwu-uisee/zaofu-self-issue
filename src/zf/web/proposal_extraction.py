"""Headless action-proposal extraction — kanban agent LLM output -> proposal.

Moved out of ``zf/web/server.py`` (which imports fastapi at module top) so
non-web consumers — the Feishu-bound kanban agent conversation in
``zf/integrations/feishu/agent_conversation.py`` — extract proposals through
the exact same gates as the Web panel: a dedicated final JSON envelope,
agent-declared semantic intent bound to the source message, canonical action
names, the KANBAN_AGENT_ALLOWED_ACTIONS surface, contract shape normalization,
and the empty-contract guard.

Payload validation stays injectable: the Web server passes its full
``_validate_action_payload`` (config-aware, ~270 lines, not portable); other
callers pass a lighter validator or rely on the built-in title check.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from zf.core.security.redaction import redact_obj
from zf.runtime.kanban_proposals import proposal_payload_digest
from zf.web.operator_contract import KANBAN_AGENT_ALLOWED_ACTIONS, canonical_action
from zf.web.projections.common import normalize_proposed_task_contract


_SEMANTIC_INTENT_REQUIRED_ACTIONS = frozenset({
    "create-task",
    "idea-to-product",
})
_FINAL_ACTION_FENCE = re.compile(
    r"```(?:json)?[ \t]*\r?\n?((?:(?!```)[\s\S])*?)```[ \t]*$",
    re.IGNORECASE,
)


def default_validate_payload(action: str, payload: dict[str, Any]) -> str:
    """Minimal portable validation: mirrors the controlled-action hard gate."""
    if action == "create-task":
        if not str(payload.get("title") or "").strip():
            return "title is required"
        execution_mode = str(payload.get("execution_mode") or "").strip()
        if execution_mode and execution_mode not in {"direct", "workflow"}:
            return "execution_mode must be direct or workflow"
        if execution_mode == "direct" and payload.get("workflow_plan") is not None:
            return "execution_mode direct cannot include workflow_plan"
        if str(payload.get("request_id") or "").strip():
            try:
                request_revision = int(payload.get("request_revision"))
            except (TypeError, ValueError):
                request_revision = 0
            if request_revision < 1:
                return "request_revision must be a positive integer"
            if execution_mode == "direct":
                return "workflow Request binding requires execution_mode workflow"
    if action == "idea-to-product" and not any(
        str(payload.get(key) or "").strip()
        for key in ("objective", "message", "title")
    ):
        return "objective or message is required"
    if action == "channel-create-from-template" and not str(
        payload.get("template_id") or ""
    ).strip():
        return "template_id is required"
    if action == "channel-create-and-start":
        if not str(payload.get("template_id") or "").strip():
            return "template_id is required"
        if not any(
            str(payload.get(key) or "").strip()
            for key in ("objective", "message", "text")
        ):
            return "objective, message, or text is required"
    if action in {"workflow-start", "task-workflow-start"}:
        return "workflow-start must originate from a task_workflow Plan"
    if action == "channel-discussion-start":
        if not str(payload.get("channel_id") or "").strip():
            return "channel_id is required"
        if not any(
            str(payload.get(key) or "").strip()
            for key in ("objective", "message", "text")
        ):
            return "objective, message, or text is required"
    if action == "channel-invite-member":
        if not str(payload.get("channel_id") or "").strip():
            return "channel_id is required"
        if not str(payload.get("member_id") or "").strip():
            return "member_id is required"
        if not str(payload.get("profile_id") or "").strip():
            return "profile_id is required for roster proposals"
    if action == "channel-remove-member":
        if not str(payload.get("channel_id") or "").strip():
            return "channel_id is required"
        if not str(payload.get("member_id") or "").strip():
            return "member_id is required"
    if action in {
        "channel-delete",
        "channel-clear-history",
        "channel-mark-read",
        "channel-pin-message",
    } and not str(payload.get("channel_id") or "").strip():
        return "channel_id is required"
    if action == "channel-pin-message" and not str(
        payload.get("message_id") or ""
    ).strip():
        return "message_id is required"
    if action == "channel-set-leader":
        if not str(payload.get("channel_id") or "").strip():
            return "channel_id is required"
        if not str(payload.get("leader_member_id") or "").strip():
            return "leader_member_id is required"
        try:
            expected_revision = int(payload.get("expected_revision"))
        except (TypeError, ValueError):
            expected_revision = -1
        if expected_revision < 0:
            return "expected_revision must be a non-negative integer"
    if action == "workflow-invoke":
        if not str(payload.get("task_id") or "").strip():
            return "task_id is required"
        if not str(payload.get("pattern_id") or "").strip():
            return "pattern_id is required"
    if action == "research-start":
        if not str(payload.get("task_id") or "").strip():
            return "task_id is required"
        if not any(
            str(payload.get(key) or "").strip()
            for key in ("topic", "objective", "message")
        ):
            return "topic, objective, or message is required"
        if str(payload.get("channel_id") or "").strip():
            if not str(payload.get("request_id") or "").strip():
                return "channel-bound research requires request_id"
            try:
                request_revision = int(payload.get("request_revision"))
            except (TypeError, ValueError):
                request_revision = 0
            if request_revision < 1:
                return "channel-bound research requires request_revision"
    if action == "research-adopt":
        for key in (
            "result_event_id",
            "request_id",
            "artifact_ref",
            "artifact_digest",
            "summary",
        ):
            if not str(payload.get(key) or "").strip():
                return f"{key} is required"
        try:
            request_revision = int(payload.get("request_revision"))
        except (TypeError, ValueError):
            request_revision = 0
        if request_revision < 1:
            return "request_revision must be a positive integer"
    return ""


def json_candidates(text: str) -> list[str]:
    """Broad JSON candidates used by the resumable Plan parser.

    Action proposals use the narrower ``action_json_candidates`` contract
    below. Plan extraction keeps its compatibility surface independently.
    """
    stripped = str(text or "").strip()
    candidates: list[str] = []
    if stripped:
        candidates.append(stripped)
    for marker in ("```json", "```JSON", "```"):
        start = stripped.find(marker)
        while start >= 0:
            body_start = start + len(marker)
            end = stripped.find("```", body_start)
            if end < 0:
                break
            body = stripped[body_start:end].strip()
            if body:
                candidates.append(body)
            start = stripped.find(marker, end + 3)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if 0 <= start < end:
        candidates.append(stripped[start:end + 1])
    return candidates


def action_json_candidates(text: str) -> list[str]:
    """Return only a dedicated action envelope, never JSON embedded in prose."""
    stripped = str(text or "").strip()
    candidates: list[str] = []
    if stripped.startswith("{"):
        candidates.append(stripped)
    fenced = _FINAL_ACTION_FENCE.search(stripped)
    if fenced:
        body = fenced.group(1).strip()
        if body and body not in candidates:
            candidates.append(body)
    return candidates


def _decode_action_candidate(candidate: str) -> Any | None:
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # Preserve the real-provider recovery for a complete object followed by
        # one or more stray closing braces. Other trailing text is not a
        # dedicated final envelope and must not be interpreted as an action.
        try:
            decoded, end = json.JSONDecoder().raw_decode(candidate)
        except json.JSONDecodeError:
            return None
        trailing = candidate[end:].strip()
        if trailing and set(trailing) != {"}"}:
            return None
        return decoded


def extract_action_proposal(
    answer: str,
    *,
    user_message: str = "",
    proposal_context: dict[str, Any] | None = None,
    validate_payload: Callable[[str, dict[str, Any]], str] | None = None,
) -> dict[str, Any] | None:
    for candidate in action_json_candidates(answer):
        decoded = _decode_action_candidate(candidate)
        if decoded is None:
            continue
        proposal = normalize_action_proposal(
            decoded,
            user_message=user_message,
            proposal_context=proposal_context or {},
            validate_payload=validate_payload,
        )
        if proposal is not None:
            return proposal
    return None


def normalize_action_proposal(
    decoded: Any,
    *,
    user_message: str = "",
    proposal_context: dict[str, Any] | None = None,
    validate_payload: Callable[[str, dict[str, Any]], str] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(decoded, dict):
        return None
    proposal = decoded.get("action_proposal")
    if not isinstance(proposal, dict):
        return None
    requested_action = str(
        proposal.get("action")
        or proposal.get("requested_action")
        or proposal.get("name")
        or ""
    ).strip()
    if not requested_action:
        return None
    action = canonical_action(requested_action)
    if action not in KANBAN_AGENT_ALLOWED_ACTIONS:
        return None
    if action in {"chat-orchestrator", "start-operator-session"}:
        return None
    intent, intent_error = _normalize_agent_intent(
        proposal,
        user_message=user_message,
        proposal_context=proposal_context or {},
        required=action in _SEMANTIC_INTENT_REQUIRED_ACTIONS,
    )
    payload = proposal.get("payload") or proposal.get("params") or {}
    if not isinstance(payload, dict):
        return None
    payload = dict(payload)
    for key, value in (proposal_context or {}).items():
        if value and not payload.get(key):
            payload[key] = value
    if action in {"create-task", "update-task", "idea-to-product"}:
        payload = normalize_proposed_task_contract(payload)
    validator = validate_payload or default_validate_payload
    validation_errors = [
        error
        for error in (
            intent_error,
            validator(action, payload),
        )
        if error
    ]
    if not validation_errors and action in {"create-task", "idea-to-product"}:
        # chat-e2e F3: a contract whose semantic fields all normalized away
        # (e.g. every criterion sat in an unknown key) must not sail through
        # as valid — the task would land with no behavior/verification.
        contract = payload.get("contract")
        if (
            isinstance(contract, dict)
            and contract
            and not str(contract.get("behavior") or "").strip()
            and not str(contract.get("verification") or "").strip()
        ):
            validation_errors.append(
                "contract has no behavior/verification after normalization"
            )
    validation_error = "; ".join(validation_errors)
    proposal_digest = proposal_payload_digest(action, payload)
    try:
        revision = max(1, int(proposal.get("revision") or 1))
    except (TypeError, ValueError):
        revision = 1
    normalized = {
        "proposal_id": f"proposal-{proposal_digest[:24]}",
        "proposal_digest": proposal_digest,
        "revision": revision,
        "expires_at": str(proposal.get("expires_at") or ""),
        "supersedes": str(proposal.get("supersedes") or ""),
        "action": action,
        "requested_action": requested_action,
        "payload": redact_obj(payload),
        "reason": str(proposal.get("reason") or proposal.get("summary") or ""),
        "confidence": str(proposal.get("confidence") or ""),
        "valid": not validation_error,
        "validation_error": validation_error,
        "mutates_task_state": action in {
            "create-task",
            "update-task",
            "archive-task",
            "link-evidence",
        },
    }
    if intent:
        normalized["intent"] = redact_obj(intent)
    return normalized


def _normalize_agent_intent(
    proposal: dict[str, Any],
    *,
    user_message: str,
    proposal_context: dict[str, Any],
    required: bool,
) -> tuple[dict[str, str], str]:
    raw = proposal.get("intent")
    if raw is None and not required:
        return {}, ""
    if not isinstance(raw, dict):
        return {}, "intent is required and must be a mapping"

    errors: list[str] = []
    unknown = sorted(set(raw) - {"decision", "source_quote"})
    if unknown:
        errors.append("unsupported intent field(s): " + ", ".join(unknown))
    decision = str(raw.get("decision") or "").strip()
    source_quote = str(raw.get("source_quote") or "").strip()
    if decision != "propose_action":
        errors.append("intent.decision must be propose_action")
    if not source_quote:
        errors.append("intent.source_quote is required")
    elif not user_message or source_quote not in user_message:
        errors.append(
            "intent.source_quote must occur verbatim in the user semantic context"
        )

    intent = {
        "decision": decision,
        "source_quote": source_quote,
    }
    source_message_event_id = str(
        proposal_context.get("causation_id")
        or proposal_context.get("source_message_event_id")
        or ""
    ).strip()
    if source_message_event_id:
        intent["source_message_event_id"] = source_message_event_id
    return intent, "; ".join(errors)
