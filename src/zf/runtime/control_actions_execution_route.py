"""Controlled task-time execution route switch and bounded continuation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from zf.core.events import ZfEvent
from zf.core.state.session import SessionStore, ZfNotInitialized
from zf.core.task.store import TERMINAL_STATES, TaskStore
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.execution_policy_routing import (
    EXECUTION_ROUTE_APPLIED_EVENT,
    EXECUTION_ROUTE_SWITCH_ACTION,
    ExecutionRouteError,
    ROUTE_SELECTION_RECEIPT_SCHEMA,
    classify_execution_route_trigger,
    execution_route_event_run_id,
    execution_route_payload,
    execution_route_policy_digest,
    execution_routing_policy,
    resolve_execution_route,
)
from zf.runtime.execution_route_state import ExecutionRouteStore


class ExecutionRouteActionsMixin:
    def _execution_route_switch(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if self.actor != "run-manager" or self.source != "run-manager":
            return self._route_failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=str(payload.get("task_id") or ""),
                reason="execution route switch requires Run Manager authority",
                status_code=403,
                status="policy_rejected",
            )
        policy = execution_routing_policy(self.config)
        if policy is None or not bool(getattr(policy, "enabled", False)):
            return self._route_failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=str(payload.get("task_id") or ""),
                reason="runtime.execution_routing is disabled",
                status_code=403,
                status="policy_rejected",
            )
        task_id = str(payload.get("task_id") or "").strip()
        route_id = str(payload.get("route_id") or "").strip()
        trigger_class = str(payload.get("trigger_class") or "").strip()
        checkpoint_id = str(payload.get("checkpoint_id") or "").strip()
        source_event_id = str(payload.get("source_event_id") or "").strip()
        if not all((task_id, route_id, trigger_class, checkpoint_id, source_event_id)):
            return self._route_failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id,
                reason="execution route switch requires task/route/trigger/checkpoint/source",
                status_code=422,
                status="invalid_payload",
            )
        try:
            session = SessionStore(self.state_dir / "session.yaml").load()
        except ZfNotInitialized as exc:
            return self._route_failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id,
                reason=str(exc),
                status_code=409,
                status="stale",
            )
        task_store = TaskStore(self.state_dir / "kanban.json")
        task = task_store.get(task_id)
        if task is None or task.status in TERMINAL_STATES:
            return self._route_failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id,
                reason="execution route task is missing or terminal",
                status_code=409,
                status="stale",
            )
        workflow_run_id = str(payload.get("workflow_run_id") or "").strip()
        expected_run_id = str(
            task.execution_binding.workflow_run_id or session.session_id
        ).strip()
        if workflow_run_id != expected_run_id:
            return self._route_failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id,
                reason="execution route workflow_run_id is stale",
                status_code=409,
                status="stale",
            )
        instance_id = str(
            payload.get("instance_id")
            or payload.get("role_instance")
            or task.assigned_to
            or ""
        ).strip()
        role = self._execution_route_role(instance_id, str(payload.get("role") or ""))
        if role is None or str(task.assigned_to or "") not in {
            role.name,
            role.instance_id,
        }:
            return self._route_failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id,
                reason="execution route task ownership is stale or ambiguous",
                status_code=409,
                status="stale",
            )
        dispatch_id = str(payload.get("dispatch_id") or "").strip()
        if dispatch_id and dispatch_id != str(task.active_dispatch_id or ""):
            return self._route_failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id,
                reason="execution route dispatch identity is stale",
                status_code=409,
                status="stale",
            )
        source_event = self._execution_route_source_event(source_event_id)
        if source_event is None:
            return self._route_failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id,
                reason="execution route source event is unavailable",
                status_code=422,
                status="invalid_evidence",
            )
        if execution_route_event_run_id(source_event) != workflow_run_id:
            return self._route_failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id,
                reason="execution route source event belongs to another workflow run",
                status_code=409,
                status="stale",
            )
        if classify_execution_route_trigger(source_event) != trigger_class:
            return self._route_failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id,
                reason="execution route source event does not prove the trigger class",
                status_code=422,
                status="invalid_evidence",
            )
        try:
            route = resolve_execution_route(
                self.config,
                route_id=route_id,
                role=role,
                trigger_class=trigger_class,
                flow_kind=str(payload.get("flow_kind") or role.flow_kind or ""),
            )
        except ExecutionRouteError as exc:
            return self._route_failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id,
                reason=str(exc),
                status_code=409,
                status="policy_rejected",
            )
        policy_digest = execution_route_policy_digest(self.config)
        supplied_policy_digest = str(payload.get("policy_digest") or "").strip()
        if supplied_policy_digest and supplied_policy_digest != policy_digest:
            return self._route_failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id,
                reason="execution route policy digest drift",
                status_code=409,
                status="stale",
            )
        action_id = str(payload.get("action_id") or "").strip() or _stable_id(
            "route-action", workflow_run_id, task_id, route_id, checkpoint_id
        )
        source_event_ids = list(dict.fromkeys([
            source_event_id,
            *[
                str(item)
                for item in payload.get("source_event_ids") or []
                if str(item).strip()
            ],
        ]))
        receipt = write_immutable_json_sidecar(
            self.state_dir,
            {
                "schema_version": ROUTE_SELECTION_RECEIPT_SCHEMA,
                "action_id": action_id,
                "checkpoint_id": checkpoint_id,
                "workflow_run_id": workflow_run_id,
                "task_id": task_id,
                "role": role.name,
                "instance_id": role.instance_id,
                "dispatch_id": str(task.active_dispatch_id or dispatch_id),
                "trigger_class": trigger_class,
                "source_event_id": source_event_id,
                "source_event_type": source_event.type,
                "source_event_ids": source_event_ids,
                "policy_digest": policy_digest,
                "previous_route": _role_route_payload(role),
                "effective_route": execution_route_payload(route),
                "switch_cap": int(getattr(policy, "max_switches_per_task", 1) or 1),
                "authority": "run_manager_controlled_action",
            },
            root="execution-routing/receipts",
            kind="route-selection-receipt",
            schema_version=ROUTE_SELECTION_RECEIPT_SCHEMA,
            created_by="run-manager",
            source_event_id=source_event_id,
        )
        try:
            activated = ExecutionRouteStore(self.state_dir).activate(
                task_id=task_id,
                workflow_run_id=workflow_run_id,
                role=role.name,
                instance_id=role.instance_id,
                dispatch_id=str(task.active_dispatch_id or dispatch_id),
                route=route,
                trigger_class=trigger_class,
                source_event_id=source_event_id,
                source_event_type=source_event.type,
                source_event_ids=source_event_ids,
                checkpoint_id=checkpoint_id,
                action_id=action_id,
                policy_digest=policy_digest,
                receipt=receipt,
                max_switches=int(getattr(policy, "max_switches_per_task", 1) or 1),
            )
        except ExecutionRouteError as exc:
            return self._route_failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id,
                reason=str(exc),
                status_code=409,
                status="policy_rejected",
            )
        if not bool(activated.get("applied")):
            return {
                "_status_code": 200,
                "ok": True,
                "status": "already_applied",
                "action": action,
                "requested_action": requested_action,
                "task_id": task_id,
                "receipt_ref": receipt,
                "route_record": activated.get("record", {}),
            }
        applied = self.writer.emit(
            EXECUTION_ROUTE_APPLIED_EVENT,
            actor="run-manager",
            task_id=task_id,
            causation_id=requested.id,
            correlation_id=workflow_run_id,
            payload={
                "schema_version": "execution-route-selection.v1",
                "action_id": action_id,
                "checkpoint_id": checkpoint_id,
                "workflow_run_id": workflow_run_id,
                "task_id": task_id,
                "role": role.name,
                "instance_id": role.instance_id,
                "route_id": route.id,
                "trigger_class": trigger_class,
                "policy_digest": policy_digest,
                "receipt_ref": str(receipt.get("ref") or ""),
                "receipt_digest": str(receipt.get("sha256") or ""),
                "switch_count": int(
                    (activated.get("record") or {}).get("switch_count") or 0
                ),
                "source_event_ids": source_event_ids,
            },
        )
        respawn = self.writer.emit(
            "worker.respawn.requested",
            actor="run-manager",
            task_id=task_id,
            causation_id=applied.id,
            correlation_id=workflow_run_id,
            payload={
                "schema_version": "execution-route-respawn.v1",
                "instance_id": role.instance_id,
                "role_instance": role.instance_id,
                "task_id": task_id,
                "workflow_run_id": workflow_run_id,
                "dispatch_id": str(task.active_dispatch_id or dispatch_id),
                "checkpoint_id": checkpoint_id,
                "route_id": route.id,
                "route_receipt_ref": str(receipt.get("ref") or ""),
                "reason": f"execution route switch: {trigger_class}",
                "source_event_ids": source_event_ids,
                "recovery_decision_owner": "run_manager",
                "recovery_effect_owner": "workflow_runtime_coordinator",
            },
        )
        task_store.update(task_id, status="in_progress", assigned_to=role.instance_id)
        rework = self.writer.emit(
            "task.rework.requested",
            actor="run-manager",
            task_id=task_id,
            causation_id=applied.id,
            correlation_id=workflow_run_id,
            payload={
                "schema_version": "task-rework-request.v1",
                "source": "execution_route_switch",
                "task_id": task_id,
                "role": role.instance_id,
                "assignee": role.instance_id,
                "route_id": route.id,
                "route_receipt_ref": str(receipt.get("ref") or ""),
                "failure_fingerprint": str(payload.get("fingerprint") or ""),
                "source_event_ids": source_event_ids,
                "recovery_owner": "run_manager",
            },
        )
        assigned = self.writer.emit(
            "task.assigned",
            actor="run-manager",
            task_id=task_id,
            causation_id=rework.id,
            correlation_id=workflow_run_id,
            payload={
                "task_id": task_id,
                "role": role.instance_id,
                "assignee": role.instance_id,
                "source": "execution_route_switch",
                "route_id": route.id,
                "route_receipt_ref": str(receipt.get("ref") or ""),
            },
        )
        completed = self.writer.emit(
            "execution.route.switch.completed",
            actor="run-manager",
            task_id=task_id,
            causation_id=applied.id,
            correlation_id=workflow_run_id,
            payload={
                "schema_version": "execution-route-switch-result.v1",
                "action_id": action_id,
                "checkpoint_id": checkpoint_id,
                "route_id": route.id,
                "receipt_ref": str(receipt.get("ref") or ""),
                "emitted_event_ids": [applied.id, respawn.id, rework.id, assigned.id],
            },
        )
        self._completed(
            requested=requested,
            event=completed,
            action=action,
            requested_action=requested_action,
            status="applied",
            task_id=task_id,
        )
        return {
            "_status_code": 200,
            "ok": True,
            "status": "applied",
            "action": action,
            "requested_action": requested_action,
            "task_id": task_id,
            "event_id": completed.id,
            "emitted_event_ids": [applied.id, respawn.id, rework.id, assigned.id],
            "receipt_ref": receipt,
            "route_record": activated.get("record", {}),
        }

    def _execution_route_role(self, instance_id: str, role_name: str) -> Any:
        roles = list(getattr(self.config, "roles", []) or [])
        exact = [role for role in roles if role.instance_id == instance_id]
        if len(exact) == 1:
            return exact[0]
        named = [role for role in roles if role.name == role_name or role.name == instance_id]
        return named[0] if len(named) == 1 else None

    def _execution_route_source_event(self, event_id: str) -> ZfEvent | None:
        event_log = getattr(self.writer, "event_log", None)
        if event_log is None:
            return None
        return next((
            event
            for event in reversed(event_log.read_all())
            if event.id == event_id
        ), None)

    def _route_failed(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        task_id: str,
        reason: str,
        status_code: int,
        status: str,
    ) -> dict[str, Any]:
        return self._failed(
            requested=requested,
            action=action,
            requested_action=requested_action,
            task_id=task_id or None,
            reason=reason,
            status_code=status_code,
            status=status,
        )


def _role_route_payload(role: Any) -> dict[str, Any]:
    return {
        "backend": str(role.backend or ""),
        "model": str(role.model or ""),
        "model_reasoning_effort": str(role.model_reasoning_effort or ""),
        "execution_profile": str(role.execution.default_profile or ""),
        "provider_session": (
            asdict(role.provider_session) if role.provider_session is not None else None
        ),
    }


def _stable_id(prefix: str, *parts: str) -> str:
    raw = json.dumps(parts, separators=(",", ":"), ensure_ascii=False)
    return prefix + "-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


__all__ = ["ExecutionRouteActionsMixin", "EXECUTION_ROUTE_SWITCH_ACTION"]
