"""Mechanical dispatch fences compiled from admitted OA checkpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zf.core.events.model import ZfEvent


@dataclass(frozen=True)
class PreImplDispatchFence:
    blocked: bool
    failure_reason: str = ""


def stage_barrier_blocks_dispatch(runtime: Any, event: ZfEvent) -> bool:
    from zf.runtime.orchestrator_agent_aggregation import (
        stage_barrier_checkpoint_state,
    )

    state = stage_barrier_checkpoint_state(runtime, event)
    if not (state.enabled and state.blocking and not state.satisfied):
        return False
    from zf.runtime.run_admission import record_run_dispatch_blocked

    payload = event.payload if isinstance(event.payload, dict) else {}
    record_run_dispatch_blocked(
        runtime,
        event=event,
        run_id=str(
            payload.get("workflow_run_id")
            or payload.get("run_id")
            or payload.get("trace_id")
            or event.correlation_id
            or ""
        ),
        reason="orchestrator_stage_barrier_pending",
    )
    return True


def pre_impl_dispatch_fence(
    runtime: Any,
    *,
    stage_id: str,
    trigger_event: ZfEvent,
    loaded: Any,
    trace_id: str,
) -> PreImplDispatchFence:
    from zf.runtime.orchestrator_agent_run_plan import (
        pre_impl_checkpoint_state,
    )

    try:
        state = pre_impl_checkpoint_state(
            runtime,
            stage_id=stage_id,
            trigger_event=trigger_event,
            loaded=loaded,
            trace_id=trace_id,
        )
    except Exception as exc:
        return PreImplDispatchFence(
            blocked=False,
            failure_reason=f"pre_impl_checkpoint_failed:{exc}",
        )
    if not (state.enabled and state.blocking and not state.satisfied):
        return PreImplDispatchFence(blocked=False)
    from zf.runtime.run_admission import record_run_dispatch_blocked

    record_run_dispatch_blocked(
        runtime,
        event=trigger_event,
        run_id=str(loaded.workflow_run_id or trace_id),
        reason="orchestrator_pre_impl_pending",
    )
    return PreImplDispatchFence(blocked=True)


__all__ = [
    "PreImplDispatchFence",
    "pre_impl_dispatch_fence",
    "stage_barrier_blocks_dispatch",
]
