"""Resolve flow-scoped Workflow stage backedges from durable event identity."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent


def backedge_for_event(config: Any, trigger_event: ZfEvent | str) -> Any | None:
    event_type = (
        trigger_event.type
        if isinstance(trigger_event, ZfEvent)
        else str(trigger_event or "")
    )
    if not event_type:
        return None
    candidates = []
    for stage in getattr(getattr(config, "workflow", None), "stages", []) or []:
        for backedge in (
            getattr(stage, "on_reject", None),
            getattr(stage, "on_fail", None),
        ):
            if backedge is not None and getattr(backedge, "event", "") == event_type:
                candidates.append((stage, backedge))
    if len(candidates) <= 1:
        return candidates[0][1] if candidates else None
    if not isinstance(trigger_event, ZfEvent):
        return None
    payload = trigger_event.payload if isinstance(trigger_event.payload, dict) else {}
    stage_id = str(payload.get("stage_id") or "").strip()
    if stage_id:
        matches = [
            backedge
            for stage, backedge in candidates
            if str(getattr(stage, "id", "") or "").strip() == stage_id
        ]
        return matches[0] if len(matches) == 1 else None
    flow_kind = str(payload.get("flow_kind") or "").strip().lower()
    if flow_kind:
        matches = [
            backedge
            for stage, backedge in candidates
            if str(getattr(stage, "flow_kind", "") or "").strip().lower()
            == flow_kind
        ]
        return matches[0] if len(matches) == 1 else None
    return None


__all__ = ["backedge_for_event"]
