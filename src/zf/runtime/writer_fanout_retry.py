"""Writer fanout retry preparation and completion identity guards."""

from __future__ import annotations

from typing import Any

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent


_WRITER_RETRY_CALL_IDENTITY_KEYS = frozenset({
    "operation_id",
    "parent_operation_id",
    "request_hash",
    "attempt_id",
    "dispatch_id",
    "lease_id",
    "operation_request_status",
    "result_scratch_ref",
    "semantic_result_submit_mode",
    "admitted_call_result_ref",
    "admitted_call_result_digest",
    "control_result_ref",
    "call_result_envelope_ref",
    "semantic_submit_admission_event_id",
    "target_snapshot_ref",
    "target_snapshot_digest",
    "impl_self_check_ref",
    "impl_self_check_digest",
})


def fanout_operation_key(*, context: Any, child: Any, payload: dict[str, Any]) -> str:
    if str(payload.get("flow_kind") or "") == "workflow":
        return f"{child.child_id}@fanout:{context.fanout_id}"
    if context.trigger_event_id:
        return f"{child.child_id}@trig:{context.trigger_event_id[:12]}"
    return child.child_id


class WriterFanoutRetryMixin:
    """Host methods for immutable writer retries and stale result rejection."""

    def _prepare_writer_fanout_retry_operation(
        self,
        *,
        context,
        child,
        task_item: dict[str, Any],
        role: RoleConfig,
        run_id: str,
        retry_attempt: int,
        previous_dispatch: ZfEvent,
    ) -> dict[str, Any]:
        """Mint a fresh immutable call while preserving writer retry work."""

        from zf.runtime.call_result_admission import result_protocol_mode
        from zf.runtime.call_result_runtime import prepare_call_operation
        from zf.runtime.workdirs import WorkdirManager

        previous_operation_id = str(
            task_item.get("operation_id")
            or previous_dispatch.payload.get("operation_id")
            or ""
        ).strip()
        for key in _WRITER_RETRY_CALL_IDENTITY_KEYS:
            task_item.pop(key, None)

        task_id = str(task_item.get("task_id") or "")
        manager = WorkdirManager(
            state_dir=self.state_dir,
            project_root=self.project_root,
            config=self.config,
        )
        # A terminal retry may already have a durable task ref even when a
        # graceful stop removed its managed worktree. Recreate the worktree and
        # restore that exact handoff instead of restarting at the project base.
        plan = manager.prepare(role)
        task_ref = manager.task_ref_metadata(task_id)
        task_ref_trace_id = str(task_ref.get("trace_id") or "").strip()
        task_ref_source = str(
            task_ref.get("source_commit") or task_ref.get("task_ref") or ""
        ).strip()
        retry_source_ref = (
            task_ref_source
            if task_ref_source and task_ref_trace_id in {"", context.trace_id}
            else ""
        )
        # A downstream task may be interrupted before it creates its own
        # TaskRef. Its previous dispatch base can still contain every admitted
        # dependency TaskRef. Preserve a writer HEAD that already has task work.
        if not retry_source_ref:
            composed_base = str(
                task_item.get("base_commit")
                or previous_dispatch.payload.get("base_commit")
                or ""
            ).strip()
            bare_dispatch_base = str(
                task_item.get("dispatch_base_commit")
                or previous_dispatch.payload.get("dispatch_base_commit")
                or ""
            ).strip()
            current_head = manager.current_writer_head(role)
            if (
                composed_base
                and bare_dispatch_base
                and current_head == bare_dispatch_base
                and composed_base != current_head
            ):
                retry_source_ref = composed_base
        workdir_sync: dict[str, str] = {}
        if retry_source_ref:
            workdir_sync = manager.sync_writer_to_source_ref(
                role,
                source_ref_override=retry_source_ref,
            )
        skill_entries = self._record_skill_provenance(
            role=role,
            task_id=task_id,
        )
        operation_payload = {
            **(child.payload if isinstance(child.payload, dict) else {}),
            **task_item,
            "fanout_id": context.fanout_id,
            "trace_id": context.trace_id,
            "stage_id": context.stage_id,
            "child_id": child.child_id,
            "run_id": run_id,
            "role_instance": role.instance_id,
            "target_ref": child.target_ref or context.target_ref,
            "skills": list(role.skills),
            "canonical_success_event": "dev.build.done",
            "canonical_failure_event": "dev.blocked",
            "retry_of_run_id": str(previous_dispatch.payload.get("run_id") or ""),
            "retry_attempt": retry_attempt,
            "workdir_sync": workdir_sync,
        }
        if previous_operation_id:
            operation_payload.update({
                "parent_operation_id": previous_operation_id,
                "retry_of_operation_id": previous_operation_id,
            })
        call_mode = result_protocol_mode(self.config, operation_payload)
        prepared_call = None
        if call_mode != "shadow" or bool(operation_payload.get("durable_operation")):
            prepared_call = prepare_call_operation(
                self,
                payload=operation_payload,
                operation_type="fanout_writer_child",
                operation_key=(
                    f"{fanout_operation_key(context=context, child=child, payload=operation_payload)}"
                    f"@retry:{retry_attempt}"
                ),
                stage_id=context.stage_id,
                task_id=task_id,
                dispatch_id=run_id,
                causation_id=previous_dispatch.id,
                correlation_id=context.trace_id,
            )
        task_item.update(operation_payload)
        if isinstance(child.payload, dict):
            child.payload.update(operation_payload)
        return {
            "plan": plan,
            "skill_entries": skill_entries,
            "operation_payload": operation_payload,
            "prepared_call": prepared_call,
            "workdir_sync": workdir_sync,
        }

    def _prepare_writer_fanout_retry_dispatch(
        self,
        *,
        manifest: dict[str, Any],
        child: dict[str, Any],
        role: RoleConfig,
        run_id: str,
        attempt: int,
        previous_dispatch: ZfEvent,
    ) -> dict[str, Any]:
        """Restore one writer child and render its retry briefing."""

        from zf.runtime.fanout import FanoutChild, FanoutContext

        fanout_id = str(manifest.get("fanout_id") or "")
        child_id = str(child.get("child_id") or "")
        trace_id = str(manifest.get("trace_id") or "")
        stage_id = str(manifest.get("stage_id") or "")
        task_item = (
            dict(child.get("payload"))
            if isinstance(child.get("payload"), dict)
            else {}
        )
        for key, value in child.items():
            if key not in {"payload", "status", "report"} and value not in (
                None,
                "",
                [],
                {},
            ):
                task_item[key] = value
        retry_child = FanoutChild(
            child_id=child_id,
            role_instance=role.instance_id,
            target_ref=str(
                child.get("target_ref") or manifest.get("target_ref") or ""
            ),
            payload=task_item,
        )
        retry_context = FanoutContext(
            fanout_id=fanout_id,
            stage_id=stage_id,
            topology="fanout_writer_scoped",
            trace_id=trace_id,
            trigger_event_id=str(manifest.get("trigger_event_id") or ""),
            target_ref=str(manifest.get("target_ref") or ""),
        )
        prepared_retry = self._prepare_writer_fanout_retry_operation(
            context=retry_context,
            child=retry_child,
            task_item=task_item,
            role=role,
            run_id=run_id,
            retry_attempt=attempt,
            previous_dispatch=previous_dispatch,
        )
        operation_payload = dict(prepared_retry.get("operation_payload") or {})
        briefing_path = self._write_writer_fanout_briefing(
            role=role,
            context=retry_context,
            child=retry_child,
            task_item=task_item,
            run_id=run_id,
            pdd_id=str(task_item.get("pdd_id") or ""),
            workdir_plan=prepared_retry["plan"],
            skill_entries=list(prepared_retry.get("skill_entries") or []),
            rework_feedback=[
                "Resume the existing writer worktree for this failed child; "
                "preserve valid implementation and submit the current result "
                "through the new blocking operation contract."
            ],
            rework_attempt=attempt,
        )
        return {
            "briefing_path": briefing_path,
            "identity_sources": (operation_payload, task_item),
            "operation_payload": operation_payload,
            "prepared_call": prepared_retry.get("prepared_call"),
            "task_item": task_item,
        }

    @staticmethod
    def _writer_completion_operation_ids(
        payload: dict[str, Any],
        child: dict[str, Any],
    ) -> tuple[str, str]:
        child_payload = (
            child.get("payload")
            if isinstance(child.get("payload"), dict)
            else {}
        )
        actual = str(payload.get("operation_id") or "").strip()
        expected = str(
            child.get("operation_id")
            or child_payload.get("operation_id")
            or ""
        ).strip()
        return actual, expected

    @classmethod
    def _writer_completion_operation_mismatch(
        cls,
        payload: dict[str, Any],
        child: dict[str, Any],
    ) -> bool:
        actual, expected = cls._writer_completion_operation_ids(payload, child)
        return bool(actual and expected and actual != expected)

    def _emit_writer_fanout_stale_completion(
        self,
        *,
        event: ZfEvent,
        payload: dict[str, Any],
        manifest: dict[str, Any],
        child: dict[str, Any],
        expected_run_id: str,
        expected_operation_id: str = "",
        actual_operation_id: str = "",
    ) -> None:
        fanout_id = str(manifest.get("fanout_id") or "")
        child_id = str(child.get("child_id") or "")
        if self._fanout_stale_completion_recorded(
            fanout_id=fanout_id,
            child_id=child_id,
            source_event_id=event.id,
        ):
            return
        reason = "fanout child run_id does not match active run"
        if expected_operation_id and actual_operation_id:
            reason = "fanout child operation_id does not match active operation"
        self.event_writer.append(ZfEvent(
            type="fanout.child.stale_completion",
            actor="zf-cli",
            task_id=event.task_id or str(payload.get("task_id") or "") or None,
            payload={
                "fanout_id": fanout_id,
                "trace_id": str(manifest.get("trace_id") or ""),
                "stage_id": str(manifest.get("stage_id") or ""),
                "child_id": child_id,
                "task_id": str(event.task_id or payload.get("task_id") or ""),
                "role_instance": str(
                    payload.get("role_instance")
                    or child.get("role_instance")
                    or event.actor
                    or ""
                ),
                "expected_run_id": expected_run_id,
                "actual_run_id": str(payload.get("run_id") or ""),
                "expected_operation_id": expected_operation_id,
                "actual_operation_id": actual_operation_id,
                "result_event_id": event.id,
                "source_event_type": event.type,
                "reason": reason,
            },
            causation_id=event.id,
            correlation_id=event.correlation_id or manifest.get("trace_id", ""),
        ))

    def _emit_writer_fanout_completion_adopted(
        self,
        *,
        event: ZfEvent,
        manifest: dict[str, Any],
        child: dict[str, Any],
        adopted_from: str,
        reason: str,
    ) -> None:
        """Record that the active child adopted a completion with old identity."""

        fanout_id = str(manifest.get("fanout_id") or "")
        child_id = str(child.get("child_id") or "")
        try:
            already_recorded = any(
                item.type == "fanout.child.completion_adopted"
                and str((item.payload or {}).get("fanout_id") or "") == fanout_id
                and str((item.payload or {}).get("child_id") or "") == child_id
                and str((item.payload or {}).get("result_event_id") or "") == event.id
                for item in reversed(self.event_log.read_all())
                if isinstance(item.payload, dict)
            )
        except OSError:
            already_recorded = False
        if already_recorded:
            return
        self.event_writer.append(ZfEvent(
            type="fanout.child.completion_adopted",
            actor="zf-cli",
            task_id=event.task_id or str(child.get("task_id") or "") or None,
            payload={
                "fanout_id": fanout_id,
                "trace_id": str(manifest.get("trace_id") or ""),
                "stage_id": str(manifest.get("stage_id") or ""),
                "child_id": child_id,
                "task_id": str(event.task_id or child.get("task_id") or ""),
                "adopted_from": adopted_from,
                "result_event_id": event.id,
                "source_event_type": event.type,
                "reason": reason,
            },
            causation_id=event.id,
            correlation_id=event.correlation_id or manifest.get("trace_id", ""),
        ))

    def _reject_stale_writer_completion_for_operation(
        self,
        *,
        event: ZfEvent,
        payload: dict[str, Any],
        manifest: dict[str, Any],
        child: dict[str, Any] | None,
    ) -> bool:
        if not child or not self._writer_completion_operation_mismatch(payload, child):
            return False
        if self._task_ref_repair_operation_rebind_allowed(
            event=event,
            payload=payload,
            manifest=manifest,
            child=child,
        ):
            return False
        actual, expected = self._writer_completion_operation_ids(payload, child)
        self._emit_writer_fanout_stale_completion(
            event=event,
            payload=payload,
            manifest=manifest,
            child=child,
            expected_run_id=str(child.get("run_id") or ""),
            expected_operation_id=expected,
            actual_operation_id=actual,
        )
        return True

    def _task_ref_repair_operation_rebind_allowed(
        self,
        *,
        event: ZfEvent,
        payload: dict[str, Any],
        manifest: dict[str, Any],
        child: dict[str, Any],
    ) -> bool:
        """Allow only an exact Kernel-issued task-ref repair to rotate identity."""

        actual_operation_id, expected_operation_id = (
            self._writer_completion_operation_ids(payload, child)
        )
        dispatch_id = str(payload.get("dispatch_id") or "").strip()
        attempt_id = str(payload.get("attempt_id") or "").strip()
        fanout_id = str(manifest.get("fanout_id") or "").strip()
        child_id = str(child.get("child_id") or "").strip()
        task_id = str(event.task_id or payload.get("task_id") or "").strip()
        if not all((
            actual_operation_id,
            expected_operation_id,
            dispatch_id,
            attempt_id,
            fanout_id,
            child_id,
            task_id,
        )):
            return False
        try:
            events = self.event_log.read_all()
        except OSError:
            return False
        events_by_id = {item.id: item for item in events}
        actual_request_hash = str(payload.get("request_hash") or "").strip()
        for dispatched in reversed(events):
            if dispatched.type != "fanout.child.dispatched":
                continue
            body = dispatched.payload if isinstance(dispatched.payload, dict) else {}
            if (
                str(body.get("fanout_id") or "") != fanout_id
                or str(body.get("child_id") or "") != child_id
                or str(body.get("task_id") or dispatched.task_id or "") != task_id
                or str(body.get("source") or "") != "task_ref_repair"
                or str(body.get("operation_id") or "") != expected_operation_id
                or str(body.get("dispatch_id") or "") != dispatch_id
                or str(body.get("attempt_id") or "") != attempt_id
            ):
                continue
            operation_input = (
                body.get("payload") if isinstance(body.get("payload"), dict) else {}
            )
            if (
                str(operation_input.get("repair_of_operation_id") or "")
                != actual_operation_id
            ):
                continue
            repair_event_id = str(body.get("repair_of_event_id") or "").strip()
            repair = events_by_id.get(repair_event_id)
            if (
                repair is None
                or repair.type != "task.ref.repair.requested"
                or str(repair.task_id or "") != task_id
                or not isinstance(repair.payload, dict)
            ):
                continue
            source = events_by_id.get(
                str(repair.payload.get("source_event_id") or "").strip()
            )
            if (
                source is None
                or source.type != "dev.build.done"
                or str(source.task_id or "") != task_id
                or not isinstance(source.payload, dict)
                or str(source.payload.get("operation_id") or "")
                != actual_operation_id
            ):
                continue
            source_request_hash = str(
                source.payload.get("request_hash") or ""
            ).strip()
            if (
                actual_request_hash
                and source_request_hash
                and actual_request_hash != source_request_hash
            ):
                continue
            return True
        return False


__all__ = ["WriterFanoutRetryMixin", "fanout_operation_key"]
