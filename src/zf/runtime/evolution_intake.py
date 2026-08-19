"""Verified Learn archive intake for unattended evolution campaigns."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.evolution_contracts import (
    EvolutionContractError,
    validate_evaluator_generation,
)
from zf.runtime.run_archive import verify_run_archive
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


_LOW_RISK_ASSET_KINDS = frozenset({
    "memory_entry",
    "runbook",
    "regression_fixture",
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
