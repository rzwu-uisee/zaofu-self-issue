"""feishu P0-3: WS sidecar single-instance guard."""

from __future__ import annotations

import json
import os
from pathlib import Path

from zf.integrations.feishu.single_instance import (
    acquire_provider_ws_lock,
    acquire_workspace_ws_lock,
    acquire_ws_lock,
)


def test_acquire_then_second_refused(tmp_path: Path):
    sd = tmp_path / ".zf"
    lock = acquire_ws_lock(sd, "cli_x")
    assert lock is not None
    # a second acquire by a DIFFERENT live holder is refused
    p = lock.path
    data = json.loads(p.read_text())
    data["pid"] = _a_live_other_pid()
    p.write_text(json.dumps(data))
    assert acquire_ws_lock(sd, "cli_x") is None


def test_stale_by_dead_pid_can_be_stolen(tmp_path: Path):
    sd = tmp_path / ".zf"
    lock = acquire_ws_lock(sd, "cli_x")
    lock.path.write_text(json.dumps({"pid": 2_147_483_000, "app_id": "cli_x",
                                     "ts": 9_999_999_999.0}))  # dead pid
    assert acquire_ws_lock(sd, "cli_x") is not None  # stolen


def test_stale_by_age_can_be_stolen(tmp_path: Path):
    sd = tmp_path / ".zf"
    lock = acquire_ws_lock(sd, "cli_x")
    # holder is THIS pid (alive) but the timestamp is ancient → stealable
    lock.path.write_text(json.dumps({"pid": _a_live_other_pid(),
                                     "app_id": "cli_x", "ts": 1000.0}))
    assert acquire_ws_lock(sd, "cli_x", now=10_000.0) is not None


def test_release_clears_lock(tmp_path: Path):
    sd = tmp_path / ".zf"
    lock = acquire_ws_lock(sd, "cli_x")
    lock.release()
    assert not lock.path.exists()
    assert acquire_ws_lock(sd, "cli_x") is not None


def test_lock_refresh_updates_held_timestamp(tmp_path: Path):
    lock = acquire_ws_lock(tmp_path / ".zf", "cli_x")
    assert lock is not None
    lock.path.write_text(json.dumps({"pid": os.getpid(), "app_id": "cli_x", "ts": 1.0}))

    lock.refresh()

    assert json.loads(lock.path.read_text())["ts"] > 1.0


def test_per_app_isolation(tmp_path: Path):
    sd = tmp_path / ".zf"
    a = acquire_ws_lock(sd, "cli_a")
    b = acquire_ws_lock(sd, "cli_b")  # different app → independent lock
    assert a is not None and b is not None and a.path != b.path


def test_workspace_app_lock_is_shared_across_projects_and_isolated_by_workspace(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    first = acquire_workspace_ws_lock("team", "cli_shared")
    assert first is not None
    data = json.loads(first.path.read_text())
    data["pid"] = _a_live_other_pid()
    first.path.write_text(json.dumps(data))

    assert acquire_workspace_ws_lock("team", "cli_shared") is None
    other_workspace = acquire_workspace_ws_lock("other", "cli_shared")
    other_app = acquire_workspace_ws_lock("team", "cli_other")
    assert other_workspace is not None and other_app is not None


def test_provider_app_lock_rejects_a_second_workspace_or_direct_bridge(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    first = acquire_provider_ws_lock("cli_shared")
    assert first is not None
    data = json.loads(first.path.read_text())
    data["pid"] = _a_live_other_pid()
    first.path.write_text(json.dumps(data))

    assert acquire_provider_ws_lock("cli_shared") is None
    assert acquire_provider_ws_lock("cli_other") is not None


def _a_live_other_pid() -> int:
    # PID 1 is always alive and not our pid — a stable "live other holder".
    return 1 if os.getpid() != 1 else 2
