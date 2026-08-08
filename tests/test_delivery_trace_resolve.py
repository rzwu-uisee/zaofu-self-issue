"""Integration smoke test for the delivery-trace resolver (loading layer).

The pure builders are unit-tested elsewhere; here we verify the disk-loading
resolver joins kanban + events + the accepted task-map, and that it performs
NO writes (projection-boundary invariant, doc 65 §3.6 / §20.5).
"""

from __future__ import annotations

import json
from pathlib import Path

from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.delivery_trace_resolve import resolve_delivery_trace

_NOW = "2026-05-29T00:00:00+00:00"


def _seed(state_dir: Path) -> None:
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="T1", title="schema", status="done",
                   assigned_to="dev-1",
                   contract=TaskContract(feature_id="F-1", owner_role="dev",
                                         wave=1, spec_ref="docs/spec.md",
                                         critic_event_id="evt-critic")))
    store.add(Task(id="T2", title="router", status="in_progress",
                   assigned_to="dev-2",
                   contract=TaskContract(feature_id="F-1", owner_role="dev", wave=2)))
    store.add(Task(id="OTHER", title="unrelated", status="backlog",
                   contract=TaskContract(feature_id="F-OTHER")))

    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(type="feature.created", id="evt-feat", task_id="",
                       payload={"feature_id": "F-1", "summary": "build api"}))
    log.append(ZfEvent(type="dev.build.done", id="evt-build", task_id="T1"))

    artifacts = state_dir / "artifacts" / "F-1"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "task_map.json").write_text(json.dumps({
        "schema_version": "task-map.v1",
        "feature_id": "F-1",
        "tasks": [
            {"task_id": "T1", "title": "schema", "owner_role": "dev", "wave": 1},
            {"task_id": "T2", "title": "router", "owner_role": "dev",
             "wave": 2, "blocked_by": ["T1"]},
        ],
    }), encoding="utf-8")


def test_resolve_joins_kanban_events_and_task_map(tmp_path: Path):
    _seed(tmp_path)
    trace = resolve_delivery_trace(
        state_dir=tmp_path, config=None, generated_at=_NOW,
        project_id="proj", feature_id="F-1",
    )
    assert trace["schema_version"] == "delivery-trace.v1"
    assert trace["feature_id"] == "F-1"
    assert trace["status"] == "in_progress"
    # only F-1 tasks are included (OTHER excluded)
    eg = trace["execution_graph"]
    assert {n["task_id"] for n in eg["nodes"]} == {"T1", "T2"}
    assert eg["done_count"] == 1 and eg["in_progress_count"] == 1
    # task-map located from artifacts/F-1/task_map.json
    assert trace["task_map"]["status"] == "accepted"
    assert eg["edges"] and eg["edges"][0]["from"] == "T1"
    # evidence joined from events
    t1 = next(n for n in eg["nodes"] if n["task_id"] == "T1")
    assert "evt-build" in t1["actual"]["evidence_events"]
    # idea + plan best-effort
    assert trace["idea"]["summary"] == "build api"
    assert trace["plan"]["spec_ref"] == "docs/spec.md"


def test_resolve_includes_archived_done_task_map_nodes(tmp_path: Path):
    store = TaskStore(tmp_path / "kanban.json")
    store.add(Task(
        id="T1",
        title="schema",
        status="in_progress",
        assigned_to="dev-1",
        contract=TaskContract(feature_id="F-1", owner_role="dev", wave=1),
    ))
    store.update("T1", status="done")
    store.add(Task(
        id="T2",
        title="router",
        status="in_progress",
        assigned_to="dev-2",
        blocked_by=["T1"],
        contract=TaskContract(feature_id="F-1", owner_role="dev", wave=2),
    ))
    log = event_log_from_project(tmp_path, config=None, warn=False)
    log.append(ZfEvent(type="dev.build.done", id="evt-build", task_id="T1"))
    artifacts = tmp_path / "artifacts" / "F-1"
    artifacts.mkdir(parents=True)
    (artifacts / "task_map.json").write_text(json.dumps({
        "schema_version": "task-map.v1",
        "feature_id": "F-1",
        "tasks": [
            {"task_id": "T1", "title": "schema", "owner_role": "dev", "wave": 1},
            {"task_id": "T2", "title": "router", "owner_role": "dev",
             "wave": 2, "blocked_by": ["T1"]},
        ],
    }), encoding="utf-8")

    trace = resolve_delivery_trace(
        state_dir=tmp_path, config=None, generated_at=_NOW, feature_id="F-1",
    )

    nodes = {node["task_id"]: node for node in trace["execution_graph"]["nodes"]}
    assert nodes["T1"]["actual"]["status"] == "done"
    edges = {
        (edge["from"], edge["to"]): edge
        for edge in trace["execution_graph"]["edges"]
    }
    assert edges[("T1", "T2")]["status"] == "satisfied"
    assert not any(
        item.get("kind") == "kanban_task_missing" and item.get("task_id") == "T1"
        for item in trace["execution_graph"].get("diagnostics", [])
    )


def test_resolve_prefers_feature_current_delivery_bundle(tmp_path: Path):
    store = TaskStore(tmp_path / "kanban.json")
    store.add(Task(
        id="T1",
        title="bundle task",
        status="in_progress",
        contract=TaskContract(feature_id="F-1", owner_role="dev", wave=1),
    ))
    event_log_from_project(tmp_path, config=None, warn=False)
    bundle_dir = tmp_path / "artifacts" / "F-1" / "v2"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "task_map.json").write_text(json.dumps({
        "schema_version": "task-map.v1",
        "feature_id": "F-1",
        "tasks": [
            {"task_id": "T1", "title": "bundle task", "owner_role": "dev", "wave": 1},
        ],
    }), encoding="utf-8")
    refs = tmp_path / "refs"
    refs.mkdir()
    (refs / "feature-index.json").write_text(json.dumps({
        "F-1": {
            "feature_id": "F-1",
            "current_bundle": {
                "schema_version": "feature-delivery-bundle.v1",
                "feature_id": "F-1",
                "current_task_map_ref": "artifacts/F-1/v2/task_map.json",
                "current_source_index_ref": "artifacts/F-1/v2/source_index.json",
            },
        },
    }), encoding="utf-8")

    trace = resolve_delivery_trace(
        state_dir=tmp_path,
        config=None,
        generated_at=_NOW,
        feature_id="F-1",
    )

    assert trace["task_map"]["status"] == "accepted"
    assert trace["current_bundle"]["current_task_map_ref"] == "artifacts/F-1/v2/task_map.json"
    assert trace["current_task_map_ref"] == "artifacts/F-1/v2/task_map.json"
    assert trace["task_map"]["task_map_ref"].endswith("artifacts/F-1/v2/task_map.json")
    assert {n["task_id"] for n in trace["execution_graph"]["nodes"]} == {"T1"}


def test_resolve_feature_id_from_task_id(tmp_path: Path):
    _seed(tmp_path)
    # task-node path: feature resolved from the task's contract
    trace = resolve_delivery_trace(
        state_dir=tmp_path, config=None, generated_at=_NOW, task_id="T2",
    )
    assert trace["feature_id"] == "F-1"


def test_resolver_does_not_mutate_state(tmp_path: Path):
    _seed(tmp_path)
    kanban = tmp_path / "kanban.json"
    events = tmp_path / "events.jsonl"
    before = (kanban.stat().st_mtime_ns, events.stat().st_mtime_ns,
              kanban.read_bytes(), events.read_bytes())
    resolve_delivery_trace(state_dir=tmp_path, config=None,
                           generated_at=_NOW, feature_id="F-1")
    after = (kanban.stat().st_mtime_ns, events.stat().st_mtime_ns,
             kanban.read_bytes(), events.read_bytes())
    assert before == after  # projection performed zero writes


def test_resolve_degrades_without_task_map(tmp_path: Path):
    store = TaskStore(tmp_path / "kanban.json")
    store.add(Task(id="L1", title="legacy", status="in_progress",
                   contract=TaskContract(feature_id="F-1")))
    event_log_from_project(tmp_path, config=None, warn=False)  # creates nothing yet
    trace = resolve_delivery_trace(state_dir=tmp_path, config=None,
                                   generated_at=_NOW, feature_id="F-1")
    assert trace["task_map"]["status"] == "missing"
    kinds = {d["kind"] for d in trace["diagnostics"]}
    assert "task_map_missing" in kinds


def test_resolve_uses_admitted_task_map_event_binding(tmp_path: Path):
    store = TaskStore(tmp_path / "kanban.json")
    store.add(Task(
        id="T1",
        title="event-bound task",
        status="done",
        contract=TaskContract(
            feature_id="F-RUN",
            contract_revision="REV-1",
            goal_claim_ids=["CLAIM-1"],
        ),
    ))
    task_map_ref = "artifacts/fanouts/plan-1/task_map.json"
    task_map_path = tmp_path / task_map_ref
    task_map_path.parent.mkdir(parents=True)
    task_map_path.write_text(json.dumps({
        "schema_version": "task-map.v1",
        "feature_id": "semantic-product-id",
        "target_commit": "abc123",
        "goal_claims": [{
            "goal_claim_id": "CLAIM-1",
            "text": "event-backed plan is visible",
            "mandatory": True,
        }],
        "tasks": [{
            "task_id": "T1",
            "title": "event-bound task",
            "goal_claim_ids": ["CLAIM-1"],
        }],
    }), encoding="utf-8")
    log = event_log_from_project(tmp_path, config=None, warn=False)
    log.append(ZfEvent(
        id="evt-map-admitted",
        type="task_map.admitted",
        payload={
            "feature_id": "F-RUN",
            "workflow_run_id": "RUN-1",
            "task_map_generation": "GEN-1",
            "task_map_ref": task_map_ref,
        },
    ))

    trace = resolve_delivery_trace(
        state_dir=tmp_path,
        config=None,
        generated_at=_NOW,
        project_id="proj",
        feature_id="F-RUN",
    )

    assert trace["task_map"]["status"] == "accepted"
    assert trace["task_map"]["task_map_ref"] == task_map_ref
    assert not any(
        item.get("kind") == "task_map_missing"
        for item in trace["diagnostics"]
    )
    identity = trace["goal_coverage_graph"]["identity"]
    assert identity["goal_id"] == "F-RUN"
    assert identity["workflow_run_id"] == "RUN-1"
    assert identity["task_map_generation"] == "GEN-1"


def test_resolve_hydrates_ref_backed_task_verification(tmp_path: Path):
    store = TaskStore(tmp_path / "kanban.json")
    store.add(Task(
        id="T1",
        title="verified task",
        status="done",
        contract=TaskContract(
            feature_id="F-RUN",
            contract_revision="REV-1",
            goal_claim_ids=["CLAIM-1"],
        ),
    ))
    task_map_ref = "artifacts/fanouts/plan-1/task_map.json"
    task_map_path = tmp_path / task_map_ref
    task_map_path.parent.mkdir(parents=True)
    task_map_path.write_text(json.dumps({
        "schema_version": "task-map.v1",
        "feature_id": "F-RUN",
        "workflow_run_id": "RUN-1",
        "task_map_generation": "GEN-1",
        "target_commit": "abc123",
        "goal_claims": [{
            "goal_claim_id": "CLAIM-1",
            "text": "verification sidecar is projected",
            "mandatory": True,
        }],
        "tasks": [{
            "task_id": "T1",
            "title": "verified task",
            "goal_claim_ids": ["CLAIM-1"],
        }],
    }), encoding="utf-8")
    verification_result = {
        "schema_version": "verification-result.v1",
        "workflow_run_id": "RUN-1",
        "task_id": "T1",
        "contract_revision": "REV-1",
        "task_map_generation": "GEN-1",
        "base_commit": "base123",
        "task_ref": "task:T1",
        "contract_snapshot_ref": "artifact://contract-t1",
        "contract_snapshot_digest": "contract-digest",
        "target_snapshot_ref": "artifact://target-t1",
        "target_commit": "abc123",
        "target_snapshot_digest": "target-digest",
        "verification_owner": "task_verify",
        "verification_tier": "task_non_smoke",
        "execution_status": "completed",
        "verdict": "passed",
        "failure_class": "none",
        "summary": "verification passed",
        "evidence_refs": ["artifact://verify-t1"],
        "requirement_results": [{
            "acceptance_id": "AC-1",
            "status": "passed",
            "verification_owner": "task_verify",
            "verification_tier": "task_non_smoke",
            "evidence_refs": ["artifact://verify-t1"],
        }],
    }
    control_result_ref = write_immutable_json_sidecar(
        tmp_path,
        verification_result,
        root="call-results/control/verification-result.v1",
        kind="call_control_result",
        schema_version="verification-result.v1",
        created_by="test",
        source_event_id="evt-task-verify",
    )
    log = event_log_from_project(tmp_path, config=None, warn=False)
    log.append(ZfEvent(
        id="evt-map-admitted",
        type="task_map.admitted",
        payload={
            "feature_id": "F-RUN",
            "workflow_run_id": "RUN-1",
            "task_map_generation": "GEN-1",
            "task_map_ref": task_map_ref,
        },
    ))
    log.append(ZfEvent(
        id="evt-task-verify",
        type="task.pipeline.verify.completed",
        task_id="T1",
        payload={
            "semantic_result_profile": {
                "profile_id": "task-verify",
                "revision": "1",
            },
            "output_profile_id": "task-verify",
            "output_profile_revision": "1",
            "control_result_ref": control_result_ref,
        },
    ))
    log.append(ZfEvent(
        id="evt-task-verify-admitted",
        type="workflow.call.result.admitted",
        task_id="T1",
        payload={
            "admission_status": "admitted",
            "control_result_schema": "verification-result.v1",
            "source_event_id": "evt-task-verify",
            "envelope_ref": control_result_ref,
        },
    ))

    trace = resolve_delivery_trace(
        state_dir=tmp_path,
        config=None,
        generated_at=_NOW,
        project_id="proj",
        feature_id="F-RUN",
    )

    graph = trace["goal_coverage_graph"]
    assert graph["summary"]["claims_with_current_results"] == 1
    claim = next(
        item for item in graph["nodes"]
        if item.get("goal_claim_id") == "CLAIM-1"
    )
    assert claim["task_verification"] == "passed"
    assert not any(
        item.get("kind") == "control_result_ref_unreadable"
        for item in trace["diagnostics"]
    )
