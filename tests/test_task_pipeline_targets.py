from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.impl_self_check import (
    self_check_payload_fields,
    write_impl_self_check,
)
from zf.runtime.task_pipeline_runtime import (
    TaskPipelineRuntimeError,
    TaskPipelineWaiting,
)
from zf.runtime.task_pipeline_targets import (
    admit_impl_self_check,
    prepare_verify_target,
)


def _runtime(tmp_path: Path, entry: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        project_root=tmp_path,
        _task_ref_entry=lambda task_id: dict(entry),
    )


def _prepare(runtime: SimpleNamespace, tmp_path: Path) -> dict[str, object]:
    return prepare_verify_target(
        runtime,
        task_id="TASK-A",
        workflow_run_id="run-1",
        task_map_generation="map-g1",
        operation_generation=1,
        workspace=SimpleNamespace(
            project_path=tmp_path / "task-a",
            base_commit="a" * 40,
        ),
        contract_snapshot={
            "task_ref": "refs/zf/tasks/TASK-A",
            "contract_revision": "contract-r1",
        },
        contract_descriptor={
            "ref": "artifacts/contracts/TASK-A.json",
            "sha256": "b" * 64,
        },
    )


def test_verify_target_waits_for_an_admitted_exact_task_ref(
    tmp_path: Path,
) -> None:
    with pytest.raises(TaskPipelineWaiting) as raised:
        _prepare(_runtime(tmp_path, {}), tmp_path)

    assert raised.value.reason == "waiting_for_task_ref"


def test_verify_target_rejects_cross_task_workspace_binding(
    tmp_path: Path,
) -> None:
    entry = {
        "source_commit": "a" * 40,
        "workdir": str(tmp_path / "task-b"),
        "task_ref": "refs/zf/tasks/TASK-A",
    }

    with pytest.raises(
        TaskPipelineRuntimeError,
        match="TaskRef workdir does not match",
    ):
        _prepare(_runtime(tmp_path, entry), tmp_path)


def test_self_check_rework_same_commit_persists_new_generation(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    runtime = SimpleNamespace(
        state_dir=tmp_path,
        event_log=log,
        event_writer=writer,
    )
    contract = {
        "task_id": "TASK-A",
        "workflow_run_id": "run-1",
        "contract_revision": "contract-r1",
        "task_map_generation": "map-g1",
        "verification_commands": [],
        "acceptance_criteria": [{
            "acceptance_id": "AC-1",
            "mandatory": True,
            "verification_command_ids": [],
        }],
    }
    target = {
        "target_commit": "c" * 40,
        "contract_snapshot_ref": "artifacts/contracts/TASK-A.json",
        "contract_snapshot_digest": "d" * 64,
    }

    def self_check(attempt_id: str, evidence_ref: str) -> dict[str, object]:
        return {
            "schema_version": "impl-self-check.v1",
            "workflow_run_id": "run-1",
            "task_id": "TASK-A",
            "attempt_id": attempt_id,
            "contract_revision": "contract-r1",
            "task_map_generation": "map-g1",
            "source_commit": "c" * 40,
            "target_commit": "c" * 40,
            "contract_snapshot_ref": "artifacts/contracts/TASK-A.json",
            "contract_snapshot_digest": "d" * 64,
            "command_receipts": [],
            "acceptance_results": [{
                "acceptance_id": "AC-1",
                "status": "passed",
                "command_receipt_ids": [],
                "evidence_refs": [evidence_ref],
                "residual_risks": [],
            }],
            "residual_risks": [],
            "evidence_refs": [evidence_ref],
        }

    old_descriptor = write_impl_self_check(
        tmp_path,
        self_check("attempt-1", "artifacts/evidence-old.json"),
        source_event_id="evt-build-1",
        created_by="dev-1",
    )
    writer.append(ZfEvent(
        type="impl.self_check.completed",
        actor="orchestrator",
        task_id="TASK-A",
        correlation_id="run-1",
        payload={
            **self_check_payload_fields(old_descriptor),
            "workflow_run_id": "run-1",
            "task_map_generation": "map-g1",
            "operation_generation": 1,
            "target_commit": "c" * 40,
            "attempt_id": "attempt-1",
        },
    ))
    writer.append(ZfEvent(
        type="dev.build.done",
        actor="dev-1",
        task_id="TASK-A",
        correlation_id="run-1",
        payload={
            "workflow_run_id": "run-1",
            "task_map_generation": "map-g1",
            "operation_generation": 2,
            "source_commit": "c" * 40,
            "attempt_id": "attempt-2",
            "impl_self_check": self_check(
                "attempt-2",
                "artifacts/evidence-new.json",
            ),
        },
    ))

    descriptor = admit_impl_self_check(
        runtime,
        task_id="TASK-A",
        workflow_run_id="run-1",
        task_map_generation="map-g1",
        operation_generation=2,
        source_commit="c" * 40,
        contract_snapshot=contract,
        target_snapshot=target,
    )

    body = json.loads((tmp_path / descriptor["ref"]).read_text(encoding="utf-8"))
    assert descriptor["ref"] != old_descriptor["ref"]
    assert body["attempt_id"] == "attempt-2"
    assert body["evidence_refs"] == ["artifacts/evidence-new.json"]
    completed = [
        event for event in log.read_all()
        if event.type == "impl.self_check.completed"
    ]
    assert [event.payload["operation_generation"] for event in completed] == [1, 2]
