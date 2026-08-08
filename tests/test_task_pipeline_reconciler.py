from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    WorkflowConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime import task_pipeline_runtime
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.task_pipeline_reconciler import (
    TaskPipelineReconciler,
    task_pipeline_policy_partitions,
)
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


def _policy():
    return {
        "profile_id": "prd-flow-v4-task-pipeline",
        "profile_digest": "profile-sha",
        "mode": "shadow",
        "max_active_task_pipelines": 3,
        "pools": {
            "impl": {
                "capacity": 1,
                "role_instances": ["impl-1"],
                "capabilities": [],
                "worker_profiles": [],
            },
            "verify": {
                "capacity": 1,
                "role_instances": ["verify-1"],
                "capabilities": [],
                "worker_profiles": [],
            },
        },
        "backpressure": {
            "max_unverified_tasks": 2,
            "max_integration_queue": 2,
        },
        "integration_admission": {
            "default": "verify_admitted",
            "risk_review": {"enabled": False},
        },
    }


def _task(task_id: str, *, priority: int, blocked_by=None):
    return {
        "id": task_id,
        "status": "backlog",
        "priority": priority,
        "created_at": f"2026-01-0{priority}T00:00:00Z",
        "blocked_by": blocked_by or [],
        "contract": {},
    }


def _operation(task_id: str, stage: str, status: str, generation: int = 1):
    return {
        "task_id": task_id,
        "task_pipeline_stage": stage,
        "status": status,
        "operation_generation": generation,
        "operation_id": f"op-{task_id}-{stage}-{generation}",
    }


def test_shadow_replay_is_deterministic_and_does_not_mutate_inputs() -> None:
    tasks = [_task("C", priority=3), _task("A", priority=1), _task("B", priority=2)]
    operations = [
        _operation("A", "impl", "settled"),
        _operation("A", "verify", "running"),
    ]
    attempts = [{"attempt_id": "att-A", "status": "sent", "instance_id": "verify-1"}]
    originals = deepcopy((tasks, operations, attempts))
    reconciler = TaskPipelineReconciler()

    first = reconciler.reconcile(
        policy=_policy(), tasks=tasks, operations=operations, attempts=attempts
    )
    second = reconciler.reconcile(
        policy=_policy(),
        tasks=reversed(tasks),
        operations=reversed(operations),
        attempts=reversed(attempts),
    )

    assert first == second
    assert (tasks, operations, attempts) == originals
    assert first["fairness"]["ordered_task_ids"] == ["A", "B", "C"]
    assert first["occupancy"]["pools"]["verify"] == 1
    assert first["dispatchable"]["impl"] == [
        {
            "task_id": "B",
            "stage": "impl",
            "role_instance": "impl-1",
            "operation_generation": "1",
        }
    ]


def test_shadow_streams_impl_to_verify_without_waiting_for_other_tasks() -> None:
    tasks = [_task("A", priority=1), _task("B", priority=2)]
    projection = TaskPipelineReconciler().reconcile(
        policy=_policy(),
        tasks=tasks,
        operations=[_operation("A", "impl", "settled")],
        attempts=[],
    )

    assert projection["queues"]["verify_ready"] == ["A"]
    assert projection["dispatchable"]["verify"] == [
        {
            "task_id": "A",
            "stage": "verify",
            "role_instance": "verify-1",
            "operation_generation": "1",
        }
    ]
    assert projection["dispatchable"]["impl"] == [
        {
            "task_id": "B",
            "stage": "impl",
            "role_instance": "impl-1",
            "operation_generation": "1",
        }
    ]


def test_task_ref_rejection_opens_new_impl_generation_not_legacy_repair() -> None:
    policy = _policy()
    policy["max_rework_attempts"] = 2
    request = {
        "schema_version": "task-pipeline-impl-rework-request.v1",
        "fault": "task_ref_admission_rejected",
        "event_id": "evt-ref-rejected",
        "source_event_id": "evt-impl-result",
        "operation_id": "op-A-impl-1",
        "operation_generation": 1,
        "reason": "source commit changes outside task scope",
        "expected_action": "repair_source_scope_and_resubmit_typed_impl_result",
        "changed_files": ["app/server.mjs", "app/server.test.mjs"],
        "out_of_scope_files": ["app/server.test.mjs"],
        "dirty_files": [],
    }

    first = TaskPipelineReconciler().reconcile(
        policy=policy,
        tasks=[_task("A", priority=1)],
        operations=[_operation("A", "impl", "settled")],
        attempts=[],
        impl_rework_requests={"A": request},
    )

    assert first["tasks"][0]["stage"] == "impl_rework_ready"
    assert first["tasks"][0]["next_operation_generation"] == 2
    assert first["dispatchable"]["verify"] == []
    assert first["dispatchable"]["impl"] == [{
        "task_id": "A",
        "stage": "impl",
        "role_instance": "impl-1",
        "operation_generation": "2",
        "impl_rework_request": request,
    }]

    second = TaskPipelineReconciler().reconcile(
        policy=policy,
        tasks=[_task("A", priority=1)],
        operations=[
            _operation("A", "impl", "settled"),
            _operation("A", "impl", "settled", generation=2),
        ],
        attempts=[],
        impl_rework_requests={"A": request},
    )

    assert second["tasks"][0]["stage"] == "verify_ready"
    assert second["dispatchable"]["verify"][0]["operation_generation"] == "2"


def test_existing_pipeline_rework_does_not_require_fresh_global_capacity() -> None:
    policy = _policy()
    policy["max_active_task_pipelines"] = 1
    policy["max_rework_attempts"] = 2
    request = {
        "event_id": "evt-ref-rejected",
        "operation_generation": 1,
    }

    projection = TaskPipelineReconciler().reconcile(
        policy=policy,
        tasks=[_task("A", priority=1), _task("B", priority=2)],
        operations=[_operation("A", "impl", "settled")],
        attempts=[],
        impl_rework_requests={"A": request},
    )

    assert projection["capacity"]["available_task_pipelines"] == 0
    assert projection["dispatchable"]["impl"] == [{
        "task_id": "A",
        "stage": "impl",
        "role_instance": "impl-1",
        "operation_generation": "2",
        "impl_rework_request": request,
    }]


def test_dependency_and_backpressure_fail_closed() -> None:
    tasks = [
        _task("A", priority=1),
        _task("B", priority=2),
        _task("C", priority=3, blocked_by=["MISSING"]),
    ]
    operations = [
        _operation("A", "impl", "running"),
        _operation("B", "impl", "settled"),
    ]
    projection = TaskPipelineReconciler().reconcile(
        policy=_policy(), tasks=tasks, operations=operations, attempts=[]
    )

    assert projection["backpressure"]["unverified_limit_reached"] is True
    assert projection["dispatchable"]["impl"] == []
    c = next(item for item in projection["tasks"] if item["task_id"] == "C")
    assert c["stage"] == "dependency_blocked"
    assert c["blockers"] == ["MISSING"]


def test_integration_high_water_stops_new_impl_but_never_blocks_drain() -> None:
    policy = _policy()
    policy["backpressure"]["max_integration_queue"] = 1
    tasks = [_task("A", priority=1), _task("B", priority=2)]
    operations = [
        _operation("A", "impl", "settled"),
        _operation("A", "verify", "settled"),
    ]

    projection = TaskPipelineReconciler().reconcile(
        policy=policy,
        tasks=tasks,
        operations=operations,
        attempts=[],
    )

    assert projection["backpressure"]["integration_limit_reached"] is True
    assert projection["queues"]["integration_ready"] == ["A"]
    assert projection["dispatchable"]["impl"] == []


def test_cancelled_or_blocked_predecessor_does_not_unlock_dependency() -> None:
    predecessor = _task("A", priority=1)
    predecessor["status"] = "cancelled"
    dependent = _task("B", priority=2, blocked_by=["A"])

    projection = TaskPipelineReconciler().reconcile(
        policy=_policy(),
        tasks=[predecessor, dependent],
        operations=[],
        attempts=[],
    )

    dependent_view = next(
        item for item in projection["tasks"] if item["task_id"] == "B"
    )
    assert dependent_view["stage"] == "dependency_blocked"
    assert dependent_view["blockers"] == ["A"]


def _risk_policy():
    policy = _policy()
    policy["max_rework_attempts"] = 2
    policy["pools"]["acceptance_review"] = {
        "capacity": 1,
        "role_instances": ["reviewer-1"],
        "capabilities": [],
        "worker_profiles": [],
    }
    policy["integration_admission"] = {
        "default": "verify_admitted",
        "risk_review": {
            "enabled": True,
            "for_risks": ["high", "critical"],
        },
    }
    return policy


def _verified_risk_task():
    task = _task("RISK", priority=1)
    task["contract"] = {
        "risk_class": "high",
        "integration_admission_profile": "risk_review",
    }
    return task


def test_verify_admitted_default_is_zero_turn() -> None:
    projection = TaskPipelineReconciler().reconcile(
        policy=_policy(),
        tasks=[_task("A", priority=1)],
        operations=[
            _operation("A", "impl", "settled"),
            _operation("A", "verify", "settled"),
        ],
        attempts=[],
    )

    assert projection["queues"]["acceptance_review_ready"] == []
    assert projection["queues"]["integration_ready"] == ["A"]


def test_admitted_high_risk_task_dispatches_bounded_reviewer() -> None:
    projection = TaskPipelineReconciler().reconcile(
        policy=_risk_policy(),
        tasks=[_verified_risk_task()],
        operations=[
            _operation("RISK", "impl", "settled"),
            _operation("RISK", "verify", "settled"),
        ],
        attempts=[],
    )

    assert projection["dispatchable"]["acceptance_review"] == [{
        "task_id": "RISK",
        "stage": "acceptance_review",
        "role_instance": "reviewer-1",
        "operation_generation": "1",
    }]


def test_risk_reviewer_verdicts_route_mechanically() -> None:
    expected = {
        "admit": ("integration_ready", 1),
        "revise": ("impl_rework_ready", 2),
        "replan": ("replan_requested", 1),
        "block": ("blocked", 1),
    }
    for verdict, outcome in expected.items():
        review = _operation("RISK", "acceptance_review", "settled")
        review["semantic_verdict"] = verdict
        projection = TaskPipelineReconciler().reconcile(
            policy=_risk_policy(),
            tasks=[_verified_risk_task()],
            operations=[
                _operation("RISK", "impl", "settled"),
                _operation("RISK", "verify", "settled"),
                review,
            ],
            attempts=[],
        )
        view = projection["tasks"][0]
        assert (view["stage"], view["next_operation_generation"]) == outcome


def test_unadmitted_risk_profile_fails_closed_without_reviewer_turn() -> None:
    task = _verified_risk_task()
    task["contract"]["risk_class"] = "medium"
    projection = TaskPipelineReconciler().reconcile(
        policy=_risk_policy(),
        tasks=[task],
        operations=[
            _operation("RISK", "impl", "settled"),
            _operation("RISK", "verify", "settled"),
        ],
        attempts=[],
    )

    assert projection["tasks"][0]["stage"] == "admission_blocked"
    assert projection["dispatchable"]["acceptance_review"] == []


def test_blocking_dispatch_preparation_respects_global_pause(monkeypatch) -> None:
    runtime = SimpleNamespace(
        config=object(),
        _dispatch_globally_paused=lambda: True,
    )
    monkeypatch.setattr(
        task_pipeline_runtime,
        "task_pipeline_policy",
        lambda config: {"mode": "blocking"},
    )
    monkeypatch.setattr(
        task_pipeline_runtime,
        "task_pipeline_managed_task_ids",
        lambda value: {"TASK-A"},
    )
    reconciled = []
    monkeypatch.setattr(
        task_pipeline_runtime,
        "reconcile_task_pipeline_runtime",
        lambda value: reconciled.append(True),
    )

    decisions, managed = task_pipeline_runtime.prepare_task_pipeline_dispatch(
        runtime,
        candidate_task_ids=["TASK-A"],
    )

    assert decisions == []
    assert managed == {"TASK-A"}
    assert reconciled == []


def test_blocking_dispatch_fences_candidates_before_generation_admission(
    monkeypatch,
) -> None:
    """Task creation must stay side-effect free until the approved controller owns it."""

    runtime = SimpleNamespace(
        config=object(),
        _dispatch_globally_paused=lambda: False,
    )
    monkeypatch.setattr(
        task_pipeline_runtime,
        "task_pipeline_policy",
        lambda config: {"mode": "blocking"},
    )
    monkeypatch.setattr(
        task_pipeline_runtime,
        "task_pipeline_managed_task_ids",
        lambda value: set(),
    )
    monkeypatch.setattr(
        task_pipeline_runtime,
        "reconcile_task_pipeline_runtime",
        lambda value: [],
    )

    decisions, managed = task_pipeline_runtime.prepare_task_pipeline_dispatch(
        runtime,
        candidate_task_ids=["TASK-ISSUE", "TASK-REFACTOR"],
    )

    assert decisions == []
    assert managed == {"TASK-ISSUE", "TASK-REFACTOR"}


def test_multi_flow_blocking_config_fences_pre_admission_candidates() -> None:
    policies = {
        kind: {
            "task_pipeline": {
                "mode": "blocking",
                "profile_id": f"{kind}-v4",
                "profile_digest": kind[0] * 64,
            }
        }
        for kind in ("issue", "prd", "refactor")
    }
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            workflow=SimpleNamespace(
                flow_metadata={},
                flow_metadata_by_kind=policies,
            )
        ),
        event_log=SimpleNamespace(read_all=lambda: []),
        _dispatch_globally_paused=lambda: False,
    )

    decisions, managed = task_pipeline_runtime.prepare_task_pipeline_dispatch(
        runtime,
        candidate_task_ids=["TASK-PRD"],
    )

    assert decisions == []
    assert managed == {"TASK-PRD"}


def test_multi_flow_history_without_active_tasks_does_not_raise() -> None:
    policies = {
        kind: {
            "task_pipeline": {
                "mode": "blocking",
                "profile_id": f"{kind}-v4",
                "profile_digest": kind[0] * 64,
            }
        }
        for kind in ("issue", "prd")
    }
    contexts = {
        "TASK-ISSUE": {"flow_kind": "issue"},
        "TASK-PRD": {"flow_kind": "prd"},
    }
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            workflow=SimpleNamespace(
                flow_metadata={},
                flow_metadata_by_kind=policies,
            )
        ),
        event_log=SimpleNamespace(read_all=lambda: []),
        task_store=SimpleNamespace(
            get=lambda task_id: SimpleNamespace(status="done"),
        ),
    )

    with (
        patch.object(
            task_pipeline_runtime,
            "task_pipeline_generation_contexts",
            return_value=contexts,
        ),
        patch(
            "zf.runtime.task_pipeline_terminal."
            "reconcile_task_pipeline_terminals",
            return_value=[],
        ) as terminals,
        patch(
            "zf.runtime.task_pipeline_terminal."
            "reconcile_task_pipeline_freeze",
            return_value=[],
        ) as freeze,
        patch(
            "zf.runtime.task_pipeline_projection."
            "write_task_pipeline_projection",
        ) as projection,
    ):
        decisions = task_pipeline_runtime.reconcile_task_pipeline_runtime(runtime)

    assert decisions == []
    terminals.assert_called_once_with(runtime, generation_contexts=contexts)
    freeze.assert_called_once_with(runtime, generation_contexts=contexts)
    projection.assert_called_once_with(runtime)


def test_active_partition_freeze_uses_complete_generation_context() -> None:
    contexts = {
        "TASK-DONE": {"flow_kind": "prd"},
        "TASK-ACTIVE": {"flow_kind": "prd"},
    }
    tasks = {
        "TASK-DONE": SimpleNamespace(id="TASK-DONE", status="done"),
        "TASK-ACTIVE": SimpleNamespace(id="TASK-ACTIVE", status="backlog"),
    }
    runtime = SimpleNamespace(
        config=object(),
        event_log=SimpleNamespace(read_all=lambda: []),
        task_store=SimpleNamespace(get=lambda task_id: tasks.get(task_id)),
    )
    freeze_contexts: list[set[str]] = []

    with (
        patch.object(
            task_pipeline_runtime,
            "task_pipeline_generation_contexts",
            return_value=contexts,
        ),
        patch.object(
            task_pipeline_runtime,
            "task_pipeline_policy_partitions",
            return_value=[{
                "task_ids": ("TASK-ACTIVE",),
                "policy": {"mode": "blocking"},
            }],
        ),
        patch(
            "zf.runtime.task_pipeline_terminal."
            "reconcile_task_pipeline_terminals",
            return_value=[],
        ),
        patch(
            "zf.runtime.task_pipeline_terminal."
            "reconcile_task_pipeline_freeze",
            side_effect=lambda _runtime, *, generation_contexts: (
                freeze_contexts.append(set(generation_contexts)) or []
            ),
        ),
        patch(
            "zf.runtime.task_pipeline_recovery."
            "reconcile_task_pipeline_redrives",
            return_value=[],
        ),
        patch(
            "zf.runtime.task_attempt_runtime.task_attempt_store",
            return_value=SimpleNamespace(current_rows=lambda: []),
        ),
        patch(
            "zf.runtime.task_pipeline_acceptance."
            "reconcile_task_pipeline_acceptance_routes",
            return_value=[],
        ),
        patch(
            "zf.runtime.task_pipeline_integration."
            "reconcile_task_pipeline_integration",
            return_value=[],
        ),
        patch.object(
            TaskPipelineReconciler,
            "reconcile",
            return_value={"dispatchable": {}},
        ),
        patch(
            "zf.runtime.task_pipeline_projection."
            "write_task_pipeline_projection",
        ),
    ):
        task_pipeline_runtime.reconcile_task_pipeline_runtime(runtime)

    assert freeze_contexts == [set(contexts), set(contexts)]


def test_multi_flow_active_generations_keep_distinct_pipeline_policies() -> None:
    policies = {
        kind: {
            "task_pipeline": {
                "mode": "blocking",
                "profile_id": f"{kind}-v4",
                "profile_digest": kind[0] * 64,
            }
        }
        for kind in ("issue", "prd")
    }
    config = SimpleNamespace(workflow=SimpleNamespace(
        flow_metadata={},
        flow_metadata_by_kind=policies,
    ))
    contexts = {
        "TASK-ISSUE": {
            "flow_kind": "issue",
            "profile_id": "issue-v4",
            "profile_digest": "i" * 64,
        },
        "TASK-PRD-A": {
            "flow_kind": "prd",
            "profile_id": "prd-v4",
            "profile_digest": "p" * 64,
        },
        "TASK-PRD-B": {
            "flow_kind": "prd",
            "profile_id": "prd-v4",
            "profile_digest": "p" * 64,
        },
    }

    partitions = task_pipeline_policy_partitions(config, contexts)

    assert [
        (
            row["flow_kind"],
            row["policy"]["profile_id"],
            row["task_ids"],
        )
        for row in partitions
    ] == [
        ("issue", "issue-v4", ("TASK-ISSUE",)),
        ("prd", "prd-v4", ("TASK-PRD-A", "TASK-PRD-B")),
    ]


def test_multi_flow_active_generations_reconcile_without_legacy_fallback() -> None:
    policies = {
        kind: {
            "task_pipeline": {
                "mode": "blocking",
                "profile_id": f"{kind}-v4",
                "profile_digest": kind[0] * 64,
            }
        }
        for kind in ("issue", "prd")
    }
    contexts = {
        "TASK-ISSUE": {"flow_kind": "issue"},
        "TASK-PRD": {"flow_kind": "prd"},
    }
    appended: list[ZfEvent] = []
    runtime = SimpleNamespace(
        config=SimpleNamespace(workflow=SimpleNamespace(
            flow_metadata={},
            flow_metadata_by_kind=policies,
        )),
        event_log=SimpleNamespace(read_all=lambda: []),
        event_writer=SimpleNamespace(append=lambda event: appended.append(event)),
        task_store=SimpleNamespace(
            get=lambda task_id: SimpleNamespace(status="backlog"),
        ),
        _dispatch_globally_paused=lambda: False,
    )

    with (
        patch.object(
            task_pipeline_runtime,
            "task_pipeline_generation_contexts",
            return_value=contexts,
        ),
        patch.object(
            task_pipeline_runtime,
            "task_pipeline_managed_task_ids",
            return_value=set(contexts),
        ),
        patch.object(
            task_pipeline_runtime,
            "reconcile_task_pipeline_runtime",
            return_value=[],
        ) as reconcile,
    ):
        decisions, managed = task_pipeline_runtime.prepare_task_pipeline_dispatch(
            runtime,
            candidate_task_ids=contexts,
        )

    assert decisions == []
    assert managed == set(contexts)
    reconcile.assert_called_once_with(runtime)
    assert not appended


def test_orchestrator_does_not_legacy_dispatch_preapproval_pipeline_task(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="pipeline-fence"),
        session=SessionConfig(tmux_session="pipeline-fence"),
        roles=[RoleConfig(name="dev", instance_id="dev", backend="mock")],
        workflow=WorkflowConfig(flow_metadata={
            "task_pipeline": {**_policy(), "mode": "blocking"},
        }),
    )
    task_store = TaskStore(state_dir / "kanban.json")
    task_store.add(Task(
        id="TASK-PENDING-APPROVAL",
        title="fix coordinate parser",
        status="backlog",
    ))
    orchestrator = Orchestrator(
        state_dir,
        config,
        TmuxTransport(TmuxSession(session_name="pipeline-fence", dry_run=True)),
    )

    decisions = orchestrator._dispatch_ready()

    assert decisions == []
    assert not any(
        event.type == "task.dispatched"
        for event in EventLog(state_dir / "events.jsonl").read_all()
    )
