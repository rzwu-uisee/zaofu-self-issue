"""Runtime leases for shared Feishu WebSocket bridge processes.

The provider App WebSocket is shared by every active project binding for one
``app_id`` on a host.  Workspace-scoped functions remain for compatibility
with pre-provider bridge callers; managed ``zf start`` uses the provider lease.
A lease only coordinates process ownership; it does not hold project, channel,
event, or routing truth.  Those remain in each project state directory and the
rebuildable binding index.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.core.workspace.registry import workspace_home
from zf.integrations.feishu.single_instance import _pid_alive, workspace_lock_path


_VERSION = 1


@dataclass(frozen=True)
class WorkspaceBridgeLease:
    workspace_id: str
    app_id: str
    project_id: str
    pid: int
    log_path: str
    shared: bool


@dataclass(frozen=True)
class WorkspaceBridgeRelease:
    pid: int
    terminate: bool
    remaining_projects: tuple[str, ...]


@dataclass(frozen=True)
class ProviderBridgeLease:
    """Lease for the host-wide bridge that owns one Feishu App connection."""

    workspace_id: str
    app_id: str
    project_id: str
    pid: int
    log_path: str
    shared: bool


@dataclass(frozen=True)
class ProviderBridgeRelease:
    pid: int
    terminate: bool
    remaining_projects: tuple[str, ...]


def workspace_bridge_lease_path(workspace_id: str) -> Path:
    """Return the derived workspace sidecar-lease path."""
    return workspace_lock_path(workspace_id, "bridge-leases").with_name(
        "bridge_leases.json"
    )


def join_live_workspace_bridge(
    *,
    workspace_id: str,
    app_id: str,
    project_id: str,
) -> WorkspaceBridgeLease | None:
    """Join a live bridge, or return ``None`` when this caller must start one."""
    path = workspace_bridge_lease_path(workspace_id)
    with locked_path(path):
        data = _read(path)
        bridges = _bridges(data)
        record = bridges.get(app_id)
        if not _is_live_record(record):
            if record is not None:
                bridges.pop(app_id, None)
                _write(path, data)
            return None
        assert isinstance(record, dict)
        projects = _projects(record)
        projects.add(project_id)
        record["projects"] = sorted(projects)
        _write(path, data)
        return WorkspaceBridgeLease(
            workspace_id=workspace_id,
            app_id=app_id,
            project_id=project_id,
            pid=int(record["pid"]),
            log_path=str(record.get("log_path") or ""),
            shared=True,
        )


def register_workspace_bridge(
    *,
    workspace_id: str,
    app_id: str,
    project_id: str,
    pid: int,
    log_path: Path,
) -> WorkspaceBridgeLease:
    """Register a healthy spawned bridge, joining an existing winner on races."""
    path = workspace_bridge_lease_path(workspace_id)
    with locked_path(path):
        data = _read(path)
        bridges = _bridges(data)
        existing = bridges.get(app_id)
        if _is_live_record(existing):
            assert isinstance(existing, dict)
            existing_pid = int(existing["pid"])
            if existing_pid != pid:
                projects = _projects(existing)
                projects.add(project_id)
                existing["projects"] = sorted(projects)
                _write(path, data)
                return WorkspaceBridgeLease(
                    workspace_id=workspace_id,
                    app_id=app_id,
                    project_id=project_id,
                    pid=existing_pid,
                    log_path=str(existing.get("log_path") or ""),
                    shared=True,
                )
        bridges[app_id] = {
            "pid": int(pid),
            "log_path": str(log_path),
            "started_at": time.time(),
            "projects": [project_id],
        }
        _write(path, data)
    return WorkspaceBridgeLease(
        workspace_id=workspace_id,
        app_id=app_id,
        project_id=project_id,
        pid=int(pid),
        log_path=str(log_path),
        shared=False,
    )


def release_workspace_bridge(
    lease: WorkspaceBridgeLease,
) -> WorkspaceBridgeRelease:
    """Release one project lease and state whether the bridge should stop."""
    path = workspace_bridge_lease_path(lease.workspace_id)
    with locked_path(path):
        data = _read(path)
        bridges = _bridges(data)
        record = bridges.get(lease.app_id)
        if not isinstance(record, dict):
            return WorkspaceBridgeRelease(
                pid=lease.pid,
                terminate=False,
                remaining_projects=(),
            )
        projects = _projects(record)
        projects.discard(lease.project_id)
        pid = int(record.get("pid") or lease.pid)
        if projects:
            record["projects"] = sorted(projects)
            _write(path, data)
            return WorkspaceBridgeRelease(
                pid=pid,
                terminate=False,
                remaining_projects=tuple(sorted(projects)),
            )
        bridges.pop(lease.app_id, None)
        _write(path, data)
        return WorkspaceBridgeRelease(
            pid=pid,
            terminate=_pid_alive(pid),
            remaining_projects=(),
        )


def provider_bridge_lease_path() -> Path:
    """Return the host-level derived lease ledger for provider WS processes."""
    return workspace_home() / "integrations" / "feishu" / "provider_bridge_leases.json"


def join_live_provider_bridge(
    *,
    workspace_id: str,
    app_id: str,
    project_id: str,
) -> ProviderBridgeLease | None:
    """Join the live provider App bridge, or return ``None`` for its owner."""
    path = provider_bridge_lease_path()
    project_key = _provider_project_key(workspace_id, project_id)
    with locked_path(path):
        data = _read(path)
        bridges = _bridges(data)
        record = bridges.get(app_id)
        if not _is_live_record(record):
            if record is not None:
                bridges.pop(app_id, None)
                _write(path, data)
            return None
        assert isinstance(record, dict)
        projects = _projects(record)
        projects.add(project_key)
        record["projects"] = sorted(projects)
        _write(path, data)
        return ProviderBridgeLease(
            workspace_id=workspace_id,
            app_id=app_id,
            project_id=project_id,
            pid=int(record["pid"]),
            log_path=str(record.get("log_path") or ""),
            shared=True,
        )


def register_provider_bridge(
    *,
    workspace_id: str,
    app_id: str,
    project_id: str,
    pid: int,
    log_path: Path,
) -> ProviderBridgeLease:
    """Register a spawned provider bridge, joining a concurrent winner safely."""
    path = provider_bridge_lease_path()
    project_key = _provider_project_key(workspace_id, project_id)
    with locked_path(path):
        data = _read(path)
        bridges = _bridges(data)
        existing = bridges.get(app_id)
        if _is_live_record(existing):
            assert isinstance(existing, dict)
            existing_pid = int(existing["pid"])
            if existing_pid != pid:
                projects = _projects(existing)
                projects.add(project_key)
                existing["projects"] = sorted(projects)
                _write(path, data)
                return ProviderBridgeLease(
                    workspace_id=workspace_id,
                    app_id=app_id,
                    project_id=project_id,
                    pid=existing_pid,
                    log_path=str(existing.get("log_path") or ""),
                    shared=True,
                )
        bridges[app_id] = {
            "pid": int(pid),
            "log_path": str(log_path),
            "started_at": time.time(),
            "projects": [project_key],
        }
        _write(path, data)
    return ProviderBridgeLease(
        workspace_id=workspace_id,
        app_id=app_id,
        project_id=project_id,
        pid=int(pid),
        log_path=str(log_path),
        shared=False,
    )


def release_provider_bridge(lease: ProviderBridgeLease) -> ProviderBridgeRelease:
    """Release one project from a provider bridge and stop only the last user."""
    path = provider_bridge_lease_path()
    project_key = _provider_project_key(lease.workspace_id, lease.project_id)
    with locked_path(path):
        data = _read(path)
        bridges = _bridges(data)
        record = bridges.get(lease.app_id)
        if not isinstance(record, dict):
            return ProviderBridgeRelease(
                pid=lease.pid,
                terminate=False,
                remaining_projects=(),
            )
        projects = _projects(record)
        projects.discard(project_key)
        pid = int(record.get("pid") or lease.pid)
        if projects:
            record["projects"] = sorted(projects)
            _write(path, data)
            return ProviderBridgeRelease(
                pid=pid,
                terminate=False,
                remaining_projects=tuple(sorted(projects)),
            )
        bridges.pop(lease.app_id, None)
        _write(path, data)
        return ProviderBridgeRelease(
            pid=pid,
            terminate=_pid_alive(pid),
            remaining_projects=(),
        )


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": _VERSION, "bridges": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": _VERSION, "bridges": {}}
    if not isinstance(raw, dict):
        return {"version": _VERSION, "bridges": {}}
    if not isinstance(raw.get("bridges"), dict):
        raw["bridges"] = {}
    return raw


def _write(path: Path, data: dict[str, Any]) -> None:
    payload = {
        "version": _VERSION,
        "bridges": _bridges(data),
    }
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _bridges(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("bridges")
    if isinstance(raw, dict):
        return raw
    data["bridges"] = {}
    return data["bridges"]


def _projects(record: dict[str, Any]) -> set[str]:
    raw = record.get("projects")
    if not isinstance(raw, list):
        return set()
    return {str(item).strip() for item in raw if str(item).strip()}


def _provider_project_key(workspace_id: str, project_id: str) -> str:
    """Avoid project-id collisions between independent workspaces."""
    return f"{str(workspace_id or 'default').strip()}:{str(project_id or '').strip()}"


def _is_live_record(record: object) -> bool:
    if not isinstance(record, dict):
        return False
    return _pid_alive(int(record.get("pid") or 0))
