from __future__ import annotations

from pathlib import Path

from zf.core.events import EventLog, ZfEvent
from zf.runtime.artifact_query.service import ArtifactQueryService
from zf.runtime.research_fanout_artifact import (
    materialize_research_fanout_artifact,
    merge_research_artifact_payload,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _provider_summary() -> dict:
    return {
        "schema_version": "provider-operation-summary.v1",
        "workflow_run_id": "run-research-1",
        "operation_id": "provider-root-1",
        "provider_session_id": "provider-session-1",
        "settlement": "settled",
        "child_count": 2,
        "child_status_counts": {"completed": 2},
        "active_child_count": 0,
        "peak_parallel_agents": 2,
        "usage": {
            "input_tokens": 1200,
            "output_tokens": 350,
        },
        "cost_usd": 0.42,
        "measurement": "provider_reported",
        "children": [
            {
                "child_id": "source-audit",
                "status": "completed",
                "evidence_refs": ["docs/source.md#case-1"],
            },
            {
                "child_id": "risk-review",
                "status": "completed",
                "evidence_refs": ["docs/source.md#risks"],
            },
        ],
    }


def _manifest() -> dict:
    return {
        "task_id": "TASK-RESEARCH",
        "fanout_id": "fanout-adaptive-1",
        "stage_id": "research-adaptive",
        "trigger_payload": {
            "task_id": "TASK-RESEARCH",
            "workflow_run_id": "run-research-1",
            "reason": "Evaluate the smallest viable simulation.",
            "source_refs": {
                "topic": "HighwayPilot construction-v0",
                "template_id": "research-adaptive.pilot.v1",
                "research_rollout": "opt_in_pilot",
            },
        },
        "children": [
            {
                "child_id": "research_root",
                "role_instance": "research_root",
                "status": "completed",
                "result_event_id": "evt-root-result",
                "report": {
                    "summary": "Root reconciled two read-only children.",
                    "findings": [{"id": "F-1", "status": "confirmed"}],
                    "evidence_refs": ["docs/source.md#case-1"],
                    "provider_operation_summary": _provider_summary(),
                },
            },
        ],
    }


def _synth_event() -> ZfEvent:
    return ZfEvent(
        id="evt-synth",
        type="fanout.synth.completed",
        actor="research_root",
        task_id="TASK-RESEARCH",
        payload={
            "fanout_id": "fanout-adaptive-1",
            "stage_id": "research-adaptive",
            "role_instance": "research_root",
            "status": "completed",
            "summary": "Proceed with a bounded construction-v0 prototype.",
            "evidence_refs": [
                "docs/source.md#case-1",
                "docs/prompt.md#acceptance",
            ],
            "open_questions": ["Confirm traffic density threshold."],
            "provider_operation_summary": _provider_summary(),
            "report": {
                "summary": "Proceed with a bounded construction-v0 prototype.",
                "findings": [
                    {
                        "id": "F-1",
                        "claim": "The scenario is implementable with highway-env.",
                        "confidence": "confirmed",
                    },
                ],
                "architecture": {
                    "environment": "highway-env",
                    "scenario": "construction-v0",
                },
                "acceptance_matrix": [
                    {
                        "id": "AC-1",
                        "criterion": "Agent slows before lane closure.",
                    },
                ],
                "test_matrix": [
                    {
                        "id": "T-1",
                        "level": "simulation",
                        "assertion": "No collision in deterministic seed.",
                    },
                ],
                "task_map": [
                    {
                        "id": "TASK-1",
                        "title": "Build the bounded scenario.",
                    },
                ],
                "prd_prompt_input": "Implement only construction-v0.",
                "refactor_prompt_input": "Keep policy and renderer separated.",
                "open_questions": ["Confirm traffic density threshold."],
            },
        },
    )


def test_research_artifact_preserves_complete_synthesis_and_provider_ref(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    descriptor = materialize_research_fanout_artifact(
        state_dir,
        manifest=_manifest(),
        synth_event=_synth_event(),
    )

    assert descriptor["ref_schema_version"] == "sidecar-ref.v1"
    assert descriptor["schema_version"] == "research-report.v1"
    assert descriptor["ref"].startswith(
        "artifacts/research/TASK-RESEARCH/"
    )
    assert descriptor["provider_operation_summary_status"] == "available"
    body = hydrate_sidecar_ref(state_dir, descriptor).payload
    assert isinstance(body, str)
    for expected in (
        "HighwayPilot construction-v0",
        "Acceptance Matrix",
        "Test Matrix",
        "Task Map",
        "Implement only construction-v0.",
        "Keep policy and renderer separated.",
        "provider-operation-summary.v1",
        "evt-root-result",
    ):
        assert expected in body

    provider_ref = descriptor["provider_operation_summary_ref"]
    provider_body = hydrate_sidecar_ref(
        state_dir,
        provider_ref,
    ).payload
    assert provider_body["child_count"] == 2
    assert provider_body["settlement"] == "settled"


def test_research_report_is_queryable_by_semantic_kind_and_task(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    descriptor = materialize_research_fanout_artifact(
        state_dir,
        manifest=_manifest(),
        synth_event=_synth_event(),
    )
    aggregate_payload = merge_research_artifact_payload({}, descriptor)
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        id="evt-aggregate",
        type="fanout.aggregate.completed",
        actor="zf-cli",
        task_id="TASK-RESEARCH",
        correlation_id="run-research-1",
        payload={
            "status": "completed",
            "workflow_run_id": "run-research-1",
            **aggregate_payload,
        },
    ))
    service = ArtifactQueryService(
        state_dir=state_dir,
        project_root=tmp_path,
    )

    result = service.catalog_list(
        context=service.context(),
        semantic_kind="research_report",
        task_id="TASK-RESEARCH",
    )

    assert result["projection_state"] == "ready"
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["semantic_kind"] == "research_report"
    assert item["storage_kinds"] == ["research_report"]
    assert item["sha256"] == descriptor["sha256"]
    shown = service.catalog_show(
        item["object_id"],
        context=service.context(),
    )
    assert shown["item"]["locators"][0]["ref"] == descriptor["ref"]


def test_research_artifact_does_not_materialize_without_summary(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    event = _synth_event()
    event.payload = {
        "fanout_id": "fanout-adaptive-1",
        "stage_id": "research-adaptive",
        "report": {},
    }

    descriptor = materialize_research_fanout_artifact(
        state_dir,
        manifest=_manifest(),
        synth_event=event,
    )

    assert descriptor == {}
    assert not list(state_dir.rglob("*.md"))
