"""Bounded canonical Channel PRD context for project agents."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from zf.runtime.channel_projection import project_channels


def canonical_channel_prd_context(
    state_dir: Path,
    *,
    limit: int = 8,
) -> dict[str, Any]:
    """Project consensus-backed PRD identities without hydrating their bodies."""
    items: list[dict[str, Any]] = []
    for channel in project_channels(Path(state_dir)).get("channels") or []:
        if not isinstance(channel, dict):
            continue
        syntheses = [
            item
            for item in channel.get("syntheses") or []
            if isinstance(item, dict)
        ]
        consensus_by_thread = channel.get("consensus")
        if not isinstance(consensus_by_thread, dict):
            continue
        for thread_id, consensus in consensus_by_thread.items():
            if not isinstance(consensus, dict):
                continue
            artifact_ref = str(consensus.get("artifact_ref") or "").strip()
            artifact_digest = str(
                consensus.get("artifact_digest") or ""
            ).strip()
            reached_event_id = str(
                consensus.get("reached_event_id") or ""
            ).strip()
            if not artifact_ref or not artifact_digest or not reached_event_id:
                continue
            synthesis = next(
                (
                    item
                    for item in reversed(syntheses)
                    if str(item.get("thread_id") or "main")
                    == str(thread_id or "main")
                    and str(item.get("artifact_ref") or "") == artifact_ref
                    and _bare_digest(item.get("artifact_digest"))
                    == _bare_digest(artifact_digest)
                ),
                None,
            )
            if synthesis is None:
                continue
            readiness_verdict = str(
                synthesis.get("readiness_verdict")
                or consensus.get("readiness_verdict")
                or "unassessed"
            ).strip()
            implementation_start = synthesis.get(
                "implementation_start",
                consensus.get("implementation_start"),
            )
            if readiness_verdict != "ready" or implementation_start is not True:
                continue
            source_refs = list(dict.fromkeys([
                *(
                    str(item).strip()
                    for item in synthesis.get("source_refs") or []
                    if str(item).strip()
                ),
                *(
                    str(item).strip()
                    for item in consensus.get("source_refs") or []
                    if str(item).strip()
                ),
            ]))
            channel_id = str(channel.get("channel_id") or "")
            normalized_thread_id = str(thread_id or "main")
            items.append({
                "channel_id": channel_id,
                "channel_name": str(channel.get("name") or ""),
                "thread_id": normalized_thread_id,
                "artifact_ref": artifact_ref,
                "artifact_digest": artifact_digest,
                "source_ref": (
                    f"channel:{channel_id}/{normalized_thread_id}"
                ),
                "synthesis_event_id": str(
                    synthesis.get("event_id") or ""
                ),
                "consensus_event_id": reached_event_id,
                "readiness_ref": str(synthesis.get("readiness_ref") or ""),
                "readiness_digest": str(
                    synthesis.get("readiness_digest") or ""
                ),
                "readiness_verdict": readiness_verdict,
                "implementation_start": True,
                "source_refs": source_refs,
                "updated_at": str(channel.get("updated_at") or ""),
            })
    bounded_limit = min(20, max(1, int(limit)))
    items.sort(
        key=lambda item: (
            str(item.get("updated_at") or ""),
            str(item.get("consensus_event_id") or ""),
        ),
        reverse=True,
    )
    return {
        "schema_version": "channel-prd-context.v1",
        "is_derived_projection": True,
        "items": items[:bounded_limit],
    }


def _bare_digest(value: object) -> str:
    return str(value or "").strip().removeprefix("sha256:")


def workflow_context_from_payload(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    value = payload.get("workflow_context")
    return dict(value) if isinstance(value, dict) else {}


def workflow_context_for_project(
    payload: Mapping[str, Any],
    project_root: Path,
) -> dict[str, Any]:
    """Add only deterministic project-local defaults to Agent Plan context."""

    context = workflow_context_from_payload(payload)
    if not str(context.get("target_root") or "").strip():
        context["target_root"] = str(Path(project_root).resolve())
    return context


__all__ = [
    "canonical_channel_prd_context",
    "workflow_context_for_project",
    "workflow_context_from_payload",
]
