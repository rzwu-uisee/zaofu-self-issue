"""Versioned, schema-aware descriptor extraction for the artifact catalog."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, Iterable, Mapping

from zf.runtime.sidecar_refs import (
    SidecarRefError,
    hydrate_sidecar_ref,
    iter_sidecar_ref_descriptors,
)


REGISTRY_VERSION = "artifact-semantic-extractor-registry.v2"
MAX_ENVELOPE_DEPTH = 3
MAX_ENVELOPE_BYTES = 4 * 1024 * 1024

_SCHEMA_SEMANTIC_KINDS = {
    "call-result-envelope.v1": "call_result_envelope",
    "artifact-delivery-result.v1": "artifact_delivery_result",
    "effective-config-snapshot.v1": "effective_config_snapshot",
    "fanout-aggregate-result.v1": "fanout_aggregate_result",
    "goal-closure-result.v1": "goal_closure_result",
    "goal-completion-receipt.v1": "goal_completion_receipt",
    "goal-dossier.v1": "goal_dossier",
    "implementation-result.v1": "implementation_result",
    "plan-artifact-package.v1": "plan_artifact_package",
    "plan-synthesis-result.v1": "plan_synthesis_result",
    "plan-synth-contract.v1": "plan_synth_contract",
    "run-contract-snapshot.v1": "run_contract",
    "run-contract.v1": "run_contract",
    "task-contract-snapshot.v1": "task_contract",
    "task-map-materialization-plan.v1": "task_map_materialization_plan",
    "verification-result.v1": "verification_result",
    "workflow-config-apply-receipt.v1": "workflow_config_apply_receipt",
    "workflow-flow-spec-snapshot.v1": "workflow_flow_spec",
    "workflow-proposal.v1": "workflow_proposal",
    "workflow-synthesis-result.v1": "workflow_synthesis_result",
}

_DESCRIPTOR_KIND_SEMANTIC_KINDS = {
    "acceptance_matrix": "acceptance_matrix",
    "call_result_envelope": "call_result_envelope",
    "artifact_delivery_result": "artifact_delivery_result",
    "goal_claim_set": "goal_claim_set",
    "goal_closure_result": "goal_closure_result",
    "goal_completion_receipt": "goal_completion_receipt",
    "goal_dossier": "goal_dossier",
    "plan_artifact_package": "plan_artifact_package",
    "run_contract": "run_contract",
    "task_contract_snapshot": "task_contract",
    "task_map": "task_map",
    "test_matrix": "test_matrix",
    "verification_result": "verification_result",
}

_CONTAINER_SCHEMAS = frozenset({
    "call-result-envelope.v1",
    "artifact-delivery-result.v1",
    "goal-completion-receipt.v1",
    "goal-dossier.v1",
    "plan-artifact-package.v1",
})
_LEGACY_CONTAINER_STORAGE_KINDS = frozenset({
    "plan_artifact_package",
})


def semantic_kind_for_descriptor(descriptor: Mapping[str, Any]) -> str:
    """Resolve a semantic kind from registered schema or descriptor rules."""

    schema_version = str(descriptor.get("schema_version") or "").strip()
    if schema_version in _SCHEMA_SEMANTIC_KINDS:
        return _SCHEMA_SEMANTIC_KINDS[schema_version]
    storage_kind = str(descriptor.get("kind") or "").strip()
    return _DESCRIPTOR_KIND_SEMANTIC_KINDS.get(storage_kind, "untyped")


def iter_catalog_descriptors(
    state_dir: Path,
    payload: Any,
) -> Iterable[dict[str, Any]]:
    """Yield typed descriptors from an event and registered container bodies."""

    queue: deque[tuple[dict[str, Any], int, str]] = deque(
        (descriptor, 0, "")
        for descriptor in iter_sidecar_ref_descriptors(payload)
    )
    seen: set[tuple[str, str, str]] = set()
    while queue:
        descriptor, depth, container_ref = queue.popleft()
        storage_kind = str(descriptor.get("kind") or "sidecar").strip()
        schema_version = str(descriptor.get("schema_version") or "").strip()
        ref = str(descriptor.get("ref") or "").strip()
        key = (storage_kind, schema_version, ref)
        if not ref or key in seen:
            continue
        seen.add(key)
        typed = {
            **descriptor,
            "storage_kind": storage_kind,
            "semantic_kind": semantic_kind_for_descriptor(descriptor),
            "extractor_registry_version": REGISTRY_VERSION,
        }
        if container_ref:
            typed["source_container_ref"] = container_ref
        yield typed

        if depth >= MAX_ENVELOPE_DEPTH:
            continue
        if (
            schema_version not in _CONTAINER_SCHEMAS
            and storage_kind not in _LEGACY_CONTAINER_STORAGE_KINDS
        ):
            continue
        try:
            body = hydrate_sidecar_ref(
                state_dir,
                descriptor,
                purpose="artifact-catalog-projection",
                actor="kernel",
                max_bytes=MAX_ENVELOPE_BYTES,
            ).payload
        except (OSError, SidecarRefError, ValueError):
            continue
        body_schema = (
            str(body.get("schema_version") or "").strip()
            if isinstance(body, Mapping)
            else ""
        )
        container_schema = schema_version or body_schema
        if container_schema not in _CONTAINER_SCHEMAS:
            continue
        for nested in _iter_registered_container_descriptors(
            body,
            schema_version=container_schema,
        ):
            nested_descriptor = dict(nested)
            nested_descriptor["access_scope"] = _intersect_access_scopes(
                typed.get("access_scope"),
                nested_descriptor.get("access_scope"),
            )
            if (
                not nested_descriptor.get("retention")
                and typed.get("retention")
            ):
                nested_descriptor["retention"] = dict(typed["retention"])
            queue.append((nested_descriptor, depth + 1, ref))


def _iter_registered_container_descriptors(
    body: Any,
    *,
    schema_version: str,
) -> Iterable[dict[str, Any]]:
    yield from iter_sidecar_ref_descriptors(body)
    if (
        schema_version != "plan-artifact-package.v1"
        or not isinstance(body, Mapping)
    ):
        return

    # Plan packages predate SidecarRef fields for the bound Run Contract.
    # The schema nevertheless binds the immutable ref and content digest, so
    # normalize that exact registered shape without guessing from filenames.
    ref = str(body.get("run_contract_ref") or "").strip()
    sha256 = str(body.get("run_contract_sha256") or "").strip()
    if ref and sha256:
        yield {
            "ref_schema_version": "sidecar-ref.v1",
            "kind": "run_contract",
            "ref": ref,
            "sha256": sha256,
            "content_type": "application/json",
            "schema_version": "run-contract-snapshot.v1",
            "encoding": "utf-8",
            "created_by": "plan-artifact-package",
            "access_scope": {},
            "required": True,
        }


def _intersect_access_scopes(
    container_value: object,
    nested_value: object,
) -> dict[str, Any]:
    container = (
        dict(container_value)
        if isinstance(container_value, Mapping)
        else {}
    )
    nested = (
        dict(nested_value)
        if isinstance(nested_value, Mapping)
        else {}
    )
    if not container:
        return nested
    if not nested:
        return container

    combined: dict[str, Any] = {}
    visibility = {
        str(container.get("visibility") or "project"),
        str(nested.get("visibility") or "project"),
    }
    combined["visibility"] = (
        "project"
        if visibility <= {"project", "public"}
        else "restricted"
    )
    for plural, legacy in (
        ("actors", "actor"),
        ("roles", "role"),
        ("purposes", "purpose"),
    ):
        container_values = _scope_values(container, plural, legacy)
        nested_values = _scope_values(nested, plural, legacy)
        if container_values and nested_values:
            allowed = container_values & nested_values
            if not allowed:
                allowed = {"__no_matching_principal__"}
        else:
            allowed = container_values or nested_values
        if allowed:
            combined[plural] = sorted(allowed)
    return combined


def _scope_values(
    scope: Mapping[str, Any],
    plural: str,
    legacy: str,
) -> set[str]:
    raw = scope.get(plural)
    values = (
        {str(item).strip() for item in raw if str(item).strip()}
        if isinstance(raw, (list, tuple, set, frozenset))
        else set()
    )
    legacy_value = str(scope.get(legacy) or "").strip()
    if legacy_value:
        values.add(legacy_value)
    return values


__all__ = [
    "REGISTRY_VERSION",
    "iter_catalog_descriptors",
    "semantic_kind_for_descriptor",
]
