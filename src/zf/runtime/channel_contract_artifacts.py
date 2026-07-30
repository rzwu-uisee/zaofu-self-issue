"""Versioned sidecars for typed Channel contribution and synthesis bodies."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

from zf.core.security.redaction import redact_obj
from zf.runtime.sidecar_refs import write_sidecar_json


CONTRIBUTION_SCHEMA_VERSION = "channel.contribution.v2"
SYNTHESIS_SCHEMA_VERSION = "channel.synthesis.v2"


def validate_channel_contract(
    body: dict[str, Any],
    *,
    kind: str,
) -> str:
    summary = body.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return "summary must be a non-empty string"
    list_fields = (
        (
            "questions",
            "open_questions",
            "findings",
            "contradictions",
            "risks",
            "source_refs",
            "evidence_refs",
        )
        if kind == "contribution"
        else (
            "decisions",
            "assumptions",
            "out_of_scope",
            "acceptance_criteria",
            "open_questions",
            "risks",
            "source_refs",
            "evidence_refs",
            "consumed_contribution_refs",
            "consumed_contribution_digests",
            "dissent",
        )
    )
    for field in list_fields:
        value = body.get(field)
        if value is not None and not isinstance(value, list):
            return f"{field} must be a list"
    if kind == "contribution":
        freeze = body.get("freeze")
        if freeze is not None and not isinstance(freeze, bool):
            return "freeze must be a boolean"
    else:
        workflow = body.get("recommended_workflow")
        if workflow is not None and not isinstance(workflow, dict):
            return "recommended_workflow must be an object"
        classification = body.get("classification")
        if classification is not None and not isinstance(
            classification,
            dict,
        ):
            return "classification must be an object"
    return ""


def persist_channel_contract(
    state_dir: Path,
    *,
    channel_id: str,
    thread_id: str,
    identity: str,
    kind: str,
    body: dict[str, Any],
    created_by: str,
    source_event_id: str,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    schema_version = (
        CONTRIBUTION_SCHEMA_VERSION
        if kind == "contribution"
        else SYNTHESIS_SCHEMA_VERSION
    )
    safe_channel = _safe_segment(channel_id)
    safe_identity = _safe_segment(identity)
    payload = redact_obj({
        "schema_version": schema_version,
        "kind": kind,
        "channel_id": channel_id,
        "thread_id": thread_id,
        "identity": identity,
        "body": body,
        "provenance": provenance or {},
    })
    return write_sidecar_json(
        Path(state_dir),
        (
            PurePosixPath("channels")
            / safe_channel
            / "contracts"
            / kind
            / f"{safe_identity}.json"
        ),
        payload,
        kind=f"channel_{kind}",
        schema_version=schema_version,
        created_by=created_by,
        source_event_id=source_event_id,
        access_scope={
            "visibility": "project",
            "channel_id": channel_id,
            "thread_id": thread_id,
        },
        retention={"class": "audit_required"},
        required=True,
        preview=str(body.get("summary") or "")[:240],
    )


def persist_channel_source_manifest(
    state_dir: Path,
    *,
    channel_id: str,
    thread_id: str,
    identity: str,
    source_refs: list[str],
    evidence_refs: list[str],
    created_by: str,
    source_event_id: str,
) -> dict[str, Any]:
    payload = {
        "schema_version": "channel.source_manifest.v1",
        "channel_id": channel_id,
        "thread_id": thread_id,
        "identity": identity,
        "source_refs": source_refs,
        "evidence_refs": evidence_refs,
    }
    return write_sidecar_json(
        Path(state_dir),
        (
            PurePosixPath("channels")
            / _safe_segment(channel_id)
            / "source-manifests"
            / f"{_safe_segment(identity)}.json"
        ),
        payload,
        kind="channel_source_manifest",
        schema_version="channel.source_manifest.v1",
        created_by=created_by,
        source_event_id=source_event_id,
        access_scope={
            "visibility": "project",
            "channel_id": channel_id,
            "thread_id": thread_id,
        },
        retention={"class": "audit_required"},
        required=True,
        preview=f"{len(source_refs)} sources, {len(evidence_refs)} evidence refs",
    )


def typed_items(value: object) -> list[Any]:
    if not isinstance(value, list):
        return []
    return [redact_obj(item) for item in value[:64]]


def string_refs(value: object, *, limit: int = 64) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(
        str(item).strip()
        for item in value
        if isinstance(item, str) and str(item).strip()
    ))[:limit]


def _safe_segment(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "")).strip(".-")
    return safe or "unknown"


__all__ = [
    "CONTRIBUTION_SCHEMA_VERSION",
    "SYNTHESIS_SCHEMA_VERSION",
    "persist_channel_contract",
    "persist_channel_source_manifest",
    "string_refs",
    "typed_items",
    "validate_channel_contract",
]
