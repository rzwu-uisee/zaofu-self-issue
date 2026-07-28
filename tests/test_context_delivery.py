from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.artifact_read_ledger import (
    active_read_ledger_path,
    build_attempt_source_manifest,
    build_input_consumption_policy,
    validate_required_reads,
    write_attempt_source_manifest,
)
from zf.runtime.call_result_runtime import (
    mark_call_operation_started,
    prepare_call_operation,
)
from zf.runtime.context_delivery import (
    CONTEXT_RENDERER_VERSION,
    attach_context_sections,
    build_context_delivery_envelope,
    build_execution_binding,
    write_context_delivery_receipt,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _manifest(
    *,
    digest: str = "a" * 64,
    source_id: str = "contract",
    artifact_id: str = "contract.json",
) -> dict:
    manifest = build_attempt_source_manifest(
        workflow_run_id="run-1",
        task_id="T1",
        attempt_id="attempt-1",
        dispatch_id="dispatch-1",
        sources=[{
            "source_id": source_id,
            "artifact_id": artifact_id,
            "kind": source_id,
            "ref": f"artifacts/inputs/{artifact_id}",
            "sha256": digest,
        }],
        metadata={
            "contract_revision": "R2",
            "task_map_generation": "G2",
            "base_commit": "base-1",
            "target_commit": "target-1",
            "plan_artifact_package_id": "package-1",
            "plan_artifact_package_digest": "p" * 64,
        },
    )
    return attach_context_sections(
        manifest,
        output_profile_id="implementation",
        explicit_required_reads=[{
            "source_id": source_id,
            "artifact_id": artifact_id,
        }],
    )


def _write_manifest(state_dir: Path, manifest: dict) -> dict:
    return write_attempt_source_manifest(state_dir, manifest)


def test_source_manifest_identity_excludes_provider_session_state(
    tmp_path: Path,
) -> None:
    first = _manifest()
    second = _manifest()

    first_descriptor = _write_manifest(tmp_path, first)
    second_descriptor = _write_manifest(tmp_path, second)

    assert first_descriptor["sha256"] == second_descriptor["sha256"]
    assert first["context_policy"] == {
        "schema_version": "context-section-policy.v1",
        "mode": "source_manifest",
        "renderer_version": CONTEXT_RENDERER_VERSION,
        "output_profile_id": "implementation",
    }
    assert first["context_sections"][0]["required"] is True
    assert first["context_sections"][0]["delta_allowed"] is False
    assert "provider_session_id" not in first


def test_delivery_receipt_enables_shadow_unchanged_but_actual_stays_full(
    tmp_path: Path,
) -> None:
    manifest = _manifest(source_id="workflow-input", artifact_id="input.json")
    descriptor = _write_manifest(tmp_path, manifest)
    binding = build_execution_binding(
        source_manifest=manifest,
        role_instance="reviewer-1",
        provider_backend="codex",
    )
    first, first_ref = build_context_delivery_envelope(
        tmp_path,
        source_manifest=manifest,
        source_manifest_descriptor=descriptor,
        workflow_run_id="run-1",
        operation_id="operation-1",
        attempt_id="attempt-1",
        dispatch_id="dispatch-1",
        role_instance="reviewer-1",
        provider_session_id="session-1",
        execution_binding=binding,
    )
    receipt = write_context_delivery_receipt(
        tmp_path,
        envelope=first,
        envelope_descriptor=first_ref,
    )

    second, _second_ref = build_context_delivery_envelope(
        tmp_path,
        source_manifest=manifest,
        source_manifest_descriptor=descriptor,
        workflow_run_id="run-1",
        operation_id="operation-2",
        attempt_id="attempt-1",
        dispatch_id="dispatch-2",
        role_instance="reviewer-1",
        provider_session_id="session-1",
        execution_binding=binding,
        previous_receipt_descriptor=receipt,
    )

    assert first["previous_state"] == "absent"
    assert second["previous_state"] == "known"
    assert second["sections"][0]["delivery"] == "full"
    assert second["sections"][0]["shadow_delivery"] == "unchanged"


def test_missing_corrupt_or_stale_baseline_falls_back_to_full(
    tmp_path: Path,
) -> None:
    manifest = _manifest(source_id="workflow-input", artifact_id="input.json")
    descriptor = _write_manifest(tmp_path, manifest)
    binding = build_execution_binding(
        source_manifest=manifest,
        role_instance="reviewer-1",
    )
    corrupt = {
        "ref": "artifacts/context-delivery/receipts/missing.json",
        "sha256": "f" * 64,
    }
    unknown, _ = build_context_delivery_envelope(
        tmp_path,
        source_manifest=manifest,
        source_manifest_descriptor=descriptor,
        workflow_run_id="run-1",
        operation_id="operation-1",
        attempt_id="attempt-1",
        dispatch_id="dispatch-1",
        role_instance="reviewer-1",
        provider_session_id="session-1",
        execution_binding=binding,
        previous_receipt_descriptor=corrupt,
    )
    assert unknown["previous_state"] == "unknown"
    assert {row["delivery"] for row in unknown["sections"]} == {"full"}
    assert {row["shadow_delivery"] for row in unknown["sections"]} == {"full"}

    first, first_ref = build_context_delivery_envelope(
        tmp_path,
        source_manifest=manifest,
        source_manifest_descriptor=descriptor,
        workflow_run_id="run-1",
        operation_id="operation-1",
        attempt_id="attempt-1",
        dispatch_id="dispatch-1",
        role_instance="reviewer-1",
        provider_session_id="session-1",
        execution_binding=binding,
    )
    receipt = write_context_delivery_receipt(
        tmp_path,
        envelope=first,
        envelope_descriptor=first_ref,
    )
    rotated, _ = build_context_delivery_envelope(
        tmp_path,
        source_manifest=manifest,
        source_manifest_descriptor=descriptor,
        workflow_run_id="run-1",
        operation_id="operation-2",
        attempt_id="attempt-1",
        dispatch_id="dispatch-2",
        role_instance="reviewer-1",
        provider_session_id="session-rotated",
        execution_binding=binding,
        previous_receipt_descriptor=receipt,
    )
    assert rotated["previous_state"] == "incompatible"
    assert {row["shadow_delivery"] for row in rotated["sections"]} == {"full"}

    changed_manifest = {
        **manifest,
        "target_commit": "target-rotated",
    }
    changed_binding = build_execution_binding(
        source_manifest=changed_manifest,
        role_instance="reviewer-1",
    )
    rebound, _ = build_context_delivery_envelope(
        tmp_path,
        source_manifest=changed_manifest,
        source_manifest_descriptor=descriptor,
        workflow_run_id="run-1",
        operation_id="operation-3",
        attempt_id="attempt-1",
        dispatch_id="dispatch-3",
        role_instance="reviewer-1",
        provider_session_id="session-1",
        execution_binding=changed_binding,
        previous_receipt_descriptor=receipt,
    )
    assert rebound["previous_state"] == "incompatible"
    assert {row["shadow_delivery"] for row in rebound["sections"]} == {"full"}


def test_changed_optional_section_generates_shadow_delta_only(
    tmp_path: Path,
) -> None:
    first_manifest = _manifest(
        source_id="workflow-input",
        artifact_id="input.json",
    )
    first_descriptor = _write_manifest(tmp_path, first_manifest)
    binding = build_execution_binding(
        source_manifest=first_manifest,
        role_instance="planner-1",
    )
    first, first_ref = build_context_delivery_envelope(
        tmp_path,
        source_manifest=first_manifest,
        source_manifest_descriptor=first_descriptor,
        workflow_run_id="run-1",
        operation_id="operation-1",
        attempt_id="attempt-1",
        dispatch_id="dispatch-1",
        role_instance="planner-1",
        provider_session_id="session-1",
        execution_binding=binding,
    )
    receipt = write_context_delivery_receipt(
        tmp_path,
        envelope=first,
        envelope_descriptor=first_ref,
    )
    second_manifest = _manifest(
        digest="b" * 64,
        source_id="workflow-input",
        artifact_id="input.json",
    )
    second_descriptor = _write_manifest(tmp_path, second_manifest)
    second, _ = build_context_delivery_envelope(
        tmp_path,
        source_manifest=second_manifest,
        source_manifest_descriptor=second_descriptor,
        workflow_run_id="run-1",
        operation_id="operation-2",
        attempt_id="attempt-1",
        dispatch_id="dispatch-2",
        role_instance="planner-1",
        provider_session_id="session-1",
        execution_binding=binding,
        previous_receipt_descriptor=receipt,
    )

    section = second["sections"][0]
    assert section["delivery"] == "full"
    assert section["shadow_delivery"] == "delta"
    assert section["previous_content_digest"] == "a" * 64
    assert section["current_content_digest"] == "b" * 64
    assert hydrate_sidecar_ref(tmp_path, section["delta_ref"]).payload == {
        "schema_version": "context-section-delta.v1",
        "algorithm": "replace-by-current-source-ref.v1",
        "section_id": section["section_id"],
        "source_id": "workflow-input",
        "artifact_id": "input.json",
        "previous_content_digest": "a" * 64,
        "current_content_digest": "b" * 64,
    }


def test_shadow_selector_miss_does_not_change_manifest_or_actual_delivery(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    before = _write_manifest(tmp_path, manifest)
    binding = build_execution_binding(
        source_manifest=manifest,
        role_instance="implementer-1",
    )

    envelope, _ = build_context_delivery_envelope(
        tmp_path,
        source_manifest=manifest,
        source_manifest_descriptor=before,
        workflow_run_id="run-1",
        operation_id="operation-1",
        attempt_id="attempt-1",
        dispatch_id="dispatch-1",
        role_instance="implementer-1",
        provider_session_id="session-1",
        execution_binding=binding,
        shadow_selected_section_ids=[],
    )
    after = _write_manifest(tmp_path, manifest)

    assert before["sha256"] == after["sha256"]
    assert envelope["shadow_selector_misses"] == [
        manifest["context_sections"][0]["section_id"]
    ]
    assert envelope["sections"][0]["shadow_delivery"] == "omitted"
    assert envelope["sections"][0]["delivery"] == "full"


def test_delivery_receipt_does_not_satisfy_required_read_policy(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    descriptor = _write_manifest(tmp_path, manifest)
    binding = build_execution_binding(
        source_manifest=manifest,
        role_instance="implementer-1",
    )
    envelope, envelope_ref = build_context_delivery_envelope(
        tmp_path,
        source_manifest=manifest,
        source_manifest_descriptor=descriptor,
        workflow_run_id="run-1",
        operation_id="operation-1",
        attempt_id="attempt-1",
        dispatch_id="dispatch-1",
        role_instance="implementer-1",
        provider_session_id="session-1",
        execution_binding=binding,
    )
    receipt = write_context_delivery_receipt(
        tmp_path,
        envelope=envelope,
        envelope_descriptor=envelope_ref,
    )
    policy = build_input_consumption_policy(
        workflow_run_id="run-1",
        attempt_id="attempt-1",
        required_reads=[{
            "source_id": "contract",
            "artifact_id": "contract.json",
            "artifact_sha256": "a" * 64,
            "json_path": "$.acceptance_criteria",
        }],
    )

    assert not active_read_ledger_path(tmp_path, "attempt-1").exists()
    assert validate_required_reads(
        tmp_path,
        policy=policy,
        ledger_descriptor=receipt,
    ) == [{
        "field": "input_consumption.read_ledger_ref",
        "code": "invalid_ledger",
    }]


def test_call_operation_pins_policy_not_session_dependent_envelope(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    state_dir = project_root / ".zf"
    state_dir.mkdir(parents=True)
    source = project_root / "input.json"
    source.write_text(json.dumps({"value": 1}), encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    log = EventLog(state_dir / "events.jsonl")
    runtime = SimpleNamespace(
        state_dir=state_dir,
        project_root=project_root,
        event_log=log,
        event_writer=EventWriter(log),
        config=SimpleNamespace(
            roles=[],
            workflow=SimpleNamespace(
                flow_metadata={"result_protocol": {"mode": "blocking"}}
            ),
        ),
    )
    base_payload = {
        "workflow_run_id": "run-context",
        "role_instance": "plan-synth-1",
        "provider_session_id": "session-1",
        "artifact_refs": [{
            "source_id": "workflow-input",
            "artifact_id": "input",
            "kind": "workflow_input",
            "ref": "input.json",
            "sha256": digest,
        }],
    }
    first_payload = dict(base_payload)
    first = prepare_call_operation(
        runtime,
        payload=first_payload,
        operation_type="fanout_synth",
        operation_key="synth-1",
        stage_id="plan",
        task_id="",
        dispatch_id="attempt-1",
    )
    request_event = next(
        event
        for event in log.read_all()
        if event.type == "workflow.operation.requested"
    )
    request = hydrate_sidecar_ref(
        state_dir,
        request_event.payload["request_ref"],
    ).payload["request"]
    assert request["context_inheritance"]["mode"] == "source_manifest"
    assert "provider_session_id" not in request
    assert "context_delivery_envelope" not in request

    replay_payload = {
        **base_payload,
        "provider_session_id": "session-rotated",
    }
    replay = prepare_call_operation(
        runtime,
        payload=replay_payload,
        operation_type="fanout_synth",
        operation_key="synth-1",
        stage_id="plan",
        task_id="",
        dispatch_id="attempt-1",
    )
    assert replay.request_hash == first.request_hash
    assert replay.ensure_status == "requested"
    assert (
        replay_payload["attempt_source_manifest_digest"]
        == first_payload["attempt_source_manifest_digest"]
    )
    assert (
        replay_payload["context_delivery_envelope_digest"]
        != first_payload["context_delivery_envelope_digest"]
    )

    mark_call_operation_started(
        runtime,
        replay,
        task_id="",
        dispatch_id="attempt-1",
    )
    started = next(
        event
        for event in log.read_all()
        if event.type == "workflow.operation.started"
    )
    assert started.payload["provider_session_id"] == "session-rotated"
    receipt = started.payload["context_delivery_receipt_ref"]
    assert receipt["schema_version"] == "context-delivery-receipt.v1"
    assert hydrate_sidecar_ref(state_dir, receipt).payload[
        "provider_session_id"
    ] == "session-rotated"
