from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.task_pipeline_rework import (
    derive_impl_rework_requests,
    impl_rework_feedback,
)


def _operation() -> dict:
    return {
        "operation_id": "op-impl-g1",
        "task_id": "TASK-A",
        "workflow_run_id": "run-1",
        "task_map_generation": "map-g1",
        "task_pipeline_stage": "impl",
        "operation_generation": 1,
        "status": "settled",
        "semantic_verdict": "passed",
        "call_result_admitted_event_id": "evt-admitted",
    }


def _result() -> ZfEvent:
    return ZfEvent(
        id="evt-result",
        type="dev.build.done",
        task_id="TASK-A",
        payload={
            "workflow_run_id": "run-1",
            "task_map_generation": "map-g1",
            "task_pipeline_stage": "impl",
            "operation_id": "op-impl-g1",
            "operation_generation": 1,
            "source_commit": "a" * 40,
        },
    )


def test_typed_impl_task_ref_rejection_projects_semantic_rework() -> None:
    result = _result()
    rejected = ZfEvent(
        id="evt-rejected",
        type="task.ref.rejected",
        task_id="TASK-A",
        payload={
            "trigger_event_id": result.id,
            "reason": "source_commit changes outside task contract scope",
            "changed_files": ["app/server.mjs", "app/server.test.mjs"],
            "out_of_scope_files": ["app/server.test.mjs"],
        },
    )

    requests = derive_impl_rework_requests(
        events=[result, rejected],
        generation_contexts={
            "TASK-A": {
                "workflow_run_id": "run-1",
                "task_map_generation": "map-g1",
            }
        },
        operation_rows=[_operation()],
    )

    request = requests["TASK-A"]
    assert request["operation_generation"] == 1
    assert request["operation_id"] == "op-impl-g1"
    assert request["expected_action"] == (
        "repair_source_scope_and_resubmit_typed_impl_result"
    )
    assert request["out_of_scope_files"] == ["app/server.test.mjs"]
    feedback = impl_rework_feedback(request)
    assert feedback[0]["blocking_event_id"] == rejected.id
    assert feedback[0]["severity"] == "blocking"


def test_task_ref_update_resolves_rejection_and_legacy_result_is_ignored() -> None:
    typed = _result()
    rejected = ZfEvent(
        id="evt-rejected",
        type="task.ref.rejected",
        task_id="TASK-A",
        payload={"trigger_event_id": typed.id, "reason": "missing handoff"},
    )
    updated = ZfEvent(
        id="evt-updated",
        type="task.ref.updated",
        task_id="TASK-A",
        payload={"trigger_event_id": typed.id},
    )
    legacy = ZfEvent(
        id="evt-legacy-result",
        type="dev.build.done",
        task_id="TASK-B",
        payload={"source_commit": "b" * 40},
    )
    legacy_rejected = ZfEvent(
        id="evt-legacy-rejected",
        type="task.ref.rejected",
        task_id="TASK-B",
        payload={"trigger_event_id": legacy.id, "reason": "missing handoff"},
    )

    requests = derive_impl_rework_requests(
        events=[typed, rejected, updated, legacy, legacy_rejected],
        generation_contexts={
            "TASK-A": {
                "workflow_run_id": "run-1",
                "task_map_generation": "map-g1",
            },
            "TASK-B": {
                "workflow_run_id": "run-1",
                "task_map_generation": "map-g1",
            },
        },
        operation_rows=[_operation()],
    )

    assert requests == {}


def test_stale_contract_rejection_does_not_reopen_impl_generation() -> None:
    result = _result()
    rejected = ZfEvent(
        id="evt-stale-rejected",
        type="task.ref.rejected",
        task_id="TASK-A",
        payload={
            "trigger_event_id": result.id,
            "rejection_kind": "stale_contract_result",
            "reason": "stale task contract revision",
        },
    )

    requests = derive_impl_rework_requests(
        events=[result, rejected],
        generation_contexts={
            "TASK-A": {
                "workflow_run_id": "run-1",
                "task_map_generation": "map-g1",
            }
        },
        operation_rows=[_operation()],
    )

    assert requests == {}
