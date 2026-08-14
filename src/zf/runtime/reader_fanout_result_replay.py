"""Replay durable reader results against one recovery snapshot."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.fanout_recovery_runtime import reader_result_replay_events
from zf.runtime.reader_fanout_recovery_snapshot import (
    event_log_snapshot_token,
    reader_recovery_event_may_match,
)
from zf.runtime.writer_contract_handoff import recoverable_writer_handoff_failure


_TERMINAL_FANOUT_STATUSES = frozenset({
    "completed",
    "failed",
    "timed_out",
    "cancelled",
})
_RESULT_STATUSES = frozenset({
    "completed",
    "passed",
    "approved",
    "success",
    "failed",
    "failure",
    "rejected",
})


def resume_unrecorded_reader_fanout_results(
    runtime: Any,
    events: list[ZfEvent],
    *,
    recovery_snapshot: Any | None = None,
) -> bool:
    """Replay reader child results whose canonical terminal was missed."""

    event_order = (
        recovery_snapshot.event_order
        if recovery_snapshot is not None
        else {event.id: index for index, event in enumerate(events)}
    )
    terminal_sources: set[str] = set()
    terminal_children: set[tuple[str, str]] = set()
    for event in events:
        if event.type not in {"fanout.child.completed", "fanout.child.failed"}:
            continue
        if recoverable_writer_handoff_failure(event):
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        fanout_id = str(payload.get("fanout_id") or "")
        child_id = str(payload.get("child_id") or "")
        if fanout_id and child_id:
            terminal_children.add((fanout_id, child_id))
        result_event_id = str(payload.get("result_event_id") or "")
        if result_event_id:
            terminal_sources.add(result_event_id)
        if event.causation_id:
            terminal_sources.add(str(event.causation_id))

    replay_events = reader_result_replay_events(
        runtime,
        events,
        terminal_children=terminal_children,
        recovery_snapshot=recovery_snapshot,
    )
    recovered = False
    for event in replay_events:
        if event.id in terminal_sources:
            continue
        if recovery_snapshot is not None:
            if not reader_recovery_event_may_match(recovery_snapshot, event):
                continue
            if (
                event_log_snapshot_token(runtime.event_log)
                != recovery_snapshot.event_log_token
            ):
                return recovered
        payload = runtime._fanout_result_payload(event)
        fanout_id = str(payload.get("fanout_id") or "")
        child_id = str(payload.get("child_id") or payload.get("child_run") or "")
        if not fanout_id or not child_id:
            resolved = runtime._resolve_orphan_reader_fanout_child(
                event,
                payload,
                event_order=event_order,
                recovery_snapshot=recovery_snapshot,
            )
            if resolved is None:
                continue
            fanout_id, child_id = resolved
        manifest = (
            recovery_snapshot.manifests_by_id.get(fanout_id)
            if recovery_snapshot is not None
            else runtime._fanout_manifest(fanout_id)
        )
        if not manifest or manifest.get("topology") != "fanout_reader":
            continue
        aggregate = (
            manifest.get("aggregate")
            if isinstance(manifest.get("aggregate"), dict)
            else {}
        )
        if (
            str(manifest.get("status") or "") in _TERMINAL_FANOUT_STATUSES
            or str(aggregate.get("status") or "") in _TERMINAL_FANOUT_STATUSES
        ):
            continue
        aggregate_config = manifest.get("aggregate_config") or {}
        success_event = str(aggregate_config.get("success_event") or "")
        failure_event = str(aggregate_config.get("failure_event") or "")
        child_success_event, child_failure_event = (
            runtime._fanout_child_result_events(aggregate_config)
        )
        status = str(payload.get("status") or "")
        if (
            event.type not in {
                child_success_event,
                child_failure_event,
                success_event,
                failure_event,
            }
            and status not in _RESULT_STATUSES
        ):
            continue
        child = runtime._fanout_child(manifest, child_id)
        if not child:
            continue
        if (
            str(child.get("status") or "") in {"completed", "failed"}
            and (fanout_id, child_id) in terminal_children
        ):
            continue
        before = event_log_snapshot_token(runtime.event_log)
        replay_event = replace(
            event,
            payload={
                **payload,
                "fanout_id": fanout_id,
                "child_id": child_id,
            },
        )
        runtime._maybe_update_reader_fanout(
            replay_event,
            recovery_snapshot=recovery_snapshot,
        )
        changed = event_log_snapshot_token(runtime.event_log) != before
        recovered = recovered or changed
        if changed and recovery_snapshot is not None:
            return True
    return recovered


__all__ = ["resume_unrecorded_reader_fanout_results"]
