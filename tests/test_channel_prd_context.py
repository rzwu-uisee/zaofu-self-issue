from __future__ import annotations

from pathlib import Path

from zf.core.events import EventLog, EventWriter
from zf.runtime.channel_prd_context import (
    canonical_channel_prd_authority,
    canonical_channel_prd_context,
    workflow_context_for_project,
    workflow_context_from_payload,
)
from zf.integrations.feishu.kanban_context import (
    build_feishu_kanban_planning_context,
)


def test_context_exposes_only_consensus_backed_matching_synthesis(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.emit(
        "channel.created",
        actor="test",
        payload={
            "channel_id": "ch-prd",
            "name": "PRD review",
            "leader_member_id": "product_pm",
            "leader_revision": 2,
            "origin_binding": {
                "schema_version": "channel-origin-binding.v1",
                "surface": "feishu",
                "channel_id": "ch-prd",
                "thread_id": "main",
                "chat_id": "oc_prd",
                "origin_message_id": "om_prd_root",
                "root_message_id": "om_prd_root",
                "source_message_id": "om_prd_source",
            },
        },
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
            "readiness_ref": "channels/ch-prd/prd/r1-readiness.json",
            "readiness_digest": "sha256:readiness",
            "readiness_verdict": "ready",
            "implementation_start": True,
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
            "readiness_ref": "channels/ch-prd/prd/r1-readiness.json",
            "readiness_digest": "readiness",
            "readiness_verdict": "ready",
            "implementation_start": True,
            "prd_revision": 3,
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
        "readiness_ref": "channels/ch-prd/prd/r1-readiness.json",
        "readiness_digest": "sha256:readiness",
        "readiness_verdict": "ready",
        "implementation_start": True,
        "source_refs": [
            "event:requirement",
            "channel:ch-prd/main",
        ],
        "updated_at": reached.ts,
    }]
    authority = canonical_channel_prd_authority(
        state_dir,
        channel_id="ch-prd",
        thread_id="main",
    )
    assert authority == {
        "channel_id": "ch-prd",
        "thread_id": "main",
        "channel_member_id": "product_pm",
        "leader_revision": 2,
        "prd_revision": 3,
        "source_ref": "channel-artifacts/ch-prd/prd.md",
        "source_digest": "canonical",
    }
    matched = build_feishu_kanban_planning_context(
        state_dir,
        None,
        chat_id="oc_prd",
        root_message_id="om_prd_root",
    )
    mismatched = build_feishu_kanban_planning_context(
        state_dir,
        None,
        chat_id="oc_prd",
        root_message_id="om_other_root",
    )
    assert matched["selection_status"] == "exact"
    assert matched["workflow_parameters"] == authority
    assert mismatched["selection_status"] == "unavailable"
    assert mismatched["workflow_parameters"] == {}


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
