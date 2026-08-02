from __future__ import annotations

from pathlib import Path

from zf.core.events import EventLog, EventWriter
from zf.runtime.channel_prd_context import (
    canonical_channel_prd_context,
    workflow_context_for_project,
    workflow_context_from_payload,
)


def test_context_exposes_only_consensus_backed_matching_synthesis(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.emit(
        "channel.created",
        actor="test",
        payload={"channel_id": "ch-prd", "name": "PRD review"},
    )
    synthesis = writer.emit(
        "channel.synthesis.proposed",
        actor="synthesizer",
        correlation_id="ch-prd",
        payload={
            "channel_id": "ch-prd",
            "thread_id": "main",
            "artifact_ref": "channel-artifacts/ch-prd/prd.md",
            "artifact_digest": "sha256:canonical",
            "source_refs": ["event:requirement"],
        },
    )
    writer.emit(
        "channel.consensus.proposed",
        actor="synthesizer",
        correlation_id="ch-prd",
        payload={
            "channel_id": "ch-prd",
            "thread_id": "main",
            "artifact_ref": "channel-artifacts/ch-prd/prd.md",
            "artifact_digest": "canonical",
            "source_refs": ["channel:ch-prd/main"],
        },
    )

    assert canonical_channel_prd_context(state_dir)["items"] == []

    reached = writer.emit(
        "channel.consensus.reached",
        actor="kernel",
        correlation_id="ch-prd",
        payload={
            "channel_id": "ch-prd",
            "thread_id": "main",
            "artifact_ref": "channel-artifacts/ch-prd/prd.md",
        },
    )

    context = canonical_channel_prd_context(state_dir)

    assert context["items"] == [{
        "channel_id": "ch-prd",
        "channel_name": "PRD review",
        "thread_id": "main",
        "artifact_ref": "channel-artifacts/ch-prd/prd.md",
        "artifact_digest": "canonical",
        "source_ref": "channel:ch-prd/main",
        "synthesis_event_id": synthesis.id,
        "consensus_event_id": reached.id,
        "source_refs": [
            "event:requirement",
            "channel:ch-prd/main",
        ],
        "updated_at": reached.ts,
    }]


def test_workflow_context_from_payload_is_a_defensive_mapping_copy() -> None:
    source = {"source_ref": "channel-artifacts/ch-prd/prd.md"}
    copied = workflow_context_from_payload({"workflow_context": source})

    assert copied == source
    assert copied is not source
    assert workflow_context_from_payload({"workflow_context": []}) == {}


def test_workflow_context_defaults_target_without_overwriting_explicit_value(
    tmp_path: Path,
) -> None:
    defaulted = workflow_context_for_project({}, tmp_path)
    explicit = workflow_context_for_project(
        {"workflow_context": {"target_root": "/explicit/target"}},
        tmp_path,
    )

    assert defaulted["target_root"] == str(tmp_path.resolve())
    assert explicit["target_root"] == "/explicit/target"
