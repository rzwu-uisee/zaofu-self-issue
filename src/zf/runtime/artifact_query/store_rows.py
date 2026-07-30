"""Artifact catalog row projection and lineage relation helpers."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable, Mapping

from zf.core.events.model import ZfEvent


def catalog_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "object_id": str(row["object_id"] or ""),
        "sha256": str(row["sha256"] or ""),
        "byte_count": int(row["byte_count"] or 0),
        "locator_id": str(row["locator_id"] or ""),
        "project_scope": str(row["project_scope"] or ""),
        "state_scope": str(row["state_scope"] or ""),
        "ref": str(row["ref"] or ""),
        "kind": str(row["kind"] or ""),
        "storage_kind": str(row["storage_kind"] or row["kind"] or ""),
        "semantic_kind": str(row["semantic_kind"] or "untyped"),
        "schema_version": str(row["schema_version"] or ""),
        "content_type": str(row["content_type"] or ""),
        "encoding": str(row["encoding"] or ""),
        "health": str(row["health"] or "unknown"),
        "occurrence_id": str(row["occurrence_id"] or ""),
        "event_id": str(row["event_id"] or ""),
        "source_event_id": str(row["source_event_id"] or ""),
        "source_seq": int(row["source_seq"] or 0),
        "source_kind": str(row["source_kind"] or ""),
        "producer_actor": str(row["producer_actor"] or ""),
        "status": str(row["status"] or ""),
        "run_id": str(row["run_id"] or ""),
        "task_id": str(row["task_id"] or ""),
        "claim_id": str(row["claim_id"] or ""),
        "stage_id": str(row["stage_id"] or ""),
        "attempt_id": str(row["attempt_id"] or ""),
        "attempt_domain": str(row["attempt_domain"] or ""),
        "operation_id": str(row["operation_id"] or ""),
        "package_id": str(row["package_id"] or ""),
        "required": bool(row["required"]),
        "access_scope": _json_object(row["access_scope_json"]),
        "retention": _json_object(row["retention_json"]),
        "created_by": str(row["created_by"] or ""),
        "preview": str(row["preview"] or ""),
    }


def subjects(row: Mapping[str, Any]) -> Iterable[tuple[str, str]]:
    for kind, key in (
        ("run", "run_id"),
        ("task", "task_id"),
        ("claim", "claim_id"),
        ("stage", "stage_id"),
        ("attempt", "attempt_id"),
        ("operation", "operation_id"),
        ("package", "package_id"),
    ):
        value = str(row.get(key) or "").strip()
        if value:
            yield kind, value


_RELATIONS = {
    "causation",
    "evidence",
    "inherits",
    "input",
    "output",
    "read",
    "supersedes",
    "target",
}

_KIND_RELATIONS = {
    "acceptance_matrix": "evidence",
    "artifact_read_ledger": "read",
    "attempt_source_manifest": "input",
    "call_result_envelope": "evidence",
    "contract_snapshot": "input",
    "goal_claim_set": "input",
    "impl_self_check": "evidence",
    "input_consumption_policy": "input",
    "issue_spec": "input",
    "planning_result": "input",
    "requirement_spec": "input",
    "run_contract": "input",
    "target_snapshot": "target",
    "task_contract_snapshot": "input",
    "task_map": "input",
    "test_matrix": "evidence",
    "verification_result": "evidence",
    "workflow_input_manifest": "input",
}

_SCHEMA_RELATIONS = {
    "artifact-read-ledger.v1": "read",
    "attempt-source-manifest.v1": "input",
    "input-consumption-policy.v1": "input",
    "target-snapshot.v1": "target",
    "task-contract-snapshot.v1": "input",
    "verification-result.v1": "evidence",
}

_EVENT_RELATIONS = {
    "artifact.read.recorded": "read",
    "plan.artifact_package.superseded": "supersedes",
}


def relation(
    *,
    event: ZfEvent,
    descriptor: Mapping[str, Any],
) -> str:
    explicit = str(
        descriptor.get("relation")
        or descriptor.get("lineage_relation")
        or ""
    ).strip()
    if explicit:
        return explicit if explicit in _RELATIONS else "output"
    schema_version = str(descriptor.get("schema_version") or "").strip()
    if schema_version in _SCHEMA_RELATIONS:
        return _SCHEMA_RELATIONS[schema_version]
    kind = str(descriptor.get("kind") or "").strip()
    if kind in _KIND_RELATIONS:
        return _KIND_RELATIONS[kind]
    return _EVENT_RELATIONS.get(event.type, "output")


def is_result_event(event_type: str) -> bool:
    lowered = str(event_type or "").lower()
    return any(
        token in lowered
        for token in ("result", "completed", "passed", "approved", "admitted")
    )


def _json_object(value: object) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


__all__ = ["catalog_row", "is_result_event", "relation", "subjects"]
