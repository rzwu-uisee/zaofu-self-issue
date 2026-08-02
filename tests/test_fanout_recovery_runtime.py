from __future__ import annotations

import json
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.fanout_recovery import find_unrecorded_writer_fanout_results
from zf.runtime.run_manager import RUN_MANAGER_ACTION_APPLIED, run_manager_tick

from tests.test_writer_fanout_runtime import (
    _child,
    _commit,
    _fanout_id,
    _manifest,
    _seed_tasks,
    _start,
    _state,
)


def test_run_manager_recovers_unrecorded_writer_fanout_terminal(tmp_path):
    state_dir, log, transport, orch = _state(
        tmp_path,
        affinity_stage_slots=True,
    )
    task_map = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    task_map.write_text(json.dumps({
        "tasks": [
            {
                "task_id": "TASK-1",
                "scope": "pi-core",
                "affinity_tag": "pi-core",
                "allowed_paths": ["a.txt"],
            },
            {
                "task_id": "TASK-2",
                "scope": "gateway",
                "affinity_tag": "gateway",
                "allowed_paths": ["b.txt"],
            },
            {
                "task_id": "TASK-3",
                "scope": "web-tui",
                "affinity_tag": "web-tui",
                "allowed_paths": ["c.txt"],
            },
        ],
    }), encoding="utf-8")
    _seed_tasks(state_dir, task_ids=("TASK-1", "TASK-2", "TASK-3"))
    _start(orch)

    fanout_id = _fanout_id(log)
    manifest = _manifest(state_dir, fanout_id)
    task2 = _child(manifest, "TASK-2")
    failed = ZfEvent(
        id="dev-failed-without-watcher",
        type="dev.failed",
        actor=task2["role_instance"],
        task_id="TASK-2",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": task2["child_id"],
            "run_id": task2["run_id"],
            "pdd_id": "F-11111111",
            "status": "failed",
            "reason": "root package.json is assembly-owned",
        },
    )
    log.append(failed)
    assert find_unrecorded_writer_fanout_results(
        state_dir=state_dir,
        events=log.read_all(),
    )

    result = run_manager_tick(
        state_dir=state_dir,
        writer=EventWriter(log),
        config=orch.config,
        project_root=tmp_path,
        event_log=log,
        transport=transport,
        spawn_repairs=False,
    )

    events = log.read_all()
    assert result.actions_applied >= 1
    assert any(
        event.type == "fanout.child.failed"
        and event.payload.get("child_id") == task2["child_id"]
        for event in events
    )
    assert any(
        event.type == RUN_MANAGER_ACTION_APPLIED
        and event.payload.get("action") == "fanout-terminal-recover"
        for event in events
    )
    final_manifest = _manifest(state_dir, fanout_id)
    task3 = _child(final_manifest, "TASK-3")
    assert task3["status"] == "dispatched"
    assert [sent[0] for sent in transport.sent] == ["dev-1", "dev-2", "dev-2"]


def test_handoff_identity_failure_reopens_result_for_one_recovery_attempt(
    tmp_path,
):
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    child = _child(_manifest(state_dir, fanout_id), "TASK-1")
    source_commit = _commit(
        Path(child["workdir"]),
        "a.txt",
        "delivered\n",
        "deliver TASK-1",
    )
    result = EventWriter(log).append(ZfEvent(
        type="dev.build.done",
        actor=child["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": child["child_id"],
            "run_id": child["run_id"],
            "source_commit": source_commit,
            "source_branch": child["source_branch"],
            "workdir": child["workdir"],
        },
    ))
    task_ref = orch._process_task_ref_for_progress_event(  # type: ignore[attr-defined]
        result
    )
    assert task_ref is not None and task_ref.status == "updated"
    log.append(ZfEvent(
        type="task.ref.updated",
        actor="zf-cli",
        task_id="TASK-1",
        payload=task_ref.payload,
        causation_id=result.id,
        correlation_id=result.correlation_id,
    ))
    failure_payload = {
        "fanout_id": fanout_id,
        "child_id": child["child_id"],
        "run_id": child["run_id"],
        "task_id": "TASK-1",
        "failure_class": "verifier_contract_failure",
        "reason": (
            "writer contract handoff snapshot failed: "
            "adopted writer completion lacks dispatch base commit for TASK-1"
        ),
    }
    EventWriter(log).append(ZfEvent(
        type="fanout.child.failed",
        actor="zf-cli",
        correlation_id="trace-1",
        causation_id=result.id,
        payload=failure_payload,
    ))

    candidates = find_unrecorded_writer_fanout_results(
        state_dir=state_dir,
        events=log.read_all(),
    )

    assert [item.result_event_id for item in candidates] == [result.id]

    orch._recover_unrecorded_writer_fanout_results()  # type: ignore[attr-defined]

    recovered = [
        event for event in log.read_all()
        if event.type == "fanout.child.completed"
        and event.causation_id == result.id
    ]
    assert len(recovered) == 1
    assert recovered[0].payload["recovered_from_status"] == "failed"
    assert _child(_manifest(state_dir, fanout_id), "TASK-1")["status"] == "completed"

    assert not find_unrecorded_writer_fanout_results(
        state_dir=state_dir,
        events=log.read_all(),
    )
