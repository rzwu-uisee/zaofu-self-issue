"""Read-only liveness truth for the Self-Issue lifecycle."""

from __future__ import annotations

import json
import os
from pathlib import Path


def self_issue_runtime_status(state_dir: Path) -> str:
    """Return live only for the process that owns the runtime watcher guard."""

    state_dir = Path(state_dir)
    guard_path = state_dir / "processes" / "watcher.pid.json"
    try:
        value = json.loads(guard_path.read_text(encoding="utf-8"))
        owner_pid = int(value.get("owner_pid") or 0) if isinstance(value, dict) else 0
    except FileNotFoundError:
        return "stopped" if (state_dir / "session.yaml").exists() else "unknown"
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return "unknown"
    return "live" if _pid_alive(owner_pid) else "stopped"


def _pid_alive(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


__all__ = ["self_issue_runtime_status"]
