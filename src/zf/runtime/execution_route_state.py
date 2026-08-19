"""Task-scoped execution route state and provider-spawn projection."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.config.schema import ExecutionRouteConfig, RoleConfig, ZfConfig
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.core.state.session import SessionStore, ZfNotInitialized
from zf.core.task.store import TERMINAL_STATES, TaskStore
from zf.runtime.execution_policy_routing import (
    EXECUTION_ROUTE_STATE_SCHEMA,
    ExecutionRouteError,
    apply_execution_route,
    execution_route_payload,
    execution_route_policy_digest,
    execution_routing_policy,
    resolve_execution_route,
)


class ExecutionRouteStore:
    """Atomic current state for task-scoped route switches."""

    def __init__(self, state_dir: Path) -> None:
        self.path = Path(state_dir) / "execution-routing" / "state.json"

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty()
        if not isinstance(raw, dict):
            return self._empty()
        raw.setdefault("schema_version", EXECUTION_ROUTE_STATE_SCHEMA)
        raw.setdefault("revision", 0)
        raw.setdefault("tasks", {})
        return raw

    def task_record(
        self,
        task_id: str,
        *,
        workflow_run_id: str = "",
    ) -> dict[str, Any] | None:
        record = self.load().get("tasks", {}).get(task_id)
        if not isinstance(record, dict):
            return None
        if workflow_run_id and str(record.get("workflow_run_id") or "") != str(
            workflow_run_id
        ):
            return None
        return dict(record)

    def activate(
        self,
        *,
        task_id: str,
        workflow_run_id: str,
        role: str,
        instance_id: str,
        dispatch_id: str,
        route: ExecutionRouteConfig,
        trigger_class: str,
        source_event_id: str,
        source_event_type: str,
        source_event_ids: list[str],
        checkpoint_id: str,
        action_id: str,
        policy_digest: str,
        receipt: dict[str, Any],
        max_switches: int,
    ) -> dict[str, Any]:
        if not task_id or not workflow_run_id or not instance_id:
            raise ExecutionRouteError(
                "route activation requires run/task/instance identity"
            )
        with locked_path(self.path):
            state = self.load()
            tasks = state.setdefault("tasks", {})
            existing = tasks.get(task_id)
            existing = dict(existing) if isinstance(existing, dict) else {}
            if str(existing.get("workflow_run_id") or "") != workflow_run_id:
                existing = {}
            if existing and str(existing.get("action_id") or "") == action_id:
                return {"applied": False, "record": existing, "state": state}
            used = [
                str(item)
                for item in existing.get("used_route_ids") or []
                if str(item).strip()
            ]
            switch_count = int(existing.get("switch_count") or 0)
            if switch_count >= max_switches:
                raise ExecutionRouteError("execution route switch cap exhausted")
            if route.id in used:
                raise ExecutionRouteError(
                    f"execution route {route.id!r} was already used by task {task_id!r}"
                )
            record = {
                "schema_version": "task-execution-route.v1",
                "task_id": task_id,
                "workflow_run_id": workflow_run_id,
                "role": role,
                "instance_id": instance_id,
                "dispatch_id": dispatch_id,
                "route_id": route.id,
                "route": execution_route_payload(route),
                "trigger_class": trigger_class,
                "source_event_id": source_event_id,
                "source_event_type": source_event_type,
                "source_event_ids": list(dict.fromkeys(source_event_ids)),
                "checkpoint_id": checkpoint_id,
                "action_id": action_id,
                "policy_digest": policy_digest,
                "receipt_ref": str(receipt.get("ref") or ""),
                "receipt_digest": str(receipt.get("sha256") or ""),
                "switch_count": switch_count + 1,
                "used_route_ids": [*used, route.id],
                "activated_at": datetime.now(timezone.utc).isoformat(),
            }
            state["revision"] = int(state.get("revision") or 0) + 1
            tasks[task_id] = record
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                self.path,
                json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            )
            return {"applied": True, "record": record, "state": state}

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "schema_version": EXECUTION_ROUTE_STATE_SCHEMA,
            "revision": 0,
            "tasks": {},
        }


def route_policy_for_spawn(
    *,
    state_dir: Path,
    config: ZfConfig | None,
    role: RoleConfig,
) -> tuple[RoleConfig, dict[str, Any]]:
    """Return a selected task route only while its exact owner is current."""

    record = _current_route_record(state_dir, config=config, role=role)
    if record is None:
        return role, {"applies": False, "reason": "no_current_task_route"}
    route = resolve_execution_route(
        config,
        route_id=str(record.get("route_id") or ""),
        role=role,
        trigger_class=str(record.get("trigger_class") or ""),
        flow_kind=role.flow_kind,
    )
    effective = apply_execution_route(role, route)
    return effective, {
        "applies": True,
        "applied": effective != role,
        "policy_id": "task_execution_route.v1",
        "task_id": str(record.get("task_id") or ""),
        "workflow_run_id": str(record.get("workflow_run_id") or ""),
        "route_id": route.id,
        "receipt_ref": str(record.get("receipt_ref") or ""),
        "receipt_digest": str(record.get("receipt_digest") or ""),
        "trigger_class": str(record.get("trigger_class") or ""),
        "changes": _route_changes(role, effective),
        "event_fields": {
            "task_id": str(record.get("task_id") or ""),
            "workflow_run_id": str(record.get("workflow_run_id") or ""),
            "route_id": route.id,
            "receipt_ref": str(record.get("receipt_ref") or ""),
            "receipt_digest": str(record.get("receipt_digest") or ""),
        },
        "reason": (
            "task-scoped execution route applied before provider session freeze"
        ),
    }


def _current_route_record(
    state_dir: Path,
    *,
    config: ZfConfig | None,
    role: RoleConfig,
) -> dict[str, Any] | None:
    policy = execution_routing_policy(config)
    if policy is None or not bool(getattr(policy, "enabled", False)):
        return None
    state_dir = Path(state_dir)
    state = ExecutionRouteStore(state_dir).load()
    records = state.get("tasks") if isinstance(state, dict) else {}
    try:
        session = SessionStore(state_dir / "session.yaml").load()
    except ZfNotInitialized:
        return None
    current: list[dict[str, Any]] = []
    task_store = TaskStore(state_dir / "kanban.json")
    worker = SessionStore(state_dir / "session.yaml").get_worker(role.instance_id)
    policy_digest = execution_route_policy_digest(config)
    for value in (records or {}).values():
        if not isinstance(value, dict):
            continue
        record = dict(value)
        if str(record.get("instance_id") or "") != role.instance_id:
            continue
        if str(record.get("policy_digest") or "") != policy_digest:
            continue
        task_id = str(record.get("task_id") or "")
        task = task_store.get(task_id)
        if task is None or task.status in TERMINAL_STATES:
            continue
        expected_run_id = str(
            task.execution_binding.workflow_run_id or session.session_id
        ).strip()
        if str(record.get("workflow_run_id") or "") != expected_run_id:
            continue
        if task.status not in {"backlog", "in_progress"}:
            continue
        if str(task.assigned_to or "") not in {role.name, role.instance_id}:
            continue
        if (
            worker is not None
            and worker.last_dispatch
            and worker.last_dispatch != task_id
        ):
            continue
        current.append(record)
    return current[0] if len(current) == 1 else None


def _route_changes(original: RoleConfig, effective: RoleConfig) -> dict[str, Any]:
    pairs = {
        "backend": (original.backend, effective.backend),
        "model": (original.model, effective.model),
        "model_reasoning_effort": (
            original.model_reasoning_effort,
            effective.model_reasoning_effort,
        ),
        "execution.default_profile": (
            original.execution.default_profile,
            effective.execution.default_profile,
        ),
        "provider_session": (
            asdict(original.provider_session) if original.provider_session else None,
            asdict(effective.provider_session) if effective.provider_session else None,
        ),
    }
    return {
        key: {"from": before, "to": after}
        for key, (before, after) in pairs.items()
        if before != after
    }


__all__ = ["ExecutionRouteStore", "route_policy_for_spawn"]
