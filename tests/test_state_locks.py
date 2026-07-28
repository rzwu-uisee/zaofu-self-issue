"""Concurrency regression tests for runtime state file locks."""

from __future__ import annotations

import os
import time
from multiprocessing import Event, Process
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.locks import FileLock
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


def _add_task(path: str, title: str) -> None:
    TaskStore(Path(path)).add(Task(title=title))


def _append_event(path: str, event_type: str) -> None:
    EventLog(Path(path)).append(ZfEvent(type=event_type, actor="test"))


def _register_session(path: str, project_root: str, instance_id: str) -> None:
    RoleSessionRegistry(Path(path), project_root=project_root).get_or_create(
        instance_id
    )


def _hold_file_lock(path: str, ready: Event) -> None:
    with FileLock(Path(path)):
        ready.set()
        time.sleep(0.5)


def _run_processes(processes: list[Process]) -> None:
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0


def test_task_store_concurrent_adds_do_not_lose_updates(tmp_path: Path):
    path = tmp_path / "kanban.json"
    processes = [
        Process(target=_add_task, args=(str(path), f"task-{i}"))
        for i in range(8)
    ]

    _run_processes(processes)

    titles = sorted(task.title for task in TaskStore(path).list_all())
    assert titles == [f"task-{i}" for i in range(8)]


def test_event_log_concurrent_append_and_rotation_stays_valid(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.append(ZfEvent(type="old.event", actor="test"))
    yesterday = time.time() - 2 * 86400
    os.utime(path, (yesterday, yesterday))

    processes = [
        Process(target=_append_event, args=(str(path), f"event.{i}"))
        for i in range(8)
    ]

    _run_processes(processes)

    events = EventLog(path).read_all()
    types = sorted(event.type for event in events)
    assert types == sorted(["old.event"] + [f"event.{i}" for i in range(8)])


def test_role_session_registry_concurrent_writes_preserve_all_entries(
    tmp_path: Path,
):
    path = tmp_path / "role_sessions.yaml"
    processes = [
        Process(target=_register_session, args=(str(path), "/project", f"dev-{i}"))
        for i in range(8)
    ]

    _run_processes(processes)

    sessions = RoleSessionRegistry(path, project_root="/project").all()
    assert sorted(sessions) == [f"dev-{i}" for i in range(8)]


def test_file_lock_optional_timeout_is_bounded(tmp_path: Path):
    path = tmp_path / "bounded.lock"
    ready = Event()
    process = Process(target=_hold_file_lock, args=(str(path), ready))
    process.start()
    assert ready.wait(timeout=2)

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="timed out acquiring lock"):
        with FileLock(path, timeout_seconds=0.05):
            pass
    assert time.monotonic() - started < 0.4

    process.join(timeout=2)
    assert process.exitcode == 0
