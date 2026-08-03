"""Shared availability and liveness fence for fanout child dispatch."""

from __future__ import annotations

from datetime import datetime
import time

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent


class FanoutDispatchLivenessMixin:
    def _fanout_role_has_active_provider_turn(
        self,
        role_instance: str,
    ) -> bool:
        try:
            return self._active_provider_turn(role_instance) is not None
        except Exception:
            return False

    @staticmethod
    def _reader_dispatch_lost_role(event: ZfEvent) -> str:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == "worker.refresh.triggered":
            reason = str(payload.get("reason") or "").strip().lower()
            if reason in {"drift", "context_pressure", "task_complete"}:
                return ""
            return str(
                payload.get("role")
                or payload.get("instance_id")
                or event.actor
                or ""
            ).strip()
        if event.type == "cost.usage.capture_miss":
            reason = str(payload.get("reason") or "")
            if "session file not found" in reason:
                return str(
                    event.actor or payload.get("role") or ""
                ).strip()
        if event.type == "worker.launch_artifact.written":
            try:
                launch_attempt = int(payload.get("launch_attempt") or 0)
            except (TypeError, ValueError):
                launch_attempt = 0
            if launch_attempt > 1:
                return str(
                    payload.get("instance_id")
                    or payload.get("role")
                    or event.actor
                    or ""
                ).strip()
        return ""

    @staticmethod
    def _fanout_role_has_activity_after_signal(
        events: list[ZfEvent],
        role_instance: str,
        signal_index: int,
    ) -> bool:
        """Return whether a role produced fresh provider output after a signal.

        Disk usage readers can append an old provider sample after a worker was
        relaunched. Event ordering alone would then treat stale usage as proof
        that the replacement session accepted the in-flight briefing.
        """

        activity_types = {
            "agent.usage",
            "agent.text",
            "agent.tool.use",
            "agent.tool.result",
            "refactor.scan.completed",
            "verify.child.completed",
            "verify.child.failed",
            "judge.passed",
            "judge.failed",
        }
        signal_epoch = (
            _iso_epoch(events[signal_index].ts)
            if 0 <= signal_index < len(events)
            else None
        )
        for index, event in enumerate(events):
            if index <= signal_index or event.type not in activity_types:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if event.type == "agent.usage" and str(
                payload.get("source") or ""
            ) == "disk_reader":
                usage_epoch = _iso_epoch(payload.get("usage_timestamp"))
                if (
                    usage_epoch is None
                    or signal_epoch is None
                    or usage_epoch <= signal_epoch
                ):
                    continue
            if str(event.actor or "").strip() == role_instance:
                return True
            if str(payload.get("role_instance") or "").strip() == role_instance:
                return True
            if str(payload.get("role") or "").strip() == role_instance:
                return True
        return False

    def _fanout_dispatch_deferred_recently(
        self,
        *,
        fanout_id: str,
        child_id: str,
        role_instance: str,
        reason: str = "",
        window_s: float = 60.0,
    ) -> bool:
        try:
            events = self.event_log.read_all()
        except Exception:
            return False
        now = self._now()
        for event in reversed(events):
            if event.type != "fanout.child.dispatch_deferred":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if (
                str(payload.get("fanout_id") or "") != fanout_id
                or str(payload.get("child_id") or "") != child_id
                or str(payload.get("role_instance") or "") != role_instance
            ):
                continue
            if reason and str(payload.get("reason") or "") != reason:
                continue
            try:
                return now - self._event_epoch(event) < window_s
            except Exception:
                return True
        return False

    def _ensure_fanout_role_dispatchable(
        self,
        *,
        role: RoleConfig,
        fanout_id: str,
        stage_id: str,
        child_id: str,
        run_id: str,
        trace_id: str,
        task_id: str | None = None,
        causation_id: str | None = None,
        prompt_kind: str = "fanout_child",
        skip_send_window: bool = False,
        provider_session_replaced: bool = False,
    ) -> bool:
        """Return whether a fanout role can receive a prompt now."""

        activation_error = ""
        if self._role_is_on_demand(role):
            try:
                self._ensure_role_active(role, task_id=task_id)
            except Exception as exc:  # noqa: BLE001
                activation_error = str(exc)

        state = getattr(self, "_last_worker_state", {}).get(role.instance_id, "idle")
        if state == "busy":
            active_task_id = str(
                getattr(self, "_last_worker_task_id", {}).get(role.instance_id, "")
                or ""
            )
            active_task = self.task_store.get(active_task_id) if active_task_id else None
            if active_task is not None and active_task.status in {
                "done",
                "cancelled",
                "superseded",
            }:
                self._set_worker_state(
                    role.instance_id,
                    "idle",
                    reason="terminal canonical task released stale busy projection",
                    force=True,
                )
                state = "idle"
            elif (
                role.role_kind == "reader"
                and self._reader_fanout_busy_projection_is_stale(role.instance_id)
            ):
                self._set_worker_state(
                    role.instance_id,
                    "idle",
                    reason="terminal reader fanout released stale busy projection",
                    force=True,
                )
                state = "idle"
        try:
            dispatchable = bool(self._worker_dispatchable(role.instance_id))
        except Exception:
            dispatchable = True
        alive = True
        alive_error = ""
        try:
            alive = bool(self.transport.is_alive(role.instance_id))
        except Exception as exc:  # noqa: BLE001
            alive = False
            alive_error = str(exc)
        if alive and dispatchable and not activation_error:
            if (
                not provider_session_replaced
                and self._fanout_role_has_active_provider_turn(role.instance_id)
            ):
                self._emit_fanout_dispatch_deferred_once(
                    fanout_id=fanout_id,
                    trace_id=trace_id,
                    stage_id=stage_id,
                    child_id=child_id,
                    run_id=run_id,
                    role_instance=role.instance_id,
                    prompt_kind=prompt_kind,
                    reason="provider_turn_active",
                    state=state,
                    alive=alive,
                    dispatchable=dispatchable,
                    causation_id=causation_id,
                )
                return False
            last = getattr(self, "_last_prompt_sent_at", {}).get(role.instance_id)
            last_key, last_sent = last or ("", 0.0)
            if (
                not skip_send_window
                and last_sent
                and last_key == run_id
                and time.monotonic() - float(last_sent) < 10.0
            ):
                self._emit_fanout_dispatch_deferred_once(
                    fanout_id=fanout_id,
                    trace_id=trace_id,
                    stage_id=stage_id,
                    child_id=child_id,
                    run_id=run_id,
                    role_instance=role.instance_id,
                    prompt_kind=prompt_kind,
                    reason="briefing_send_window_active",
                    state=state,
                    alive=alive,
                    dispatchable=dispatchable,
                    causation_id=causation_id,
                )
                return False
            if state == "busy":
                self._emit_fanout_dispatch_deferred_once(
                    fanout_id=fanout_id,
                    trace_id=trace_id,
                    stage_id=stage_id,
                    child_id=child_id,
                    run_id=run_id,
                    role_instance=role.instance_id,
                    prompt_kind=prompt_kind,
                    reason="worker_state_not_dispatchable:busy",
                    state=state,
                    alive=alive,
                    dispatchable=False,
                    causation_id=causation_id,
                )
                return False
            return True

        non_self_healing = state == "blocked_human"
        if self._fanout_dispatch_deferred_recently(
            fanout_id=fanout_id,
            child_id=child_id,
            role_instance=role.instance_id,
            window_s=900.0 if non_self_healing else 60.0,
        ):
            return False
        reason_parts: list[str] = []
        if not alive:
            reason_parts.append("worker_transport_not_alive")
        if activation_error:
            reason_parts.append(f"role_activation_failed:{activation_error}")
        if alive_error:
            reason_parts.append(alive_error)
        if not dispatchable:
            reason_parts.append(f"worker_state_not_dispatchable:{state}")
        reason = "; ".join(reason_parts) or "worker_not_dispatchable"
        respawn_action = ""
        respawn_reason = ""
        if not alive and state != "respawning" and not activation_error:
            try:
                decision = self._respawn_instance(
                    role,
                    inject_idle_prompt=False,
                )
                respawn_action = str(getattr(decision, "action", "") or "")
                respawn_reason = str(getattr(decision, "reason", "") or "")
            except Exception as exc:  # noqa: BLE001
                respawn_action = "respawn_exception"
                respawn_reason = str(exc)
        self.event_writer.append(ZfEvent(
            type="fanout.child.dispatch_deferred",
            actor="zf-cli",
            payload={
                "fanout_id": fanout_id,
                "trace_id": trace_id,
                "stage_id": stage_id,
                "child_id": child_id,
                "run_id": run_id,
                "role_instance": role.instance_id,
                "prompt_kind": prompt_kind,
                "reason": reason,
                "worker_state": state,
                "transport_alive": alive,
                "dispatchable": dispatchable,
                "respawn_action": respawn_action,
                "respawn_reason": respawn_reason,
            },
            causation_id=causation_id,
            correlation_id=trace_id,
        ))
        return False

    def _emit_fanout_dispatch_deferred_once(
        self,
        *,
        fanout_id: str,
        trace_id: str,
        stage_id: str,
        child_id: str,
        run_id: str,
        role_instance: str,
        prompt_kind: str,
        reason: str,
        state: str,
        alive: bool,
        dispatchable: bool,
        causation_id: str | None,
    ) -> None:
        if self._fanout_dispatch_deferred_recently(
            fanout_id=fanout_id,
            child_id=child_id,
            role_instance=role_instance,
            reason=reason,
        ):
            return
        self.event_writer.append(ZfEvent(
            type="fanout.child.dispatch_deferred",
            actor="zf-cli",
            payload={
                "fanout_id": fanout_id,
                "trace_id": trace_id,
                "stage_id": stage_id,
                "child_id": child_id,
                "run_id": run_id,
                "role_instance": role_instance,
                "prompt_kind": prompt_kind,
                "reason": reason,
                "worker_state": state,
                "transport_alive": alive,
                "dispatchable": dispatchable,
            },
            causation_id=causation_id,
            correlation_id=trace_id,
        ))

    def _reader_fanout_busy_projection_is_stale(self, role_instance: str) -> bool:
        """Recognize restart-persistent reader busy state after child terminal."""

        try:
            if self._active_fanout_child_for_instance(role_instance) is not None:
                return False
            events = self.event_log.read_all()
        except Exception:
            return False
        for event in reversed(events):
            if event.type != "worker.state.changed" or event.actor != role_instance:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            return (
                str(payload.get("to") or "") == "busy"
                and str(payload.get("reason") or "").startswith(
                    "dispatched fanout child "
                )
            )
        return False


__all__ = ["FanoutDispatchLivenessMixin"]


def _iso_epoch(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None
