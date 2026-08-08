"""Resolve OA checkpoint authority for the current workflow route."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any


_FLOW_ALIASES = {
    "feat": "prd",
    "feature": "prd",
    "general": "workflow",
}
_FLOW_KINDS = frozenset({"issue", "prd", "refactor", "workflow", "research"})
_NESTED_CONTEXT_KEYS = (
    "payload",
    "metadata",
    "evidence_contract",
    "source_refs",
    "contract",
    "workflow",
)
_RESEARCH_MARKER_KEYS = (
    "event_type",
    "stage_id",
    "pattern_id",
    "workflow_invoke_pattern_id",
    "template_id",
    "success_event",
    "failure_event",
    "workflow_intent",
    "request_kind",
)
_FLOW_KIND_KEYS = ("flow_kind", "goal_kind", "request_kind")


@dataclass(frozen=True)
class ShadowCheckpointSelection:
    selected: bool
    sample_percent: int
    bucket: int
    reason: str
    risk_signals: tuple[str, ...] = ()


def checkpoint_policy(
    config: Any,
    checkpoint: str,
    *,
    flow_kind: str = "",
) -> str:
    """Return the effective checkpoint policy for one normalized flow."""

    policy = _effective_policy(config, flow_kind)
    if str(getattr(policy, "mode", "exception_advisor")) != "semantic_control":
        return ""
    checkpoints = list(getattr(policy, "checkpoints", []) or [])
    if checkpoint not in checkpoints:
        return ""
    configured = dict(getattr(policy, "checkpoint_policies", {}) or {})
    value = str(configured.get(checkpoint) or "blocking")
    return value if value in {"shadow", "blocking"} else ""


def shadow_checkpoint_selection(
    config: Any,
    checkpoint: str,
    *,
    workflow_run_id: str,
    revision: str,
    flow_kind: str = "",
    payload: Mapping[str, Any] | None = None,
) -> ShadowCheckpointSelection:
    """Deterministically sample low-risk shadow reviews; never sample gates."""

    policy = _effective_policy(config, flow_kind)
    percent = int(getattr(policy, "shadow_sample_percent", 100) or 0)
    risk_signals = checkpoint_risk_signals(checkpoint, payload or {})
    seed = "\0".join((
        normalize_orchestration_flow_kind(flow_kind),
        str(workflow_run_id or ""),
        str(checkpoint or ""),
        str(revision or ""),
    ))
    bucket = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16) % 100
    if risk_signals:
        return ShadowCheckpointSelection(
            True,
            percent,
            bucket,
            "risk_override",
            risk_signals,
        )
    if percent >= 100:
        return ShadowCheckpointSelection(True, percent, bucket, "sample_all")
    if bucket < percent:
        return ShadowCheckpointSelection(True, percent, bucket, "sample_selected")
    return ShadowCheckpointSelection(
        False,
        percent,
        bucket,
        "shadow_sample_not_selected",
    )


def _effective_policy(config: Any, flow_kind: str) -> Any:
    workflow = getattr(config, "workflow", None)
    root = getattr(workflow, "orchestration", None)
    policy = root
    normalized = normalize_orchestration_flow_kind(flow_kind)
    overrides = getattr(root, "flow_policies", {}) or {}
    if normalized and normalized in overrides:
        policy = overrides[normalized]
    return policy


def checkpoint_risk_signals(
    checkpoint: str,
    payload: Mapping[str, Any],
) -> tuple[str, ...]:
    if checkpoint != "plan_candidate":
        return (f"checkpoint:{checkpoint}",)
    signals: list[str] = []
    risk_level = str(payload.get("risk_level") or "").strip().lower()
    if risk_level in {"high", "critical"}:
        signals.append(f"risk_level:{risk_level}")
    for key in (
        "failure_fingerprint",
        "feedback_revision",
        "rework_feedback_ref",
    ):
        if str(payload.get(key) or "").strip():
            signals.append(key)
    for key in (
        "admission_errors",
        "open_gap_refs",
        "unclosed_claim_ids",
    ):
        value = payload.get(key)
        if isinstance(value, (list, tuple)) and value:
            signals.append(key)
    revision = str(payload.get("plan_revision") or "").strip()
    if revision.isdigit() and int(revision) > 1:
        signals.append("revised_plan")
    return tuple(sorted(set(signals)))


def orchestration_flow_kind(*sources: Any) -> str:
    """Infer the route; fixed Research markers override generic workflow tags."""

    rows = [_context_mapping(source) for source in sources]
    if any(_has_research_marker(row) for row in rows):
        return "research"
    for row in rows:
        for candidate in _flow_kind_candidates(row):
            normalized = normalize_orchestration_flow_kind(candidate)
            if normalized:
                return normalized
    return ""


def normalize_orchestration_flow_kind(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    normalized = _FLOW_ALIASES.get(normalized, normalized)
    return normalized if normalized in _FLOW_KINDS else ""


def _context_mapping(source: Any) -> dict[str, Any]:
    if isinstance(source, Mapping):
        return dict(source)
    if source is None:
        return {}
    row = {
        key: getattr(source, key)
        for key in (*_FLOW_KIND_KEYS, *_RESEARCH_MARKER_KEYS)
        if getattr(source, key, None) not in (None, "")
    }
    payload = getattr(source, "payload", None)
    if isinstance(payload, Mapping):
        row["payload"] = dict(payload)
    event_type = getattr(source, "type", None)
    if event_type:
        row["event_type"] = str(event_type)
    evidence = getattr(source, "evidence_contract", None)
    if isinstance(evidence, Mapping):
        row["evidence_contract"] = dict(evidence)
    return row


def _has_research_marker(row: Mapping[str, Any]) -> bool:
    for key in _RESEARCH_MARKER_KEYS:
        value = str(row.get(key) or "").strip().lower()
        if value == "research" or value.startswith(("research-", "research.")):
            return True
    return any(
        _has_research_marker(value)
        for key in _NESTED_CONTEXT_KEYS
        if isinstance((value := row.get(key)), Mapping)
    )


def _flow_kind_candidates(row: Mapping[str, Any]):
    for key in _FLOW_KIND_KEYS:
        value = row.get(key)
        if value not in (None, ""):
            yield value
    for key in _NESTED_CONTEXT_KEYS:
        nested = row.get(key)
        if isinstance(nested, Mapping):
            yield from _flow_kind_candidates(nested)


__all__ = [
    "ShadowCheckpointSelection",
    "checkpoint_risk_signals",
    "checkpoint_policy",
    "normalize_orchestration_flow_kind",
    "orchestration_flow_kind",
    "shadow_checkpoint_selection",
]
