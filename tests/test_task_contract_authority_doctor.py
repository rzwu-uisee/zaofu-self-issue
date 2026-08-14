from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract, TaskExecutionBinding
from zf.core.task.store import TaskStore
from zf.runtime.task_contract_authority import TaskContractAuthorityService
from zf.runtime.task_contract_authority_doctor import (
    build_task_contract_authority_report,
)


def _runtime(tmp_path: Path):  # noqa: ANN202
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    log = EventLog(state_dir / "events.jsonl")
    service = TaskContractAuthorityService(
        task_store=store,
        event_writer=EventWriter(log),
        state_dir=state_dir,
    )
    return state_dir, store, log, service


def test_contract_authority_doctor_reports_consistent_receipt(tmp_path: Path) -> None:
    state_dir, store, log, service = _runtime(tmp_path)
    store.add(Task(
        id="TASK-CLEAN",
        title="Clean",
        contract=TaskContract(behavior="R1", verification="pytest"),
    ))
    task = store.get("TASK-CLEAN")
    assert task is not None
    service.replace(
        task,
        contract=task.contract,
        execution_binding=TaskExecutionBinding(
            owner="workflow",
            request_id="request-1",
            request_revision=1,
            workflow_run_id="run-1",
            origin_binding_digest="binding-1",
            origin_task_digest="task-1",
        ),
        source="workflow_submit",
    )

    report = build_task_contract_authority_report(state_dir, log.read_all())

    assert report["ok"] is True
    assert report["stamped_task_count"] == 1
    assert report["issue_count"] == 0


def test_contract_authority_doctor_finds_prepared_store_and_binding_drift(
    tmp_path: Path,
) -> None:
    state_dir, store, log, service = _runtime(tmp_path)
    store.add(Task(
        id="TASK-DRIFT",
        title="Drift",
        contract=TaskContract(behavior="R1", verification="pytest"),
    ))
    task = store.get("TASK-DRIFT")
    assert task is not None
    applied = service.replace(
        task,
        contract=task.contract,
        execution_binding=TaskExecutionBinding(
            owner="workflow",
            request_id="request-1",
            request_revision=1,
            workflow_run_id="run-1",
        ),
        source="workflow_submit",
    )
    raw = store._load_raw()  # deterministic corruption fixture
    raw[0]["contract"]["behavior"] = "bypassed service"
    raw[0]["contract"]["evidence_contract"]["execution_owner"] = "legacy"
    raw[0]["contract_authority_revision"] = "authority-unreceipted"
    store._save_raw(raw)
    log.append(ZfEvent(
        type="task.contract.mutation.prepared",
        task_id="TASK-DRIFT",
        payload={
            "contract_authority_revision": "authority-orphan",
            "contract_mutation_ref": applied.receipt_ref,
            "contract_mutation_digest": applied.receipt_digest,
        },
    ))

    report = build_task_contract_authority_report(state_dir, log.read_all())
    codes = {issue["code"] for issue in report["issues"]}

    assert report["ok"] is False
    assert "prepared_without_terminal_receipt" in codes
    assert "store_authority_revision_mismatch" in codes
    assert "store_authority_receipt_missing" in codes
    assert "execution_binding_drift" in codes
