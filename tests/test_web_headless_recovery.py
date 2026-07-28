from __future__ import annotations

import json
from pathlib import Path

from zf.core.events import EventLog, ZfEvent
from zf.web.server import create_app


def test_create_app_reconciles_interrupted_turn_thread_and_action(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        id="evt-turn-start",
        type="kanban.agent.turn.started",
        actor="web",
        payload={
            "turn_id": "turn-stale",
            "thread_key": "main",
            "project_id": "p1",
            "conversation_id": "c1",
            "backend": "codex-headless",
            "permission_profile": "read_only",
            "message_event_id": "evt-message",
        },
    ))
    thread_dir = state_dir / "operator" / "threads"
    thread_dir.mkdir(parents=True)
    thread_path = thread_dir / "thread-1.json"
    thread_path.write_text(json.dumps({
        "thread_id": "thread-1",
        "status": "running",
        "providers": {
            "codex-headless": {
                "status": "running",
                "provider_session_id": "session-1",
            },
        },
    }), encoding="utf-8")
    idempotency = state_dir / "web-actions" / "idempotency.jsonl"
    idempotency.parent.mkdir(parents=True)
    idempotency.write_text(json.dumps({
        "key": "action-stale",
        "action": "update-task",
        "payload_hash": "hash-1",
        "state": "pending",
        "ts": "2026-07-25T00:00:00+00:00",
    }) + "\n", encoding="utf-8")

    app = create_app(state_dir, project_root=tmp_path)

    assert app.state.kanban_agent_recovery == {
        "turns": 1,
        "threads": 1,
        "idempotency_keys": 1,
    }
    events = log.read_all()
    terminal = [
        event
        for event in events
        if event.type == "kanban.agent.turn.failed"
        and event.payload.get("turn_id") == "turn-stale"
    ]
    assert terminal
    assert terminal[-1].payload["status"] == "interrupted"
    thread = json.loads(thread_path.read_text(encoding="utf-8"))
    assert thread["status"] == "interrupted"
    assert thread["providers"]["codex-headless"]["status"] == "interrupted"
    rows = [
        json.loads(line)
        for line in idempotency.read_text(encoding="utf-8").splitlines()
    ]
    assert rows[-1]["state"] == "completed"
    assert rows[-1]["response"]["status"] == "interrupted_by_server_restart"
