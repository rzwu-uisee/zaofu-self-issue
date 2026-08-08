"""Goal closure aggregate payload hydration and identity propagation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.runtime.fanout_payload_data import first_child_mapping, first_child_value
from zf.runtime.goal_closure_result import (
    GoalClosureResultError,
    validate_goal_closure_result,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def apply_goal_closure_aggregate_payload(
    *,
    state_dir: Path,
    manifest: dict[str, Any],
    payloads: list[dict[str, Any]],
    payload: dict[str, Any],
) -> None:
    """Hydrate one admitted Goal closure result into its aggregate event."""

    result = first_child_mapping(manifest, payloads, "goal_closure_result")
    envelope_ref = first_child_mapping(
        manifest,
        payloads,
        "admitted_call_result_ref",
    )
    control_ref = first_child_mapping(manifest, payloads, "control_result_ref")
    result_error = ""
    if not result and control_ref:
        try:
            hydrated = hydrate_sidecar_ref(
                state_dir,
                control_ref,
                purpose="fanout-goal-closure-aggregate",
                actor="zf-cli",
            ).payload
            if isinstance(hydrated, dict):
                result = dict(hydrated)
            else:
                result_error = "goal closure control result must be an object"
        except Exception as exc:
            result_error = f"goal closure control result unavailable: {exc}"
    if result:
        try:
            validate_goal_closure_result(result)
        except GoalClosureResultError as exc:
            result_error = str(exc)
    elif not result_error:
        result_error = "goal closure result is missing"

    trigger_payload = (
        manifest.get("trigger_payload")
        if isinstance(manifest.get("trigger_payload"), dict)
        else {}
    )
    payload.update({
        "goal_closure_result": result,
        "admitted_call_result_ref": envelope_ref,
        "control_result_ref": control_ref,
    })
    if result_error:
        payload["goal_closure_result_error"] = result_error
    for key in (
        "workflow_run_id", "goal_id", "flow_kind",
        "task_map_generation", "candidate_head_commit",
        "closure_identity", "closure_fact_ref", "closure_fact_digest",
        "goal_claim_set_ref", "goal_claim_set_digest", "candidate_ref",
        "target_ref", "pdd_id", "feature_id", "operation_id",
        "request_hash", "contract_snapshot_ref", "contract_snapshot_digest",
        "target_snapshot_ref", "target_snapshot_digest",
    ):
        value = first_child_value(manifest, payloads, key)
        if value in (None, ""):
            value = result.get(key)
        if value in (None, ""):
            value = trigger_payload.get(key)
        if value not in (None, ""):
            payload[key] = value
    payload.setdefault("pdd_id", str(payload.get("goal_id") or ""))
    payload.setdefault(
        "feature_id",
        str(payload.get("pdd_id") or payload.get("goal_id") or ""),
    )


__all__ = ["apply_goal_closure_aggregate_payload"]
