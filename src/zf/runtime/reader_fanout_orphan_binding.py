"""Conservative recovery of reader results that lost fanout identity."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from zf.core.events.model import ZfEvent


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
_EXPLICIT_IDENTITY_FIELDS = (
    "operation_id",
    "attempt_id",
    "run_id",
    "task_id",
    "stage_id",
    "output_profile_id",
    "target_commit",
    "candidate_head_commit",
    "task_map_generation",
    "contract_revision",
    "plan_artifact_package_digest",
)


def resolve_orphan_reader_fanout_child(
    runtime: Any,
    event: ZfEvent,
    payload: dict[str, Any],
    *,
    event_order: Mapping[str, int] | None = None,
    recovery_snapshot: Any | None = None,
) -> tuple[str, str] | None:
    """Resolve one bare result without crossing dispatch authority domains."""

    status = str(payload.get("status") or "")
    if status not in _RESULT_STATUSES and not isinstance(payload.get("report"), dict):
        return None
    role_instance = str(payload.get("role_instance") or event.actor or "")
    if not role_instance:
        return None
    if recovery_snapshot is not None:
        candidates = recovery_snapshot.orphan_candidates.get(role_instance, ())
        if not candidates:
            return None
        from zf.runtime.reader_fanout_recovery_snapshot import (
            event_log_snapshot_token,
        )

        if (
            event_log_snapshot_token(runtime.event_log)
            != recovery_snapshot.event_log_token
        ):
            return None
        event_order = event_order or recovery_snapshot.event_order
        matches = _matching_snapshot_candidates(
            runtime,
            event,
            payload,
            candidates=candidates,
            event_order=event_order,
            recovery_snapshot=recovery_snapshot,
        )
        return matches[0] if len(matches) == 1 else None

    fanout_root = runtime.state_dir / "fanouts"
    if not fanout_root.exists():
        return None
    if event_order is None:
        try:
            event_order = {
                item.id: index
                for index, item in enumerate(runtime.event_log.read_all())
            }
        except OSError:
            event_order = {}

    matches: list[tuple[str, str]] = []
    for manifest_path in fanout_root.glob("*/manifest.json"):
        fanout_id = manifest_path.parent.name
        manifest = runtime._fanout_manifest(fanout_id)
        if not manifest or manifest.get("topology") != "fanout_reader":
            continue
        aggregate = manifest.get("aggregate")
        aggregate = aggregate if isinstance(aggregate, dict) else {}
        if (
            str(manifest.get("status") or "") in _TERMINAL_FANOUT_STATUSES
            or str(aggregate.get("status") or "") in _TERMINAL_FANOUT_STATUSES
        ):
            continue
        stale_reason, _superseded_by = runtime._fanout_identity_stale_reason(
            fanout_id,
        )
        if stale_reason:
            continue
        aggregate_config = manifest.get("aggregate_config") or {}
        success_event = str(aggregate_config.get("success_event") or "")
        failure_event = str(aggregate_config.get("failure_event") or "")
        child_success_event, child_failure_event = (
            runtime._fanout_child_result_events(aggregate_config)
        )
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
        for child in manifest.get("children", []) or []:
            if not isinstance(child, dict):
                continue
            if str(child.get("role_instance") or "") != role_instance:
                continue
            if str(child.get("status") or "") in {"completed", "failed"}:
                continue
            if not _result_follows_dispatch(event, child, event_order):
                continue
            if not _explicit_identity_matches(event, payload, manifest, child):
                continue
            child_id = str(child.get("child_id") or "")
            if child_id:
                matches.append((fanout_id, child_id))
    if len(matches) == 1:
        return matches[0]
    return None


def _matching_snapshot_candidates(
    runtime: Any,
    event: ZfEvent,
    payload: dict[str, Any],
    *,
    candidates: tuple[Any, ...],
    event_order: Mapping[str, int],
    recovery_snapshot: Any,
) -> list[tuple[str, str]]:
    matches: list[tuple[str, str]] = []
    status = str(payload.get("status") or "")
    for candidate in candidates:
        fanout_id = str(candidate.fanout_id or "")
        child_id = str(candidate.child_id or "")
        if recovery_snapshot.currentness.get(fanout_id, ("", ""))[0]:
            continue
        manifest = recovery_snapshot.manifests_by_id.get(fanout_id)
        if not isinstance(manifest, dict):
            continue
        aggregate = manifest.get("aggregate")
        aggregate = aggregate if isinstance(aggregate, dict) else {}
        if (
            str(manifest.get("status") or "") in _TERMINAL_FANOUT_STATUSES
            or str(aggregate.get("status") or "") in _TERMINAL_FANOUT_STATUSES
        ):
            continue
        child = runtime._fanout_child(manifest, child_id)
        if not isinstance(child, dict):
            continue
        if str(child.get("status") or "") in {"completed", "failed"}:
            continue
        aggregate_config = manifest.get("aggregate_config") or {}
        success_event = str(aggregate_config.get("success_event") or "")
        failure_event = str(aggregate_config.get("failure_event") or "")
        child_success_event, child_failure_event = (
            runtime._fanout_child_result_events(aggregate_config)
        )
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
        if not _result_follows_dispatch(event, child, event_order):
            continue
        if not _explicit_identity_matches(event, payload, manifest, child):
            continue
        matches.append((fanout_id, child_id))
    return matches


def _result_follows_dispatch(
    event: ZfEvent,
    child: Mapping[str, Any],
    event_order: Mapping[str, int],
) -> bool:
    result_index = event_order.get(event.id)
    dispatch_index = event_order.get(str(child.get("last_event_id") or ""))
    if result_index is None or dispatch_index is None:
        return True
    return result_index > dispatch_index


def _explicit_identity_matches(
    event: ZfEvent,
    payload: Mapping[str, Any],
    manifest: Mapping[str, Any],
    child: Mapping[str, Any],
) -> bool:
    child_payload = child.get("payload")
    child_payload = child_payload if isinstance(child_payload, Mapping) else {}
    expected = {**manifest, **child, **child_payload}
    supplied = dict(payload)
    if event.task_id and not supplied.get("task_id"):
        supplied["task_id"] = event.task_id

    supplied_run = str(
        supplied.get("run_id") or supplied.get("dispatch_id") or ""
    )
    if supplied_run:
        supplied["run_id"] = supplied_run
    for field in _EXPLICIT_IDENTITY_FIELDS:
        actual = str(supplied.get(field) or "")
        authoritative = str(expected.get(field) or "")
        if actual and authoritative and actual != authoritative:
            return False
    return True


__all__ = ["resolve_orphan_reader_fanout_child"]
