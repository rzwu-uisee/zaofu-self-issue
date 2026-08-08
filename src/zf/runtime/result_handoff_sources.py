"""Exact source-manifest entries for admitted worker results."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


def result_handoff_source_entries(
    payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for source_id, field in (
        ("admitted-call-result", "admitted_call_result_ref"),
        ("control-result", "control_result_ref"),
    ):
        descriptor = payload.get(field)
        if not isinstance(descriptor, Mapping):
            continue
        ref = str(descriptor.get("ref") or "").strip()
        digest = str(descriptor.get("sha256") or "").strip()
        if not ref or not digest:
            continue
        entries.append({
            "source_id": source_id,
            "artifact_id": Path(ref).name,
            "kind": str(descriptor.get("kind") or source_id),
            "schema_version": str(descriptor.get("schema_version") or ""),
            "ref": ref,
            "sha256": digest,
            "allowed_paths": ["$"],
        })
    return entries


__all__ = ["result_handoff_source_entries"]
