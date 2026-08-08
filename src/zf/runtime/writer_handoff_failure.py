"""Ownership and bounded retry facts for writer call-result failures."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping


def writer_call_result_failure_payload(
    events: Iterable[Any],
    *,
    task_id: str,
    contract_revision: str,
    task_map_generation: str,
    call_result_status: str,
    issues: Iterable[Mapping[str, Any]],
    source_event_id: str,
) -> dict[str, Any]:
    normalized = [
        {
            key: str(issue.get(key) or "")
            for key in (
                "field",
                "code",
                "message",
                "failure_owner",
                "recovery_owner",
                "recovery_action",
            )
        }
        for issue in issues
        if isinstance(issue, Mapping)
    ]
    canonical_plan = any(
        item.get("failure_owner") == "canonical_plan"
        or item.get("recovery_action") in {"return_to_plan", "replan"}
        for item in normalized
    )
    fingerprint = _fingerprint(
        task_id=task_id,
        contract_revision=contract_revision,
        task_map_generation=task_map_generation,
        issues=normalized,
    )
    prior = _matching_failures(events, task_id=task_id, fingerprint=fingerprint)
    failure_count = len(prior) + 1
    no_progress = failure_count >= 2
    recovery_owner = "planner" if canonical_plan else "implementation_owner"
    recovery_action = "return_to_plan" if canonical_plan else "result_repair"
    redispatch_allowed = not canonical_plan and not no_progress
    reason = (
        normalized[0].get("message")
        if normalized
        else f"blocking call result was {call_result_status or 'invalid'}"
    )
    payload: dict[str, Any] = {
        "status": "failed",
        "reason": reason,
        "failure_class": (
            "canonical_plan_contract_failure"
            if canonical_plan
            else "worker_result_contract_failure"
        ),
        "failure_scope": "plan_contract" if canonical_plan else "worker_result",
        "recovery_owner": recovery_owner,
        "recovery_action": recovery_action,
        "rework_scope": "plan_contract" if canonical_plan else "result_payload",
        "call_result_status": call_result_status,
        "call_result_issues": normalized,
        "handoff_failure_fingerprint": fingerprint,
        "failure_fingerprint": fingerprint,
        "handoff_failure_count": failure_count,
        "redispatch_allowed": redispatch_allowed,
        "source_event_id": source_event_id,
        "evidence_event_ids": [
            *[
                str(getattr(event, "id", "") or "")
                for event in prior
                if str(getattr(event, "id", "") or "")
            ],
            source_event_id,
        ],
    }
    if no_progress:
        payload.update({
            "no_progress": True,
            "bounded_recovery_decision": {
                "status": "safe_halt",
                "reason": "writer_handoff_fingerprint_repeated",
                "recovery_owner": "run_manager",
                "allowed_actions": [recovery_action, "operator_review"],
                "max_additional_writer_attempts": 0,
            },
        })
    return payload


def writer_redispatch_block(
    events: Iterable[Any],
    *,
    task_id: str,
    contract_revision: str,
    task_map_generation: str,
) -> dict[str, Any]:
    """Return the latest unchanged-contract writer handoff dispatch fence."""

    for event in reversed(list(events)):
        if str(getattr(event, "type", "") or "") != "fanout.child.failed":
            continue
        payload = getattr(event, "payload", None)
        if not isinstance(payload, Mapping):
            continue
        if str(payload.get("task_id") or getattr(event, "task_id", "") or "") != task_id:
            continue
        if str(payload.get("contract_revision") or "") != contract_revision:
            continue
        if str(payload.get("task_map_generation") or "") != task_map_generation:
            continue
        if bool(payload.get("redispatch_allowed", True)):
            continue
        fingerprint = str(payload.get("handoff_failure_fingerprint") or "")
        if not fingerprint:
            continue
        return {
            "reason": "unchanged writer handoff contract is not redispatchable",
            "failure_class": "writer_handoff_redispatch_blocked",
            "failure_scope": str(payload.get("failure_scope") or "worker_result"),
            "recovery_owner": str(payload.get("recovery_owner") or "run_manager"),
            "recovery_action": str(payload.get("recovery_action") or "safe_halt"),
            "handoff_failure_fingerprint": fingerprint,
            "failure_fingerprint": fingerprint,
            "handoff_failure_count": int(payload.get("handoff_failure_count") or 1),
            "redispatch_allowed": False,
            "dispatch_suppressed": True,
            "blocked_by_event_id": str(getattr(event, "id", "") or ""),
        }
    return {}


def _matching_failures(
    events: Iterable[Any],
    *,
    task_id: str,
    fingerprint: str,
) -> list[Any]:
    return [
        event
        for event in events
        if str(getattr(event, "type", "") or "") == "fanout.child.failed"
        and isinstance(getattr(event, "payload", None), Mapping)
        and str(
            getattr(event, "task_id", "")
            or getattr(event, "payload", {}).get("task_id")
            or ""
        ) == task_id
        and str(
            getattr(event, "payload", {}).get("handoff_failure_fingerprint")
            or ""
        ) == fingerprint
    ]


def _fingerprint(
    *,
    task_id: str,
    contract_revision: str,
    task_map_generation: str,
    issues: list[dict[str, str]],
) -> str:
    body = {
        "task_id": task_id,
        "contract_revision": contract_revision,
        "task_map_generation": task_map_generation,
        "issues": sorted(
            (item.get("field", ""), item.get("code", ""))
            for item in issues
        ),
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return "writer-handoff-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


__all__ = ["writer_call_result_failure_payload", "writer_redispatch_block"]
