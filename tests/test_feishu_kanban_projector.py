from __future__ import annotations

import json
from pathlib import Path

from zf.core.events import EventLog, EventWriter
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.integrations.feishu.kanban_projector import (
    FeishuKanbanProjector,
    ProjectorCursorStore,
)
from zf.integrations.feishu.mock_clients import MockFeishuBitableClient
from zf.integrations.feishu.transport import FeishuTransportError


class ToggleFailureClient(MockFeishuBitableClient):
    fail_updates = False

    def update_record(self, app_token, table_id, record_id, fields):
        if self.fail_updates:
            raise FeishuTransportError("temporary projection outage")
        return super().update_record(app_token, table_id, record_id, fields)


class ReconcileFailureClient(MockFeishuBitableClient):
    def find_record_id(self, *args, **kwargs):
        raise FeishuTransportError("temporary reconciliation outage")


def _projector(
    state_dir: Path,
    client: MockFeishuBitableClient,
    *,
    interval: float = 3600.0,
) -> FeishuKanbanProjector:
    return FeishuKanbanProjector(
        state_dir=state_dir,
        project_id="proj",
        project_name="Project",
        app_token="app_token",
        table_id="tbl",
        client=client,
        writer=EventWriter(EventLog(state_dir / "events.jsonl")),
        reconcile_interval_seconds=interval,
    )


def test_projector_initial_reconcile_then_coalesces_status_events(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-1", title="Task one"))
    client = MockFeishuBitableClient()
    projector = _projector(state_dir, client)

    initial = projector.tick(now=100.0)
    assert initial["reconciled"] is True
    assert len(client.created) == 1

    store.update("TASK-1", status="in_progress")
    log = EventLog(state_dir / "events.jsonl")
    log.append(
        ZfEvent(type="task.status_changed", task_id="TASK-1", payload={"to": "ready"})
    )
    log.append(
        ZfEvent(
            type="task.status_changed",
            task_id="TASK-1",
            payload={"to": "in_progress"},
        )
    )

    incremental = projector.tick(now=101.0)

    assert incremental == {
        "ok": True,
        "reconciled": False,
        "processed": 1,
        "failed": 0,
        "pending": 0,
    }
    assert len(client.updated) == 1
    assert client.updated[0][3]["Status"] == "in_progress"
    types = [event.type for event in log.read_all()]
    assert "feishu.kanban_projection.reconciled" in types
    assert "feishu.kanban_projection.synced" in types


def test_projector_persists_retry_without_tight_loop(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-1", title="Task one"))
    client = ToggleFailureClient()
    projector = _projector(state_dir, client)
    projector.tick(now=100.0)
    client.fail_updates = True
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(
            type="task.status_changed",
            task_id="TASK-1",
            payload={"to": "in_progress"},
        )
    )

    failed = projector.tick(now=101.0)
    waiting = projector.tick(now=102.0)

    assert failed["failed"] == 1
    assert failed["pending"] == 1
    assert waiting["processed"] == 0
    assert waiting["pending"] == 1
    cursor, invalid = ProjectorCursorStore.for_state_dir(state_dir).read()
    assert invalid is False
    assert cursor.attempts["TASK-1"]["attempt"] == 1
    assert cursor.attempts["TASK-1"]["next_retry_at"] == 103.0

    client.fail_updates = False
    recovered = projector.tick(now=103.0)
    assert recovered["ok"] is True
    assert recovered["pending"] == 0


def test_projector_invalid_cursor_forces_full_reconcile(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(Task(id="TASK-1", title="Task one"))
    client = MockFeishuBitableClient()
    projector = _projector(state_dir, client)
    projector.tick(now=100.0)
    cursor_path = ProjectorCursorStore.for_state_dir(state_dir).path
    cursor_path.write_text("{broken", encoding="utf-8")

    result = projector.tick(now=101.0)

    assert result["reconciled"] is True
    events = EventLog(state_dir / "events.jsonl").read_all()
    reconciled = [
        event for event in events if event.type == "feishu.kanban_projection.reconciled"
    ][-1]
    assert reconciled.payload["cursor_recovered"] is True


def test_projector_truncated_event_log_forces_full_reconcile(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(Task(id="TASK-1", title="Task one"))
    client = MockFeishuBitableClient()
    projector = _projector(state_dir, client)
    projector.tick(now=100.0)
    cursor_store = ProjectorCursorStore.for_state_dir(state_dir)
    cursor, invalid = cursor_store.read()
    assert invalid is False
    cursor.event_offset = 999
    cursor_store.write(cursor)

    result = projector.tick(now=101.0)

    assert result["reconciled"] is True
    reconciled = [
        event
        for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.type == "feishu.kanban_projection.reconciled"
    ][-1]
    assert reconciled.payload["cursor_recovered"] is True


def test_projector_reconcile_failure_uses_durable_backoff(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(Task(id="TASK-1", title="Task one"))
    projector = _projector(state_dir, ReconcileFailureClient())

    failed = projector.tick(now=100.0)
    waiting = projector.tick(now=101.0)

    assert failed["ok"] is False
    assert failed["retry_at"] == 102.0
    assert waiting["ok"] is False
    assert waiting["processed"] == 0
    cursor, invalid = ProjectorCursorStore.for_state_dir(state_dir).read()
    assert invalid is False
    assert cursor.attempts["__reconcile__"]["attempt"] == 1
    assert cursor.attempts["__reconcile__"]["next_retry_at"] == 102.0


def test_projector_restart_replays_persisted_pending_task(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-1", title="Task one"))
    client = ToggleFailureClient()
    _projector(state_dir, client).tick(now=100.0)
    client.fail_updates = True
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(
            type="task.status_changed",
            task_id="TASK-1",
            payload={"to": "done"},
        )
    )
    assert _projector(state_dir, client).tick(now=101.0)["pending"] == 1

    client.fail_updates = False
    recovered = _projector(state_dir, client).tick(now=103.0)

    assert recovered["ok"] is True
    assert recovered["pending"] == 0


def test_projector_cursor_is_atomic_json(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = ProjectorCursorStore.for_state_dir(state_dir)
    cursor, invalid = store.read()
    assert invalid is False

    store.write(cursor)

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "feishu-kanban-projector-cursor.v1"
