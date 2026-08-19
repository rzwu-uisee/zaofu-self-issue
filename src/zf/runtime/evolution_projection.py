"""Read-only metrics and lineage projection for self-evolution state."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from zf.runtime.evolution_contracts import stable_digest


def build_evolution_projection(
    trial_state: Mapping[str, Any],
    capability_state: Mapping[str, Any],
    *,
    generated_at: str,
) -> dict[str, Any]:
    return {
        "schema_version": "evolution-projection.v1",
        "generated_at": generated_at,
        "attempts": sorted(
            (deepcopy(row) for row in trial_state["attempts"].values()),
            key=lambda row: str(row.get("created_at") or ""),
            reverse=True,
        ),
        "trials": sorted(
            (deepcopy(row) for row in trial_state["trials"].values()),
            key=lambda row: (
                str(row.get("attempt_id") or ""),
                int(row.get("replicate") or 0),
            ),
        ),
        "comparisons": sorted(
            (deepcopy(row) for row in trial_state["comparisons"].values()),
            key=lambda row: str(row.get("created_at") or ""),
            reverse=True,
        ),
        "assets": sorted(
            (deepcopy(row) for row in capability_state["assets"].values()),
            key=lambda row: (
                str(row.get("asset_id") or ""),
                int(row.get("version") or 0),
            ),
        ),
        "active_versions": deepcopy(capability_state["active_versions"]),
        "canary_versions": deepcopy(capability_state["canary_versions"]),
        "lineage": _lineage(trial_state, capability_state),
        "metrics": _metrics(trial_state, capability_state),
    }


def _metrics(
    trials: Mapping[str, Any],
    capabilities: Mapping[str, Any],
) -> dict[str, Any]:
    attempts = list(trials["attempts"].values())
    comparisons = list(trials["comparisons"].values())
    assets = list(capabilities["assets"].values())
    eligible = [item for item in comparisons if item.get("status") != "incomparable"]
    retained = [item for item in assets if item.get("state") == "active_retained"]
    revoked = [item for item in assets if item.get("state") == "revoked"]
    cohort_rows: dict[str, dict[str, Any]] = {}
    for asset in assets:
        for outcome in asset.get("outcomes") or []:
            if not bool(outcome.get("matched")):
                continue
            cohort = (
                outcome.get("cohort")
                if isinstance(outcome.get("cohort"), Mapping)
                else {}
            )
            cohort_key = "|".join(
                f"{key}={cohort[key]}" for key in sorted(cohort)
            ) or "unspecified"
            row = cohort_rows.setdefault(cohort_key, {
                "cohort": deepcopy(dict(cohort)),
                "matched_outcomes": 0,
                "passed_outcomes": 0,
                "negative_transfer_count": 0,
                "known_reuse_gains": [],
            })
            row["matched_outcomes"] += 1
            if outcome.get("outcome") == "passed":
                row["passed_outcomes"] += 1
            if bool(outcome.get("negative_transfer")):
                row["negative_transfer_count"] += 1
            gain = (
                (outcome.get("evaluation") or {}).get("reuse_gain")
                if isinstance(outcome.get("evaluation"), Mapping)
                else None
            )
            if isinstance(gain, (int, float)):
                row["known_reuse_gains"].append(float(gain))
    cohort_projection: list[dict[str, Any]] = []
    for key, row in sorted(cohort_rows.items()):
        gains = row.pop("known_reuse_gains")
        cohort_projection.append({
            "cohort_id": stable_digest({"key": key})[:16],
            **row,
            "success_rate": _ratio(
                int(row["passed_outcomes"]), int(row["matched_outcomes"])
            ),
            "mean_reuse_gain": (
                round(sum(gains) / len(gains), 6) if gains else None
            ),
        })
    return {
        "attempt_count": len(attempts),
        "attempt_identity_coverage": _ratio(len(attempts), len(attempts)),
        "comparison_count": len(comparisons),
        "comparison_eligibility_rate": _ratio(len(eligible), len(comparisons)),
        "asset_count": len(assets),
        "adoption_survival_rate": _ratio(
            len(retained), len(retained) + len(revoked)
        ),
        "negative_transfer_count": sum(
            1
            for asset in assets
            for outcome in asset.get("outcomes") or []
            if outcome.get("negative_transfer")
        ),
        "generalization_cohorts": cohort_projection,
    }


def _lineage(
    trials: Mapping[str, Any],
    capabilities: Mapping[str, Any],
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    for row in trials["attempts"].values():
        nodes.append({
            "id": str(row["attempt_id"]),
            "kind": "attempt",
            "status": str(row.get("status") or ""),
            "artifact_ref": deepcopy(row.get("artifact_ref") or {}),
        })
    for row in trials["comparisons"].values():
        comparison_id = str(row["comparison_id"])
        attempt_id = str(row.get("attempt_id") or "")
        nodes.append({
            "id": comparison_id,
            "kind": "comparison",
            "status": str(row.get("status") or ""),
            "adoption_eligible": bool(row.get("adoption_eligible")),
            "evaluator_generation_id": str(
                row.get("evaluator_generation_id") or ""
            ),
            "artifact_ref": deepcopy(row.get("artifact_ref") or {}),
        })
        if attempt_id:
            edges.append({
                "source": attempt_id,
                "target": comparison_id,
                "relation": "evaluated_by",
            })
    for row in capabilities["assets"].values():
        asset_node = f"{row['asset_id']}@{row['version']}"
        nodes.append({
            "id": asset_node,
            "kind": "learning_asset",
            "asset_kind": str(row.get("asset_kind") or ""),
            "status": str(row.get("state") or ""),
            "canary_scope_ref": str(
                (row.get("activation") or {}).get("canary_scope_ref") or ""
            ),
            "artifact_ref": deepcopy(row.get("artifact_ref") or {}),
        })
        for attempt_id in row.get("source_attempt_ids") or []:
            edges.append({
                "source": str(attempt_id),
                "target": asset_node,
                "relation": "proposed_asset",
            })
    return {
        "schema_version": "evolution-lineage-projection.v1",
        "nodes": sorted(nodes, key=lambda item: (item["kind"], item["id"])),
        "edges": sorted(
            edges,
            key=lambda item: (
                item["source"], item["target"], item["relation"]
            ),
        ),
        "projection_only": True,
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


__all__ = ["build_evolution_projection"]
