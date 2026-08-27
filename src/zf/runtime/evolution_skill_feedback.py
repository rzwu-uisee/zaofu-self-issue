"""Observed Skill outcome attribution and bounded automatic revocation."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.evolution_contracts import EvolutionContractError, stable_digest
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.skill_invocation_projection import project_skill_invocations


def record_skill_outcome(
    coordinator: Any,
    *,
    asset_id: str,
    version: int,
    skill_name: str,
    task_id: str,
    role_instance: str,
    outcome: str,
    cost: Mapping[str, Any],
    feedback: Mapping[str, Any] | None = None,
    config: Any | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Credit a Skill asset only when invocation evidence is observable."""

    key = f"{asset_id}@{int(version)}"
    asset = coordinator.capabilities.load()["assets"].get(key)
    if not isinstance(asset, dict) or asset.get("asset_kind") != "skill_prompt":
        raise EvolutionContractError("skill outcome requires a skill_prompt asset")
    hydrated = hydrate_sidecar_ref(
        coordinator.state_dir,
        dict(asset["artifact_ref"]),
        purpose="skill-evolution-outcome",
        actor="evolution-observer",
    )
    body = hydrated.payload if isinstance(hydrated.payload, Mapping) else {}
    declared = str(body.get("skill_name") or body.get("name") or "").strip()
    if declared and declared != skill_name:
        raise EvolutionContractError("skill asset name does not match invocation")
    projection = project_skill_invocations(
        coordinator.state_dir,
        config=config,
        project_root=project_root or coordinator.state_dir.parent,
        task_id=task_id,
        role_instance=role_instance,
    )
    invoked = [
        row
        for row in projection.get("skills") or []
        if row.get("skill") == skill_name and bool(row.get("invoked"))
    ]
    if not invoked:
        raise EvolutionContractError("skill outcome has no observed invocation evidence")
    evidence_ids = sorted({
        str(item.get("event_id") or "")
        for row in invoked
        for item in row.get("evidence") or []
        if str(item.get("event_id") or "")
    })
    invocation_id = "skill-invocation://" + stable_digest({
        "asset": key,
        "skill": skill_name,
        "task_id": task_id,
        "role_instance": role_instance,
        "evidence_ids": evidence_ids,
    })
    feedback_body = dict(feedback or {})
    rework_count = _nonnegative_int(
        feedback_body.get("rework_count"), field="rework_count"
    )
    replan_count = _nonnegative_int(
        feedback_body.get("replan_count"), field="replan_count"
    )
    blocking_regression = bool(feedback_body.get("blocking_regression", False))
    normalized_feedback = {
        "rework_count": rework_count,
        "replan_count": replan_count,
        "blocking_regression": blocking_regression,
        "negative_transfer": bool(
            outcome in {"failed", "regressed"}
            or blocking_regression
            or rework_count > 0
            or replan_count > 0
        ),
    }
    feedback_ref = write_immutable_json_sidecar(
        coordinator.state_dir,
        {
            "schema_version": "skill-feedback-observation.v1",
            "asset_id": asset_id,
            "version": version,
            "skill_name": skill_name,
            "task_id": task_id,
            "role_instance": role_instance,
            "invocation_ref": invocation_id,
            "invocation_event_ids": evidence_ids,
            "outcome": outcome,
            "cost": deepcopy(dict(cost)),
            **normalized_feedback,
        },
        root="evolution/skill-feedback",
        kind="skill_feedback_observation",
        schema_version="skill-feedback-observation.v1",
        created_by="skill-invocation-projector",
    )
    usage_ref = str(feedback_ref["ref"])
    result = coordinator.record_asset_outcome(
        asset_id=asset_id,
        version=version,
        usage_ref=usage_ref,
        matched=True,
        outcome=outcome,
        cost=cost,
        actor="skill-invocation-projector",
    )
    result["invocation"] = {
        "skill": skill_name,
        "task_id": task_id,
        "role_instance": role_instance,
        "evidence_event_ids": evidence_ids,
        "usage_ref": usage_ref,
        "invocation_ref": invocation_id,
        "feedback_ref": feedback_ref,
    }
    if str(asset.get("state") or "") != "canary_active":
        return result

    current = result["asset"]
    revoke_reasons = _skill_revoke_reasons(
        current,
        outcome=outcome,
        feedback=normalized_feedback,
    )
    if not revoke_reasons:
        return result

    from zf.runtime.evolution_automation_support import controlled_transition

    activation = asset.get("activation")
    if not isinstance(activation, Mapping):
        raise EvolutionContractError("Skill overlay activation policy is missing")
    policy_digest = str(activation.get("automation_policy_digest") or "")
    if not policy_digest:
        raise EvolutionContractError("Skill overlay automation policy digest is missing")
    campaign_id = str(
        (asset.get("source_attempt_ids") or [asset_id])[0]
        or asset_id
    )
    revoked = controlled_transition(
        state_dir=coordinator.state_dir,
        project_root=project_root or coordinator.state_dir.parent,
        writer=coordinator.writer,
        config=config,
        campaign={
            "campaign_id": campaign_id,
            "policy_digest": policy_digest,
        },
        asset=current,
        target_state="revoked",
        reason=",".join(revoke_reasons),
    )
    result["asset"] = revoked["asset"]
    result["auto_revoke"] = {
        "applied": bool(revoked["applied"]),
        "receipt_ref": revoked["receipt_ref"],
        "reasons": revoke_reasons,
    }
    return result


def _nonnegative_int(value: object, *, field: str) -> int:
    try:
        result = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise EvolutionContractError(f"Skill feedback {field} must be an integer") from exc
    if result < 0:
        raise EvolutionContractError(f"Skill feedback {field} must be non-negative")
    return result


def _skill_revoke_reasons(
    asset: Mapping[str, Any],
    *,
    outcome: str,
    feedback: Mapping[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if outcome in {"failed", "regressed"}:
        reasons.append(f"outcome_{outcome}")
    if bool(feedback.get("blocking_regression")):
        reasons.append("blocking_regression")
    if int(feedback.get("rework_count") or 0) > 0:
        reasons.append("rework_increased")
    if int(feedback.get("replan_count") or 0) > 0:
        reasons.append("replan_increased")
    activation = asset.get("activation")
    budget = activation.get("budget") if isinstance(activation, Mapping) else {}
    if isinstance(budget, Mapping):
        totals = {"tokens": 0.0, "cost_usd": 0.0}
        for row in asset.get("outcomes") or []:
            if not isinstance(row, Mapping):
                continue
            cost = row.get("cost") if isinstance(row.get("cost"), Mapping) else {}
            totals["tokens"] += float(cost.get("tokens") or 0.0)
            totals["cost_usd"] += float(cost.get("cost_usd") or 0.0)
        limits = {
            "tokens": float(budget.get("max_tokens") or 0.0),
            "cost_usd": float(budget.get("max_cost_usd") or 0.0),
        }
        if any(
            limits[key] > 0 and totals[key] > limits[key]
            for key in totals
        ):
            reasons.append("budget_exceeded")
    return list(dict.fromkeys(reasons))


__all__ = ["record_skill_outcome"]
