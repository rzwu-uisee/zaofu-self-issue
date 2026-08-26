"""Verified Learn archive intake for unattended evolution campaigns."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.evolution_contracts import (
    EvolutionContractError,
    stable_digest,
    validate_evaluator_generation,
)
from zf.runtime.evolution_skill import validate_skill_candidate
from zf.runtime.evolution_skill_campaign import verify_skill_routing_stress_ref
from zf.runtime.evolution_skill_eval import validate_skill_eval_suite
from zf.runtime.run_archive import verify_run_archive
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


_LOW_RISK_ASSET_KINDS = frozenset({
    "memory_entry",
    "runbook",
    "regression_fixture",
    # Evaluation is unattended; source mutation remains owner-only.
    "skill_prompt",
})
_HIGH_RISK_MUTATION_KINDS = frozenset({
    "framework_code",
    "workflow_config",
    "provider_route",
    "tool_capability",
})


class EvolutionPolicyDeclined(EvolutionContractError):
    """A valid candidate that policy deliberately keeps out of automation."""

    def __init__(self, message: str, *, disposition: str, asset_kind: str) -> None:
        super().__init__(message)
        self.disposition = disposition
        self.asset_kind = asset_kind


def deposition_from_archive(
    *,
    state_dir: Path,
    event: ZfEvent,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = event.payload if isinstance(event.payload, Mapping) else {}
    refs = payload.get("archive_refs")
    if not isinstance(refs, Mapping):
        raise EvolutionContractError("learn completion lacks archive_refs")
    manifest_path = Path(str(refs.get("manifest") or "")).expanduser()
    expected_digest = str(refs.get("manifest_digest") or "")
    if not manifest_path.is_absolute():
        manifest_path = Path(state_dir) / manifest_path
    manifest = verify_run_archive(
        manifest_path,
        expected_digest=expected_digest,
    )
    candidates = [
        item for item in manifest.get("artifacts") or []
        if isinstance(item, Mapping)
        and str(item.get("path") or "").startswith("supplemental/")
        and "deposition" in Path(str(item.get("path") or "")).name
        and str(item.get("path") or "").endswith(".json")
    ]
    if len(candidates) != 1:
        raise EvolutionContractError(
            f"learn archive requires exactly one deposition artifact; found {len(candidates)}"
        )
    record = dict(candidates[0])
    path = manifest_path.parent / str(record["path"])
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise EvolutionContractError("capability deposition must be a JSON object")
    schema = str(body.get("schema_version") or "").replace("_", "-")
    if schema != "capability-deposition.v1":
        raise EvolutionContractError("unsupported capability deposition schema")
    for key in ("artifact_id", "run_id", "capability", "verification"):
        if not str(body.get(key) or "").strip():
            raise EvolutionContractError(f"capability deposition requires {key}")
    descriptor = {
        "ref_schema_version": "run-archive-artifact-ref.v1",
        "kind": "capability_deposition",
        "ref": str(path),
        "sha256": str(record.get("sha256") or ""),
        "byte_count": int(record.get("bytes") or 0),
        "schema_version": "capability-deposition.v1",
        "archive_manifest_ref": str(manifest_path),
        "archive_manifest_digest": expected_digest,
    }
    return body, descriptor


def validate_candidate(
    deposition: Mapping[str, Any],
    *,
    state_dir: Path,
    policy: Any,
) -> dict[str, Any]:
    raw = deposition.get("evolution_candidate")
    if not isinstance(raw, Mapping):
        raise EvolutionContractError(
            "capability deposition has no typed evolution_candidate; retained as observation"
        )
    body = dict(raw)
    if str(body.get("schema_version") or "") != "evolution-candidate.v1":
        raise EvolutionContractError("evolution_candidate schema must be evolution-candidate.v1")
    for key in ("asset_id", "asset_kind", "task_family", "content"):
        if not str(body.get(key) or "").strip():
            raise EvolutionContractError(f"evolution_candidate requires {key}")
    kind = str(body["asset_kind"])
    if kind in _HIGH_RISK_MUTATION_KINDS:
        raise EvolutionPolicyDeclined(
            f"high-risk evolution kind remains proposal-only: {kind}",
            disposition="proposal_only",
            asset_kind=kind,
        )
    if kind not in _LOW_RISK_ASSET_KINDS:
        raise EvolutionContractError(f"unsupported unattended evolution asset kind: {kind}")
    evaluator_ref = body.get("evaluator_ref")
    if not isinstance(evaluator_ref, Mapping):
        raise EvolutionContractError("evolution_candidate requires evaluator_ref")
    hydrated = hydrate_sidecar_ref(
        state_dir,
        dict(evaluator_ref),
        purpose="evolution-campaign-intake",
        actor="run-manager",
    )
    if not isinstance(hydrated.payload, Mapping):
        raise EvolutionContractError("evolution evaluator payload is invalid")
    evaluator = validate_evaluator_generation(hydrated.payload)
    body["evaluator_ref"] = dict(evaluator_ref)
    body["evaluator"] = evaluator
    if kind == "skill_prompt":
        _normalize_skill_candidate(
            body,
            deposition=deposition,
            state_dir=state_dir,
            evaluator=evaluator,
        )
    canary_ref = body.get("canary_evaluator_ref")
    if str(getattr(policy, "mode", "evaluate_only")) == "auto_low_risk":
        if not isinstance(canary_ref, Mapping):
            raise EvolutionContractError(
                "auto_low_risk evolution requires an independent canary_evaluator_ref"
            )
        hydrated_canary = hydrate_sidecar_ref(
            state_dir,
            dict(canary_ref),
            purpose="evolution-canary-intake",
            actor="run-manager",
        )
        if not isinstance(hydrated_canary.payload, Mapping):
            raise EvolutionContractError("canary evaluator payload is invalid")
        canary = validate_evaluator_generation(hydrated_canary.payload)
        if canary["generation_digest"] == evaluator["generation_digest"]:
            raise EvolutionContractError("canary evaluator must be independent")
        body["canary_evaluator"] = canary
        body["canary_evaluator_ref"] = dict(canary_ref)
    return body


def _normalize_skill_candidate(
    body: dict[str, Any],
    *,
    deposition: Mapping[str, Any],
    state_dir: Path,
    evaluator: Mapping[str, Any],
) -> None:
    skill_name = str(body.get("skill_name") or "").strip()
    role_instance = str(body.get("role_instance") or "").strip()
    if not skill_name or not role_instance:
        raise EvolutionContractError(
            "skill_prompt candidate requires skill_name and role_instance"
        )
    suite_ref = body.get("skill_eval_suite_ref")
    if not isinstance(suite_ref, Mapping):
        raise EvolutionContractError(
            "skill_prompt candidate requires skill_eval_suite_ref"
        )
    hydrated = hydrate_sidecar_ref(
        state_dir,
        dict(suite_ref),
        purpose="skill-evolution-suite-intake",
        actor="run-manager",
    )
    if not isinstance(hydrated.payload, Mapping):
        raise EvolutionContractError("Skill eval suite payload is invalid")
    suite = validate_skill_eval_suite(hydrated.payload)
    task_family = str(body["task_family"])
    source_rows = body.get("source_trajectories")
    if not isinstance(source_rows, list) or not source_rows:
        deposition_id = str(deposition.get("artifact_id") or body["asset_id"])
        source_rows = [{
            "ref": f"capability-deposition://{deposition_id}",
            "digest": stable_digest(deposition),
            "outcome": "passed",
        }]
    applicability = (
        dict(body.get("applicability") or {})
        if isinstance(body.get("applicability"), Mapping)
        else {}
    )
    applicability_identity = {
        "task_family": task_family,
        "role_instance": role_instance,
        "applicability": applicability,
    }
    candidate = validate_skill_candidate({
        "schema_version": "skill-candidate.v1",
        "skill_name": skill_name,
        "candidate_version": str(body.get("candidate_version") or ""),
        "task_families": [task_family],
        "applicability_ref": (
            f"capability-deposition://"
            f"{str(deposition.get('artifact_id') or body['asset_id'])}"
        ),
        "applicability_digest": stable_digest(applicability_identity),
        "source_trajectories": source_rows,
        "content": str(body["content"]),
        "public_eval_suite_ref": str(suite_ref.get("ref") or ""),
        "public_eval_suite_digest": str(suite["suite_digest"]),
        "sealed_eval_generation_ref": str(
            evaluator.get("holdout_authority_ref") or ""
        ),
        "evaluation_purpose": str(suite["evaluation_purpose"]),
        "routing_mode": str(suite["routing_mode"]),
    })
    body["skill_name"] = skill_name
    body["role_instance"] = role_instance
    body["skill_candidate"] = candidate
    body["skill_eval_suite"] = suite
    body["skill_eval_suite_ref"] = dict(suite_ref)
    body["support_skill_names"] = _string_list(body.get("support_skill_names"))
    if suite["evaluation_purpose"] == "adoption_lift":
        routing_ref = body.get("routing_stress_ref")
        if not isinstance(routing_ref, Mapping):
            raise EvolutionContractError(
                "adoption_lift skill candidate requires routing_stress_ref"
            )
        verify_skill_routing_stress_ref(
            state_dir,
            routing_ref,
            expected_skill=skill_name,
            expected_candidate_digest=str(candidate["content_digest"]),
            expected_eval_suite_digest=str(suite["suite_digest"]),
        )
        body["routing_stress_ref"] = dict(routing_ref)


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(
        str(item).strip() for item in value if str(item).strip()
    ))
