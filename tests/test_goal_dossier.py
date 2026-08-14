from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.goal_dossier import (
    GoalDossierError,
    build_goal_dossier,
    write_goal_dossier_projection,
)
from zf.runtime.goal_dossier_history import build_goal_dossier_history
from zf.runtime.goal_dossier_consistency import (
    evaluate_goal_dossier_delivery_readiness,
)
from zf.runtime.goal_claim_set import build_goal_claim_set
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import (
    build_plan_artifact_package,
    package_event_payload,
    write_plan_artifact_package,
)
from zf.runtime.run_contract import stable_json_sha256, write_run_contract_snapshot
from zf.runtime.workflow_anchor import (
    mark_workflow_fanout_anchor,
    mark_workflow_managed_task,
    workflow_anchor_task_ids,
)
from zf.web.server import create_app


NOW = datetime(2026, 7, 21, 6, 30, tzinfo=timezone.utc)


def _readiness_fixture(
    tmp_path: Path,
) -> tuple[Path, dict, ZfEvent, dict]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    claim_source = write_immutable_json_sidecar(
        state_dir,
        {
            "schema_version": "goal-claim-set.v1",
            "goal_claims": [{
                "goal_claim_id": "CLAIM-1",
                "mandatory": True,
            }],
        },
        root="readiness/claims",
        kind="goal_claim_set",
        schema_version="goal-claim-set.v1",
        created_by="test",
    )
    dossier = {
        "run_id": "run-ready",
        "goal_id": "GOAL-READY",
        "freshness": {"status": "ready"},
        "terminal": {"status": "completed"},
        "state": {
            "task_counts": {"total": 1, "terminal": 1, "open": 0},
            "tasks": [{"id": "TASK-1", "status": "done"}],
        },
        "claim_to_evidence": {
            "summary": {
                "mandatory_claims": 1,
                "closed_claims": 1,
                "open_gaps": 0,
            },
            "rows": [{
                "goal_claim_id": "CLAIM-1",
                "mandatory": True,
                "verdict": "closed",
            }],
        },
        "gaps": [],
        "roadmap": {},
    }
    terminal = ZfEvent(
        id="evt-ready",
        type="run.goal.completed",
        correlation_id="run-ready",
        payload={
            "workflow_run_id": "run-ready",
            "goal_id": "GOAL-READY",
            "completed_task_ids": ["TASK-1"],
            "target_commit": "a" * 40,
            "verified_target_commit": "a" * 40,
            "goal_claim_set_ref": claim_source["ref"],
            "goal_claim_set_digest": claim_source["sha256"],
        },
    )
    receipt = {
        "workflow_run_id": "run-ready",
        "goal_id": "GOAL-READY",
        "completion_gate": {
            "target_commit": "a" * 40,
            "verified_target_commit": "a" * 40,
        },
        "goal_closure": {
            "goal_claim_set_ref": claim_source["ref"],
            "goal_claim_set_digest": claim_source["sha256"],
        },
    }
    return state_dir, dossier, terminal, receipt


def _state(
    tmp_path: Path,
    *,
    complete_run_a: bool = True,
) -> tuple[Path, EventLog]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-A", title="A", status="done", assigned_to="dev-a"))
    store.add(Task(id="TASK-B", title="B", status="done", assigned_to="dev-b"))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        id="evt-run-a-start",
        type="run.goal.started",
        correlation_id="trace-a",
        payload={
            "run_id": "run-a",
            "goal_id": "GOAL-A",
            "objective": "deliver A TOKEN=secret-value",
            "token": "secret-value",
        },
    ))
    log.append(ZfEvent(
        id="evt-run-a-task",
        type="dev.build.done",
        task_id="TASK-A",
        correlation_id="trace-a",
        payload={
            "run_id": "run-a",
            "dispatch_id": "dispatch-a",
            "evidence_refs": ["artifacts/a/result.json"],
            "artifact_digest": "sha256:a",
        },
    ))
    if complete_run_a:
        log.append(ZfEvent(
            id="evt-run-a-complete",
            type="run.goal.completed",
            correlation_id="trace-a",
            payload={"run_id": "run-a", "goal_id": "GOAL-A"},
        ))
    log.append(ZfEvent(
        id="evt-run-b-start",
        type="run.goal.started",
        correlation_id="trace-b",
        payload={"run_id": "run-b", "goal_id": "GOAL-B", "objective": "deliver B"},
    ))
    log.append(ZfEvent(
        id="evt-run-b-task",
        type="dev.build.done",
        task_id="TASK-B",
        correlation_id="trace-b",
        payload={"run_id": "run-b", "dispatch_id": "dispatch-b"},
    ))
    return state_dir, log


def test_goal_dossier_is_run_scoped_redacted_and_rebuildable(tmp_path: Path) -> None:
    state_dir, _log = _state(tmp_path)
    event_digest = _sha256(state_dir / "events.jsonl")
    task_digest = _sha256(state_dir / "kanban.json")

    first = build_goal_dossier(state_dir, "trace-a", now=NOW)
    projection = write_goal_dossier_projection(state_dir, first)

    assert projection == (
        state_dir / "projections/goals/run-a/goal-dossier.v1.json"
    )
    assert first["run_id"] == "run-a"
    assert first["requested_run_id"] == "trace-a"
    assert first["goal"]["status"] == "complete"
    assert first["closure"]["status"] == "goal_completed"
    assert first["state"]["task_counts"] == {"total": 1, "terminal": 1, "open": 0}
    assert first["state"]["tasks"][0]["id"] == "TASK-A"
    assert "TASK-B" not in str(first)
    assert "secret-value" not in str(first)
    assert "[REDACTED_SECRET]" in str(first)
    assert first["source_manifest"]["artifact_refs"] == ["artifacts/a/result.json"]
    assert first["delivery_readiness"]["status"] == "unknown"
    assert {
        issue["code"] for issue in first["delivery_readiness"]["issues"]
    } == {"claim_source_unreadable"}

    projection.unlink()
    second = build_goal_dossier(state_dir, "run-a", now=NOW)
    assert second["source_fingerprint"] == first["source_fingerprint"]
    assert second["source_manifest"] == first["source_manifest"]
    assert _sha256(state_dir / "events.jsonl") == event_digest
    assert _sha256(state_dir / "kanban.json") == task_digest


def test_goal_dossier_delivery_readiness_is_ready_only_after_mechanical_closure(
    tmp_path: Path,
) -> None:
    state_dir, dossier, terminal, receipt = _readiness_fixture(tmp_path)

    readiness = evaluate_goal_dossier_delivery_readiness(
        state_dir=state_dir,
        dossier=dossier,
        terminal=terminal,
        receipt=receipt,
    )

    assert dossier["freshness"]["status"] == "ready"
    assert readiness["status"] == "ready"
    assert readiness["issues"] == []
    assert readiness["source_snapshot"]["status"] == "ready"


def test_goal_dossier_open_mandatory_claim_is_incomplete_not_stale(
    tmp_path: Path,
) -> None:
    state_dir, dossier, terminal, receipt = _readiness_fixture(tmp_path)
    dossier["claim_to_evidence"]["rows"][0]["verdict"] = "open"
    dossier["claim_to_evidence"]["summary"].update({
        "closed_claims": 0,
        "open_gaps": 1,
    })

    readiness = evaluate_goal_dossier_delivery_readiness(
        state_dir=state_dir,
        dossier=dossier,
        terminal=terminal,
        receipt=receipt,
    )

    assert dossier["freshness"]["status"] == "ready"
    assert readiness["status"] == "incomplete"
    assert {
        issue["code"] for issue in readiness["issues"]
    } == {"claim_summary_inconsistent", "mandatory_claims_open"}


def test_goal_dossier_claim_digest_mismatch_is_unknown(
    tmp_path: Path,
) -> None:
    state_dir, dossier, terminal, receipt = _readiness_fixture(tmp_path)
    terminal.payload["goal_claim_set_digest"] = "f" * 64

    readiness = evaluate_goal_dossier_delivery_readiness(
        state_dir=state_dir,
        dossier=dossier,
        terminal=terminal,
        receipt=receipt,
    )

    assert readiness["status"] == "unknown"
    assert readiness["issues"][0]["code"] == "claim_source_unreadable"
    assert readiness["source_snapshot"]["sources"][0]["status"] == (
        "digest_mismatch"
    )


def test_goal_dossier_stale_source_or_missing_receipt_is_unknown(
    tmp_path: Path,
) -> None:
    state_dir, dossier, terminal, receipt = _readiness_fixture(tmp_path)
    dossier["freshness"] = {
        "status": "incomplete",
        "diagnostics": [{"type": "artifact_missing"}],
    }

    stale = evaluate_goal_dossier_delivery_readiness(
        state_dir=state_dir,
        dossier=dossier,
        terminal=terminal,
        receipt=receipt,
    )
    missing_receipt = evaluate_goal_dossier_delivery_readiness(
        state_dir=state_dir,
        dossier={**dossier, "freshness": {"status": "ready"}},
        terminal=terminal,
        receipt=None,
    )

    assert stale["status"] == "unknown"
    assert stale["issues"][0]["code"] == "dossier_source_not_ready"
    assert missing_receipt["status"] == "unknown"
    assert missing_receipt["issues"][0]["code"] == (
        "completion_receipt_unavailable"
    )


def test_goal_dossier_detects_summary_task_and_gap_inconsistency(
    tmp_path: Path,
) -> None:
    state_dir, dossier, terminal, receipt = _readiness_fixture(tmp_path)
    dossier["claim_to_evidence"]["rows"][0]["verdict"] = "open"
    dossier["state"]["task_counts"] = {
        "total": 2,
        "terminal": 1,
        "open": 1,
    }
    dossier["gaps"] = [{"goal_claim_id": "CLAIM-1"}]

    readiness = evaluate_goal_dossier_delivery_readiness(
        state_dir=state_dir,
        dossier=dossier,
        terminal=terminal,
        receipt=receipt,
    )

    assert readiness["status"] == "incomplete"
    assert {
        issue["code"] for issue in readiness["issues"]
    } == {
        "claim_summary_inconsistent",
        "completed_dossier_has_open_gaps",
        "completed_task_counts_inconsistent",
        "mandatory_claims_open",
    }


def test_goal_dossier_allows_evidence_backed_done_superset(
    tmp_path: Path,
) -> None:
    state_dir, dossier, terminal, receipt = _readiness_fixture(tmp_path)
    dossier["state"]["tasks"].append({"id": "TASK-2", "status": "done"})
    dossier["state"]["task_counts"] = {
        "total": 2,
        "terminal": 2,
        "open": 0,
    }

    readiness = evaluate_goal_dossier_delivery_readiness(
        state_dir=state_dir,
        dossier=dossier,
        terminal=terminal,
        receipt=receipt,
    )

    assert readiness["status"] == "ready"
    assert readiness["issues"] == []


def test_goal_dossier_rejects_missing_terminal_completed_task(
    tmp_path: Path,
) -> None:
    state_dir, dossier, terminal, receipt = _readiness_fixture(tmp_path)
    dossier["state"]["tasks"] = [{"id": "TASK-2", "status": "done"}]

    readiness = evaluate_goal_dossier_delivery_readiness(
        state_dir=state_dir,
        dossier=dossier,
        terminal=terminal,
        receipt=receipt,
    )

    assert readiness["status"] == "incomplete"
    assert {
        issue["code"] for issue in readiness["issues"]
    } == {"completed_task_set_mismatch"}


def test_terminal_goal_coverage_closes_authoritative_task() -> None:
    from zf.runtime.goal_dossier_history import (
        _authoritative_task_rows,
        _latest_terminal,
    )

    terminal = _latest_terminal([ZfEvent(
        type="run.goal.completed",
        payload={
            "completed_task_ids": ["TASK-FINAL-BATCH"],
            "goal_coverage": [
                {"goal_claim_id": "CLAIM-1", "status": "closed"},
                {"goal_claim_id": "CLAIM-2", "status": "closed"},
            ],
        },
    )])

    rows = _authoritative_task_rows(
        historical_tasks=[{
            "id": "TASK-CLAIMS",
            "title": "claim-owned task",
            "status": "blocked",
            "status_source": "event_history",
            "contract": {"goal_claim_ids": ["CLAIM-1", "CLAIM-2"]},
        }],
        task_map={"tasks": [{"task_id": "TASK-CLAIMS"}]},
        terminal=terminal,
    )

    assert rows[0]["status"] == "done"
    assert rows[0]["status_source"] == "run_goal_coverage"


def test_goal_dossier_blocked_terminal_stays_blocked(
    tmp_path: Path,
) -> None:
    state_dir, dossier, terminal, receipt = _readiness_fixture(tmp_path)
    terminal.type = "run.goal.blocked"
    dossier["terminal"]["status"] = "blocked"

    readiness = evaluate_goal_dossier_delivery_readiness(
        state_dir=state_dir,
        dossier=dossier,
        terminal=terminal,
        receipt=receipt,
    )

    assert readiness["status"] == "blocked"
    assert readiness["terminal_status"] == "blocked"


def test_goal_dossier_unknown_run_fails_closed(tmp_path: Path) -> None:
    state_dir, _log = _state(tmp_path)

    with pytest.raises(GoalDossierError, match="unknown run_id"):
        build_goal_dossier(state_dir, "missing-run")


def test_goal_dossier_hydrates_absolute_task_map_ref_within_state_dir(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    task_map = write_immutable_json_sidecar(
        state_dir,
        {
            "schema_version": "task-map.v1",
            "workflow_run_id": "run-absolute-ref",
            "goal_id": "GOAL-ABSOLUTE-REF",
            "tasks": [{"task_id": "TASK-1", "title": "Task"}],
        },
        root="dossier-fixtures/task-maps",
        kind="task_map",
        schema_version="task-map.v1",
        created_by="test",
    )
    absolute_ref = str(state_dir / task_map["ref"])
    history = build_goal_dossier_history(
        state_dir,
        run_id="run-absolute-ref",
        goal_id="GOAL-ABSOLUTE-REF",
        events=[ZfEvent(
            id="evt-run-absolute-ref-complete",
            type="run.goal.completed",
            correlation_id="run-absolute-ref",
            payload={
                "run_id": "run-absolute-ref",
                "goal_id": "GOAL-ABSOLUTE-REF",
                "completed_task_ids": ["TASK-1"],
                "task_map_ref": absolute_ref,
                "task_map_digest": task_map["sha256"],
            },
        )],
        current_tasks=[],
        package_roadmap={},
    )

    assert history["task_map"]["status"] == "ready"
    assert history["task_map"]["ref"] == task_map["ref"]
    assert not [
        item
        for item in history["diagnostics"]
        if item.get("type") == "task_map_hydrate_failed"
    ]


def test_goal_dossier_rejects_absolute_task_map_ref_outside_state_dir(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    outside_ref = tmp_path / "outside-task-map.json"
    outside_ref.write_text('{"schema_version":"task-map.v1","tasks":[]}\n')

    history = build_goal_dossier_history(
        state_dir,
        run_id="run-outside-ref",
        goal_id="GOAL-OUTSIDE-REF",
        events=[ZfEvent(
            id="evt-run-outside-ref-complete",
            type="run.goal.completed",
            correlation_id="run-outside-ref",
            payload={
                "run_id": "run-outside-ref",
                "goal_id": "GOAL-OUTSIDE-REF",
                "task_map_ref": str(outside_ref),
            },
        )],
        current_tasks=[],
        package_roadmap={},
    )

    assert history["task_map"]["status"] == "unavailable"
    assert history["task_map"]["ref"] == str(outside_ref)
    assert any(
        item.get("type") == "task_map_hydrate_failed"
        and "clean relative path" in str(item.get("reason") or "")
        for item in history["diagnostics"]
    )


def test_terminal_dossier_uses_terminal_plan_and_ignores_superseded_plan_ids(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    run_id = "run-replanned"
    goal_id = "GOAL-REPLANNED"
    generation = "GEN-FINAL"
    target_commit = "a" * 40
    stale_task_map = write_immutable_json_sidecar(
        state_dir,
        {
            "schema_version": "task-map.v1",
            "workflow_run_id": run_id,
            "goal_id": goal_id,
            "task_map_generation": "GEN-STALE",
            "tasks": [{"task_id": "TASK-STALE", "title": "Stale task"}],
        },
        root="dossier-fixtures/task-maps",
        kind="task_map",
        schema_version="task-map.v1",
        created_by="test",
    )
    final_map_body = {
        "schema_version": "task-map.v1",
        "workflow_run_id": run_id,
        "goal_id": goal_id,
        "task_map_generation": generation,
        "tasks": [{"task_id": "TASK-FINAL", "title": "Final task"}],
    }
    final_task_map = write_immutable_json_sidecar(
        state_dir,
        final_map_body,
        root="dossier-fixtures/task-maps",
        kind="task_map",
        schema_version="task-map.v1",
        created_by="test",
    )
    claim_set = build_goal_claim_set(
        final_map_body,
        workflow_run_id=run_id,
        goal_id=goal_id,
        task_map_generation=generation,
        objective_ref="objective:replanned",
        objective={"acceptance": ["The final delivery is demonstrated."]},
    )
    claim_set_ref = write_immutable_json_sidecar(
        state_dir,
        claim_set,
        root="goal-closure/claim-sets",
        kind="goal_claim_set",
        schema_version="goal-claim-set.v1",
        created_by="test",
    )
    claim_id = claim_set["claims"][0]["goal_claim_id"]
    result_ref = "artifacts/call-results/final.json"
    closure_event = ZfEvent(
        id="evt-closure",
        type="goal.closure.synthesized",
        correlation_id=run_id,
        payload={
            "goal_closure_result": {
                "schema_version": "goal-closure-result.v1",
                "workflow_run_id": run_id,
                "goal_id": goal_id,
                "flow_kind": "prd",
                "task_map_generation": generation,
                "target_commit": target_commit,
                "objective_ref": "objective:replanned",
                "goal_claim_set_ref": claim_set_ref["ref"],
                "goal_claim_set_digest": claim_set_ref["sha256"],
                "planning_result_ref": final_task_map["ref"],
                "candidate_ref": "candidate/final",
                "closure_fact_ref": "artifacts/closure/final.json",
                "closure_fact_digest": "closure-digest",
                "verdict": "passed",
                "summary": "Final evidence closes the objective.",
                "goal_coverage": [{
                    "goal_claim_id": claim_id,
                    "status": "closed",
                    "supporting_result_refs": [result_ref],
                }],
                "input_result_refs": [result_ref],
                "open_gap_refs": [],
                "recommended_action": "complete",
            },
        },
    )
    events = [
        ZfEvent(
            id="evt-plan-proposal",
            type="prd.plan.child.completed",
            correlation_id=run_id,
            payload={
                "workflow_run_id": run_id,
                "task_ids": ["TASK-SUPERSEDED"],
            },
        ),
        ZfEvent(
            id="evt-stale-task-result",
            type="dev.build.done",
            task_id="TASK-SUPERSEDED",
            correlation_id=run_id,
            payload={"workflow_run_id": run_id},
        ),
        ZfEvent(
            id="evt-claim-set",
            type="goal.claim_set.pinned",
            correlation_id=run_id,
            payload={
                "workflow_run_id": run_id,
                "goal_id": goal_id,
                "task_map_generation": generation,
                "goal_claim_set_ref": claim_set_ref["ref"],
                "goal_claim_set_digest": claim_set_ref["sha256"],
            },
        ),
        closure_event,
        ZfEvent(
            id="evt-closure-admitted",
            type="workflow.call.result.admitted",
            correlation_id=run_id,
            payload={
                "schema_version": "call-result-admission.v1",
                "admission_status": "admitted",
                "control_result_schema": "goal-closure-result.v1",
                "source_event_id": closure_event.id,
                "envelope_ref": {
                    "ref": result_ref,
                    "sha256": "result-digest",
                },
            },
        ),
        ZfEvent(
            id="evt-terminal",
            type="run.goal.completed",
            correlation_id=run_id,
            payload={
                "workflow_run_id": run_id,
                "goal_id": goal_id,
                "completed_task_ids": ["TASK-FINAL"],
                "task_map_ref": f".zf/{final_task_map['ref']}",
                "task_map_digest": final_task_map["sha256"],
                "target_commit": target_commit,
            },
        ),
    ]

    history = build_goal_dossier_history(
        state_dir,
        run_id=run_id,
        goal_id=goal_id,
        events=events,
        current_tasks=[
            {"id": "TASK-FINAL", "title": "Final task", "status": "done"},
            {"id": "TASK-SUPERSEDED", "status": "unknown", "missing": True},
        ],
        package_roadmap={
            "current_plan_package": {
                "ports": [{
                    "logical_name": "task_map",
                    "ref": stale_task_map["ref"],
                    "sha256": stale_task_map["sha256"],
                }],
            },
        },
    )

    assert history["task_map"]["source"] == "run_terminal"
    assert history["task_map"]["ref"] == final_task_map["ref"]
    assert [
        task["id"] for task in history["historical_tasks"]
    ] == ["TASK-FINAL", "TASK-SUPERSEDED"]
    assert [
        task["id"] for task in history["current_generation_tasks"]
    ] == ["TASK-FINAL"]
    assert [
        task["id"] for task in history["superseded_tasks"]
    ] == ["TASK-SUPERSEDED"]
    matrix = history["claim_to_evidence"]
    assert matrix["status"] == "ready"
    assert matrix["diagnostics"] == []
    assert matrix["advisories"][0]["code"] == "mandatory_claim_uncovered"
    assert matrix["rows"][0]["plan_coverage"] == "uncovered"
    assert matrix["rows"][0]["verdict"] == "closed"


def test_goal_dossier_projects_current_plan_package_and_history(tmp_path: Path) -> None:
    state_dir, log = _state(tmp_path, complete_run_a=False)
    contract = {
        "schema_version": "run-contract.v1",
        "workflow": {"kind": "prd"},
    }
    contract["contract_digest"] = stable_json_sha256(contract)
    run_contract = write_run_contract_snapshot(state_dir, contract)

    def package(revision: str, generation: str):
        ports = []
        for name in (
            "requirement_spec",
            "goal_claim_set",
            "task_map",
            "planning_result",
        ):
            descriptor = write_immutable_json_sidecar(
                state_dir,
                {"schema_version": f"{name}.v1", "revision": revision},
                root=f"dossier-fixtures/{name}",
                kind=name,
                schema_version=f"{name}.v1",
                created_by="test",
            )
            ports.append({
                "logical_name": name,
                "artifact_kind": name,
                "schema_version": f"{name}.v1",
                "producer_stage_id": "prd-plan",
                "ref": descriptor["ref"],
                "sha256": descriptor["sha256"],
            })
        body = build_plan_artifact_package(
            workflow_run_id="run-a",
            flow_kind="prd",
            producer_stage_id="prd-plan",
            run_contract=run_contract,
            plan_revision=revision,
            task_map_generation=generation,
            produced=ports,
            required_ports=[item["logical_name"] for item in ports],
        )
        return body, write_plan_artifact_package(state_dir, body)

    first, first_ref = package("r1", "g1")
    second, second_ref = package("r2", "g2")
    log.append(ZfEvent(
        type="plan.artifact_package.admitted",
        correlation_id="trace-a",
        payload=package_event_payload(first, first_ref, status="admitted"),
    ))
    log.append(ZfEvent(
        type="plan.artifact_package.admitted",
        correlation_id="trace-a",
        payload=package_event_payload(second, second_ref, status="admitted"),
    ))
    log.append(ZfEvent(
        id="evt-run-a-complete",
        type="run.goal.completed",
        correlation_id="trace-a",
        payload={"run_id": "run-a", "goal_id": "GOAL-A"},
    ))

    dossier = build_goal_dossier(state_dir, "run-a", now=NOW)

    assert dossier["roadmap"]["current_plan_package"]["plan_revision"] == "r2"
    assert dossier["roadmap"]["current_plan_package"]["hydrate_status"] == "ready"
    assert dossier["roadmap"]["plan_package_history"][0]["plan_revision"] == "r1"
    assert dossier["roadmap"]["plan_package_freshness"]["status"] == "ready"


def test_goal_dossier_cli_writes_json_and_markdown(tmp_path: Path) -> None:
    state_dir, _log = _state(tmp_path)
    out = tmp_path / "reports" / "dossier.md"

    rc = main([
        "report",
        "goal-dossier",
        "--state-dir",
        str(state_dir),
        "--run-id",
        "run-a",
        "--out",
        str(out),
    ])

    assert rc == 0
    assert "Goal Dossier: run-a" in out.read_text(encoding="utf-8")
    assert (state_dir / "projections/goals/run-a/goal-dossier.v1.json").is_file()


def test_goal_dossier_web_endpoint_is_read_only(tmp_path: Path) -> None:
    state_dir, _log = _state(tmp_path)
    before = _sha256(state_dir / "events.jsonl")
    client = TestClient(create_app(state_dir))

    response = client.get("/api/runs/run-a/dossier")
    project_response = client.get("/api/projects/default/runs/run-a/dossier")
    preview = client.get("/api/runs/run-a/dossier?preview=true")
    section = client.get("/api/runs/run-a/dossier?section=closure")
    bad_section = client.get("/api/runs/run-a/dossier?section=unknown")
    missing = client.get("/api/runs/missing/dossier")

    assert response.status_code == 200
    assert project_response.status_code == 200
    assert project_response.json()["run_id"] == "run-a"
    assert response.json()["schema_version"] == "goal-dossier.v1"
    assert response.json()["run_id"] == "run-a"
    assert preview.status_code == 200
    assert preview.json()["view"] == "preview"
    assert preview.json()["task_counts"]["total"] == 1
    assert preview.json()["delivery_readiness"]["status"] == "unknown"
    assert section.status_code == 200
    assert section.json()["section"] == "closure"
    assert section.json()["data"]["status"] == "goal_completed"
    assert bad_section.status_code == 404
    assert missing.status_code == 404
    assert _sha256(state_dir / "events.jsonl") == before
    assert not (state_dir / "projections/goals/run-a/goal-dossier.v1.json").exists()


def test_goal_dossier_groups_and_settles_failure_incidents(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-A", title="A", status="done",
    ))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        id="evt-start",
        type="run.goal.started",
        payload={"run_id": "run-incidents", "goal_id": "GOAL-A"},
    ))
    for index in range(2):
        log.append(ZfEvent(
            id=f"evt-fail-{index}",
            type="verify.failed",
            task_id="TASK-A",
            payload={
                "run_id": "run-incidents",
                "failure_fingerprint": "same-gap",
                "reason": "expected output missing",
            },
        ))

    active = build_goal_dossier(state_dir, "run-incidents", now=NOW)

    assert len(active["incident_history"]) == 1
    assert active["incident_history"][0]["status"] == "active"
    assert active["incident_history"][0]["count"] == 2
    failure_gaps = [
        gap for gap in active["gaps"]
        if gap["type"] == "failure_incident"
    ]
    assert len(failure_gaps) == 1
    assert failure_gaps[0]["occurrence_count"] == 2

    log.append(ZfEvent(
        id="evt-pass",
        type="verify.passed",
        task_id="TASK-A",
        payload={"run_id": "run-incidents"},
    ))
    settled = build_goal_dossier(state_dir, "run-incidents", now=NOW)

    assert settled["incident_history"][0]["status"] == "resolved"
    assert settled["incident_history"][0]["resolved_by_event_id"] == "evt-pass"
    assert not [
        gap for gap in settled["gaps"]
        if gap["type"] == "failure_incident"
    ]


def test_goal_dossier_excludes_workflow_anchor_from_task_roadmap(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-A", title="A", status="done"))
    store.add(mark_workflow_fanout_anchor(Task(
        id="TASK-ROOT", title="workflow root", status="backlog",
    )))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        id="evt-start",
        type="run.goal.started",
        payload={"run_id": "run-anchor", "goal_id": "GOAL-A"},
    ))
    for task_id in ("TASK-ROOT", "TASK-A"):
        log.append(ZfEvent(
            id=f"evt-{task_id}",
            type="task.assigned",
            task_id=task_id,
            payload={"run_id": "run-anchor"},
        ))
    log.append(ZfEvent(
        id="evt-completed",
        type="run.goal.completed",
        payload={
            "run_id": "run-anchor",
            "goal_id": "GOAL-A",
            "completed_task_ids": ["TASK-A"],
        },
    ))

    dossier = build_goal_dossier(state_dir, "run-anchor", now=NOW)

    assert dossier["state"]["task_counts"] == {
        "total": 1, "terminal": 1, "open": 0,
    }
    assert dossier["roadmap"]["task_order"] == ["TASK-A"]
    assert dossier["roadmap"]["workflow_anchor_task_ids"] == ["TASK-ROOT"]
    assert not [
        gap for gap in dossier["gaps"]
        if gap.get("task_id") == "TASK-ROOT"
    ]


def test_goal_dossier_excludes_workflow_managed_parent(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-A", title="A", status="done"))
    store.add(mark_workflow_managed_task(Task(
        id="TASK-PARENT",
        title="operator-created workflow parent",
        status="done",
    )))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        id="evt-start",
        type="run.goal.started",
        payload={"run_id": "run-managed", "goal_id": "GOAL-M"},
    ))
    log.append(ZfEvent(
        id="evt-accepted",
        type="workflow.invoke.accepted",
        task_id="TASK-PARENT",
        correlation_id="run-managed",
        payload={
            "run_id": "run-managed",
            "workflow_run_id": "run-managed",
            "task_id": "TASK-PARENT",
        },
    ))
    log.append(ZfEvent(
        id="evt-task",
        type="task.assigned",
        task_id="TASK-A",
        payload={"run_id": "run-managed"},
    ))
    log.append(ZfEvent(
        id="evt-completed",
        type="run.goal.completed",
        payload={
            "run_id": "run-managed",
            "goal_id": "GOAL-M",
            "completed_task_ids": ["TASK-A"],
        },
    ))

    dossier = build_goal_dossier(state_dir, "run-managed", now=NOW)

    assert dossier["state"]["task_counts"] == {
        "total": 1, "terminal": 1, "open": 0,
    }
    assert dossier["roadmap"]["task_order"] == ["TASK-A"]
    assert dossier["roadmap"]["workflow_anchor_task_ids"] == ["TASK-PARENT"]


def test_workflow_anchor_classification_recovers_archived_managed_parent(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json")
    events = [
        ZfEvent(
            id="evt-created",
            type="task.created",
            task_id="TASK-PARENT",
            payload={"task_id": "TASK-PARENT", "source": "kanban-agent"},
        ),
        ZfEvent(
            id="evt-accepted",
            type="workflow.invoke.accepted",
            task_id="TASK-PARENT",
            payload={
                "run_id": "run-managed",
                "workflow_run_id": "run-managed",
                "task_id": "TASK-PARENT",
            },
        ),
    ]

    assert workflow_anchor_task_ids(
        state_dir,
        ["TASK-PARENT"],
        events,
    ) == ["TASK-PARENT"]


@pytest.mark.parametrize("flow_kind", ["workflow", "prd"])
def test_goal_dossier_excludes_unmaterialized_workflow_anchor(
    tmp_path: Path,
    flow_kind: str,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json")
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        id="evt-start",
        type="run.goal.started",
        payload={"run_id": "run-workflow", "goal_id": "GOAL-W"},
    ))
    log.append(ZfEvent(
        id="evt-invoke",
        type="workflow.invoke.requested",
        task_id="FLOW-REQ-W",
        correlation_id="run-workflow",
        payload={
            "run_id": "run-workflow",
            "workflow_run_id": "run-workflow",
            "goal_id": "GOAL-W",
            "flow_kind": flow_kind,
            "task_id": "FLOW-REQ-W",
            "source": "workflow-submit",
            "light_entry_trigger": (
                "prd.requested" if flow_kind == "prd" else ""
            ),
        },
    ))
    log.append(ZfEvent(
        id="evt-blocked",
        type="run.goal.blocked",
        payload={
            "run_id": "run-workflow",
            "goal_id": "GOAL-W",
            "reason": "operator pause",
        },
    ))

    dossier = build_goal_dossier(state_dir, "run-workflow", now=NOW)

    assert dossier["state"]["task_counts"] == {
        "total": 0, "terminal": 0, "open": 0,
    }
    assert dossier["roadmap"]["task_order"] == []
    assert dossier["roadmap"]["workflow_anchor_task_ids"] == ["FLOW-REQ-W"]
    assert not [
        gap
        for gap in dossier["gaps"]
        if gap.get("task_id") == "FLOW-REQ-W"
    ]


@pytest.mark.parametrize(
    ("store_task", "creation_source", "expected"),
    [
        (True, "", []),
        (False, "task_map_materialization", []),
        (False, "workflow_invoke_bootstrap", ["TASK-TARGET"]),
    ],
)
def test_workflow_anchor_classification_respects_canonical_task_facts(
    tmp_path: Path,
    store_task: bool,
    creation_source: str,
    expected: list[str],
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    if store_task:
        store.add(Task(id="TASK-TARGET", title="Delivery task"))
    events = [ZfEvent(
        id="evt-invoke",
        type="workflow.invoke.requested",
        task_id="TASK-TARGET",
        payload={
            "flow_kind": "workflow",
            "task_id": "TASK-TARGET",
        },
    )]
    if creation_source:
        events.append(ZfEvent(
            id="evt-created",
            type="task.created",
            task_id="TASK-TARGET",
            payload={
                "task_id": "TASK-TARGET",
                "source": creation_source,
            },
        ))

    assert workflow_anchor_task_ids(
        state_dir,
        ["TASK-TARGET"],
        events,
    ) == expected


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
