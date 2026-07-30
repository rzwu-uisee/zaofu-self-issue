"""Payload projection for admitted Refactor plan-to-task-map bridging."""

from __future__ import annotations

from typing import Any, Mapping


_IDENTITY_KEYS = (
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
    "project_adapter_ref",
    "skill_adapter_plan_ref",
)


def build_refactor_plan_bridge_payload(
    *,
    manifest: Mapping[str, Any],
    projection_payload: Mapping[str, Any],
    trace_id: str,
    task_map_ref: str,
    replan_payload: Mapping[str, Any],
) -> dict[str, Any]:
    def pick(key: str) -> str:
        return str(
            projection_payload.get(key) or manifest.get(key) or ""
        ).strip()

    payload: dict[str, Any] = {
        "pdd_id": pick("pdd_id") or pick("feature_id"),
        "feature_id": pick("feature_id") or pick("pdd_id"),
        "trace_id": trace_id,
        "workflow_run_id": pick("workflow_run_id") or trace_id,
        "flow_kind": pick("flow_kind") or "refactor",
        "request_kind": pick("request_kind") or "refactor",
        "task_map_ref": task_map_ref,
        "source_index_ref": pick("source_index_ref"),
        "source_commit": pick("source_commit"),
        "candidate_base_commit": (
            pick("candidate_base_commit") or pick("source_commit")
        ),
        "target_ref": pick("target_ref"),
        "source": "refactor_plan_bridge",
        **replan_payload,
    }
    for key in _IDENTITY_KEYS:
        value = projection_payload.get(key)
        if value not in (None, "", {}):
            payload[key] = value
    return payload
