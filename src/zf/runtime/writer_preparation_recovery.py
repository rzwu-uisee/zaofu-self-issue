"""Bounded recovery for writer failures before provider delivery."""

from __future__ import annotations

from typing import Any

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent


_WRITER_PREPARATION_RETRY_CAP = 3


class WriterPreparationRecoveryMixin:
    def _prepare_writer_dispatch_or_defer(
        self,
        *,
        context: Any,
        child: Any,
        task_item: dict[str, Any],
        role: RoleConfig,
        causation_id: str,
        prepared_dispatch: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if prepared_dispatch and prepared_dispatch.get("preparation_failed"):
            return None
        try:
            return prepared_dispatch or self._prepare_writer_fanout_child_operation(
                context=context,
                child=child,
                task_item=task_item,
                role=role,
                causation_id=causation_id,
            )
        except Exception as exc:
            self._record_writer_preparation_failure(
                context=context,
                child=child,
                task_item=task_item,
                role=role,
                run_id=f"run-{context.fanout_id}-{child.child_id}",
                causation_id=causation_id,
                reason=f"writer dispatch preparation failed: {exc}",
            )
            return None

    def _record_writer_preparation_failure(
        self,
        *,
        context: Any,
        child: Any,
        task_item: dict[str, Any],
        role: RoleConfig,
        run_id: str,
        causation_id: str,
        reason: str,
    ) -> bool:
        """Record a bounded pre-send failure and release its false claim."""

        task_id = str(task_item.get("task_id") or "")
        prior_deferrals = self._fanout_child_dispatch_deferrals(
            context.fanout_id,
            child.child_id,
        )
        deferred = prior_deferrals < _WRITER_PREPARATION_RETRY_CAP
        event_type = (
            "fanout.child.dispatch_deferred"
            if deferred
            else "fanout.child.failed"
        )
        self.event_writer.append(ZfEvent(
            type=event_type,
            actor="zf-cli",
            task_id=task_id or None,
            payload={
                "fanout_id": context.fanout_id,
                "trace_id": context.trace_id,
                "stage_id": context.stage_id,
                "child_id": child.child_id,
                "run_id": run_id,
                "role_instance": role.instance_id,
                "task_id": task_id,
                "reason": reason,
                "failure_kind": "dispatch_preparation",
                "attempt": prior_deferrals + 1,
                "max_attempts": _WRITER_PREPARATION_RETRY_CAP,
            },
            causation_id=causation_id,
            correlation_id=context.trace_id,
        ))
        task = self.task_store.get(task_id) if task_id else None
        if task is not None and task.status == "in_progress":
            self._move_task(task_id, "blocked", trigger_event=event_type)
        task = self.task_store.get(task_id) if task_id else None
        if task is not None and task.status == "backlog":
            self._park_writer_fanout_deferred_task(
                task_id=task_id,
                fanout_id=context.fanout_id,
                child_id=child.child_id,
            )
        task = self.task_store.get(task_id) if task_id else None
        if task is not None and task.status == "blocked":
            blocked_reason = (
                f"fanout_dispatch_deferred:{context.fanout_id}:{child.child_id}"
                if deferred
                else (
                    "fanout_dispatch_preparation_failed:"
                    f"{context.fanout_id}:{child.child_id}"
                )
            )
            self.task_store.update(
                task_id,
                assigned_to="",
                active_dispatch_id="",
                blocked_reason=blocked_reason,
            )
        getattr(self, "_active_dispatch_ids", {}).pop(task_id, None)
        self._release_writer_fanout_slot(
            context=context,
            child=child,
            task_item=task_item,
            role=role,
            causation_id=causation_id,
            reason="dispatch_preparation_failed",
        )
        self._set_worker_state(
            role.instance_id,
            "idle",
            reason="writer dispatch preparation failed before provider send",
            task_id=task_id,
            force=True,
        )
        return deferred

    def _fanout_child_dispatch_deferrals(
        self,
        fanout_id: str,
        child_id: str,
    ) -> int:
        try:
            events = self.event_log.read_all()
        except Exception:
            return 0
        return sum(
            1
            for event in events
            if event.type == "fanout.child.dispatch_deferred"
            and isinstance(event.payload, dict)
            and str(event.payload.get("fanout_id") or "") == fanout_id
            and str(event.payload.get("child_id") or "") == child_id
            and str(event.payload.get("failure_kind") or "")
            == "dispatch_preparation"
        )
