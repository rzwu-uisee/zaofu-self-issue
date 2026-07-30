from __future__ import annotations

from pathlib import Path

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.channel_workflow_bridge import (
    emit_fanout_channel_state_update,
)
from zf.runtime.workflow_results import emit_research_result_available


def _writer(tmp_path: Path) -> EventWriter:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    return EventWriter(EventLog(state_dir / "events.jsonl"))


def _terminal(*, stage_id: str = "research-fanout") -> ZfEvent:
    digest = "a" * 64
    return ZfEvent(
        type="fanout.aggregate.completed",
        actor="orchestrator",
        task_id="TASK-1",
        payload={
            "fanout_id": "fanout-1",
            "stage_id": stage_id,
            "status": "completed",
            "artifact_refs": [{
                "kind": "research_report",
                "ref": "research/TASK-1/fanout-1.md",
                "sha256": digest,
                "summary": "Evidence-backed result.",
                "task_id": "TASK-1",
                "stage_id": stage_id,
                "fanout_id": "fanout-1",
                "workflow_run_id": "wf-1",
                "request_id": "REQ-1",
                "request_revision": 1,
            }],
            "research_artifact_ref": "research/TASK-1/fanout-1.md",
            "research_artifact_digest": digest,
        },
    )


def test_research_result_available_is_idempotent_and_uses_origin(
    tmp_path: Path,
) -> None:
    writer = _writer(tmp_path)
    terminal = writer.append(_terminal())
    manifest = {
        "task_id": "TASK-1",
        "stage_id": "research-fanout",
        "workflow_run_id": "wf-1",
        "request_id": "REQ-1",
        "request_revision": 1,
        "origin_binding": {
            "schema_version": "workflow-origin-binding.v1",
            "surface": "kanban_agent",
            "source": "kanban-agent",
            "project_id": "demo",
            "channel_id": "",
            "thread_id": "",
            "conversation_id": "kanban:demo",
            "thread_key": "main",
        },
    }

    first = emit_research_result_available(
        writer=writer,
        terminal_event=terminal,
        manifest=manifest,
    )
    replay = emit_research_result_available(
        writer=writer,
        terminal_event=terminal,
        manifest=manifest,
    )

    assert first is not None
    assert replay is not None
    assert replay.id == first.id
    assert first.payload["conversation_id"] == "kanban:demo"
    assert first.payload["artifact_digest"] == "a" * 64
    assert sum(
        event.type == "workflow.result.available"
        for event in writer.event_log.read_all()
    ) == 1


def test_autoresearch_artifact_is_not_a_product_workflow_result(
    tmp_path: Path,
) -> None:
    writer = _writer(tmp_path)
    terminal = writer.append(_terminal(stage_id="autoresearch-diagnosis"))

    result = emit_research_result_available(
        writer=writer,
        terminal_event=terminal,
        manifest={"stage_id": "autoresearch-diagnosis"},
    )

    assert result is None
    assert not any(
        event.type == "workflow.result.available"
        for event in writer.event_log.read_all()
    )


def test_invalid_result_origin_records_one_return_diagnostic(
    tmp_path: Path,
) -> None:
    writer = _writer(tmp_path)
    terminal = writer.append(_terminal())
    manifest = {
        "task_id": "TASK-1",
        "stage_id": "research-fanout",
        "workflow_run_id": "wf-1",
        "request_id": "REQ-1",
        "request_revision": 1,
        "origin_binding": {
            "schema_version": "workflow-origin-binding.v1",
            "surface": "invalid",
        },
    }

    diagnostic = emit_fanout_channel_state_update(
        writer=writer,
        terminal_event=terminal,
        manifest=manifest,
    )
    replay = emit_fanout_channel_state_update(
        writer=writer,
        terminal_event=terminal,
        manifest=manifest,
    )

    assert diagnostic is not None
    assert replay is not None
    assert diagnostic.type == "workflow.result.return.skipped"
    assert replay.id == diagnostic.id
    assert sum(
        event.type == "workflow.result.return.skipped"
        for event in writer.event_log.read_all()
    ) == 1
