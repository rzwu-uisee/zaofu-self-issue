"""Immutable attempt input for semantic replan and repair dispatches."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.runtime.call_result_envelope import write_immutable_json_sidecar

SEMANTIC_REPLAN_REQUEST_SCHEMA = "semantic-replan-request.v1"
SEMANTIC_REPLAN_SOURCE_ID = "semantic-replan-request"


def replan_sources(
    state_dir: Path,
    payload: Mapping[str, Any],
    *,
    attempt_id: str,
    source_event_id: str,
) -> list[dict[str, Any]]:
    trigger = (
        payload.get("trigger_payload")
        if isinstance(payload.get("trigger_payload"), Mapping)
        else {}
    )
    schema = str(trigger.get("schema_version") or "").strip()
    if not (
        schema == SEMANTIC_REPLAN_REQUEST_SCHEMA
        or (
            bool(str(trigger.get("guidance") or "").strip())
            and str(trigger.get("recommended_action") or "").strip()
            in {"replan", "repair"}
        )
    ):
        return []
    descriptor = write_immutable_json_sidecar(
        state_dir,
        dict(trigger),
        root=f"attempts/{_safe_component(attempt_id)}/handoff-inputs",
        kind=SEMANTIC_REPLAN_SOURCE_ID,
        schema_version=schema or SEMANTIC_REPLAN_REQUEST_SCHEMA,
        created_by="semantic-replan-handoff",
        source_event_id=source_event_id,
    )
    return [{
        **descriptor,
        "source_id": SEMANTIC_REPLAN_SOURCE_ID,
        "artifact_id": SEMANTIC_REPLAN_SOURCE_ID,
        "allowed_paths": ["$"],
    }]


def required_read_paths(sources: list[Any]) -> dict[str, tuple[str, ...]]:
    return {
        SEMANTIC_REPLAN_SOURCE_ID: ("$",)
        for source in sources
        if isinstance(source, Mapping)
        and str(source.get("source_id") or "") == SEMANTIC_REPLAN_SOURCE_ID
    }


def _safe_component(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "-_." else "_"
        for character in str(value or "")
    ) or "attempt"
