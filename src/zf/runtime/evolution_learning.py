"""Capability accumulation and proposal-only workflow/provider evolution."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.evolution_contracts import (
    EvolutionContractError,
    normalize_digest,
    stable_digest,
)
from zf.runtime.evolution_evaluator import pareto_frontier
from zf.runtime.evolution_store import CapabilityRegistry
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, verify_sidecar_ref
from zf.runtime.workflow_proposal import build_workflow_proposal


WORKFLOW_VARIANT_SCHEMA = "evolution-workflow-variant.v1"
PROVIDER_ROUTE_VARIANT_SCHEMA = "evolution-provider-route-variant.v1"
PORTABLE_ASSET_SCHEMA = "portable-learning-asset.v1"
CHALLENGE_CASE_SCHEMA = "challenge-case.v1"
_FLOW_FAMILY_BY_REQUEST_KIND = {
    "issue": "IssueFlow",
    "feat": "PrdFlow",
    "prd": "PrdFlow",
    "refactor": "RefactorFlow",
    "workflow": "Workflow",
}


def compile_workflow_learning_proposal(
    state_dir: Path,
    *,
    promotion_descriptor: Mapping[str, Any],
    request: Mapping[str, Any],
    base_config_path: Path,
    candidate_config_path: Path,
    preflight: Mapping[str, Any],
    writer: EventWriter | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Adapt Loop Learning output into the standard Workflow Proposal path."""

    hydrated = hydrate_sidecar_ref(
        Path(state_dir),
        dict(promotion_descriptor),
        purpose="workflow-learning-proposal",
        actor="evolution-coordinator",
    )
    promotion = hydrated.payload
    if not isinstance(promotion, dict):
        raise EvolutionContractError("loop learning promotion body must be an object")
    if (
        promotion.get("schema_version") != "loop-learning-promotion.v1"
        or promotion.get("target") != "workflow_patch_proposal"
    ):
        raise EvolutionContractError(
            "only workflow_patch_proposal learning can enter Workflow Proposal"
        )
    policy = promotion.get("promotion_policy")
    if not isinstance(policy, Mapping) or not bool(policy.get("proposal_only")):
        raise EvolutionContractError("learning promotion must remain proposal_only")
    state_dir = Path(state_dir)
    effective_writer = writer or EventWriter(event_log_from_project(state_dir))
    request_kind = str(request.get("kind") or "workflow").strip().lower()
    flow_family = _FLOW_FAMILY_BY_REQUEST_KIND.get(request_kind, "Workflow")
    proposal, proposal_descriptor = build_workflow_proposal(
        state_dir,
        request=request,
        base_config_path=Path(base_config_path),
        candidate_config_path=Path(candidate_config_path),
        synthesis_result_ref=None,
        preflight=preflight,
        flow_kind=flow_family,
        actor="evolution-coordinator",
        writer=effective_writer,
    )
    link = {
        "schema_version": "workflow-learning-proposal-link.v1",
        "promotion_ref": dict(promotion_descriptor),
        "promotion_digest": str(promotion_descriptor.get("sha256") or ""),
        "workflow_proposal_ref": dict(proposal_descriptor),
        "workflow_proposal_digest": str(proposal.get("proposal_digest") or ""),
        "request_id": str(request.get("request_id") or ""),
        "apply_mode": "proposal_only",
    }
    link_descriptor = write_immutable_json_sidecar(
        state_dir,
        link,
        root="evolution/workflow-proposal-links",
        kind="workflow_learning_proposal_link",
        schema_version="workflow-learning-proposal-link.v1",
        created_by="evolution-coordinator",
    )
    effective_writer.emit(
        "evolution.workflow.proposal.compiled",
        actor="evolution-coordinator",
        correlation_id=str(request.get("request_id") or ""),
        payload={
            "request_id": str(request.get("request_id") or ""),
            "promotion_ref": dict(promotion_descriptor),
            "workflow_proposal_ref": dict(proposal_descriptor),
            "link_ref": link_descriptor,
            "apply_mode": "proposal_only",
        },
    )
    return proposal, proposal_descriptor


def build_workflow_variant(raw: Mapping[str, Any]) -> dict[str, Any]:
    body = deepcopy(dict(raw))
    if body.get("schema_version") != WORKFLOW_VARIANT_SCHEMA:
        raise EvolutionContractError(f"schema_version must be {WORKFLOW_VARIANT_SCHEMA}")
    for key in (
        "variant_id",
        "task_family",
        "config_ref",
        "hypothesis_ref",
    ):
        if not str(body.get(key) or "").strip():
            raise EvolutionContractError(f"workflow variant {key} is required")
    body["config_digest"] = normalize_digest(
        body.get("config_digest"), field="workflow variant config_digest"
    )
    body["hypothesis_digest"] = normalize_digest(
        body.get("hypothesis_digest"),
        field="workflow variant hypothesis_digest",
    )
    _validate_variant_common(body)
    stages = body.get("stage_graph")
    if not isinstance(stages, list) or not stages:
        raise EvolutionContractError("workflow variant stage_graph is required")
    policy = body.get("policy")
    if not isinstance(policy, Mapping) or policy.get("apply_mode") != "proposal_only":
        raise EvolutionContractError("workflow variant must be proposal_only")
    body["variant_digest"] = stable_digest({
        key: value for key, value in body.items() if key != "variant_digest"
    })
    return body


def build_provider_route_variant(raw: Mapping[str, Any]) -> dict[str, Any]:
    body = deepcopy(dict(raw))
    if body.get("schema_version") != PROVIDER_ROUTE_VARIANT_SCHEMA:
        raise EvolutionContractError(
            f"schema_version must be {PROVIDER_ROUTE_VARIANT_SCHEMA}"
        )
    for key in (
        "variant_id",
        "task_family",
        "provider",
        "model",
        "health_ref",
    ):
        if not str(body.get(key) or "").strip():
            raise EvolutionContractError(f"provider route variant {key} is required")
    for key in ("capability_digest", "route_policy_digest", "health_digest"):
        body[key] = normalize_digest(
            body.get(key), field=f"provider route variant {key}"
        )
    _validate_variant_common(body)
    for key in ("cost_receipt_refs", "outcome_refs"):
        values = [str(item) for item in body.get(key) or [] if str(item)]
        if not values:
            raise EvolutionContractError(f"provider route variant {key} is required")
        body[key] = values
    body["provider_fingerprint"] = stable_digest({
        key: body[key]
        for key in (
            "provider", "model", "capability_digest", "route_policy_digest"
        )
    })
    if not isinstance(body.get("allowed_by_config"), bool) or not body["allowed_by_config"]:
        raise EvolutionContractError("provider route is outside the configured allowlist")
    body["variant_digest"] = stable_digest({
        key: value for key, value in body.items() if key != "variant_digest"
    })
    return body


def compare_variant_archive(
    variants: Sequence[Mapping[str, Any]],
    *,
    dimensions: Mapping[str, str],
) -> dict[str, Any]:
    rows = [deepcopy(dict(item)) for item in variants]
    provider_fingerprints = {
        str(item.get("provider_fingerprint") or "") for item in rows
        if item.get("schema_version") == PROVIDER_ROUTE_VARIANT_SCHEMA
    }
    if "" in provider_fingerprints:
        raise EvolutionContractError("provider route comparison lacks fingerprint")
    task_families = {str(item.get("task_family") or "") for item in rows}
    if len(task_families) != 1:
        return {
            "schema_version": "evolution-variant-comparison.v1",
            "status": "incomparable",
            "reason": "variant task_family differs",
            "pareto_frontier": [],
        }
    identities = {
        stable_digest(dict(item.get("comparison_identity") or {}))
        for item in rows
    }
    if len(identities) != 1:
        return {
            "schema_version": "evolution-variant-comparison.v1",
            "status": "incomparable",
            "reason": "variant comparison_identity differs",
            "pareto_frontier": [],
        }
    frontier = pareto_frontier(rows, dimensions=dimensions)
    return {
        "schema_version": "evolution-variant-comparison.v1",
        "status": "comparable",
        "task_family": next(iter(task_families)),
        "dimensions": dict(dimensions),
        "candidate_count": len(rows),
        "pareto_frontier": [
            str(item.get("variant_id") or item.get("candidate_id") or "")
            for item in frontier
        ],
        "variants": rows,
    }


def provider_comparison_is_current(
    comparison: Mapping[str, Any],
    *,
    current_fingerprints: Mapping[str, str],
) -> tuple[bool, str]:
    for variant in comparison.get("variants") or []:
        if not isinstance(variant, Mapping):
            return False, "malformed provider variant"
        provider = str(variant.get("provider") or "")
        model = str(variant.get("model") or "")
        expected = str(variant.get("provider_fingerprint") or "")
        current = current_fingerprints.get(f"{provider}:{model}")
        if current is None:
            current = current_fingerprints.get(provider)
        if not provider or current != expected:
            return False, (
                "provider fingerprint changed: "
                f"{provider or 'unknown'}:{model or 'unknown'}"
            )
    return True, "current"


def opportunity_to_variant_proposal(insight: Mapping[str, Any]) -> dict[str, Any]:
    """Turn positive evidence into a proposal, never an automatic topology edit."""

    if str(insight.get("kind") or "") not in {
        "reusable_module",
        "coordination_overhead",
        "verification_no_increment",
        "provider_capability",
        "skill_uplift",
    }:
        raise EvolutionContractError("unsupported evolution opportunity kind")
    evidence_refs = [str(item) for item in insight.get("evidence_refs") or [] if str(item)]
    if not evidence_refs:
        raise EvolutionContractError("evolution opportunity requires evidence_refs")
    body = {
        "schema_version": "evolution-opportunity-proposal.v1",
        "opportunity_id": str(insight.get("opportunity_id") or ""),
        "kind": str(insight["kind"]),
        "task_family": str(insight.get("task_family") or ""),
        "summary": str(insight.get("summary") or ""),
        "evidence_refs": evidence_refs,
        "policy": {
            "apply_mode": "proposal_only",
            "requires_independent_evaluation": True,
        },
    }
    if not body["opportunity_id"] or not body["task_family"] or not body["summary"]:
        raise EvolutionContractError("evolution opportunity identity is incomplete")
    body["proposal_digest"] = stable_digest(body)
    return body


def export_learning_asset(
    state_dir: Path,
    *,
    registry: CapabilityRegistry,
    asset_id: str,
    version: int,
) -> dict[str, Any]:
    key = f"{asset_id}@{int(version)}"
    row = registry.load()["assets"].get(key)
    if not isinstance(row, dict):
        raise EvolutionContractError(f"learning asset not found: {key}")
    if row.get("state") != "active_retained":
        raise EvolutionContractError("only retained learning assets are exportable")
    taint = row.get("taint") if isinstance(row.get("taint"), Mapping) else {}
    if any(bool(taint.get(flag)) for flag in ("blocked", "secret", "pii", "license_unknown")):
        raise EvolutionContractError("tainted learning asset is not portable")
    source_descriptor = row.get("artifact_ref")
    if not isinstance(source_descriptor, Mapping):
        raise EvolutionContractError("retained learning asset has no immutable body")
    hydrated = hydrate_sidecar_ref(
        Path(state_dir),
        dict(source_descriptor),
        purpose="learning-asset-export",
        actor="evolution-coordinator",
    )
    if not isinstance(hydrated.payload, Mapping):
        raise EvolutionContractError("retained learning asset body is invalid")
    asset_body = deepcopy(dict(hydrated.payload))
    package = {
        "schema_version": PORTABLE_ASSET_SCHEMA,
        "asset": asset_body,
        "source_state": "active_retained",
        "source_artifact_digest": hydrated.sha256,
        "import_policy": {
            "initial_state": "candidate",
            "target_validation_required": True,
            "automatic_activation": False,
        },
    }
    descriptor = write_immutable_json_sidecar(
        Path(state_dir),
        package,
        root="evolution/exports",
        kind="portable_learning_asset",
        schema_version=PORTABLE_ASSET_SCHEMA,
        created_by="evolution-coordinator",
    )
    return {"package": package, "artifact_ref": descriptor}


def import_learning_asset(
    state_dir: Path,
    *,
    registry: CapabilityRegistry,
    package_descriptor: Mapping[str, Any],
    target_project: str,
    imported_at: str,
    source_state_dir: Path | None = None,
) -> dict[str, Any]:
    hydrated = hydrate_sidecar_ref(
        Path(source_state_dir or state_dir),
        dict(package_descriptor),
        purpose="learning-asset-import",
        actor="evolution-coordinator",
    )
    package = hydrated.payload
    if not isinstance(package, dict) or package.get("schema_version") != PORTABLE_ASSET_SCHEMA:
        raise EvolutionContractError("portable learning asset package is invalid")
    asset = deepcopy(dict(package.get("asset") or {}))
    provenance = deepcopy(dict(asset.get("provenance") or {}))
    provenance.update({
        "imported": True,
        "target_project": target_project,
        "target_validation": "pending",
        "source_package_digest": hydrated.sha256,
    })
    asset["provenance"] = provenance
    versions = [
        int(row.get("version") or 0)
        for row in registry.load()["assets"].values()
        if row.get("asset_id") == asset.get("asset_id")
    ]
    asset["version"] = max([int(asset.get("version") or 0), *versions]) + 1
    asset["proposal_fingerprint"] = stable_digest({
        "source_package_digest": hydrated.sha256,
        "target_project": target_project,
    })
    descriptor = write_immutable_json_sidecar(
        Path(state_dir),
        asset,
        root="evolution/imports",
        kind="imported_learning_asset",
        schema_version="learning-asset.v1",
        created_by="evolution-coordinator",
    )
    row, created = registry.propose(
        asset,
        artifact_ref=descriptor,
        created_at=imported_at,
    )
    return {"asset": row, "artifact_ref": descriptor, "created": created}


def evolution_economics(
    *,
    candidate_generation: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    post_adoption: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute economics only from attributed receipts; otherwise say unknown."""

    generation_cost = _optional_finite(candidate_generation.get("cost_usd"))
    evaluation_cost = _optional_finite(evaluation.get("cost_usd"))
    gain = _optional_finite(evaluation.get("score_delta"))
    if generation_cost is None or evaluation_cost is None or gain is None:
        return {
            "schema_version": "evolution-economics.v1",
            "status": "unknown",
            "reason": "attributed generation/evaluation receipts are incomplete",
        }
    total = generation_cost + evaluation_cost
    tasks_observed = int((post_adoption or {}).get("tasks_observed") or 0)
    time_saved = _optional_finite((post_adoption or {}).get("time_saved_hours"))
    saved_cost = _optional_finite(
        (post_adoption or {}).get("saved_cost_per_task_usd")
    )
    return {
        "schema_version": "evolution-economics.v1",
        "status": "measured",
        "incremental_cost_usd": round(total, 6),
        "marginal_gain_per_usd": round(gain / total, 6) if total > 0 else None,
        "tasks_observed": tasks_observed,
        "time_saved_after_adoption_hours": time_saved,
        "break_even_task_count": (
            round(total / saved_cost, 3)
            if saved_cost is not None and saved_cost > 0
            else None
        ),
    }


def learning_context_projection(
    state_dir: Path,
    *,
    context: Mapping[str, Any],
    now: str | None = None,
) -> dict[str, Any]:
    """Resolve active assets and hydrate only briefing-safe learning bodies."""

    registry = CapabilityRegistry(Path(state_dir) / "evolution" / "capabilities.json")
    resolution = registry.resolve_assets(
        context,
        now=now or datetime.now(timezone.utc).isoformat(),
    )
    selected: list[dict[str, Any]] = []
    for row in resolution["selected"]:
        descriptor = row.get("artifact_ref")
        if not isinstance(descriptor, dict):
            continue
        hydrated = hydrate_sidecar_ref(
            Path(state_dir),
            descriptor,
            purpose="learning-context",
            actor=str(context.get("actor") or "runtime"),
        )
        body = hydrated.payload if isinstance(hydrated.payload, dict) else {}
        item = {
            "asset_id": row["asset_id"],
            "asset_kind": row["asset_kind"],
            "version": row["version"],
            "state": row["state"],
            "digest": row["digest"],
            "artifact_ref": descriptor,
        }
        if row["asset_kind"] in {"memory_entry", "runbook"}:
            item["content"] = str(body.get("content") or body.get("summary") or "")[:4000]
        selected.append(item)
    return {
        "schema_version": "learning-context-projection.v1",
        "selected": selected,
        "excluded": resolution["excluded"],
    }


class ChallengeBank:
    """Small CAS projection for visible shadow challenges and holdout proposals."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def materialize(
        self,
        state_dir: Path,
        raw: Mapping[str, Any],
        *,
        writer: EventWriter,
    ) -> dict[str, Any]:
        body = deepcopy(dict(raw))
        if body.get("schema_version") != CHALLENGE_CASE_SCHEMA:
            raise EvolutionContractError(f"schema_version must be {CHALLENGE_CASE_SCHEMA}")
        for key in (
            "challenge_id", "source_event_ref", "run_ref", "trace_ref",
            "reproduction_ref", "expected_invariant", "visibility_policy",
        ):
            if not str(body.get(key) or "").strip():
                raise EvolutionContractError(f"challenge {key} is required")
        if body.get("secret_status") not in {"clear", "redacted", "blocked"}:
            raise EvolutionContractError("challenge secret_status is invalid")
        observations = body.get("stability_observations")
        if not isinstance(observations, list) or not observations:
            raise EvolutionContractError(
                "challenge stability_observations must be a non-empty list"
            )
        body["status"] = "shadow"
        body["challenge_digest"] = stable_digest(body)
        descriptor = write_immutable_json_sidecar(
            Path(state_dir),
            body,
            root="evolution/challenges",
            kind="challenge_case",
            schema_version=CHALLENGE_CASE_SCHEMA,
            created_by="evolution-coordinator",
        )
        with locked_path(self.path):
            data = self._load()
            existing = data["challenges"].get(body["challenge_id"])
            if existing and existing.get("challenge_digest") != body["challenge_digest"]:
                raise EvolutionContractError("challenge identity conflict")
            if isinstance(existing, dict):
                return deepcopy(existing)
            data["challenges"][body["challenge_id"]] = {
                "challenge_id": body["challenge_id"],
                "challenge_digest": body["challenge_digest"],
                "status": "shadow",
                "revision": 1,
                "artifact_ref": descriptor,
            }
            self._save(data)
        writer.emit(
            "challenge.candidate.materialized",
            actor="evolution-coordinator",
            payload={
                "challenge_id": body["challenge_id"],
                "status": "shadow",
                "artifact_ref": descriptor,
            },
        )
        return data["challenges"][body["challenge_id"]]

    def decide(
        self,
        *,
        challenge_id: str,
        expected_revision: int,
        verdict: str,
        evaluator_receipt_ref: Mapping[str, Any],
        writer: EventWriter,
    ) -> dict[str, Any]:
        if verdict not in {"promoted", "rejected"}:
            raise EvolutionContractError("challenge verdict must be promoted or rejected")
        state_dir = self.path.parent.parent
        verify_sidecar_ref(state_dir, dict(evaluator_receipt_ref))
        with locked_path(self.path):
            data = self._load()
            row = data["challenges"].get(challenge_id)
            if not isinstance(row, dict):
                raise EvolutionContractError("challenge does not exist")
            if row.get("status") == verdict:
                return deepcopy(row)
            if int(row.get("revision") or 0) != int(expected_revision):
                raise EvolutionContractError("challenge revision is stale")
            if verdict == "promoted":
                hydrated = hydrate_sidecar_ref(
                    state_dir,
                    dict(row["artifact_ref"]),
                    purpose="challenge-promotion",
                    actor="evaluator-authority",
                )
                body = hydrated.payload if isinstance(hydrated.payload, Mapping) else {}
                if body.get("secret_status") == "blocked":
                    raise EvolutionContractError(
                        "blocked challenge cannot enter an evaluator generation"
                    )
                observations = body.get("stability_observations") or []
                if len(observations) < 2 or not all(
                    isinstance(item, Mapping) and bool(item.get("reproduced"))
                    for item in observations
                ):
                    raise EvolutionContractError(
                        "challenge promotion requires repeated stable reproduction"
                    )
            row.update({
                "status": verdict,
                "revision": int(row["revision"]) + 1,
                "evaluator_receipt_ref": dict(evaluator_receipt_ref),
            })
            self._save(data)
        writer.emit(
            f"challenge.{verdict}",
            actor="evaluator-authority",
            payload={
                "challenge_id": challenge_id,
                "status": verdict,
                "evaluator_receipt_ref": dict(evaluator_receipt_ref),
            },
        )
        return deepcopy(row)

    def _load(self) -> dict[str, Any]:
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            body = {}
        if not isinstance(body, dict):
            raise EvolutionContractError("challenge bank must be an object")
        return {
            "schema_version": "challenge-bank.v1",
            "revision": int(body.get("revision") or 0),
            "challenges": deepcopy(dict(body.get("challenges") or {})),
        }

    def _save(self, data: Mapping[str, Any]) -> None:
        body = {**dict(data), "revision": int(data.get("revision") or 0) + 1}
        atomic_write_text(
            self.path,
            json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )


def _optional_finite(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _validate_variant_common(body: dict[str, Any]) -> None:
    objective = body.get("objective")
    if not isinstance(objective, Mapping) or not all(
        str(objective.get(key) or "").strip() for key in ("kind", "summary")
    ):
        raise EvolutionContractError("evolution variant requires a typed objective")
    identity = body.get("comparison_identity")
    if not isinstance(identity, Mapping) or not identity or any(
        not str(value or "").strip() for value in identity.values()
    ):
        raise EvolutionContractError(
            "evolution variant requires a complete comparison_identity"
        )
    evidence_refs = [
        str(item) for item in body.get("evidence_refs") or [] if str(item)
    ]
    if not evidence_refs:
        raise EvolutionContractError("evolution variant requires evidence_refs")
    body["evidence_refs"] = evidence_refs
    if not isinstance(body.get("metrics"), Mapping) or not body["metrics"]:
        raise EvolutionContractError("evolution variant requires metrics")


__all__ = [
    "CHALLENGE_CASE_SCHEMA",
    "ChallengeBank",
    "PORTABLE_ASSET_SCHEMA",
    "PROVIDER_ROUTE_VARIANT_SCHEMA",
    "WORKFLOW_VARIANT_SCHEMA",
    "build_provider_route_variant",
    "build_workflow_variant",
    "compare_variant_archive",
    "compile_workflow_learning_proposal",
    "evolution_economics",
    "export_learning_asset",
    "import_learning_asset",
    "learning_context_projection",
    "opportunity_to_variant_proposal",
    "provider_comparison_is_current",
]
