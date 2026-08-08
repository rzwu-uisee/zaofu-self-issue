from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.runtime.goal_claim_set import build_goal_claim_set
from zf.runtime.goal_coverage_graph import build_goal_coverage_graph


def _task_map() -> dict:
    return {
        "schema_version": "task-map.v1",
        "feature_id": "F-GOAL",
        "workflow_run_id": "RUN-1",
        "task_map_generation": "GEN-2",
        "target_commit": "abc123",
        "goal_claims": [
            {"goal_claim_id": "CLAIM-A", "text": "Authentication is safe", "mandatory": True},
            {"goal_claim_id": "CLAIM-B", "text": "Replay is deterministic", "mandatory": True},
        ],
        "tasks": [
            {
                "task_id": "TASK-A",
                "title": "Implement auth",
                "goal_claim_ids": ["CLAIM-A"],
            },
        ],
    }


def _tasks() -> dict[str, Task]:
    return {
        "TASK-A": Task(
            id="TASK-A",
            title="Implement auth",
            status="done",
            contract=TaskContract(
                feature_id="F-GOAL",
                contract_revision="REV-2",
                goal_claim_ids=["CLAIM-A"],
            ),
        ),
    }


def _claim_set_digest(task_map: dict | None = None) -> str:
    source = task_map or _task_map()
    claim_set = build_goal_claim_set(
        source,
        workflow_run_id=str(source.get("workflow_run_id") or "RUN-1"),
        goal_id=str(source.get("feature_id") or "F-GOAL"),
        task_map_generation=str(source.get("task_map_generation") or "GEN-2"),
    )
    return str(claim_set["claim_set_digest"])


def _verification_result(**overrides: object) -> dict:
    result = {
        "schema_version": "verification-result.v1",
        "workflow_run_id": "RUN-1",
        "task_id": "TASK-A",
        "contract_revision": "REV-2",
        "task_map_generation": "GEN-2",
        "base_commit": "base123",
        "task_ref": "task:TASK-A",
        "contract_snapshot_ref": "artifact://contract-a",
        "contract_snapshot_digest": "contract-digest",
        "target_snapshot_ref": "artifact://target-a",
        "target_commit": "abc123",
        "target_snapshot_digest": "target-digest",
        "verification_owner": "task_verify",
        "verification_tier": "task_non_smoke",
        "execution_status": "completed",
        "verdict": "passed",
        "failure_class": "none",
        "summary": "verification passed",
        "evidence_refs": ["artifact://verify-a"],
        "requirement_results": [{
            "acceptance_id": "AC-1",
            "status": "passed",
            "verification_owner": "task_verify",
            "verification_tier": "task_non_smoke",
            "evidence_refs": ["artifact://verify-a"],
        }],
    }
    result.update(overrides)
    return result


def _closure_result(**overrides: object) -> dict:
    result = {
        "schema_version": "goal-closure-result.v1",
        "workflow_run_id": "RUN-1",
        "goal_id": "F-GOAL",
        "flow_kind": "issue",
        "task_map_generation": "GEN-2",
        "target_commit": "abc123",
        "objective_ref": "objective:F-GOAL",
        "goal_claim_set_ref": "artifact://claims",
        "goal_claim_set_digest": _claim_set_digest(),
        "planning_result_ref": "artifact://planning",
        "candidate_ref": "git:abc123",
        "closure_fact_ref": "artifact://closure-current",
        "closure_fact_digest": "closure-digest",
        "verdict": "rejected",
        "summary": "one claim remains open",
        "goal_coverage": [
            {
                "goal_claim_id": "CLAIM-A",
                "status": "closed",
                "supporting_result_refs": ["artifact://verify-a"],
            },
            {
                "goal_claim_id": "CLAIM-B",
                "status": "open",
                "supporting_result_refs": [],
            },
        ],
        "input_result_refs": ["artifact://verify-a"],
        "open_gap_refs": ["artifact://gap-b"],
        "recommended_action": "gap_plan",
    }
    result.update(overrides)
    return result


def _admission_event(
    source_event_id: str,
    *,
    schema: str = "verification-result.v1",
    task_id: str | None = "TASK-A",
) -> ZfEvent:
    return ZfEvent(
        id=f"admit-{source_event_id}",
        type="workflow.call.result.admitted",
        task_id=task_id,
        payload={
            "schema_version": "call-result-admission.v1",
            "admission_status": "admitted",
            "control_result_schema": schema,
            "source_event_id": source_event_id,
            "envelope_ref": {
                "ref": f"artifact://admitted/{source_event_id}",
                "sha256": f"digest-{source_event_id}",
            },
        },
    )


def test_goal_coverage_graph_projects_covered_and_uncovered_claims() -> None:
    graph = build_goal_coverage_graph(
        task_map=_task_map(),
        tasks=_tasks(),
        events=[],
        project_id="zaofu",
        feature_id="F-GOAL",
        task_map_ref=".zf/artifacts/F-GOAL/task_map.json",
    )

    assert graph["schema_version"] == "goal-coverage-graph.v1"
    assert graph["coverage_mode"] == "explicit"
    assert graph["summary"] == {
        "mandatory_claims": 2,
        "planned_claims": 1,
        "claims_with_current_results": 0,
        "closed_claims": 0,
        "open_gaps": 0,
    }
    claims = {
        node["goal_claim_id"]: node
        for node in graph["nodes"]
        if node["kind"] == "goal_claim"
    }
    assert claims["CLAIM-A"]["plan_coverage"] == "covered"
    assert claims["CLAIM-A"]["execution"] == "done"
    assert claims["CLAIM-A"]["task_verification"] == "unverified"
    assert claims["CLAIM-A"]["closure"] == "unknown"
    assert claims["CLAIM-B"]["plan_coverage"] == "uncovered"
    assert any(
        item["code"] == "mandatory_claim_uncovered"
        and item["goal_claim_id"] == "CLAIM-B"
        for item in graph["diagnostics"]
    )


def test_goal_coverage_graph_marks_old_verification_result_stale() -> None:
    event = ZfEvent(
        id="verify-old",
        type="verify.passed",
        task_id="TASK-A",
        payload={
            "verification_result": _verification_result(
                task_map_generation="GEN-1",
                contract_revision="REV-1",
                target_commit="old123",
                summary="old verification",
                evidence_refs=["artifact://verify-old"],
            ),
        },
    )

    graph = build_goal_coverage_graph(
        task_map=_task_map(),
        tasks=_tasks(),
        events=[(1, event), (2, _admission_event(event.id))],
        project_id="zaofu",
        feature_id="F-GOAL",
        task_map_ref=".zf/artifacts/F-GOAL/task_map.json",
    )

    claim = next(
        node for node in graph["nodes"]
        if node.get("goal_claim_id") == "CLAIM-A"
    )
    assert claim["task_verification"] == "stale"
    assert graph["summary"]["claims_with_current_results"] == 0


def test_goal_coverage_graph_uses_closure_rows_without_rejudging() -> None:
    closure = _closure_result()
    events = [
        (1, ZfEvent(
            id="verify-a",
            type="verify.passed",
            task_id="TASK-A",
            payload={
                "verification_result": _verification_result(),
            },
        )),
        (2, _admission_event("verify-a")),
        (3, ZfEvent(
            id="closure-1",
            type="goal.closure.rejected",
            payload={"goal_closure_result": closure},
        )),
        (4, _admission_event(
            "closure-1",
            schema="goal-closure-result.v1",
            task_id=None,
        )),
    ]

    first = build_goal_coverage_graph(
        task_map=_task_map(), tasks=_tasks(), events=events,
        project_id="zaofu", feature_id="F-GOAL", task_map_ref="task-map.json",
    )
    second = build_goal_coverage_graph(
        task_map=_task_map(), tasks=_tasks(), events=events,
        project_id="zaofu", feature_id="F-GOAL", task_map_ref="task-map.json",
    )

    claims = {
        node["goal_claim_id"]: node
        for node in first["nodes"]
        if node["kind"] == "goal_claim"
    }
    assert claims["CLAIM-A"]["task_verification"] == "passed"
    assert claims["CLAIM-A"]["closure"] == "closed"
    assert claims["CLAIM-B"]["closure"] == "open"
    assert first["summary"]["closed_claims"] == 1
    assert first["summary"]["open_gaps"] == 1
    assert first == second


def test_goal_coverage_graph_scopes_claim_results_to_covering_tasks() -> None:
    task_map = _task_map()
    task_map["tasks"].append({
        "task_id": "TASK-B",
        "title": "Implement replay",
        "goal_claim_ids": ["CLAIM-B"],
    })
    tasks = _tasks()
    tasks["TASK-B"] = Task(
        id="TASK-B",
        title="Implement replay",
        status="done",
        contract=TaskContract(
            feature_id="F-GOAL",
            contract_revision="REV-B",
            goal_claim_ids=["CLAIM-B"],
        ),
    )
    verify_a = ZfEvent(
        id="verify-a",
        type="verify.passed",
        task_id="TASK-A",
        payload={"verification_result": _verification_result()},
    )
    verify_b = ZfEvent(
        id="verify-b",
        type="verify.passed",
        task_id="TASK-B",
        payload={"verification_result": _verification_result(
            task_id="TASK-B",
            contract_revision="REV-B",
            contract_snapshot_ref="artifact://contract-b",
            target_snapshot_ref="artifact://target-b",
            evidence_refs=["artifact://evidence-b"],
        )},
    )
    result_a = "artifact://admitted/verify-a"
    result_b = "artifact://admitted/verify-b"
    closure_result = _closure_result(
        goal_claim_set_digest=_claim_set_digest(task_map),
        verdict="passed",
        goal_coverage=[
            {
                "goal_claim_id": "CLAIM-A",
                "status": "closed",
                "supporting_result_refs": [result_a, result_b],
            },
            {
                "goal_claim_id": "CLAIM-B",
                "status": "closed",
                "supporting_result_refs": [result_a, result_b],
            },
        ],
        input_result_refs=[result_a, result_b],
        open_gap_refs=[],
        recommended_action="complete",
    )
    closure = ZfEvent(
        id="closure-both",
        type="goal.closure.synthesized",
        payload={"goal_closure_result": closure_result},
    )
    graph = build_goal_coverage_graph(
        task_map=task_map,
        tasks=tasks,
        events=[
            (1, verify_a),
            (2, _admission_event(verify_a.id)),
            (3, verify_b),
            (4, _admission_event(verify_b.id, task_id="TASK-B")),
            (5, closure),
            (6, _admission_event(
                closure.id,
                schema="goal-closure-result.v1",
                task_id=None,
            )),
        ],
        project_id="zaofu",
        feature_id="F-GOAL",
        task_map_ref="task-map.json",
    )

    claims = {
        node["goal_claim_id"]: node
        for node in graph["nodes"]
        if node.get("kind") == "goal_claim"
    }
    assert claims["CLAIM-A"]["supporting_result_refs"] == [result_a]
    assert claims["CLAIM-B"]["supporting_result_refs"] == [result_b]


def test_goal_coverage_graph_prefers_admitted_candidate_over_plan_base() -> None:
    task_map = _task_map()
    task_map["target_commit"] = "base123"
    verify = ZfEvent(
        id="verify-candidate",
        type="verify.passed",
        task_id="TASK-A",
        payload={
            "verification_result": _verification_result(
                target_commit="candidate123",
            ),
        },
    )
    closure = ZfEvent(
        id="closure-candidate",
        type="goal.closure.rejected",
        payload={
            "goal_closure_result": _closure_result(
                target_commit="candidate123",
            ),
        },
    )
    events = [
        (1, verify),
        (2, _admission_event(verify.id)),
        (3, closure),
        (4, _admission_event(
            closure.id,
            schema="goal-closure-result.v1",
            task_id=None,
        )),
    ]

    graph = build_goal_coverage_graph(
        task_map=task_map,
        tasks=_tasks(),
        events=events,
        project_id="zaofu",
        feature_id="F-GOAL",
        task_map_ref="task-map.json",
    )

    claim = next(
        node for node in graph["nodes"]
        if node.get("goal_claim_id") == "CLAIM-A"
    )
    assert graph["identity"]["target_commit"] == "candidate123"
    assert claim["task_verification"] == "passed"
    assert claim["closure"] == "closed"
    assert not any(
        "mismatch:target_commit" in item.get("stale_reasons", [])
        for item in graph["diagnostics"]
    )


def test_goal_coverage_graph_uses_pinned_claim_set_body() -> None:
    task_map = _task_map()
    pinned = build_goal_claim_set(
        {
            "tasks": [{
                "task_id": "TASK-A",
                "acceptance_criteria": [
                    "CLAIM-PINNED: Preserve the accepted historical claim.",
                ],
            }],
        },
        workflow_run_id="RUN-1",
        goal_id="F-GOAL",
        task_map_generation="GEN-2",
    )
    pinned_ref_digest = "d" * 64
    closure = ZfEvent(
        id="closure-pinned",
        type="goal.closure.synthesized",
        payload={
            "goal_closure_result": _closure_result(
                goal_claim_set_digest=pinned_ref_digest,
                goal_coverage=[{
                    "goal_claim_id": "CLAIM-PINNED",
                    "status": "closed",
                    "supporting_result_refs": ["artifact://verify-a"],
                }],
                open_gap_refs=[],
                verdict="passed",
                recommended_action="complete",
            ),
        },
    )
    events = [
        (1, ZfEvent(
            type="goal.claim_set.pinned",
            payload={
                "workflow_run_id": "RUN-1",
                "goal_id": "F-GOAL",
                "task_map_generation": "GEN-2",
                "goal_claim_set_ref": "artifact://claims-pinned",
                "goal_claim_set_digest": pinned_ref_digest,
            },
        )),
        (2, closure),
        (3, _admission_event(
            closure.id,
            schema="goal-closure-result.v1",
            task_id=None,
        )),
    ]

    graph = build_goal_coverage_graph(
        task_map=task_map,
        tasks=_tasks(),
        events=events,
        project_id="zaofu",
        feature_id="F-GOAL",
        goal_claim_set=pinned,
    )

    claim = next(
        node for node in graph["nodes"]
        if node.get("kind") == "goal_claim"
    )
    assert graph["coverage_mode"] == "legacy_derived"
    assert graph["summary"]["closed_claims"] == 1
    assert claim["goal_claim_id"] == "CLAIM-PINNED"
    assert claim["plan_coverage"] == "covered"
    assert claim["closure"] == "closed"


def test_pinned_claim_set_supplies_generation_missing_from_task_map() -> None:
    task_map = _task_map()
    task_map.pop("task_map_generation")
    pinned = build_goal_claim_set(
        task_map,
        workflow_run_id="RUN-1",
        goal_id="F-GOAL",
        task_map_generation="GEN-PINNED",
    )

    graph = build_goal_coverage_graph(
        task_map=task_map,
        tasks=_tasks(),
        events=[],
        project_id="zaofu",
        feature_id="F-GOAL",
        task_map_ref="artifacts/task-map.json",
        goal_claim_set=pinned,
    )

    assert graph["identity"]["workflow_run_id"] == "RUN-1"
    assert graph["identity"]["task_map_generation"] == "GEN-PINNED"
    assert graph["summary"]["mandatory_claims"] > 0


def test_goal_coverage_graph_rejects_incomplete_typed_result_from_current_summary() -> None:
    event = ZfEvent(
        id="verify-incomplete",
        type="worker.reported",
        task_id="TASK-A",
        payload={
            "verification_result": {
                "schema_version": "verification-result.v1",
                "task_id": "TASK-A",
                "execution_status": "completed",
                "verdict": "passed",
                "evidence_refs": ["artifact://unadmitted"],
            },
        },
    )

    graph = build_goal_coverage_graph(
        task_map=_task_map(), tasks=_tasks(), events=[(1, event)],
        project_id="zaofu", feature_id="F-GOAL",
    )

    claim = next(node for node in graph["nodes"] if node.get("goal_claim_id") == "CLAIM-A")
    assert claim["task_verification"] == "stale"
    assert graph["summary"]["claims_with_current_results"] == 0
    diagnostic = next(
        item for item in graph["diagnostics"]
        if item["code"] == "stale_verification_result"
    )
    assert diagnostic["task_id"] == "TASK-A"
    assert "missing:workflow_run_id" in diagnostic["stale_reasons"]


def test_goal_coverage_graph_requires_admission_for_complete_typed_result() -> None:
    event = ZfEvent(
        id="verify-unadmitted",
        type="verify.passed",
        task_id="TASK-A",
        payload={"verification_result": _verification_result()},
    )

    graph = build_goal_coverage_graph(
        task_map=_task_map(), tasks=_tasks(), events=[(1, event)],
        project_id="zaofu", feature_id="F-GOAL",
    )

    claim = next(node for node in graph["nodes"] if node.get("goal_claim_id") == "CLAIM-A")
    diagnostic = next(
        item for item in graph["diagnostics"]
        if item["code"] == "stale_verification_result"
    )
    assert claim["task_verification"] == "stale"
    assert graph["summary"]["claims_with_current_results"] == 0
    assert diagnostic["stale_reasons"] == ["not_admitted"]


def test_goal_coverage_graph_keeps_current_result_when_stale_result_arrives_later() -> None:
    events = [
        (1, ZfEvent(
            id="verify-current",
            type="verify.passed",
            task_id="TASK-A",
            payload={"verification_result": _verification_result()},
        )),
        (2, _admission_event("verify-current")),
        (3, ZfEvent(
            id="verify-late-old",
            type="verify.passed",
            task_id="TASK-A",
            payload={"verification_result": _verification_result(
                task_map_generation="GEN-1",
                contract_revision="REV-1",
                target_commit="old123",
                summary="late old result",
            )},
        )),
        (4, _admission_event("verify-late-old")),
    ]

    graph = build_goal_coverage_graph(
        task_map=_task_map(), tasks=_tasks(), events=events,
        project_id="zaofu", feature_id="F-GOAL",
    )

    claim = next(node for node in graph["nodes"] if node.get("goal_claim_id") == "CLAIM-A")
    result = next(node for node in graph["nodes"] if node.get("kind") == "verification_result")
    assert claim["task_verification"] == "passed"
    assert graph["summary"]["claims_with_current_results"] == 1
    assert result["title"] == "verification passed"
    assert result["current"] is True


def test_goal_coverage_graph_keeps_current_closure_when_stale_closure_arrives_later() -> None:
    events = [
        (1, ZfEvent(
            id="closure-current",
            type="goal.closure.rejected",
            payload={"goal_closure_result": _closure_result()},
        )),
        (2, _admission_event(
            "closure-current",
            schema="goal-closure-result.v1",
            task_id=None,
        )),
        (3, ZfEvent(
            id="closure-late-old",
            type="goal.closure.rejected",
            payload={"goal_closure_result": _closure_result(
                task_map_generation="GEN-1",
                target_commit="old123",
                closure_fact_ref="artifact://closure-old",
            )},
        )),
        (4, _admission_event(
            "closure-late-old",
            schema="goal-closure-result.v1",
            task_id=None,
        )),
    ]

    graph = build_goal_coverage_graph(
        task_map=_task_map(), tasks=_tasks(), events=events,
        project_id="zaofu", feature_id="F-GOAL",
    )

    assert graph["summary"]["closed_claims"] == 1
    assert graph["summary"]["open_gaps"] == 1
    closure = next(node for node in graph["nodes"] if node.get("kind") == "goal_closure")
    assert closure["result_ref"] == "artifact://closure-current"
    assert any(item["code"] == "stale_goal_closure_result" for item in graph["diagnostics"])


def test_goal_coverage_graph_accepts_equivalent_task_map_generation_encodings() -> None:
    generation = "536e8b2e05d5acd499e79694e96973276da40d031bf62632b3d7395e360a1760"
    shortened_generation = f"task-map-{generation[:20]}"
    task_map = _task_map()
    task_map["task_map_generation"] = generation
    claim_set = build_goal_claim_set(
        task_map,
        workflow_run_id="RUN-1",
        goal_id="F-GOAL",
        task_map_generation=generation,
    )
    verification = ZfEvent(
        id="verify-short-generation",
        type="verify.passed",
        task_id="TASK-A",
        payload={
            "verification_result": _verification_result(
                task_map_generation=shortened_generation,
            ),
        },
    )
    closure = ZfEvent(
        id="closure-short-generation",
        type="goal.closure.rejected",
        payload={
            "goal_closure_result": _closure_result(
                task_map_generation=shortened_generation,
                goal_claim_set_digest=claim_set["claim_set_digest"],
            ),
        },
    )

    graph = build_goal_coverage_graph(
        task_map=task_map,
        tasks=_tasks(),
        events=[
            (1, verification),
            (2, _admission_event(verification.id)),
            (3, closure),
            (4, _admission_event(
                closure.id,
                schema="goal-closure-result.v1",
                task_id=None,
            )),
        ],
        project_id="zaofu",
        feature_id="F-GOAL",
        goal_claim_set=claim_set,
    )

    claim = next(
        node for node in graph["nodes"]
        if node.get("goal_claim_id") == "CLAIM-A"
    )
    assert claim["task_verification"] == "passed"
    assert claim["closure"] == "closed"
    assert not any(
        item["code"] in {
            "stale_verification_result",
            "stale_goal_closure_result",
        }
        for item in graph["diagnostics"]
    )


def test_goal_coverage_graph_planned_summary_counts_only_mandatory_claims() -> None:
    task_map = _task_map()
    task_map["goal_claims"].append({
        "goal_claim_id": "CLAIM-OPTIONAL",
        "text": "Optional documentation",
        "mandatory": False,
    })
    task_map["tasks"].append({
        "task_id": "TASK-OPTIONAL",
        "title": "Write optional docs",
        "goal_claim_ids": ["CLAIM-OPTIONAL"],
    })
    tasks = _tasks()
    tasks["TASK-OPTIONAL"] = Task(
        id="TASK-OPTIONAL",
        title="Write optional docs",
        status="done",
        contract=TaskContract(
            feature_id="F-GOAL",
            contract_revision="REV-1",
            goal_claim_ids=["CLAIM-OPTIONAL"],
        ),
    )

    graph = build_goal_coverage_graph(
        task_map=task_map, tasks=tasks, events=[],
        project_id="zaofu", feature_id="F-GOAL",
    )

    assert graph["summary"]["mandatory_claims"] == 2
    assert graph["summary"]["planned_claims"] == 1
