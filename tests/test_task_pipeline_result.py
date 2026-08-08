from __future__ import annotations

from types import SimpleNamespace

from zf.core.events.model import ZfEvent
from zf.runtime.task_pipeline_result import (
    is_admitted_task_pipeline_stage_result,
)


class _EventLog:
    def __init__(self, events: list[ZfEvent]) -> None:
        self._events = events

    def read_all(self) -> list[ZfEvent]:
        return list(self._events)


def _runtime(events: list[ZfEvent]) -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(
            workflow=SimpleNamespace(
                flow_metadata={"task_pipeline": {"mode": "blocking"}},
                flow_metadata_by_kind={},
            )
        ),
        event_log=_EventLog(events),
    )


def _operation_events() -> list[ZfEvent]:
    identity = {
        "workflow_run_id": "run-1",
        "operation_id": "op-1",
        "request_hash": "request-sha",
        "task_pipeline_stage": "impl",
        "operation_generation": 1,
        "task_map_generation": "map-g1",
    }
    return [
        ZfEvent(
            type="task.pipeline.generation.admitted",
            task_id=None,
            payload={
                "schema_version": "task-pipeline-generation.v1",
                "workflow_run_id": "run-1",
                "task_map_generation": "map-g1",
                "task_ids": ["T1"],
            },
        ),
        ZfEvent(
            type="workflow.operation.requested",
            task_id="T1",
            payload={**identity, "role_instance": "impl-1"},
        ),
        ZfEvent(
            type="workflow.call.result.admitted",
            task_id="T1",
            payload={
                **identity,
                "semantic_verdict": "passed",
                "control_result_ref": {
                    "ref": "call-results/control/impl.json",
                    "sha256": "control-sha",
                },
            },
        ),
        ZfEvent(
            type="workflow.operation.settled",
            task_id="T1",
            payload={
                **identity,
                "admitted_call_result_ref": {
                    "ref": "call-results/envelopes/impl.json",
                    "sha256": "envelope-sha",
                },
            },
        ),
    ]


def _result_event(**payload_overrides: object) -> ZfEvent:
    payload = {
        "workflow_run_id": "run-1",
        "operation_id": "op-1",
        "task_pipeline_stage": "impl",
        "operation_generation": 1,
        "task_map_generation": "map-g1",
        "control_result_ref": {
            "ref": "call-results/control/impl.json",
            "sha256": "control-sha",
        },
        **payload_overrides,
    }
    return ZfEvent(type="dev.build.done", task_id="T1", payload=payload)


def test_only_admitted_current_operation_result_bypasses_legacy_routing() -> None:
    runtime = _runtime(_operation_events())

    assert is_admitted_task_pipeline_stage_result(runtime, _result_event())
    assert not is_admitted_task_pipeline_stage_result(
        runtime,
        _result_event(operation_generation=2),
    )


def test_payload_marker_without_controlled_result_ref_is_not_authority() -> None:
    runtime = _runtime(_operation_events())

    assert not is_admitted_task_pipeline_stage_result(
        runtime,
        _result_event(control_result_ref={}),
    )
