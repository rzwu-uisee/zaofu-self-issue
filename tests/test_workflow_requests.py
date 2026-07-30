from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.workflow_requests import (
    WorkflowRequestError,
    load_workflow_request,
    mark_workflow_request,
    register_workflow_intake,
    request_readiness_blockers,
    revise_workflow_request,
)


def _request_fixture(tmp_path: Path) -> tuple[Path, Path, EventWriter]:
    state_dir = tmp_path / ".zf"
    workflow_dir = tmp_path / "artifacts" / "workflow" / "REQ-1"
    intake_ref = tmp_path / "artifacts" / "intake" / "REQ-1.json"
    manifest_ref = workflow_dir / "workflow-input-manifest.json"
    intake_ref.parent.mkdir(parents=True)
    workflow_dir.mkdir(parents=True)
    intake_ref.write_text(json.dumps({
        "schema_version": "workflow.intake.v1",
        "request_id": "REQ-1",
        "effective_kind": "refactor",
        "objective": "rebuild the target package",
        "source_root": "",
        "target_root": "",
        "acceptance": [],
        "constraints": [],
        "open_questions": ["which source tree is canonical?"],
    }), encoding="utf-8")
    manifest_ref.write_text(json.dumps({
        "schema_version": "workflow.input_manifest.v1",
        "request_id": "REQ-1",
        "project_id": "demo",
        "kind": "refactor",
        "objective": "rebuild the target package",
        "intake_json_ref": str(intake_ref),
        "workflow_dir": str(workflow_dir),
        "workflow_input_manifest_ref": str(manifest_ref),
        "artifact_refs": [str(intake_ref)],
    }), encoding="utf-8")
    state_dir.mkdir()
    return state_dir, manifest_ref, EventWriter(EventLog(state_dir / "events.jsonl"))


def test_workflow_request_revision_reaches_ready_with_versioned_spec(tmp_path: Path) -> None:
    state_dir, manifest_ref, writer = _request_fixture(tmp_path)
    source_manifest = manifest_ref.read_bytes()

    initial = register_workflow_intake(
        state_dir,
        manifest_ref,
        actor="test",
        writer=writer,
    )
    assert initial["status"] == "clarifying"
    assert initial["revision"] == 1
    assert initial["missing_required_fields"] == ["source_root", "target_root"]
    assert request_readiness_blockers(initial)

    ready = revise_workflow_request(
        state_dir,
        manifest_ref,
        actor="owner",
        source_root="/repo/source",
        target_root="/repo/target",
        acceptance=["all public commands retain parity"],
        open_questions=[],
        confirm=True,
        writer=writer,
    )

    assert ready["status"] == "ready"
    assert ready["revision"] == 2
    assert ready["confirmed"] is True
    assert request_readiness_blockers(ready) == []
    assert manifest_ref.read_bytes() == source_manifest
    assert Path(ready["requirement_spec_ref"]).is_relative_to(state_dir)
    assert Path(ready["workflow_input_manifest_ref"]).is_relative_to(state_dir)
    assert ready["source_workflow_input_manifest_ref"] == str(manifest_ref)
    effective = json.loads(
        Path(ready["workflow_input_manifest_ref"]).read_text(encoding="utf-8")
    )
    assert effective["request_revision"] == 2
    assert effective["source_workflow_input_manifest_ref"] == str(manifest_ref)
    assert effective["source_workflow_input_manifest_digest"]
    spec = json.loads(Path(ready["requirement_spec_ref"]).read_text(encoding="utf-8"))
    assert spec["schema_version"] == "requirement-spec.v1"
    assert spec["revision"] == 2
    assert spec["acceptance"] == ["all public commands retain parity"]
    assert load_workflow_request(state_dir, "REQ-1")["requirement_spec_digest"]
    types = [event.type for event in writer.event_log.read_all()]
    assert types == [
        "workflow.intake.created",
        "workflow.intake.clarification.required",
        "workflow.request.updated",
        "workflow.intake.ready",
    ]


def test_existing_legacy_request_is_migrated_to_origin_binding(
    tmp_path: Path,
) -> None:
    state_dir, manifest_ref, writer = _request_fixture(tmp_path)
    register_workflow_intake(
        state_dir,
        manifest_ref,
        actor="test",
        writer=writer,
    )
    projection_path = state_dir / "workflow-requests" / "REQ-1.json"
    legacy = json.loads(projection_path.read_text(encoding="utf-8"))
    legacy.pop("origin_binding")
    projection_path.write_text(
        json.dumps(legacy, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    resumed = register_workflow_intake(
        state_dir,
        manifest_ref,
        actor="test",
        writer=writer,
    )

    assert resumed["origin_binding"] == {
        "schema_version": "workflow-origin-binding.v1",
        "surface": "cli",
        "source": "legacy",
        "project_id": "demo",
        "channel_id": "",
        "thread_id": "",
        "conversation_id": "",
        "thread_key": "",
    }
    effective = json.loads(
        Path(resumed["workflow_input_manifest_ref"]).read_text(
            encoding="utf-8",
        )
    )
    assert effective["origin_binding"] == resumed["origin_binding"]


def test_workflow_request_lifecycle_rejects_invalid_transition(tmp_path: Path) -> None:
    state_dir, manifest_ref, writer = _request_fixture(tmp_path)
    register_workflow_intake(state_dir, manifest_ref, actor="test", writer=writer)
    revise_workflow_request(
        state_dir,
        manifest_ref,
        actor="owner",
        source_root="/repo/source",
        target_root="/repo/target",
        open_questions=[],
        confirm=True,
        writer=writer,
    )
    for status, event_type in (
        ("proposed", "workflow.request.proposed"),
        ("approved", "workflow.request.approved"),
        ("submitted", "workflow.request.submitted"),
        ("running", "workflow.request.running"),
    ):
        mark_workflow_request(
            state_dir,
            "REQ-1",
            status=status,
            actor="test",
            writer=writer,
            event_type=event_type,
        )
    assert load_workflow_request(state_dir, "REQ-1")["status"] == "running"
    with pytest.raises(WorkflowRequestError, match="running -> proposed"):
        mark_workflow_request(
            state_dir,
            "REQ-1",
            status="proposed",
            actor="test",
        )


def test_workflow_request_semantic_replan_records_attempt_domain(
    tmp_path: Path,
) -> None:
    state_dir, manifest_ref, writer = _request_fixture(tmp_path)
    initial = register_workflow_intake(
        state_dir,
        manifest_ref,
        actor="test",
        writer=writer,
    )

    revised = revise_workflow_request(
        state_dir,
        manifest_ref,
        actor="owner",
        source_root="/repo/source",
        target_root="/repo/target",
        acceptance=["close the mandatory evidence gap"],
        open_questions=[],
        revision_reason="semantic_replan",
        source_event_id="evt-verify-rejected",
        writer=writer,
    )

    updated = next(
        event
        for event in writer.event_log.read_all()
        if event.type == "workflow.request.updated"
    )
    assert revised["revision"] == initial["revision"] + 1
    assert updated.causation_id == "evt-verify-rejected"
    assert updated.payload["previous_revision"] == initial["revision"]
    assert updated.payload["revision_reason"] == "semantic_replan"
    assert updated.payload["source_event_id"] == "evt-verify-rejected"
    assert updated.payload["attempt_domain"] == "gap"
    assert updated.payload["semantic_attempt_incremented"] is True


def test_workflow_request_rejects_unknown_revision_reason(
    tmp_path: Path,
) -> None:
    state_dir, manifest_ref, writer = _request_fixture(tmp_path)
    register_workflow_intake(
        state_dir,
        manifest_ref,
        actor="test",
        writer=writer,
    )

    with pytest.raises(WorkflowRequestError, match="revision reason"):
        revise_workflow_request(
            state_dir,
            manifest_ref,
            actor="owner",
            revision_reason="protocol_repair",
            writer=writer,
        )
