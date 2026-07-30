"""Ordered periodic housekeeping groups for the kernel Orchestrator."""

from __future__ import annotations

from typing import Any, Iterable

from zf.core.events.model import ZfEvent


def run_replay_sweep(
    runtime: Any,
    *,
    events: Iterable[ZfEvent] | None,
) -> None:
    event_batch = list(events or [])
    if not event_batch or any(
        event.type in {
            "task_map.ready",
            "product.delivery.wave.ready",
            "candidate.ready",
        }
        for event in event_batch
    ):
        runtime._safe_housekeeping(
            "writer_fanout_task_bindings",
            runtime._recover_writer_fanout_task_bindings,
        )
    if event_batch:
        return
    runtime._safe_housekeeping(
        "writer_fanout_result_replay",
        runtime._recover_unrecorded_writer_fanout_results,
    )
    runtime._safe_housekeeping(
        "reader_fanout_trigger_replay",
        runtime._reconcile_reader_fanout_triggers,
    )
    runtime._safe_housekeeping(
        "reader_fanout_result_replay",
        lambda: _recover_reader_results_and_goal(runtime),
    )


def _recover_reader_results_and_goal(runtime: Any) -> None:
    runtime._recover_unrecorded_reader_fanout_results()
    runtime._reconcile_run_goal_completion()


def run_context_sweep(runtime: Any) -> None:
    runtime._safe_housekeeping(
        "context_thresholds",
        runtime._check_context_thresholds,
    )
    runtime._safe_housekeeping(
        "pending_recycles",
        runtime._check_pending_recycles,
    )


def run_post_dispatch_sweep(
    runtime: Any,
    *,
    events: Iterable[ZfEvent] | None,
    periodic_sweep: bool,
) -> None:
    if periodic_sweep:
        runtime._safe_housekeeping("orphaned_tasks", runtime._check_orphaned_tasks)
        runtime._safe_housekeeping(
            "unclaimed_new_tasks",
            runtime._check_unclaimed_new_tasks,
        )
        runtime._safe_housekeeping(
            "channel_reply_remediation",
            runtime._check_channel_reply_remediation,
        )
        runtime._safe_housekeeping("drift", runtime._check_drift)
        runtime._safe_housekeeping("refresh", runtime._check_refresh_triggers)
    if periodic_sweep or any(
        event.type == "candidate.ready" for event in (events or [])
    ):
        runtime._safe_housekeeping(
            "fanout_timeouts",
            runtime._check_fanout_timeouts,
        )
