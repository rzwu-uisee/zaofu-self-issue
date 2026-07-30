"""Managed Feishu Kanban projection sidecar for ``zf start``."""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zf.core.events import ZfEvent
from zf.core.state.atomic_io import atomic_write_text
from zf.integrations.feishu.lark_cli import LarkCliBitableClient
from zf.integrations.feishu.projection_target import (
    FeishuKanbanTargetStore,
    redact_target_token,
    redact_target_url,
    resolve_or_create_kanban_target,
)
from zf.runtime.cli_command import zf_cli_cmd


@dataclass
class FeishuProjectionSidecar:
    process: subprocess.Popen
    log_handle: Any
    pid_path: Path
    log_path: Path
    backend: str


def build_feishu_projection_command(
    *,
    state_dir: Path,
    poll_interval_seconds: float,
    app_token: str = "",
    table_id: str = "",
    create_target_if_missing: bool = False,
) -> list[str]:
    command = [
        *shlex.split(zf_cli_cmd()),
        "feishu",
        "project-kanban",
        "--watch",
        "--state-dir",
        str(state_dir),
        "--backend",
        "lark-cli",
        "--poll-interval-seconds",
        str(poll_interval_seconds),
    ]
    if app_token and table_id:
        command.extend(["--app-token", app_token, "--table-id", table_id])
    if create_target_if_missing:
        command.append("--create-target-if-missing")
    return command


def _start_projection_process(command: list[str], **kwargs: Any) -> subprocess.Popen:
    return subprocess.Popen(command, **kwargs)


def start_feishu_projection_sidecar(
    *,
    config: object,
    state_dir: Path,
    project_root: Path,
    event_log: Any | None = None,
    dry_run: bool = False,
) -> FeishuProjectionSidecar | None:
    runtime = getattr(config, "runtime", None)
    projection = getattr(runtime, "feishu_projection", None)
    if not projection or not bool(getattr(projection, "enabled", False)):
        return None
    backend = "lark-cli"
    auto_create_target = bool(
        getattr(projection, "auto_create_target", False)
    )
    has_auth = bool(
        os.environ.get("FEISHU_TENANT_ACCESS_TOKEN", "").strip()
        or (
            os.environ.get("FEISHU_APP_ID", "").strip()
            and os.environ.get("FEISHU_APP_SECRET", "").strip()
        )
    )
    try:
        stored_target = FeishuKanbanTargetStore.for_state_dir(state_dir).read()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _append_event(
            event_log,
            "feishu.kanban_projection.failed",
            {
                "schema_version": "feishu-kanban-projection.v1",
                "reason": "invalid_project_target",
                "backend": backend,
                "error": str(exc)[:400],
            },
        )
        return None
    if stored_target is not None:
        app_token = stored_target.app_token
        table_id = stored_target.table_id
    elif auto_create_target:
        app_token = ""
        table_id = ""
    else:
        app_token = os.environ.get("FEISHU_BITABLE_APP_TOKEN", "").strip()
        table_id = os.environ.get("FEISHU_BITABLE_TABLE_ID", "").strip()

    if not has_auth or (
        not auto_create_target and (not app_token or not table_id)
    ):
        _append_event(
            event_log,
            "feishu.kanban_projection.failed",
            {
                "schema_version": "feishu-kanban-projection.v1",
                "reason": "missing_target_or_credentials",
                "backend": backend,
            },
        )
        return None
    if auto_create_target and not dry_run:
        try:
            target_result = resolve_or_create_kanban_target(
                state_dir=state_dir,
                project_name=str(
                    getattr(getattr(config, "project", None), "name", "")
                    or Path(project_root).name
                ),
                client=LarkCliBitableClient(),
                create_if_missing=True,
                folder_token=os.environ.get("FEISHU_FOLDER_TOKEN", ""),
                base_name=str(getattr(projection, "base_name", "") or ""),
                table_name=str(
                    getattr(projection, "table_name", "Kanban") or "Kanban"
                ),
                time_zone=str(
                    getattr(projection, "time_zone", "Asia/Shanghai")
                    or "Asia/Shanghai"
                ),
            )
        except (OSError, ValueError, RuntimeError) as exc:
            _append_event(
                event_log,
                "feishu.kanban_projection.failed",
                {
                    "schema_version": "feishu-kanban-projection.v1",
                    "reason": "target_bootstrap_failed",
                    "backend": backend,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:400],
                },
            )
            return None
        app_token = target_result.target.app_token
        table_id = target_result.target.table_id
        if target_result.created:
            _append_event(
                event_log,
                "feishu.kanban_projection.target_created",
                {
                    "schema_version": "feishu-kanban-projection.v1",
                    "backend": backend,
                    "app_token": redact_target_token(app_token),
                    "table_id": table_id,
                    "base_url": redact_target_url(
                        target_result.target.base_url,
                        app_token,
                    ),
                    "fields_created": target_result.fields_created,
                    "views_created": target_result.views_created,
                    "views_configured": target_result.views_configured,
                },
            )
    poll_interval = float(getattr(projection, "poll_interval_seconds", 2.0) or 2.0)
    logs_dir = Path(state_dir) / "logs"
    processes_dir = Path(state_dir) / "processes"
    logs_dir.mkdir(parents=True, exist_ok=True)
    processes_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "feishu-kanban-projector.log"
    pid_path = processes_dir / "feishu-kanban-projector.pid.json"
    command = build_feishu_projection_command(
        state_dir=Path(state_dir).resolve(),
        poll_interval_seconds=poll_interval,
        app_token=app_token,
        table_id=table_id,
        create_target_if_missing=auto_create_target and not app_token,
    )
    payload = {
        "schema_version": "feishu-kanban-projection.v1",
        "command": command,
        "state_dir": str(state_dir),
        "backend": backend,
        "log_path": str(log_path),
    }
    if dry_run:
        _append_event(
            event_log,
            "feishu.kanban_projection.started",
            {**payload, "dry_run": True},
        )
        return None

    log_handle = log_path.open("a", encoding="utf-8")
    try:
        process = _start_projection_process(
            command,
            cwd=str(project_root),
            env=os.environ.copy(),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    except Exception as exc:
        log_handle.close()
        _append_event(
            event_log,
            "feishu.kanban_projection.failed",
            {
                **payload,
                "error_type": type(exc).__name__,
                "error": str(exc)[:400],
            },
        )
        return None
    atomic_write_text(
        pid_path,
        json.dumps(
            {
                "pid": process.pid,
                "command": command,
                "backend": backend,
                "log_path": str(log_path),
                "started_at": time.time(),
            },
            indent=2,
        )
        + "\n",
    )
    sidecar = FeishuProjectionSidecar(
        process=process,
        log_handle=log_handle,
        pid_path=pid_path,
        log_path=log_path,
        backend=backend,
    )
    time.sleep(0.25)
    exit_code = process.poll()
    if exit_code is not None:
        log_handle.close()
        pid_path.unlink(missing_ok=True)
        _append_event(
            event_log,
            "feishu.kanban_projection.failed",
            {
                **payload,
                "pid": process.pid,
                "exit_code": exit_code,
                "reason": "exited_early",
            },
        )
        return None
    return sidecar


def stop_feishu_projection_sidecar(
    sidecar: FeishuProjectionSidecar | None,
    *,
    event_log: Any | None = None,
    timeout: float = 10.0,
) -> None:
    if sidecar is None:
        return
    process = sidecar.process
    if process.poll() is None:
        if _signal_dedicated_process_group(process.pid, signal.SIGTERM):
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _signal_dedicated_process_group(process.pid, signal.SIGKILL)
                process.wait(timeout=5.0)
        else:
            process.terminate()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)
    try:
        sidecar.log_handle.close()
    except Exception:
        pass
    sidecar.pid_path.unlink(missing_ok=True)
    _append_event(
        event_log,
        "feishu.kanban_projection.stopped",
        {
            "schema_version": "feishu-kanban-projection.v1",
            "pid": process.pid,
            "exit_code": process.returncode,
            "backend": sidecar.backend,
            "log_path": str(sidecar.log_path),
        },
    )


def stop_feishu_projection_sidecar_by_pidfile(
    state_dir: Path,
    *,
    event_log: Any | None = None,
    timeout: float = 10.0,
) -> bool:
    """Stop an externally owned projector without trusting a stale PID."""

    pid_path = Path(state_dir) / "processes" / "feishu-kanban-projector.pid.json"
    try:
        record = json.loads(pid_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    pid = int(record.get("pid") or 0)
    if pid <= 0 or not _pid_is_projector(pid):
        pid_path.unlink(missing_ok=True)
        return False
    if not _terminate_process_group(pid, timeout=timeout):
        if not _pid_is_alive(pid):
            pid_path.unlink(missing_ok=True)
        return False
    pid_path.unlink(missing_ok=True)
    _append_event(
        event_log,
        "feishu.kanban_projection.stopped",
        {
            "schema_version": "feishu-kanban-projection.v1",
            "pid": pid,
            "backend": str(record.get("backend") or ""),
            "log_path": str(record.get("log_path") or ""),
            "stopped_by": "pidfile",
        },
    )
    return True


def _pid_is_projector(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        parts = [
            value.decode("utf-8", errors="replace")
            for value in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            if value
        ]
    except (OSError, ProcessLookupError):
        return False
    return any(
        parts[index : index + 2] == ["feishu", "project-kanban"]
        for index in range(max(0, len(parts) - 1))
    )


def _terminate_process_group(pid: int, *, timeout: float) -> bool:
    if not _signal_dedicated_process_group(pid, signal.SIGTERM):
        return False
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if not _pid_is_alive(pid):
            return True
        time.sleep(0.05)
    _signal_dedicated_process_group(pid, signal.SIGKILL)
    return True


def _signal_dedicated_process_group(pid: int, sig: signal.Signals) -> bool:
    try:
        pgid = os.getpgid(pid)
    except (OSError, ProcessLookupError):
        return False
    if pgid != pid:
        return False
    try:
        os.killpg(pgid, sig)
    except (OSError, ProcessLookupError):
        return False
    return True


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _append_event(
    event_log: Any | None,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    if event_log is None:
        return
    try:
        event_log.append(ZfEvent(type=event_type, actor="zf-cli", payload=payload))
    except Exception:
        pass
