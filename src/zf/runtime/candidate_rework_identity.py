"""Candidate rework identity and generation guards."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zf.core.events.module_parity import is_module_parity_scan_completed_event


_AUTHORITATIVE_GENERATION_EVENTS = frozenset({
    "task_map.ready",
    "product.delivery.wave.ready",
    "candidate.ready",
    "fanout.started",
})
_CURRENT_GENERATION_EVENTS = frozenset({
    "task_map.ready",
    "product.delivery.wave.ready",
    "candidate.ready",
})

_CANDIDATE_REWORK_IDENTITY_KEYS = (
    "workflow_run_id",
    "flow_kind",
    "request_kind",
    "request_id",
    "requirement_spec_ref",
    "requirement_spec_digest",
    "workflow_proposal_ref",
    "workflow_proposal_digest",
    "effective_config_ref",
    "effective_config_digest",
    "run_contract_ref",
    "run_contract_digest",
    "plan_artifact_package_id",
    "plan_artifact_package_ref",
    "plan_artifact_package_digest",
    "goal_claim_set_ref",
    "goal_claim_set_digest",
    "artifact_package_mode",
    "artifact_package_status",
    "plan_revision",
    "task_map_generation",
    "source_commit",
    "candidate_base_commit",
    "candidate_ref",
    "candidate_head_commit",
    "project_adapter_ref",
    "skill_adapter_plan_ref",
)

_TASK_MAP_GENERATION_BOUND_IDENTITY_KEYS = (
    "plan_artifact_package_id",
    "plan_artifact_package_ref",
    "plan_artifact_package_digest",
    "goal_claim_set_ref",
    "goal_claim_set_digest",
    "artifact_package_status",
    "plan_revision",
    "task_map_generation",
)


@dataclass(frozen=True)
class _CandidateClosure:
    index: int
    event_id: str
    event_type: str
    pdd_id: str
    trace_id: str
    target_ref: str
    candidate_ref: str


def _candidate_rework_identity_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    return {
        key: payload[key]
        for key in _CANDIDATE_REWORK_IDENTITY_KEYS
        if payload.get(key) not in (None, "", [], {})
    }


def _candidate_rework_amended_identity_payload(
    payload: object,
    *,
    task_map_generation: str,
) -> dict[str, Any]:
    identity = _candidate_rework_identity_payload(payload)
    for key in _TASK_MAP_GENERATION_BOUND_IDENTITY_KEYS:
        identity.pop(key, None)
    if task_map_generation:
        identity["task_map_generation"] = task_map_generation
    return identity


def _pdd_from_event(
    payload: dict,
    target_ref: str,
    *,
    pdd_by_fanout_id: dict[str, str] | None = None,
) -> str:
    pdd = str(payload.get("pdd_id") or "").strip()
    if pdd:
        return pdd
    fanout_id = str(payload.get("fanout_id") or "").strip()
    if fanout_id and pdd_by_fanout_id:
        fanout_pdd = str(pdd_by_fanout_id.get(fanout_id) or "").strip()
        if fanout_pdd:
            return fanout_pdd
    # candidate target_ref looks like "<candidate-prefix>/<PDD>"; the PDD is
    # the last path segment.
    return target_ref.rsplit("/", 1)[-1].strip() if target_ref else ""


def _candidate_generation_stale(
    events: list,
    *,
    event_idx: int,
    event: object,
    payload: dict[str, Any],
    pdd_by_fanout_id: dict[str, str],
    ignored_event_ids: set[str] | None = None,
) -> bool:
    """A later authoritative run/generation makes this failure audit-only."""

    workflow_run_id = str(payload.get("workflow_run_id") or "").strip()
    generation = str(payload.get("task_map_generation") or "").strip()
    if not (workflow_run_id or generation):
        return False
    pdd_id = _pdd_from_event(
        payload,
        _candidate_scope_ref(payload),
        pdd_by_fanout_id=pdd_by_fanout_id,
    )
    for current in reversed(events[:event_idx]):
        if str(getattr(current, "type", "") or "") not in (
            _CURRENT_GENERATION_EVENTS
        ):
            continue
        current_payload = getattr(current, "payload", {}) or {}
        if not isinstance(current_payload, dict):
            continue
        current_pdd = _pdd_from_event(
            current_payload,
            _candidate_scope_ref(current_payload),
            pdd_by_fanout_id=pdd_by_fanout_id,
        )
        if pdd_id and current_pdd and pdd_id != current_pdd:
            continue
        current_run = str(
            current_payload.get("workflow_run_id") or ""
        ).strip()
        current_generation = str(
            current_payload.get("task_map_generation") or ""
        ).strip()
        if workflow_run_id and current_run and workflow_run_id != current_run:
            return True
        if generation and current_generation:
            return generation != current_generation
    for later in events[event_idx + 1:]:
        if str(getattr(later, "id", "") or "") in (ignored_event_ids or set()):
            continue
        if str(getattr(later, "type", "") or "") not in _AUTHORITATIVE_GENERATION_EVENTS:
            continue
        later_payload = getattr(later, "payload", {}) or {}
        if not isinstance(later_payload, dict):
            continue
        later_pdd = _pdd_from_event(
            later_payload,
            _candidate_scope_ref(later_payload),
            pdd_by_fanout_id=pdd_by_fanout_id,
        )
        if pdd_id and later_pdd and pdd_id != later_pdd:
            continue
        later_run = str(later_payload.get("workflow_run_id") or "").strip()
        later_generation = str(
            later_payload.get("task_map_generation") or ""
        ).strip()
        if workflow_run_id and later_run and workflow_run_id != later_run:
            return True
        if generation and later_generation and generation != later_generation:
            return True
    return False


def _candidate_success_closures(
    events: list,
    *,
    pdd_by_fanout_id: dict[str, str],
) -> list[_CandidateClosure]:
    closures: list[_CandidateClosure] = []
    for idx, event in enumerate(events):
        event_type = str(getattr(event, "type", "") or "")
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            payload = {}
        if not _is_candidate_success_closure(event_type, payload):
            continue
        target_ref = _candidate_scope_ref(payload)
        closures.append(_CandidateClosure(
            index=idx,
            event_id=str(getattr(event, "id", "") or ""),
            event_type=event_type,
            pdd_id=_pdd_from_event(
                payload,
                target_ref,
                pdd_by_fanout_id=pdd_by_fanout_id,
            ),
            trace_id=str(
                payload.get("trace_id")
                or getattr(event, "correlation_id", "")
                or ""
            ).strip(),
            target_ref=target_ref,
            candidate_ref=str(payload.get("candidate_ref") or "").strip(),
        ))
    return closures


def _candidate_failure_superseded(
    event: object,
    payload: dict[str, Any],
    event_idx: int,
    *,
    pdd_by_fanout_id: dict[str, str],
    success_closures: list[_CandidateClosure],
) -> bool:
    if not success_closures:
        return False
    target_ref = _candidate_scope_ref(payload)
    failure_pdd = _pdd_from_event(
        payload,
        target_ref,
        pdd_by_fanout_id=pdd_by_fanout_id,
    )
    failure_trace = str(
        payload.get("trace_id") or getattr(event, "correlation_id", "") or ""
    ).strip()
    failure_candidate = str(payload.get("candidate_ref") or "").strip()
    if not (failure_pdd or failure_trace or target_ref or failure_candidate):
        return False
    for closure in success_closures:
        if closure.index <= event_idx:
            continue
        if _candidate_closure_matches_failure(
            closure,
            pdd_id=failure_pdd,
            trace_id=failure_trace,
            target_ref=target_ref,
            candidate_ref=failure_candidate,
        ):
            return True
    return False


def _is_candidate_success_closure(
    event_type: str,
    payload: dict[str, Any],
) -> bool:
    if (
        event_type == "candidate.ready"
        and str(payload.get("source") or "") == "workflow_resume_batch"
    ):
        return False
    if is_module_parity_scan_completed_event(event_type):
        return (
            "open_p0_p1_gap_count" in payload
            and _safe_int(payload.get("open_p0_p1_gap_count")) == 0
        )
    if event_type == "module.parity.closed":
        return True
    return event_type in {
        "candidate.ready",
        "candidate.quality.passed",
        "verify.passed",
        "judge.passed",
    }


def _candidate_closure_matches_failure(
    closure: _CandidateClosure,
    *,
    pdd_id: str,
    trace_id: str,
    target_ref: str,
    candidate_ref: str,
) -> bool:
    closure_refs = {
        ref
        for ref in (closure.target_ref, closure.candidate_ref)
        if ref
    }
    failure_refs = {
        ref
        for ref in (target_ref, candidate_ref)
        if ref
    }
    pdd_match = bool(pdd_id and closure.pdd_id and pdd_id == closure.pdd_id)
    trace_match = bool(trace_id and closure.trace_id and trace_id == closure.trace_id)
    ref_match = bool(closure_refs and failure_refs and closure_refs & failure_refs)
    if pdd_id and closure.pdd_id and not pdd_match:
        return False
    if pdd_match and (trace_match or ref_match or not trace_id or not closure.trace_id):
        return True
    if trace_match and (ref_match or not pdd_id or not closure.pdd_id):
        return True
    return ref_match and (pdd_match or trace_match)


def _pdd_by_fanout_id(events: list) -> dict[str, str]:
    out: dict[str, str] = {}
    for event in events:
        if getattr(event, "type", "") != "fanout.started":
            continue
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            continue
        fanout_id = str(payload.get("fanout_id") or "").strip()
        fanout_pdd = str(payload.get("pdd_id") or "").strip()
        if fanout_id and fanout_pdd:
            out[fanout_id] = fanout_pdd
    return out


def _candidate_scope_ref(payload: dict[str, Any]) -> str:
    return str(
        payload.get("target_ref")
        or payload.get("candidate_ref")
        or payload.get("branch")
        or ""
    ).strip()


def _safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
