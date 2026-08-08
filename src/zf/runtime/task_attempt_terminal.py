"""TaskAttempt terminal classification and active-dispatch cleanup."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent


SUCCESS_EVENTS = frozenset({
    "arch.proposal.done",
    "design.critique.done",
    "dev.build.done",
    "fanout.synth.completed",
    "review.approved",
    "verify.passed",
    "test.passed",
    "judge.passed",
    "task.done.evidence",
    "worker.completed",
    "task.pipeline.verify.completed",
    "task.pipeline.acceptance.completed",
})
FAILURE_EVENTS = frozenset({
    "dev.failed",
    "dev.blocked",
    "review.rejected",
    "review.suspended",
    "verify.failed",
    "test.failed",
    "test.suspended",
    "judge.failed",
    "task.pipeline.verify.failed",
    "task.pipeline.acceptance.failed",
})


def result_status(event_type: str) -> str:
    if event_type in SUCCESS_EVENTS:
        return "succeeded"
    if event_type in FAILURE_EVENTS:
        return "failed"
    return ""


def admitted_result_status(event_type: str) -> str:
    terminal = result_status(event_type)
    if terminal:
        return terminal
    if event_type.endswith(".child.completed"):
        return "succeeded"
    if event_type.endswith(".child.failed"):
        return "failed"
    return ""


def clear_matching_active_dispatch(runtime: Any, event: ZfEvent) -> None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    task_id = str(event.task_id or payload.get("task_id") or "").strip()
    dispatch_id = str(payload.get("dispatch_id") or "").strip()
    if not task_id or not dispatch_id:
        return
    try:
        task = runtime.task_store.get(task_id)
    except Exception:
        task = None
    if task is not None and str(task.active_dispatch_id or "") == dispatch_id:
        runtime.task_store.update(task_id, active_dispatch_id="")
    active = getattr(runtime, "_active_dispatch_ids", {})
    if active.get(task_id) == dispatch_id:
        active.pop(task_id, None)


__all__ = [
    "admitted_result_status",
    "clear_matching_active_dispatch",
    "result_status",
]
