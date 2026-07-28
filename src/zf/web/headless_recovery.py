"""Startup reconciliation for daemon-owned Kanban Agent turns."""

from __future__ import annotations

import json
from pathlib import Path

from zf.core.config.schema import ZfConfig
from zf.core.events import EventWriter
from zf.core.events.factory import event_log_from_project
from zf.core.events.segments import iter_event_records
from zf.core.state.atomic_io import atomic_write_text
from zf.web.projections.request_util import reconcile_pending_idempotency_keys


def reconcile_kanban_startup(
    state_dir: Path,
    *,
    config: ZfConfig | None,
) -> dict[str, int]:
    writer = EventWriter(event_log_from_project(state_dir, config=config))
    return {
        **reconcile_interrupted_headless_turns(state_dir, writer),
        "idempotency_keys": reconcile_pending_idempotency_keys(state_dir),
    }


def reconcile_interrupted_headless_turns(
    state_dir: Path,
    writer: EventWriter,
) -> dict[str, int]:
    """Fail turns that were running before this Web process started."""
    events = [record.event for record in iter_event_records(state_dir)]
    terminal_turn_ids = {
        str((event.payload or {}).get("turn_id") or "")
        for event in events
        if event.type in {
            "kanban.agent.turn.completed",
            "kanban.agent.turn.failed",
        }
        and isinstance(event.payload, dict)
    }
    starts = {}
    for event in events:
        if event.type != "kanban.agent.turn.started":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        turn_id = str(payload.get("turn_id") or "")
        if turn_id:
            starts[turn_id] = event

    recovered = 0
    for turn_id, started in starts.items():
        if turn_id in terminal_turn_ids:
            continue
        payload = started.payload if isinstance(started.payload, dict) else {}
        failed = writer.emit(
            "kanban.agent.turn.failed",
            actor="web",
            task_id=started.task_id,
            causation_id=started.id,
            correlation_id=started.correlation_id,
            payload={
                "turn_id": turn_id,
                "thread_key": str(payload.get("thread_key") or ""),
                "project_id": str(payload.get("project_id") or ""),
                "conversation_id": str(payload.get("conversation_id") or ""),
                "backend": str(payload.get("backend") or ""),
                "permission_profile": str(
                    payload.get("permission_profile") or "read_only"
                ),
                "status": "interrupted",
                "reason": "web_server_restart_interrupted_in_flight_turn",
                "message_event_id": str(payload.get("message_event_id") or ""),
                "delta_count": 0,
            },
        )
        writer.emit(
            "runtime.action.failed",
            actor="web",
            task_id=started.task_id,
            causation_id=failed.id,
            correlation_id=started.correlation_id,
            payload={
                "action": "chat-orchestrator",
                "requested_action": str(
                    payload.get("requested_action") or "chat-orchestrator"
                ),
                "status": "interrupted",
                "reason": "web_server_restart_interrupted_in_flight_turn",
                "turn_id": turn_id,
            },
        )
        writer.emit(
            "web.action.failed",
            actor="web",
            task_id=started.task_id,
            causation_id=failed.id,
            correlation_id=started.correlation_id,
            payload={
                "action": "chat-orchestrator",
                "requested_action": str(
                    payload.get("requested_action") or "chat-orchestrator"
                ),
                "status": "interrupted",
                "reason": "web_server_restart_interrupted_in_flight_turn",
                "turn_id": turn_id,
            },
        )
        recovered += 1
    threads = _reconcile_thread_sidecars(Path(state_dir))
    return {"turns": recovered, "threads": threads}


def _reconcile_thread_sidecars(state_dir: Path) -> int:
    thread_dir = state_dir / "operator" / "threads"
    if not thread_dir.exists():
        return 0
    updated = 0
    for path in thread_dir.glob("*.json"):
        try:
            thread = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(thread, dict):
            continue
        changed = False
        if thread.get("status") == "running":
            thread["status"] = "interrupted"
            thread["last_error"] = "web_server_restart_interrupted_in_flight_turn"
            changed = True
        providers = thread.get("providers")
        if isinstance(providers, dict):
            for provider in providers.values():
                if isinstance(provider, dict) and provider.get("status") == "running":
                    provider["status"] = "interrupted"
                    provider["error"] = (
                        "web_server_restart_interrupted_in_flight_turn"
                    )
                    changed = True
        if changed:
            atomic_write_text(
                path,
                json.dumps(thread, ensure_ascii=False, indent=2) + "\n",
            )
            updated += 1
    return updated
