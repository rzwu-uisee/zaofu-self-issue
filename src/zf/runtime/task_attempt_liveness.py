"""Read-only liveness queries over canonical TaskAttempt leases."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.state.task_attempts import TaskAttemptStore


def iso_epoch(value: str) -> float:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def live_task_attempt_for_parent(
    runtime: Any,
    *,
    parent_task_id: str,
    now_epoch: float,
) -> dict[str, Any] | None:
    """Return the freshest non-expired child lease owned by a parent Task."""

    parent_task_id = str(parent_task_id or "").strip()
    if not parent_task_id:
        return None
    store = TaskAttemptStore(Path(runtime.state_dir) / "task_attempts.json")
    candidates = [
        row
        for row in store.current_rows()
        if str(row.get("status") or "") in {"prepared", "delivering", "sent"}
        and parent_task_id
        in {
            str(row.get("task_id") or ""),
            str(row.get("parent_task_id") or ""),
        }
        and iso_epoch(str(row.get("lease_expires_at") or "")) > now_epoch
    ]
    return max(
        candidates,
        key=lambda row: (
            iso_epoch(str(row.get("updated_at") or "")),
            int(row.get("ordinal") or 0),
        ),
        default=None,
    )


__all__ = ["iso_epoch", "live_task_attempt_for_parent"]
