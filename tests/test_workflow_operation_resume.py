from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import ProjectConfig, WorkflowConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    reduce_workflow_operations,
)
from zf.runtime.workflow_resume import build_workflow_resume_projection
from zf.runtime.workflow_resume_apply import apply_workflow_resume


def _config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="operation-resume"),
        workflow=WorkflowConfig(),
    )


def test_interrupted_operation_dry_run_then_cancel_is_idempotent(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    service = WorkflowOperationService(
        state_dir=state_dir,
        event_log=log,
        event_writer=EventWriter(log),
    )
    ensured = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-1",
        operation_type="agent",
        request={"prompt": "plan"},
        task_id="FLOW-1",
        parent_task_id="FLOW-1",
    )
    service.mark_started(
        operation_id="op-1",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id="FLOW-1",
        dispatch_id="dispatch-1",
    )
    service.interrupt(
        operation_id="op-1",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id="FLOW-1",
        reason="graceful_stop",
    )

    projection = build_workflow_resume_projection(state_dir, _config())
    checkpoint = projection["operation_checkpoints"][0]
    assert projection["summary"]["resumable_operations"] == 1
    assert projection["summary"]["operation_pending"] == 1
    assert checkpoint["safe_resume_action"] == "cancel_interrupted_operation"
    dry_run = apply_workflow_resume(
        state_dir,
        _config(),
        dry_run=True,
        checkpoint_id=checkpoint["checkpoint_id"],
    )
    assert dry_run["applied"] == 0
    assert dry_run["operation_results"][0]["reason"] == "dry run"

    applied = apply_workflow_resume(
        state_dir,
        _config(),
        checkpoint_id=checkpoint["checkpoint_id"],
    )
    assert applied["applied"] == 1
    assert reduce_workflow_operations(log.read_all())["op-1"]["status"] == "cancelled"
    second = apply_workflow_resume(state_dir, _config())
    assert second["applied"] == 0
    assert second["projection"]["summary"]["resumable_operations"] == 0
