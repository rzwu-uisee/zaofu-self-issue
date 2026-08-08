from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.artifact_read_ledger import (
    active_read_ledger_path,
    build_attempt_source_manifest,
    live_attempt_ids,
    read_attempt_artifact,
)
from zf.runtime.task_attempt_runtime import reconcile_task_attempts


def _runtime(tmp_path: Path) -> SimpleNamespace:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    return SimpleNamespace(
        state_dir=state_dir,
        event_log=log,
        event_writer=EventWriter(log),
        config=SimpleNamespace(
            workflow=SimpleNamespace(
                task_attempt=SimpleNamespace(mode="enforce", max_attempts=3),
                attempt_lease_grace_s=900,
            ),
        ),
    )


@pytest.mark.parametrize(
    ("event_type", "expected_status", "occurrence_type"),
    [
        ("workflow.operation.failed", "failed", "task.attempt.failed"),
        ("workflow.operation.blocked", "failed", "task.attempt.failed"),
        (
            "workflow.operation.superseded",
            "superseded",
            "task.attempt.superseded",
        ),
        (
            "workflow.operation.cancelled",
            "superseded",
            "task.attempt.superseded",
        ),
    ],
)
def test_terminal_operation_closes_attempt_and_active_read_ledger(
    tmp_path: Path,
    event_type: str,
    expected_status: str,
    occurrence_type: str,
) -> None:
    runtime = _runtime(tmp_path)
    operation_id = f"op-{event_type.rsplit('.', 1)[-1]}"
    attempt_id = f"attempt-{event_type.rsplit('.', 1)[-1]}"
    store = TaskAttemptStore(runtime.state_dir / "task_attempts.json")
    attempt = store.ensure_for_dispatch(
        run_id="run-1",
        task_id="T1",
        dispatch_id=attempt_id,
        role="dev",
        instance_id="dev-1",
        operation_id=operation_id,
        briefing_ref="briefings/T1.md",
        created_at="2026-07-31T00:00:00+00:00",
        lease_expires_at="2099-07-31T00:00:00+00:00",
        max_attempts=3,
    ).attempt
    attempt_id = str(attempt["attempt_id"])
    store.claim_delivery(
        attempt_id,
        updated_at="2026-07-31T00:00:01+00:00",
    )
    store.mark_sent(
        attempt_id,
        updated_at="2026-07-31T00:00:02+00:00",
    )
    artifact = runtime.state_dir / "artifacts" / "input.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps({"value": 1}), encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    manifest = build_attempt_source_manifest(
        workflow_run_id="run-1",
        task_id="T1",
        attempt_id=attempt_id,
        dispatch_id=attempt_id,
        sources=[{
            "source_id": "input",
            "artifact_id": "input.json",
            "ref": "artifacts/input.json",
            "sha256": digest,
        }],
    )
    read_attempt_artifact(
        runtime.state_dir,
        manifest=manifest,
        source_id="input",
        artifact_id="input.json",
    )
    runtime.event_writer.append(ZfEvent(
        type="workflow.operation.started",
        actor="zf-cli",
        origin="kernel",
        task_id="T1",
        correlation_id="run-1",
        payload={
            "workflow_run_id": "run-1",
            "operation_id": operation_id,
            "active_attempt_id": attempt_id,
        },
    ))
    terminal = runtime.event_writer.append(ZfEvent(
        type=event_type,
        actor="zf-cli",
        origin="kernel",
        task_id="T1",
        correlation_id="run-1",
        payload={
            "workflow_run_id": "run-1",
            "operation_id": operation_id,
            "request_hash": "a" * 64,
            "reason": "terminalized",
        },
    ))

    assert reconcile_task_attempts(runtime) >= 1

    current = store.get(attempt_id)
    assert current is not None
    assert current["status"] == expected_status
    assert current["terminal_event_id"] == terminal.id
    assert not active_read_ledger_path(runtime.state_dir, attempt_id).exists()
    assert list(
        (runtime.state_dir / "artifacts" / "attempts" / attempt_id).glob(
            "read-ledger-*.jsonl"
        )
    )
    assert any(
        event.type == occurrence_type
        and str((event.payload or {}).get("attempt_id") or "") == attempt_id
        for event in runtime.event_log.read_all()
    )


def test_live_attempt_projection_clears_operation_terminal_without_attempt_id() -> None:
    events = [
        ZfEvent(
            type="workflow.operation.started",
            payload={"operation_id": "op-1", "active_attempt_id": "attempt-1"},
        ),
        ZfEvent(
            type="workflow.operation.superseded",
            payload={"operation_id": "op-1"},
        ),
    ]

    assert live_attempt_ids(events) == set()
