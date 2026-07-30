"""Durable event-driven projection of ZaoFu tasks into Feishu Base."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zf.core.events import EventLog, EventWriter
from zf.core.events.model import ZfEvent
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.integrations.feishu.client_ports import BitableClient
from zf.integrations.feishu.sync import FeishuSyncLedger, sync_kanban_bitable


_CURSOR_SCHEMA = "feishu-kanban-projector-cursor.v1"
_RECONCILE_ATTEMPT_KEY = "__reconcile__"


@dataclass
class ProjectorCursor:
    event_offset: int = 0
    last_event_id: str = ""
    last_reconcile_at: float = 0.0
    pending_task_ids: list[str] = field(default_factory=list)
    attempts: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectorCursor":
        if data.get("schema_version") != _CURSOR_SCHEMA:
            raise ValueError("unsupported cursor schema")
        pending = data.get("pending_task_ids") or []
        attempts = data.get("attempts") or {}
        if not isinstance(pending, list) or not isinstance(attempts, dict):
            raise ValueError("invalid cursor collections")
        return cls(
            event_offset=max(0, int(data.get("event_offset") or 0)),
            last_event_id=str(data.get("last_event_id") or ""),
            last_reconcile_at=max(0.0, float(data.get("last_reconcile_at") or 0.0)),
            pending_task_ids=list(
                dict.fromkeys(
                    str(value).strip() for value in pending if str(value).strip()
                )
            ),
            attempts={
                str(key): dict(value)
                for key, value in attempts.items()
                if str(key).strip() and isinstance(value, dict)
            },
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": _CURSOR_SCHEMA,
            "event_offset": self.event_offset,
            "last_event_id": self.last_event_id,
            "last_reconcile_at": self.last_reconcile_at,
            "pending_task_ids": self.pending_task_ids,
            "attempts": self.attempts,
        }


class ProjectorCursorStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @classmethod
    def for_state_dir(cls, state_dir: Path) -> "ProjectorCursorStore":
        return cls(
            Path(state_dir) / "integrations" / "feishu" / "kanban-projector-cursor.json"
        )

    def read(self) -> tuple[ProjectorCursor, bool]:
        with locked_path(self.path):
            if not self.path.exists():
                return ProjectorCursor(), False
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("cursor must be an object")
                return ProjectorCursor.from_dict(data), False
            except (OSError, ValueError, json.JSONDecodeError, TypeError):
                return ProjectorCursor(), True

    def write(self, cursor: ProjectorCursor) -> None:
        with locked_path(self.path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                self.path,
                json.dumps(
                    cursor.as_dict(),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
            )


class FeishuKanbanProjector:
    def __init__(
        self,
        *,
        state_dir: Path,
        project_id: str,
        project_name: str,
        app_token: str,
        table_id: str,
        client: BitableClient,
        writer: EventWriter,
        include_archive_days: int = 30,
        reconcile_interval_seconds: float = 3600.0,
        max_actions_per_tick: int = 20,
        cursor_store: ProjectorCursorStore | None = None,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.project_id = project_id
        self.project_name = project_name
        self.app_token = app_token
        self.table_id = table_id
        self.client = client
        self.writer = writer
        self.include_archive_days = max(0, int(include_archive_days))
        self.reconcile_interval_seconds = max(1.0, float(reconcile_interval_seconds))
        self.max_actions_per_tick = max(1, int(max_actions_per_tick))
        self.cursor_store = cursor_store or ProjectorCursorStore.for_state_dir(
            self.state_dir
        )
        self.ledger = FeishuSyncLedger.for_state_dir(self.state_dir)

    def tick(
        self,
        *,
        now: float | None = None,
        force_reconcile: bool = False,
    ) -> dict[str, Any]:
        now_value = time.time() if now is None else float(now)
        cursor, cursor_invalid = self.cursor_store.read()
        events = EventLog(self.state_dir / "events.jsonl").read_all()
        log_truncated = cursor.event_offset > len(events)
        if log_truncated:
            cursor.event_offset = 0
            cursor.last_event_id = ""
        for event in events[cursor.event_offset :]:
            if event.type == "task.status_changed":
                task_id = _event_task_id(event)
                if task_id and task_id not in cursor.pending_task_ids:
                    cursor.pending_task_ids.append(task_id)
        cursor.event_offset = len(events)
        if events:
            cursor.last_event_id = events[-1].id

        reconcile_due = (
            force_reconcile
            or cursor_invalid
            or log_truncated
            or cursor.last_reconcile_at <= 0
            or now_value - cursor.last_reconcile_at >= self.reconcile_interval_seconds
        )
        if reconcile_due:
            reconcile_attempt = cursor.attempts.get(_RECONCILE_ATTEMPT_KEY) or {}
            next_retry_at = float(reconcile_attempt.get("next_retry_at") or 0.0)
            if (
                not force_reconcile
                and not cursor_invalid
                and not log_truncated
                and next_retry_at > now_value
            ):
                self.cursor_store.write(cursor)
                return {
                    "ok": False,
                    "reconciled": False,
                    "processed": 0,
                    "pending": len(cursor.pending_task_ids),
                    "retry_at": next_retry_at,
                }
            try:
                result = sync_kanban_bitable(
                    state_dir=self.state_dir,
                    project_id=self.project_id,
                    project_name=self.project_name,
                    app_token=self.app_token,
                    table_id=self.table_id,
                    client=self.client,
                    ledger=self.ledger,
                    include_archive_days=self.include_archive_days,
                    writer=None,
                )
            except Exception as exc:
                attempt = int(reconcile_attempt.get("attempt") or 0) + 1
                retry_at = now_value + min(
                    300.0,
                    float(2 ** min(attempt, 8)),
                )
                cursor.attempts[_RECONCILE_ATTEMPT_KEY] = {
                    "attempt": attempt,
                    "next_retry_at": retry_at,
                    "error": str(exc)[:400],
                }
                self._emit_failure(
                    task_id="",
                    error=exc,
                    attempt=attempt,
                    next_retry_at=retry_at,
                    mode="reconcile",
                )
                self.cursor_store.write(cursor)
                return {
                    "ok": False,
                    "reconciled": False,
                    "processed": 0,
                    "pending": len(cursor.pending_task_ids),
                    "error": str(exc),
                    "retry_at": retry_at,
                }
            cursor.last_reconcile_at = now_value
            cursor.pending_task_ids.clear()
            cursor.attempts.clear()
            self._emit(
                "feishu.kanban_projection.reconciled",
                {
                    "rows": int(result.get("rows") or 0),
                    "created": int(result.get("created") or 0),
                    "recreated": int(result.get("recreated") or 0),
                    "updated": int(result.get("updated") or 0),
                    "cursor_recovered": cursor_invalid or log_truncated,
                },
            )
            self.cursor_store.write(cursor)
            return {
                "ok": True,
                "reconciled": True,
                "processed": int(result.get("rows") or 0),
                "pending": 0,
                **result,
            }

        processed = 0
        failed = 0
        for task_id in list(cursor.pending_task_ids):
            if processed >= self.max_actions_per_tick:
                break
            attempt_state = cursor.attempts.get(task_id) or {}
            next_retry_at = float(attempt_state.get("next_retry_at") or 0.0)
            if next_retry_at > now_value:
                continue
            try:
                result = sync_kanban_bitable(
                    state_dir=self.state_dir,
                    project_id=self.project_id,
                    project_name=self.project_name,
                    app_token=self.app_token,
                    table_id=self.table_id,
                    client=self.client,
                    ledger=self.ledger,
                    include_archive_days=self.include_archive_days,
                    task_ids={task_id},
                    writer=None,
                )
            except Exception as exc:
                attempt = int(attempt_state.get("attempt") or 0) + 1
                retry_at = now_value + min(300.0, float(2 ** min(attempt, 8)))
                cursor.attempts[task_id] = {
                    "attempt": attempt,
                    "next_retry_at": retry_at,
                    "error": str(exc)[:400],
                }
                self._emit_failure(
                    task_id=task_id,
                    error=exc,
                    attempt=attempt,
                    next_retry_at=retry_at,
                    mode="incremental",
                )
                failed += 1
                processed += 1
                continue
            cursor.pending_task_ids.remove(task_id)
            cursor.attempts.pop(task_id, None)
            self._emit(
                "feishu.kanban_projection.synced",
                {
                    "task_id": task_id,
                    "rows": int(result.get("rows") or 0),
                    "created": int(result.get("created") or 0),
                    "recreated": int(result.get("recreated") or 0),
                    "updated": int(result.get("updated") or 0),
                },
                task_id=task_id,
            )
            processed += 1

        self.cursor_store.write(cursor)
        return {
            "ok": failed == 0,
            "reconciled": False,
            "processed": processed,
            "failed": failed,
            "pending": len(cursor.pending_task_ids),
        }

    def _emit_failure(
        self,
        *,
        task_id: str,
        error: Exception,
        attempt: int,
        next_retry_at: float,
        mode: str,
    ) -> None:
        self._emit(
            "feishu.kanban_projection.failed",
            {
                "task_id": task_id,
                "mode": mode,
                "error_type": type(error).__name__,
                "error": str(error)[:400],
                "attempt": attempt,
                "next_retry_at": next_retry_at,
            },
            task_id=task_id,
        )

    def _emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        task_id: str = "",
    ) -> None:
        self.writer.append(
            ZfEvent(
                type=event_type,
                actor="zf-feishu-projector",
                task_id=task_id,
                payload={
                    "schema_version": "feishu-kanban-projection.v1",
                    "source": "feishu-kanban-projector",
                    "project_id": self.project_id,
                    "project_name": self.project_name,
                    "app_token": _redact_token(self.app_token),
                    "table_id": self.table_id,
                    **payload,
                },
            )
        )


def _event_task_id(event: ZfEvent) -> str:
    return str(event.task_id or event.payload.get("task_id") or "").strip()


def _redact_token(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"
