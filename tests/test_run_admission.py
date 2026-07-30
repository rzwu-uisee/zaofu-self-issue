from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.loader import ConfigError, load_config
from zf.core.config.schema import (
    FanoutAggregateConfig,
    ProjectConfig,
    RoleConfig,
    WorkflowRunAdmissionConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.run_admission import (
    build_run_admission_projection,
    reject_workflow_invoke_admission,
    request_admission_view,
    run_dispatch_block_reason,
)


class _Transport:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    def send_task(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self.sent.append((*args, kwargs))

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


def _config(
    *,
    concurrent: bool = False,
    worktree_isolation: bool = False,
) -> ZfConfig:
    config = ZfConfig(
        project=ProjectConfig(name="run-admission"),
        roles=[
            RoleConfig(
                name="reviewer",
                instance_id="reviewer",
                backend="mock",
                role_kind="reader",
            ),
        ],
        workflow=WorkflowConfig(
            run_admission=WorkflowRunAdmissionConfig(
                mode="concurrent" if concurrent else "serial",
                max_active_runs=2 if concurrent else 1,
            ),
            stages=[
                WorkflowStageConfig(
                    id="review",
                    trigger="candidate.ready",
                    topology="fanout_reader",
                    roles=["reviewer"],
                    aggregate=FanoutAggregateConfig(
                        success_event="review.approved",
                        failure_event="review.rejected",
                    ),
                ),
            ],
        ),
    )
    config.runtime.workdirs.enabled = worktree_isolation
    config.runtime.workdirs.mode = (
        "worktree" if worktree_isolation else "dry-run"
    )
    return config


def _runtime(
    tmp_path: Path,
    *,
    config: ZfConfig | None = None,
) -> tuple[Path, EventLog, Orchestrator]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    store = TaskStore(state_dir / "kanban.json")
    for task_id, run_id in (
        ("TASK-A", "RUN-A"),
        ("TASK-B", "RUN-B"),
        ("TASK-C", "RUN-C"),
        ("TASK-D", "RUN-D"),
    ):
        store.add(Task(
            id=task_id,
            title=task_id,
            active_dispatch_id=f"dispatch-{task_id}",
            contract=TaskContract(
                evidence_contract={"workflow_run_id": run_id},
            ),
        ))
    log = EventLog(state_dir / "events.jsonl")
    runtime = Orchestrator(
        state_dir,
        config or _config(),
        _Transport(),
    )  # type: ignore[arg-type]
    return state_dir, log, runtime


def _invoke(
    runtime: Orchestrator,
    *,
    run_id: str,
    task_id: str,
    scope: str = "",
    effective_config_digest: str = "",
    run_contract_digest: str = "",
    payload_updates: dict | None = None,
) -> ZfEvent:
    event = _invoke_event(
        runtime,
        run_id=run_id,
        task_id=task_id,
        scope=scope,
        effective_config_digest=effective_config_digest,
        run_contract_digest=run_contract_digest,
        payload_updates=payload_updates,
    )
    runtime.run_once(events=[event])
    return event


def _invoke_event(
    runtime: Orchestrator,
    *,
    run_id: str,
    task_id: str,
    scope: str = "",
    effective_config_digest: str = "",
    run_contract_digest: str = "",
    payload_updates: dict | None = None,
) -> ZfEvent:
    task = runtime.task_store.get(task_id)
    dispatch_id = str(
        getattr(task, "active_dispatch_id", "")
        or f"dispatch-{task_id}"
    )
    payload = {
        "request_id": run_id,
        "run_id": run_id,
        "workflow_run_id": run_id,
        "task_id": task_id,
        "pattern_id": "review",
        "dispatch_id": dispatch_id,
        "requested_by": "test",
        "reason": "admission test",
        "source": "test",
        "source_refs": {},
        "scope": scope,
        "effective_config_digest": effective_config_digest,
        "run_contract_digest": run_contract_digest,
    }
    payload.update(payload_updates or {})
    event = runtime.event_writer.append(ZfEvent(
        type="workflow.invoke.requested",
        actor="test",
        task_id=task_id,
        correlation_id=run_id,
        payload=payload,
    ))
    return event


def _control(
    runtime: Orchestrator,
    *,
    action: str,
    run_id: str,
) -> dict:
    requested = runtime.event_writer.append(ZfEvent(
        type="control.action.requested",
        actor="operator",
        correlation_id=run_id,
        payload={"action": action, "run_id": run_id},
    ))
    return ControlledActionService(
        runtime.state_dir,
        runtime.event_writer,
        config=runtime.config,
        project_root=runtime.project_root,
        actor="operator",
        source="test",
        surface="web",
    ).execute(
        action=action,
        requested_action=action,
        payload={"run_id": run_id, "reason": "test"},
        requested=requested,
    )


def test_serial_run_admission_queues_and_releases_exactly_one(
    tmp_path: Path,
) -> None:
    state_dir, log, runtime = _runtime(tmp_path)
    first = _invoke(runtime, run_id="RUN-A", task_id="TASK-A")
    second = _invoke(runtime, run_id="RUN-B", task_id="TASK-B")

    events = log.read_all()
    projection = build_run_admission_projection(events)
    assert projection.active_run_ids == ["RUN-A"]
    assert projection.queued_run_ids == ["RUN-B"]
    assert request_admission_view(
        events,
        request_id="RUN-B",
    )["queue_position"] == 1
    assert run_dispatch_block_reason(
        runtime,
        task=runtime.task_store.get("TASK-B"),
    ) == "run_not_admitted:queued"
    briefing = state_dir / "briefings" / "queued.md"
    briefing.parent.mkdir(parents=True, exist_ok=True)
    briefing.write_text("# queued\n", encoding="utf-8")
    queued_context = runtime._dispatch_context(
        role=runtime.config.roles[0],
        briefing_path=briefing,
        task_id="TASK-B",
    )
    with pytest.raises(RuntimeError, match="run_not_admitted:queued"):
        runtime._send_transport_task(
            "reviewer",
            briefing,
            "queued",
            queued_context,
        )
    assert sum(
        event.type == "workflow.invoke.accepted"
        and event.payload.get("source_event_id") == first.id
        for event in events
    ) == 1
    assert not any(
        event.type == "workflow.invoke.accepted"
        and event.payload.get("source_event_id") == second.id
        for event in events
    )
    queued_result = ZfEvent(
        type="review.approved",
        actor="reviewer",
        task_id="TASK-B",
        payload={
            "workflow_run_id": "RUN-B",
            "dispatch_id": "dispatch-TASK-B",
        },
    )
    queued_decision = runtime._reject_invalid_lifecycle_event(queued_result)
    assert queued_decision is not None
    assert "run_not_admitted:queued" in queued_decision.reason

    terminal = runtime.event_writer.append(ZfEvent(
        type="run.goal.completed",
        actor="orchestrator",
        task_id="TASK-A",
        correlation_id="RUN-A",
        payload={"run_id": "RUN-A", "status": "completed"},
    ))
    runtime.run_once(events=[terminal])

    events = log.read_all()
    projection = build_run_admission_projection(events)
    assert projection.runs["RUN-A"].status == "completed"
    assert projection.active_run_ids == ["RUN-B"]
    assert projection.queued_run_ids == []
    assert sum(
        event.type == "run.admission.released"
        and event.payload.get("source_event_id") == second.id
        for event in events
    ) == 1
    assert sum(
        event.type == "workflow.invoke.accepted"
        and event.payload.get("source_event_id") == second.id
        for event in events
    ) == 1

    restarted = Orchestrator(
        state_dir,
        _config(),
        _Transport(),
    )  # type: ignore[arg-type]
    restarted.run_once()
    replayed = log.read_all()
    assert sum(
        event.type == "workflow.invoke.accepted"
        and event.payload.get("source_event_id") == second.id
        for event in replayed
    ) == 1


@pytest.mark.parametrize("terminal_kind", ["cancel", "admission_rejection"])
def test_serial_admission_releases_after_controlled_or_invoke_terminal(
    tmp_path: Path,
    terminal_kind: str,
) -> None:
    _state_dir, log, runtime = _runtime(tmp_path)
    first = _invoke(runtime, run_id="RUN-A", task_id="TASK-A")
    second = _invoke(runtime, run_id="RUN-B", task_id="TASK-B")

    if terminal_kind == "cancel":
        _control(runtime, action="run-cancel", run_id="RUN-A")
        terminal = next(
            event
            for event in reversed(log.read_all())
            if event.type == "run.cancelled"
        )
    else:
        reject_workflow_invoke_admission(
            runtime,
            first,
            reason="invoke contract rejected",
        )
        terminal = next(
            event
            for event in reversed(log.read_all())
            if event.type == "run.admission.rejected"
        )

    runtime.run_once(events=[terminal])

    projection = build_run_admission_projection(log.read_all())
    assert projection.active_run_ids == ["RUN-B"]
    assert projection.queued_run_ids == []
    assert sum(
        event.type == "workflow.invoke.accepted"
        and event.payload.get("source_event_id") == second.id
        for event in log.read_all()
    ) == 1


def test_serial_admission_keeps_submit_order_when_goal_anchors_are_batched(
    tmp_path: Path,
) -> None:
    _state_dir, log, runtime = _runtime(tmp_path)
    invokes: list[ZfEvent] = []
    for run_id, task_id in (("RUN-A", "TASK-A"), ("RUN-B", "TASK-B")):
        runtime.event_writer.append(ZfEvent(
            type="run.goal.started",
            actor="zf-cli",
            task_id=task_id,
            correlation_id=run_id,
            payload={"run_id": run_id, "objective": run_id},
        ))
        invokes.append(_invoke_event(
            runtime,
            run_id=run_id,
            task_id=task_id,
        ))

    runtime.run_once(events=invokes)

    projection = build_run_admission_projection(log.read_all())
    assert projection.active_run_ids == ["RUN-A"]
    assert projection.queued_run_ids == ["RUN-B"]
    accepted_sources = [
        event.payload.get("source_event_id")
        for event in log.read_all()
        if event.type == "workflow.invoke.accepted"
    ]
    assert invokes[0].id in accepted_sources
    assert invokes[1].id not in accepted_sources


def test_rejected_nested_invoke_does_not_terminalize_active_run(
    tmp_path: Path,
) -> None:
    _state_dir, log, runtime = _runtime(tmp_path)
    _invoke(runtime, run_id="RUN-A", task_id="TASK-A")
    nested = _invoke_event(
        runtime,
        run_id="RUN-A",
        task_id="TASK-A",
        payload_updates={"workflow_generation": "generation-2"},
    )

    reject_workflow_invoke_admission(
        runtime,
        nested,
        reason="nested operation rejected",
    )

    projection = build_run_admission_projection(log.read_all())
    assert projection.active_run_ids == ["RUN-A"]
    assert projection.runs["RUN-A"].status == "running"
    assert not any(
        event.type == "run.admission.rejected"
        and event.payload.get("source_event_id") == nested.id
        for event in log.read_all()
    )


def test_light_invoke_uses_configured_entry_after_admission(
    tmp_path: Path,
) -> None:
    config = _config()
    config.workflow.flow_metadata = {
        "topology": "light",
        "flow_kind": "prd",
        "light_entry_trigger": "prd.requested",
    }
    _state_dir, log, runtime = _runtime(tmp_path, config=config)
    event = _invoke_event(
        runtime,
        run_id="RUN-A",
        task_id="TASK-A",
        payload_updates={
            "kind": "prd",
            "flow_kind": "prd",
            "light_entry_trigger": "prd.requested",
            "light_entry_payload": {
                "workflow_run_id": "RUN-A",
                "kind": "prd",
            },
        },
    )

    runtime.run_once(events=[event])

    events = log.read_all()
    accepted = next(
        row
        for row in events
        if row.type == "workflow.invoke.accepted"
        and row.payload.get("source_event_id") == event.id
    )
    entry = next(row for row in events if row.type == "prd.requested")
    assert entry.causation_id == accepted.id
    assert entry.payload["workflow_run_id"] == "RUN-A"


def test_multi_kind_issue_light_entry_stays_kernel_owned_with_layer2(
    tmp_path: Path,
) -> None:
    config = _config()
    config.roles.append(RoleConfig(
        name="orchestrator",
        instance_id="orchestrator",
        backend="mock",
        role_kind="reader",
    ))
    config.workflow.flow_metadata_by_kind = {
        "issue": {
            "topology": "light",
            "flow_kind": "issue",
            "light_entry_trigger": "issue.requested",
            "issue_ref": "docs/issues/session.md",
            "target_root": ".",
        },
        "prd": {
            "flow_kind": "prd",
            "topology": "fanout",
        },
    }
    _state_dir, log, runtime = _runtime(tmp_path, config=config)
    invoke = _invoke_event(
        runtime,
        run_id="RUN-A",
        task_id="TASK-A",
        payload_updates={
            "kind": "issue",
            "flow_kind": "issue",
            "pattern_id": "issue-lanes-impl",
            "light_entry_trigger": "issue.requested",
            "light_entry_payload": {
                "workflow_run_id": "RUN-A",
                "kind": "issue",
                "flow_kind": "issue",
                "pdd_id": "issue-run-a",
                "objective": "Fix session expiry",
            },
        },
    )

    runtime.run_once(events=[invoke])
    entry = next(row for row in log.read_all() if row.type == "issue.requested")
    runtime.run_once(events=[entry])

    ready = next(row for row in log.read_all() if row.type == "task_map.ready")
    assert ready.payload["flow_kind"] == "issue"
    assert ready.payload["workflow_run_id"] == "RUN-A"
    assert runtime.transport.sent == []


@pytest.mark.parametrize(
    ("entry_trigger", "entry_run_id", "reason"),
    [
        ("issue.requested", "RUN-A", "trigger is not configured"),
        ("prd.requested", "RUN-B", "Run identity mismatch"),
    ],
)
def test_light_invoke_rejects_unconfigured_or_cross_run_entry(
    tmp_path: Path,
    entry_trigger: str,
    entry_run_id: str,
    reason: str,
) -> None:
    config = _config()
    config.workflow.flow_metadata = {
        "topology": "light",
        "flow_kind": "prd",
        "light_entry_trigger": "prd.requested",
    }
    _state_dir, log, runtime = _runtime(tmp_path, config=config)
    event = _invoke_event(
        runtime,
        run_id="RUN-A",
        task_id="TASK-A",
        payload_updates={
            "kind": "prd",
            "flow_kind": "prd",
            "light_entry_trigger": entry_trigger,
            "light_entry_payload": {
                "workflow_run_id": entry_run_id,
                "kind": "prd",
            },
        },
    )

    runtime.run_once(events=[event])

    events = log.read_all()
    rejected = next(
        row
        for row in events
        if row.type == "workflow.invoke.rejected"
        and row.payload.get("source_event_id") == event.id
    )
    assert reason in rejected.payload["reason"]
    assert not any(row.type == entry_trigger for row in events)


def test_pause_resume_cancel_are_idempotent_and_fence_late_result(
    tmp_path: Path,
) -> None:
    _state_dir, log, runtime = _runtime(tmp_path)
    _invoke(runtime, run_id="RUN-A", task_id="TASK-A")

    paused = _control(runtime, action="run-pause", run_id="RUN-A")
    paused_replay = _control(runtime, action="run-pause", run_id="RUN-A")
    assert paused["status"] == "paused"
    assert paused_replay["idempotent_replay"] is True
    assert run_dispatch_block_reason(
        runtime,
        task=runtime.task_store.get("TASK-A"),
    ) == "run_paused"
    assert sum(event.type == "run.paused" for event in log.read_all()) == 1

    resumed = _control(runtime, action="run-resume", run_id="RUN-A")
    resumed_replay = _control(runtime, action="run-resume", run_id="RUN-A")
    assert resumed["status"] == "running"
    assert resumed_replay["idempotent_replay"] is True
    assert run_dispatch_block_reason(
        runtime,
        task=runtime.task_store.get("TASK-A"),
    ) == ""
    assert sum(event.type == "run.resumed" for event in log.read_all()) == 1

    cancelled = _control(runtime, action="run-cancel", run_id="RUN-A")
    cancelled_replay = _control(runtime, action="run-cancel", run_id="RUN-A")
    assert cancelled["status"] == "cancelled"
    assert cancelled_replay["idempotent_replay"] is True
    assert sum(event.type == "run.cancelled" for event in log.read_all()) == 1

    late = ZfEvent(
        type="review.approved",
        actor="reviewer",
        task_id="TASK-A",
        correlation_id="RUN-A",
        payload={
            "workflow_run_id": "RUN-A",
            "dispatch_id": "dispatch-TASK-A",
        },
    )
    decision = runtime._reject_invalid_lifecycle_event(late)
    assert decision is not None
    assert decision.action == "block"
    assert "run_terminal:cancelled" in decision.reason
    assert any(
        event.type == "run.result.rejected"
        and event.payload.get("source_event_id") == late.id
        for event in log.read_all()
    )


def test_terminal_run_dispatch_blocked_event_does_not_self_amplify(
    tmp_path: Path,
) -> None:
    _state_dir, log, runtime = _runtime(tmp_path)
    _invoke(runtime, run_id="RUN-A", task_id="TASK-A")
    _control(runtime, action="run-cancel", run_id="RUN-A")
    blocked = runtime.event_writer.append(ZfEvent(
        type="run.dispatch.blocked",
        actor="orchestrator",
        task_id="TASK-A",
        correlation_id="RUN-A",
        payload={
            "run_id": "RUN-A",
            "workflow_run_id": "RUN-A",
            "source_event_id": "source-1",
            "reason": "run_terminal:cancelled",
        },
    ))
    before = sum(
        event.type == "run.dispatch.blocked"
        for event in log.read_all()
    )

    runtime.run_once(events=[blocked])

    assert sum(
        event.type == "run.dispatch.blocked"
        for event in log.read_all()
    ) == before


def test_terminal_run_non_stage_event_does_not_emit_dispatch_block(
    tmp_path: Path,
) -> None:
    _state_dir, log, runtime = _runtime(tmp_path)
    _invoke(runtime, run_id="RUN-A", task_id="TASK-A")
    _control(runtime, action="run-cancel", run_id="RUN-A")
    snapshot = runtime.event_writer.append(ZfEvent(
        type="runtime.snapshot.recorded",
        actor="runtime",
        task_id="TASK-A",
        correlation_id="RUN-A",
        payload={
            "run_id": "RUN-A",
            "workflow_run_id": "RUN-A",
        },
    ))
    before = sum(
        event.type == "run.dispatch.blocked"
        for event in log.read_all()
    )

    runtime.run_once(events=[snapshot])

    assert sum(
        event.type == "run.dispatch.blocked"
        for event in log.read_all()
    ) == before


def test_run_admission_config_defaults_and_concurrent_cap(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "zf.yaml"
    config_path.write_text(
        """\
version: "1.0"
project:
  name: admission
workflow:
  run_admission:
    version: v1
    mode: concurrent
    max_active_runs: 3
""",
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert config.workflow.run_admission.mode == "concurrent"
    assert config.workflow.run_admission.max_active_runs == 3

    config_path.write_text(
        """\
version: "1.0"
project:
  name: admission
workflow:
  run_admission:
    mode: concurrent
    max_active_runs: 9
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="between 1 and 8"):
        load_config(config_path)

    config_path.write_text(
        """\
version: "1.0"
project:
  name: admission
workflow:
  task_attempt:
    mode: enforce
    max_attempts: 0
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="between 1 and 10"):
        load_config(config_path)


def test_concurrent_runs_require_isolation_and_respect_capacity(
    tmp_path: Path,
) -> None:
    _state_dir, log, runtime = _runtime(
        tmp_path,
        config=_config(concurrent=True, worktree_isolation=True),
    )
    _invoke(
        runtime,
        run_id="RUN-A",
        task_id="TASK-A",
        scope="src/a",
        effective_config_digest="cfg-1",
        run_contract_digest="contract-a",
    )
    _invoke(
        runtime,
        run_id="RUN-B",
        task_id="TASK-B",
        scope="src/b",
        effective_config_digest="cfg-1",
        run_contract_digest="contract-b",
    )
    third = _invoke(
        runtime,
        run_id="RUN-C",
        task_id="TASK-C",
        scope="src/c",
        effective_config_digest="cfg-1",
        run_contract_digest="contract-c",
    )

    projection = build_run_admission_projection(log.read_all())
    assert projection.active_run_ids == ["RUN-A", "RUN-B"]
    assert projection.queued_run_ids == ["RUN-C"]
    assert projection.runs["RUN-C"].blocker == (
        "project active Run capacity reached"
    )
    assert not any(
        event.type == "workflow.invoke.accepted"
        and event.payload.get("source_event_id") == third.id
        for event in log.read_all()
    )


def test_concurrent_reconcile_refills_all_capacity_after_batched_terminals(
    tmp_path: Path,
) -> None:
    _state_dir, log, runtime = _runtime(
        tmp_path,
        config=_config(concurrent=True, worktree_isolation=True),
    )
    for run_id, task_id, scope in (
        ("RUN-A", "TASK-A", "src/a"),
        ("RUN-B", "TASK-B", "src/b"),
        ("RUN-C", "TASK-C", "src/c"),
        ("RUN-D", "TASK-D", "src/d"),
    ):
        _invoke(
            runtime,
            run_id=run_id,
            task_id=task_id,
            scope=scope,
            effective_config_digest="cfg-1",
            run_contract_digest=f"contract-{run_id}",
        )
    projection = build_run_admission_projection(log.read_all())
    assert projection.active_run_ids == ["RUN-A", "RUN-B"]
    assert projection.queued_run_ids == ["RUN-C", "RUN-D"]

    terminals = [
        runtime.event_writer.append(ZfEvent(
            type="run.goal.completed",
            actor="orchestrator",
            task_id=task_id,
            correlation_id=run_id,
            payload={"run_id": run_id, "status": "completed"},
        ))
        for run_id, task_id in (
            ("RUN-A", "TASK-A"),
            ("RUN-B", "TASK-B"),
        )
    ]
    runtime.run_once(events=terminals)

    projection = build_run_admission_projection(log.read_all())
    assert projection.active_run_ids == ["RUN-C", "RUN-D"]
    assert projection.queued_run_ids == []
    accepted_sources = {
        str(event.payload.get("source_event_id") or "")
        for event in log.read_all()
        if event.type == "workflow.invoke.accepted"
    }
    queued_sources = {
        entry.source_event_id
        for run_id, entry in projection.runs.items()
        if run_id in {"RUN-C", "RUN-D"}
    }
    assert queued_sources <= accepted_sources


@pytest.mark.parametrize(
    ("worktree_isolation", "second_scope", "second_digest", "second_task", "reason"),
    [
        (
            False,
            "src/b",
            "cfg-1",
            "TASK-B",
            "requires runtime.workdirs worktree mode",
        ),
        (
            True,
            "",
            "cfg-1",
            "TASK-B",
            "requires explicit work scope",
        ),
        (
            True,
            "src/b",
            "cfg-2",
            "TASK-B",
            "effective config digest mismatch",
        ),
        (
            True,
            "src/b",
            "cfg-1",
            "TASK-A",
            "task_id collision",
        ),
        (
            True,
            "src/a/submodule",
            "cfg-1",
            "TASK-B",
            "scope overlaps",
        ),
        (
            True,
            "src/b,../escape",
            "cfg-1",
            "TASK-B",
            "invalid work scope",
        ),
        (
            True,
            "/tmp/run-b",
            "cfg-1",
            "TASK-B",
            "invalid work scope",
        ),
        (
            True,
            r"C:\tmp\run-b",
            "cfg-1",
            "TASK-B",
            "invalid work scope",
        ),
        (
            True,
            "~/run-b",
            "cfg-1",
            "TASK-B",
            "invalid work scope",
        ),
    ],
)
def test_concurrent_unsafe_second_run_fails_closed_to_queue(
    tmp_path: Path,
    worktree_isolation: bool,
    second_scope: str,
    second_digest: str,
    second_task: str,
    reason: str,
) -> None:
    _state_dir, log, runtime = _runtime(
        tmp_path,
        config=_config(
            concurrent=True,
            worktree_isolation=worktree_isolation,
        ),
    )
    _invoke(
        runtime,
        run_id="RUN-A",
        task_id="TASK-A",
        scope="src/a",
        effective_config_digest="cfg-1",
        run_contract_digest="contract-a",
    )
    _invoke(
        runtime,
        run_id="RUN-B",
        task_id=second_task,
        scope=second_scope,
        effective_config_digest=second_digest,
        run_contract_digest="contract-b",
    )

    projection = build_run_admission_projection(log.read_all())
    assert projection.active_run_ids == ["RUN-A"]
    assert projection.queued_run_ids == ["RUN-B"]
    assert reason in projection.runs["RUN-B"].blocker


@pytest.mark.parametrize(
    ("active_scope", "second_scope", "reason"),
    [
        ("", "src/b", "explicit work scope for active Run"),
        ("../escape", "src/b", "invalid work scope for active Run"),
        ("src/a/**", "src/b", "invalid work scope for active Run"),
        ("src/a,../escape", "src/b", "invalid work scope for active Run"),
        ("src/a/../b", "src/b/module", "scope overlaps"),
    ],
)
def test_concurrent_active_run_scope_is_normalized_or_fails_closed(
    tmp_path: Path,
    active_scope: str,
    second_scope: str,
    reason: str,
) -> None:
    _state_dir, log, runtime = _runtime(
        tmp_path,
        config=_config(concurrent=True, worktree_isolation=True),
    )
    _invoke(
        runtime,
        run_id="RUN-A",
        task_id="TASK-A",
        scope=active_scope,
        effective_config_digest="cfg-1",
        run_contract_digest="contract-a",
    )
    _invoke(
        runtime,
        run_id="RUN-B",
        task_id="TASK-B",
        scope=second_scope,
        effective_config_digest="cfg-1",
        run_contract_digest="contract-b",
    )

    projection = build_run_admission_projection(log.read_all())
    assert projection.active_run_ids == ["RUN-A"]
    assert projection.queued_run_ids == ["RUN-B"]
    assert reason in projection.runs["RUN-B"].blocker


def test_concurrent_second_run_requires_active_run_pinned_identity(
    tmp_path: Path,
) -> None:
    _state_dir, log, runtime = _runtime(
        tmp_path,
        config=_config(concurrent=True, worktree_isolation=True),
    )
    _invoke(
        runtime,
        run_id="RUN-A",
        task_id="TASK-A",
        scope="src/a",
    )
    _invoke(
        runtime,
        run_id="RUN-B",
        task_id="TASK-B",
        scope="src/b",
        effective_config_digest="cfg-1",
        run_contract_digest="contract-b",
    )

    projection = build_run_admission_projection(log.read_all())
    assert projection.active_run_ids == ["RUN-A"]
    assert projection.queued_run_ids == ["RUN-B"]
    assert "pinned config for active Run RUN-A" in projection.runs["RUN-B"].blocker


@pytest.mark.parametrize(
    ("changed_key", "reason"),
    [
        ("effective_config_digest", "config identity divergence"),
        ("run_contract_digest", "run contract identity divergence"),
    ],
)
def test_concurrent_second_run_rejects_divergent_active_identity(
    tmp_path: Path,
    changed_key: str,
    reason: str,
) -> None:
    _state_dir, log, runtime = _runtime(
        tmp_path,
        config=_config(concurrent=True, worktree_isolation=True),
    )
    _invoke(
        runtime,
        run_id="RUN-A",
        task_id="TASK-A",
        scope="src/a",
        effective_config_digest="cfg-1",
        run_contract_digest="contract-a",
    )
    runtime.event_writer.append(ZfEvent(
        type="run.goal.updated",
        actor="orchestrator",
        task_id="TASK-A",
        payload={
            "workflow_run_id": "RUN-A",
            changed_key: "changed",
        },
        correlation_id="RUN-A",
    ))
    _invoke(
        runtime,
        run_id="RUN-B",
        task_id="TASK-B",
        scope="src/b",
        effective_config_digest="cfg-1",
        run_contract_digest="contract-b",
    )

    projection = build_run_admission_projection(log.read_all())
    assert projection.active_run_ids == ["RUN-A"]
    assert projection.queued_run_ids == ["RUN-B"]
    assert reason in projection.runs["RUN-B"].blocker


def test_concurrent_run_cancel_and_late_result_do_not_fence_peer(
    tmp_path: Path,
) -> None:
    _state_dir, log, runtime = _runtime(
        tmp_path,
        config=_config(concurrent=True, worktree_isolation=True),
    )
    for run_id, task_id, scope in (
        ("RUN-A", "TASK-A", "src/a"),
        ("RUN-B", "TASK-B", "src/b"),
    ):
        _invoke(
            runtime,
            run_id=run_id,
            task_id=task_id,
            scope=scope,
            effective_config_digest="cfg-1",
            run_contract_digest=f"contract-{run_id}",
        )

    _control(runtime, action="run-cancel", run_id="RUN-A")

    projection = build_run_admission_projection(log.read_all())
    assert projection.runs["RUN-A"].status == "cancelled"
    assert projection.runs["RUN-B"].status == "running"
    assert run_dispatch_block_reason(
        runtime,
        task=runtime.task_store.get("TASK-B"),
    ) == ""
    late_a = ZfEvent(
        type="review.approved",
        actor="reviewer",
        task_id="TASK-A",
        payload={
            "workflow_run_id": "RUN-A",
            "dispatch_id": "dispatch-TASK-A",
        },
    )
    assert runtime._reject_invalid_lifecycle_event(late_a) is not None
    peer_b = ZfEvent(
        type="review.approved",
        actor="reviewer",
        task_id="TASK-B",
        payload={
            "workflow_run_id": "RUN-B",
            "dispatch_id": "dispatch-TASK-B",
        },
    )
    decision = runtime._reject_invalid_lifecycle_event(peer_b)
    assert decision is None or "run_terminal" not in decision.reason
