from __future__ import annotations

from types import SimpleNamespace

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.task_pipeline_rework import (
    derive_impl_rework_requests,
    impl_rework_feedback,
    reconcile_task_ref_repair_replays,
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


def test_task_ref_repair_replays_existing_typed_result_once(tmp_path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    result = _result()
    rejected = ZfEvent(
        id="evt-rejected",
        type="task.ref.rejected",
        task_id="TASK-A",
        payload={
            "trigger_event_id": result.id,
            "reason": "source_commit changes outside task contract scope",
        },
    )
    repair = ZfEvent(
        id="evt-repair",
        type="task.ref.repair.requested",
        task_id="TASK-A",
        payload={"source_event_id": result.id},
    )
    for event in (result, rejected, repair):
        log.append(event)
    calls: list[str] = []

    def process(source: ZfEvent):
        calls.append(source.id)
        return SimpleNamespace(
            status="updated",
            payload={
                "task_id": source.task_id,
                "trigger_event_id": source.id,
                "source_commit": source.payload["source_commit"],
            },
        )

    runtime = SimpleNamespace(
        event_log=log,
        event_writer=writer,
        _process_task_ref_for_progress_event=process,
    )
    contexts = {
        "TASK-A": {
            "workflow_run_id": "run-1",
            "task_map_generation": "map-g1",
        }
    }

    first = reconcile_task_ref_repair_replays(
        runtime,
        events=log.read_all(),
        generation_contexts=contexts,
    )
    second = reconcile_task_ref_repair_replays(
        runtime,
        events=log.read_all(),
        generation_contexts=contexts,
    )

    assert calls == [result.id]
    assert len(first) == 1
    assert second == []
    updated = first[0]
    assert updated.type == "task.ref.updated"
    assert updated.causation_id == repair.id
    assert updated.payload["trigger_event_id"] == result.id
    assert updated.payload["source"] == "task_ref_repair_reconcile"


def test_task_ref_repair_replay_keeps_semantic_rework_when_still_rejected(
    tmp_path,
) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    result = _result()
    rejected = ZfEvent(
        id="evt-rejected",
        type="task.ref.rejected",
        task_id="TASK-A",
        payload={"trigger_event_id": result.id, "reason": "missing handoff"},
    )
    repair = ZfEvent(
        id="evt-repair",
        type="task.ref.repair.requested",
        task_id="TASK-A",
        payload={"source_event_id": result.id},
    )
    for event in (result, rejected, repair):
        log.append(event)
    calls: list[str] = []

    def process(source: ZfEvent):
        calls.append(source.id)
        return SimpleNamespace(status="rejected", payload={"reason": "missing handoff"})

    runtime = SimpleNamespace(
        event_log=log,
        event_writer=writer,
        _process_task_ref_for_progress_event=process,
    )
    emitted = reconcile_task_ref_repair_replays(
        runtime,
        events=log.read_all(),
        generation_contexts={
            "TASK-A": {
                "workflow_run_id": "run-1",
                "task_map_generation": "map-g1",
            }
        },
    )

    assert calls == [result.id]
    assert emitted == []
    assert derive_impl_rework_requests(
        events=log.read_all(),
        generation_contexts={
            "TASK-A": {
                "workflow_run_id": "run-1",
                "task_map_generation": "map-g1",
            }
        },
        operation_rows=[_operation()],
    )["TASK-A"]["event_id"] == rejected.id
