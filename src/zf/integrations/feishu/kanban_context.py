"""Bounded canonical planning context for Feishu Kanban Agent turns."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.runtime.channel_prd_context import (
    canonical_channel_prd_authority,
    canonical_channel_prd_context,
)
from zf.runtime.channel_projection import project_channel
from zf.runtime.workflow_route_catalog import workflow_route_catalog


def build_feishu_kanban_planning_context(
    state_dir: Path,
    config: Any | None,
    *,
    chat_id: str,
    root_message_id: str = "",
    parent_message_id: str = "",
) -> dict[str, Any]:
    """Select authority only when one ready PRD matches the Feishu origin."""

    state = Path(state_dir)
    prds = canonical_channel_prd_context(state)
    origin_message_id = str(root_message_id or parent_message_id or "").strip()
    candidates: list[dict[str, Any]] = []
    for item in prds.get("items") or []:
        if not isinstance(item, dict):
            continue
        channel_id = str(item.get("channel_id") or "")
        thread_id = str(item.get("thread_id") or "main")
        channel = project_channel(state, channel_id) or {}
        origin = (
            channel.get("origin_binding")
            if isinstance(channel.get("origin_binding"), dict)
            else {}
        )
        if (
            str(origin.get("surface") or "") != "feishu"
            or str(origin.get("chat_id") or "") != str(chat_id or "")
        ):
            continue
        bound_message_id = str(origin.get("origin_message_id") or "")
        if origin_message_id and bound_message_id != origin_message_id:
            continue
        authority = canonical_channel_prd_authority(
            state,
            channel_id=channel_id,
            thread_id=thread_id,
        )
        if authority:
            candidates.append(authority)

    status = "exact" if len(candidates) == 1 else (
        "unavailable" if not candidates else "ambiguous"
    )
    return {
        "schema_version": "feishu-kanban-planning-context.v1",
        "selection_status": status,
        "workflow_route_catalog": workflow_route_catalog(config),
        "canonical_channel_prds": prds,
        "workflow_parameters": candidates[0] if len(candidates) == 1 else {},
    }


__all__ = ["build_feishu_kanban_planning_context"]
