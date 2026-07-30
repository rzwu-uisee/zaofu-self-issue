from __future__ import annotations

import hashlib
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_workflow_bridge import emit_fanout_channel_state_update
from zf.runtime.fanout import FanoutManifestProjector


def test_fanout_manifest_preserves_channel_source_refs_for_bridge(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    requested = writer.emit(
        "fanout.requested",
        actor="zf-cli",
        task_id="TASK-1",
        correlation_id="ch-research",
        payload={
            "fanout_id": "fanout-research-1",
            "stage_id": "research-council",
            "topology": "fanout_reader",
            "trace_id": "trace-1",
            "task_id": "TASK-1",
            "channel_id": "ch-research",
            "thread_id": "topic-a",
            "pattern_id": "research-council",
            "workflow_run_id": "wf-research",
            "workflow_input_manifest_ref": "workflow-inputs/wf-research/manifest.json",
            "workflow_prompt_ref": "workflow-inputs/wf-research/prompt.md",
            "prompt_kind": "prd",
            "source_refs": {"channel_id": "ch-research", "thread_id": "topic-a"},
            "artifact_refs": [{"kind": "research_seed", "path": "research/seed.md"}],
        },
    )
    writer.emit(
        "fanout.started",
        actor="zf-cli",
        task_id="TASK-1",
        causation_id=requested.id,
        correlation_id="trace-1",
        payload={
            "fanout_id": "fanout-research-1",
            "stage_id": "research-council",
            "topology": "fanout_reader",
            "trace_id": "trace-1",
            "trigger_event_id": requested.id,
        },
    )

    manifest = FanoutManifestProjector(state_dir).rebuild("fanout-research-1", log.read_all())

    assert manifest["channel_id"] == "ch-research"
    assert manifest["source_refs"]["thread_id"] == "topic-a"
    assert manifest["workflow_prompt_ref"].endswith("prompt.md")
    assert manifest["artifact_refs"][0]["path"] == "research/seed.md"


def test_fanout_terminal_event_posts_research_result_to_channel(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    artifact = state_dir / "research" / "TASK-1" / "report.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("Research result.\n", encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    synth = ZfEvent(
        type="fanout.synth.completed",
        actor="research-synth",
        task_id="TASK-1",
        payload={
            "fanout_id": "fanout-research-1",
            "stage_id": "research-council",
            "status": "completed",
            "recommendation": "approve",
            "summary": "The research result supports drafting the PRD.",
            "report": {"summary": "research report summary"},
        },
    )
    terminal = writer.emit(
        "fanout.aggregate.completed",
        actor="zf-cli",
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "fanout_id": "fanout-research-1",
            "stage_id": "research-council",
            "status": "completed",
            "synth_event_id": synth.id,
            "research_artifact_ref": "research/TASK-1/report.md",
            "research_artifact_digest": digest,
            "research_summary": "The research result supports drafting the PRD.",
            "artifact_refs": [{
                "kind": "research_report",
                "ref": "research/TASK-1/report.md",
                "sha256": digest,
                "summary": "The research result supports drafting the PRD.",
                "task_id": "TASK-1",
                "stage_id": "research-council",
                "fanout_id": "fanout-research-1",
                "workflow_run_id": "wf-research",
                "request_id": "REQ-1",
                "request_revision": 2,
                "synth_event_id": synth.id,
            }],
        },
    )

    manifest = {
        "fanout_id": "fanout-research-1",
        "stage_id": "research-council",
        "task_id": "TASK-1",
        "channel_id": "legacy-channel-must-not-route",
        "thread_id": "legacy-thread",
        "workflow_run_id": "wf-research",
        "workflow_input_manifest_ref": "workflow-inputs/wf-research/manifest.json",
        "workflow_prompt_ref": "workflow-inputs/wf-research/prompt.md",
        "prompt_kind": "prd",
        "request_id": "REQ-1",
        "request_revision": 2,
        "origin_binding": {
            "schema_version": "workflow-origin-binding.v1",
            "surface": "channel",
            "source": "kanban-agent",
            "project_id": "demo",
            "channel_id": "ch-research",
            "thread_id": "topic-a",
            "conversation_id": "",
            "thread_key": "",
        },
        "source_refs": {
            "channel_id": "legacy-channel-must-not-route",
            "thread_id": "legacy-thread",
        },
        "artifact_refs": terminal.payload["artifact_refs"],
    }
    update = emit_fanout_channel_state_update(
        writer=writer,
        terminal_event=terminal,
        synth_event=synth,
        manifest=manifest,
    )
    replay = emit_fanout_channel_state_update(
        writer=writer,
        terminal_event=terminal,
        synth_event=synth,
        manifest=manifest,
    )

    assert update is not None
    assert replay is not None
    assert replay.id == update.id
    assert update.type == "channel.state_update.posted"
    assert update.payload["status"] == "research_result_available"
    assert update.payload["channel_id"] == "ch-research"
    assert update.payload["thread_id"] == "topic-a"
    assert update.payload["refs"]["artifact_digest"] == digest
    result_events = [
        event
        for event in log.read_all()
        if event.type == "workflow.result.available"
    ]
    assert len(result_events) == 1
    assert (
        update.payload["refs"]["workflow_result_event_id"]
        == result_events[0].id
    )
    assert sum(
        event.type == "channel.state_update.posted"
        and event.payload.get("status") == "research_result_available"
        for event in log.read_all()
    ) == 1
    assert update.payload["refs"]["adopt_payload"]["request_id"] == "REQ-1"
    detail = project_channel(state_dir, "ch-research")
    assert detail is not None
    assert detail["state_updates"][0]["summary"] == "The research result supports drafting the PRD."
