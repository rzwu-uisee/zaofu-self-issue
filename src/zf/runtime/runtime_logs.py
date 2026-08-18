"""Bounded, redacted runtime JSONL logs distinct from EventLog projections."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.security.redaction import redact_obj, redact_text
from zf.core.state.locks import FileLock


_SCHEMA_VERSION = "runtime-log.v1"
_MAX_BYTES = 8 * 1024 * 1024
_ALLOWED_LEVELS = frozenset({"DEBUG", "INFO", "WARN", "ERROR"})
_SAFE_FIELDS = frozenset({
    "zaofu_correlation_id",
    "task_id",
    "workflow_run_id",
    "dispatch_id",
    "attempt_id",
    "role_instance_id",
    "provider",
    "provider_session_id",
    "event_ref",
    "route",
    "operation_kind",
    "status",
})


def runtime_log_path(state_dir: Path) -> Path:
    return Path(state_dir) / "logs" / "runtime.jsonl"


def write_runtime_log(
    state_dir: Path,
    *,
    level: str,
    component: str,
    message: str,
    failure_class: str = "",
    fields: dict[str, Any] | None = None,
    enabled: bool = True,
) -> dict[str, Any] | None:
    """Append one operational diagnostic without altering canonical truth."""

    if not enabled:
        return None
    normalized_level = str(level or "INFO").upper()
    if normalized_level not in _ALLOWED_LEVELS:
        normalized_level = "INFO"
    record: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "level": normalized_level,
        "component": str(component or "runtime")[:96],
        "message": redact_text(str(message or ""))[:600],
        "failure_class": str(failure_class or "")[:120],
    }
    for key, value in (fields or {}).items():
        if key not in _SAFE_FIELDS or value in (None, ""):
            continue
        record[key] = redact_obj(value)
    path = runtime_log_path(state_dir)
    lock = path.with_name(path.name + ".lock")
    with FileLock(lock):
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return record


def read_runtime_logs(
    state_dir: Path,
    *,
    limit: int = 200,
    level: str = "DEBUG",
    provider: str = "",
    task_id: str = "",
) -> list[dict[str, Any]]:
    threshold = _level_rank(level)
    rows: list[dict[str, Any]] = []
    path = runtime_log_path(state_dir)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in reversed(lines[-max(1, min(limit * 4, 4000)):]):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if _level_rank(str(row.get("level") or "INFO")) < threshold:
            continue
        if provider and str(row.get("provider") or "") != provider:
            continue
        if task_id and str(row.get("task_id") or "") != task_id:
            continue
        rows.append(redact_obj(row))
        if len(rows) >= max(1, min(limit, 500)):
            break
    return rows


def summarize_runtime_logs(state_dir: Path) -> dict[str, Any]:
    rows = read_runtime_logs(state_dir, limit=500, level="DEBUG")
    counts = {level: 0 for level in ("DEBUG", "INFO", "WARN", "ERROR")}
    for row in rows:
        level = str(row.get("level") or "INFO")
        counts[level] = counts.get(level, 0) + 1
    return {
        "schema_version": _SCHEMA_VERSION,
        "source": "state-dir/logs/runtime.jsonl",
        "count": len(rows),
        "levels": counts,
        "latest_at": str(rows[0].get("timestamp") or "") if rows else "",
    }


def _rotate_if_needed(path: Path) -> None:
    try:
        if path.stat().st_size < _MAX_BYTES:
            return
    except OSError:
        return
    rolled = path.with_suffix(path.suffix + ".1")
    try:
        rolled.unlink(missing_ok=True)
        path.replace(rolled)
    except OSError:
        pass


def _level_rank(value: str) -> int:
    return {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}.get(
        str(value or "INFO").upper(),
        0,
    )


__all__ = [
    "read_runtime_logs",
    "runtime_log_path",
    "summarize_runtime_logs",
    "write_runtime_log",
]
