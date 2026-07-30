"""Read-only readiness evidence for promoting TaskAttempt shadow mode."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from zf.core.events.model import ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore, TaskAttemptStoreError


TASK_ATTEMPT_READINESS_SCHEMA_VERSION = "task-attempt-readiness.v1"
_ACTIVE_STATUSES = frozenset({"prepared", "delivering", "sent"})
_IDENTITY_FIELDS = (
    "attempt_id",
    "lease_id",
    "run_id",
    "task_id",
    "operation_id",
    "dispatch_id",
)


def build_task_attempt_readiness(
    state_dir: Path,
    events: Iterable[ZfEvent],
    *,
    mode: str,
    min_comparisons: int = 20,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a mechanical promotion report without changing configuration."""

    state_dir = Path(state_dir)
    mode = str(mode or "shadow").strip().lower()
    min_comparisons = max(int(min_comparisons or 0), 1)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    event_rows = list(events)
    store = TaskAttemptStore(state_dir / "task_attempts.json")
    try:
        rows = store.rows()
        current_rows = store.current_rows()
    except (TaskAttemptStoreError, OSError, ValueError) as exc:
        return _report(
            mode=mode,
            min_comparisons=min_comparisons,
            blockers=[_blocker("store_unreadable", 1, str(exc))],
            status_counts={},
            row_count=0,
            current_count=0,
            comparison_count=0,
            matched_comparison_count=0,
            mismatch_count=0,
            rejection_count=0,
            terminal_without_comparison=[],
            identity_gap_attempt_ids=[],
            active_attempt_ids=[],
            expired_attempt_ids=[],
            retry_pending_attempt_ids=[],
            deadletter_attempt_ids=[],
        )

    comparisons = [
        event
        for event in event_rows
        if event.type == "task.attempt.shadow.compared"
    ]
    comparison_attempt_ids = {
        str(_payload(event).get("attempt_id") or "").strip()
        for event in comparisons
        if str(_payload(event).get("attempt_id") or "").strip()
    }
    matched_comparisons = [
        event for event in comparisons if _payload(event).get("match") is True
    ]
    comparison_mismatches = [
        event for event in comparisons if _payload(event).get("match") is not True
    ]
    shadow_mismatches = [
        event
        for event in event_rows
        if event.type == "task.attempt.shadow_mismatch"
    ]
    rejections = [
        event
        for event in event_rows
        if event.type == "task.attempt.result_rejected"
    ]
    settled = [
        row for row in rows if str(row.get("terminal_event_id") or "").strip()
    ]
    terminal_without_comparison = sorted(
        str(row.get("attempt_id") or "")
        for row in settled
        if str(row.get("attempt_id") or "") not in comparison_attempt_ids
    )
    identity_gap_attempt_ids = sorted(
        str(row.get("attempt_id") or "<unknown>")
        for row in rows
        if any(not str(row.get(field) or "").strip() for field in _IDENTITY_FIELDS)
    )
    active_attempt_ids = sorted(
        str(row.get("attempt_id") or "")
        for row in current_rows
        if str(row.get("status") or "") in _ACTIVE_STATUSES
    )
    expired_attempt_ids = sorted(
        str(row.get("attempt_id") or "")
        for row in current_rows
        if (
            str(row.get("status") or "") == "expired"
            or (
                str(row.get("status") or "") in _ACTIVE_STATUSES
                and _timestamp_expired(
                    str(row.get("lease_expires_at") or ""),
                    now=now,
                )
            )
        )
    )
    retry_pending_attempt_ids = sorted(
        str(row.get("attempt_id") or "")
        for row in current_rows
        if (
            str(row.get("status") or "") in {"failed", "expired"}
            and row.get("retryable") is not False
            and str(row.get("recovery_owner") or "scheduler") == "scheduler"
        )
    )
    deadletter_attempt_ids = sorted(
        str(row.get("attempt_id") or "")
        for row in current_rows
        if str(row.get("status") or "") == "deadlettered"
    )

    blockers: list[dict[str, Any]] = []
    comparison_count = len(comparisons)
    mismatch_count = len(comparison_mismatches) + len(shadow_mismatches)
    if comparison_count < min_comparisons:
        blockers.append(_blocker(
            "insufficient_comparisons",
            min_comparisons - comparison_count,
            f"need {min_comparisons} comparisons; observed {comparison_count}",
        ))
    if mismatch_count:
        blockers.append(_blocker(
            "shadow_mismatch",
            mismatch_count,
            "shadow comparison or identity mismatches remain",
        ))
    if rejections:
        blockers.append(_blocker(
            "result_rejected",
            len(rejections),
            "TaskAttempt result rejections remain in the evidence ledger",
        ))
    if terminal_without_comparison:
        blockers.append(_blocker(
            "terminal_without_comparison",
            len(terminal_without_comparison),
            "terminal canonical attempts lack a shadow comparison",
        ))
    if identity_gap_attempt_ids:
        blockers.append(_blocker(
            "identity_gap",
            len(identity_gap_attempt_ids),
            "canonical attempts have incomplete dispatch identity",
        ))
    if active_attempt_ids:
        blockers.append(_blocker(
            "active_attempts",
            len(active_attempt_ids),
            "wait for a quiet point before promotion",
        ))
    if expired_attempt_ids:
        blockers.append(_blocker(
            "expired_attempts",
            len(expired_attempt_ids),
            "expired attempts require recovery or closeout",
        ))
    if retry_pending_attempt_ids:
        blockers.append(_blocker(
            "retry_pending",
            len(retry_pending_attempt_ids),
            "scheduler-owned retries remain unresolved",
        ))
    if deadletter_attempt_ids:
        blockers.append(_blocker(
            "deadlettered",
            len(deadletter_attempt_ids),
            "deadlettered attempts require operator closeout",
        ))

    return _report(
        mode=mode,
        min_comparisons=min_comparisons,
        blockers=blockers,
        status_counts=dict(sorted(Counter(
            str(row.get("status") or "unknown") for row in rows
        ).items())),
        row_count=len(rows),
        current_count=len(current_rows),
        comparison_count=comparison_count,
        matched_comparison_count=len(matched_comparisons),
        mismatch_count=mismatch_count,
        rejection_count=len(rejections),
        terminal_without_comparison=terminal_without_comparison,
        identity_gap_attempt_ids=identity_gap_attempt_ids,
        active_attempt_ids=active_attempt_ids,
        expired_attempt_ids=expired_attempt_ids,
        retry_pending_attempt_ids=retry_pending_attempt_ids,
        deadletter_attempt_ids=deadletter_attempt_ids,
    )


def _report(
    *,
    mode: str,
    min_comparisons: int,
    blockers: list[dict[str, Any]],
    status_counts: dict[str, int],
    row_count: int,
    current_count: int,
    comparison_count: int,
    matched_comparison_count: int,
    mismatch_count: int,
    rejection_count: int,
    terminal_without_comparison: list[str],
    identity_gap_attempt_ids: list[str],
    active_attempt_ids: list[str],
    expired_attempt_ids: list[str],
    retry_pending_attempt_ids: list[str],
    deadletter_attempt_ids: list[str],
) -> dict[str, Any]:
    candidate = mode == "shadow" and not blockers
    decision = (
        "already_enforced"
        if mode == "enforce"
        else "candidate"
        if candidate
        else "blocked"
    )
    return {
        "schema_version": TASK_ATTEMPT_READINESS_SCHEMA_VERSION,
        "mode": mode,
        "decision": decision,
        "promotion_candidate": candidate,
        "automatic_apply": False,
        "minimum_comparisons": min_comparisons,
        "summary": {
            "attempts": row_count,
            "current_attempts": current_count,
            "comparisons": comparison_count,
            "matched_comparisons": matched_comparison_count,
            "mismatches": mismatch_count,
            "rejections": rejection_count,
            "blockers": len(blockers),
        },
        "status_counts": status_counts,
        "blockers": blockers,
        "evidence": {
            "terminal_without_comparison": terminal_without_comparison,
            "identity_gap_attempt_ids": identity_gap_attempt_ids,
            "active_attempt_ids": active_attempt_ids,
            "expired_attempt_ids": expired_attempt_ids,
            "retry_pending_attempt_ids": retry_pending_attempt_ids,
            "deadletter_attempt_ids": deadletter_attempt_ids,
        },
    }


def _blocker(code: str, count: int, message: str) -> dict[str, Any]:
    return {"code": code, "count": int(count), "message": message}


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


def _timestamp_expired(value: str, *, now: datetime) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc) <= now


__all__ = [
    "TASK_ATTEMPT_READINESS_SCHEMA_VERSION",
    "build_task_attempt_readiness",
]
