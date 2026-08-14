from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract
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


def test_verify_only_target_admits_exact_ref_without_impl_self_check(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "task-a"
    workspace.mkdir()
    target = "c" * 40
    task = Task(
        id="TASK-A",
        contract=TaskContract(evidence_contract={"runtime_only": True}),
    )
    context = {
        "workflow_run_id": "run-1",
        "task_map_generation": "map-g1",
        "dispatch_base_commit": target,
        "generation_admitted_event_id": "evt-generation",
    }
    runtime = SimpleNamespace(state_dir=tmp_path, project_root=tmp_path)
    entry = {
        "source_commit": target,
        "workdir": str(workspace),
        "task_ref": "refs/zf/tasks/TASK-A",
        "trigger_event_id": "evt-generation",
    }
    with (
        patch(
            "zf.runtime.task_pipeline_entry.task_pipeline_entry_target",
            return_value=target,
        ),
        patch(
            "zf.runtime.task_pipeline_entry.admit_task_pipeline_read_only_ref",
            return_value=entry,
        ) as admit,
        patch("zf.runtime.task_pipeline_runtime._git", return_value=target),
    ):
        fields = prepare_verify_target(
            runtime,
            task_id=task.id,
            workflow_run_id="run-1",
            task_map_generation="map-g1",
            operation_generation=1,
            workspace=SimpleNamespace(
                project_path=workspace,
                base_commit=target,
            ),
            task=task,
            generation_context=context,
            entry_mode="verify_only",
            contract_snapshot={
                "workflow_run_id": "run-1",
                "task_id": task.id,
                "contract_revision": "contract-r1",
                "task_map_generation": "map-g1",
                "base_commit": target,
                "task_ref": "refs/zf/tasks/TASK-A",
            },
            contract_descriptor={
                "ref": "artifacts/contracts/TASK-A.json",
                "sha256": "b" * 64,
            },
        )

    assert fields["target_commit"] == target
    assert "impl_self_check_ref" not in fields
    assert admit.call_args.kwargs["workdir"] == str(workspace.resolve())


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


def test_self_check_same_operation_does_not_reuse_prior_task_map_generation(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    runtime = SimpleNamespace(
        state_dir=tmp_path,
        event_log=log,
        event_writer=writer,
    )
    target = {
        "target_commit": "c" * 40,
        "contract_snapshot_ref": "artifacts/contracts/TASK-A-g2.json",
        "contract_snapshot_digest": "e" * 64,
    }
    contract = {
        "task_id": "TASK-A",
        "workflow_run_id": "run-1",
        "contract_revision": "contract-r2",
        "task_map_generation": "map-g2",
        "verification_commands": [],
        "acceptance_criteria": [{
            "acceptance_id": "AC-2",
            "mandatory": True,
            "verification_command_ids": [],
        }],
    }
    old_body = {
        "schema_version": "impl-self-check.v1",
        "workflow_run_id": "run-1",
        "task_id": "TASK-A",
        "attempt_id": "attempt-old",
        "contract_revision": "contract-r1",
        "task_map_generation": "map-g1",
        "source_commit": "c" * 40,
        "target_commit": "c" * 40,
        "contract_snapshot_ref": "artifacts/contracts/TASK-A-g1.json",
        "contract_snapshot_digest": "d" * 64,
        "command_receipts": [],
        "acceptance_results": [{
            "acceptance_id": "AC-1",
            "status": "passed",
            "command_receipt_ids": [],
            "evidence_refs": ["artifacts/evidence-old.json"],
            "residual_risks": [],
        }],
        "residual_risks": [],
        "evidence_refs": ["artifacts/evidence-old.json"],
    }
    old_descriptor = write_impl_self_check(
        tmp_path,
        old_body,
        source_event_id="evt-build-old",
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
            "attempt_id": "attempt-old",
        },
    ))
    new_body = {
        **old_body,
        "attempt_id": "attempt-new",
        "contract_revision": "contract-r2",
        "task_map_generation": "map-g2",
        "contract_snapshot_ref": "artifacts/contracts/TASK-A-g2.json",
        "contract_snapshot_digest": "e" * 64,
        "acceptance_results": [{
            "acceptance_id": "AC-2",
            "status": "passed",
            "command_receipt_ids": [],
            "evidence_refs": ["artifacts/evidence-new.json"],
            "residual_risks": [],
        }],
        "evidence_refs": ["artifacts/evidence-new.json"],
    }
    writer.append(ZfEvent(
        type="dev.build.done",
        actor="dev-1",
        task_id="TASK-A",
        correlation_id="run-1",
        payload={
            "workflow_run_id": "run-1",
            "task_map_generation": "map-g2",
            "operation_generation": 1,
            "source_commit": "c" * 40,
            "attempt_id": "attempt-new",
            "impl_self_check": new_body,
        },
    ))

    descriptor = admit_impl_self_check(
        runtime,
        task_id="TASK-A",
        workflow_run_id="run-1",
        task_map_generation="map-g2",
        operation_generation=1,
        source_commit="c" * 40,
        contract_snapshot=contract,
        target_snapshot=target,
    )

    body = json.loads((tmp_path / descriptor["ref"]).read_text(encoding="utf-8"))
    assert descriptor["ref"] != old_descriptor["ref"]
    assert body["task_map_generation"] == "map-g2"
    assert body["contract_revision"] == "contract-r2"
    assert body["attempt_id"] == "attempt-new"
