from __future__ import annotations

import json
from pathlib import Path

from zf.runtime.provider_operation_summary import (
    prepare_provider_operation_summary,
    validate_provider_operation_summary,
)


def _summary(**overrides) -> dict:
    value = {
        "schema_version": "provider-operation-summary.v1",
        "workflow_run_id": "run-1",
        "operation_id": "op-1",
        "provider_session_id": "provider-session-1",
        "settlement": "settled",
        "child_count": 3,
        "child_status_counts": {
            "completed": 2,
            "failed": 1,
        },
        "active_child_count": 0,
        "peak_parallel_agents": 2,
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
        },
        "cost_usd": 0.25,
        "failure_refs": ["artifacts/provider/failure-1.json"],
    }
    value.update(overrides)
    return value


def test_valid_summary_writes_immutable_sidecar(tmp_path: Path) -> None:
    descriptor, issues = prepare_provider_operation_summary(
        state_dir=tmp_path,
        source_payload={"provider_operation_summary": _summary()},
        workflow_run_id="run-1",
        operation_id="op-1",
        max_parallel_agents=4,
        budget_usd=1.0,
        source_event_id="evt-1",
    )

    assert issues == []
    assert descriptor is not None
    assert descriptor["schema_version"] == "provider-operation-summary.v1"
    assert descriptor["ref"].startswith("artifacts/provider-operations/summaries/")
    body = json.loads((tmp_path / descriptor["ref"]).read_text(encoding="utf-8"))
    assert body["child_status_counts"]["failed"] == 1


def test_valid_summary_ref_is_hydrated_and_rebound(tmp_path: Path) -> None:
    inline_descriptor, issues = prepare_provider_operation_summary(
        state_dir=tmp_path,
        source_payload={"provider_operation_summary": _summary()},
        workflow_run_id="run-1",
        operation_id="op-1",
        source_event_id="evt-inline",
    )
    assert issues == []
    assert inline_descriptor is not None

    ref_descriptor, issues = prepare_provider_operation_summary(
        state_dir=tmp_path,
        source_payload={
            "provider_operation_summary_ref": inline_descriptor,
        },
        workflow_run_id="run-1",
        operation_id="op-1",
        source_event_id="evt-ref",
    )

    assert issues == []
    assert ref_descriptor is not None
    assert ref_descriptor["sha256"] == inline_descriptor["sha256"]
    assert ref_descriptor["ref"] == inline_descriptor["ref"]


def test_terminal_summary_rejects_active_children_and_count_drift() -> None:
    issues = validate_provider_operation_summary(
        _summary(
            active_child_count=1,
            child_status_counts={"completed": 2, "running": 1},
        ),
        workflow_run_id="run-1",
        operation_id="op-1",
        require_terminal=True,
    )
    codes = {(item["field"], item["code"]) for item in issues}
    assert (
        "provider_operation_summary.active_child_count",
        "terminal_has_active_children",
    ) in codes


def test_summary_enforces_concurrency_and_budget_ceiling() -> None:
    issues = validate_provider_operation_summary(
        _summary(peak_parallel_agents=5, cost_usd=2.0),
        workflow_run_id="run-1",
        operation_id="op-1",
        max_parallel_agents=4,
        budget_usd=1.0,
        require_terminal=True,
    )
    codes = {item["code"] for item in issues}
    assert "concurrency_ceiling_exceeded" in codes
    assert "budget_ceiling_exceeded" in codes


def test_summary_identity_mismatch_fails_closed(tmp_path: Path) -> None:
    descriptor, issues = prepare_provider_operation_summary(
        state_dir=tmp_path,
        source_payload={"provider_operation_summary": _summary(operation_id="old")},
        workflow_run_id="run-1",
        operation_id="op-1",
    )
    assert descriptor is None
    assert any(item["code"] == "identity_mismatch" for item in issues)
