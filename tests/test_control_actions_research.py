from __future__ import annotations

import hashlib
import json
from pathlib import Path

from zf.core.config.loader import load_config
from zf.core.config.schema import (
    FanoutAggregateConfig,
    FanoutChildConfig,
    ProjectConfig,
    ProviderSessionConfig,
    RoleConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.events import EventLog, EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.task_workflow_plans import task_workflow_binding_digest
from zf.runtime.research_generation import (
    reconcile_stale_research_generations,
    research_generation_binding_error,
)
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.run_contract import load_run_contract_snapshot
from zf.runtime.workflow_anchor import (
    bind_workflow_request_to_task,
    mark_workflow_managed_task,
)
from zf.runtime.workflow_origin import (
    build_workflow_origin_binding,
    workflow_origin_digest,
)
from zf.runtime.workflow_route_catalog import workflow_route_catalog


def _config() -> ZfConfig:
    roles = [
        RoleConfig(name=name, backend="mock", role_kind="reader")
        for name in (
            "source_researcher",
            "product_analyst",
            "technical_analyst",
            "risk_critic",
            "synthesizer",
        )
    ]
    research_root = RoleConfig(
        name="research_root",
        instance_id="research_root",
        backend="claude-code",
        role_kind="reader",
        permission_mode="allowlist",
        allowed_tools=[
            "Read",
            "Glob",
            "Grep",
            "WebSearch",
            "WebFetch",
            "Agent",
            "Bash(zf emit *)",
        ],
        provider_session=ProviderSessionConfig(effort="high"),
    )
    roles.append(research_root)
    fixed_stage = WorkflowStageConfig(
        id="research-fanout",
        trigger="workflow.invoke.requested",
        topology="fanout_reader",
        roles=[role.name for role in roles],
        children=[
            FanoutChildConfig(role_instance=name, payload={"child_id": name})
            for name in (
                "source_researcher",
                "product_analyst",
                "technical_analyst",
                "risk_critic",
            )
        ],
        aggregate=FanoutAggregateConfig(
            mode="wait_for_all",
            child_success_event="research.child.completed",
            child_failure_event="research.child.failed",
            synth_role="synthesizer",
            success_event="research.fanout.completed",
            failure_event="research.fanout.failed",
        ),
    )
    adaptive_stage = WorkflowStageConfig(
        id="research-adaptive",
        trigger="workflow.invoke.requested",
        topology="fanout_reader",
        roles=["research_root"],
        children=[
            FanoutChildConfig(
                role_instance="research_root",
                payload={"child_id": "research_root"},
            ),
        ],
        aggregate=FanoutAggregateConfig(
            mode="wait_for_all",
            child_success_event="research.child.completed",
            child_failure_event="research.child.failed",
            success_event="research.adaptive.completed",
            failure_event="research.adaptive.failed",
        ),
    )
    return ZfConfig(
        project=ProjectConfig(name="research-test"),
        roles=roles,
        workflow=WorkflowConfig(
            stages=[adaptive_stage, fixed_stage],
        ),
    )


def _service(tmp_path: Path) -> tuple[Path, EventWriter, ControlledActionService]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    TaskStore(state_dir / "kanban.json").add(
        Task(id="TASK-RESEARCH", title="Research", status="in_progress")
    )
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    service = ControlledActionService(
        state_dir,
        writer,
        config=_config(),
        project_root=tmp_path,
        actor="web",
        source="kanban-agent",
        surface="web",
    )
    return state_dir, writer, service


def _execute(
    service: ControlledActionService,
    writer: EventWriter,
    action: str,
    payload: dict,
) -> dict:
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        payload={"action": action, "request": payload},
    )
    return service.execute(
        action=action,
        requested_action=action,
        payload=payload,
        requested=requested,
    )


def test_research_start_invokes_fixed_template(tmp_path: Path) -> None:
    _state_dir, writer, service = _service(tmp_path)

    result = _execute(service, writer, "research-start", {
        "task_id": "TASK-RESEARCH",
        "topic": "Kanban collaboration closure",
    })

    assert result["ok"] is True
    assert result["template_id"] == "research-fanout.fixed.v1"
    assert result["pattern_id"] == "research-fanout"
    invoke = next(
        event
        for event in writer.event_log.read_all()
        if event.type == "workflow.invoke.requested"
    )
    assert invoke.payload["source_refs"]["template_id"] == "research-fanout.fixed.v1"
    assert invoke.payload["source_refs"]["topic"] == "Kanban collaboration closure"
    assert invoke.payload["prompt_kind"] == "research"
    assert invoke.payload["request_kind"] == "research"
    assert invoke.payload["route_id"] == "research:fixed"
    assert len(invoke.payload["workflow_generation"]) == 64
    assert invoke.payload["expected_generation"] == invoke.payload["workflow_generation"]
    assert invoke.payload["effective_config_digest"]
    assert invoke.payload["run_contract_digest"]
    assert invoke.payload["workflow_prompt_ref"].startswith(
        "artifacts/workflow-inputs/"
    )
    task = TaskStore(_state_dir / "kanban.json").get("TASK-RESEARCH")
    assert task is not None
    assert research_generation_binding_error(
        _state_dir,
        config=service.config,
        task=task,
        payload=invoke.payload,
    ) == ""
    snapshot = load_run_contract_snapshot(
        _state_dir,
        invoke.payload["research_generation_contract_ref"],
    )
    generation = snapshot["contract"]["research_generation"]
    assert generation["workflow_generation"] == invoke.payload["workflow_generation"]
    assert generation["prompt_ref"]["sha256"]
    assert len(generation["role_bindings"]) == 5


def test_research_start_explicitly_invokes_adaptive_pilot(
    tmp_path: Path,
) -> None:
    _state_dir, writer, service = _service(tmp_path)

    result = _execute(service, writer, "research-start", {
        "task_id": "TASK-RESEARCH",
        "topic": "HighwayPilot construction-v0",
        "template_id": "research-adaptive.pilot.v1",
    })

    assert result["ok"] is True
    assert result["template_id"] == "research-adaptive.pilot.v1"
    assert result["pattern_id"] == "research-adaptive"
    invoke = next(
        event
        for event in writer.event_log.read_all()
        if event.type == "workflow.invoke.requested"
    )
    assert invoke.payload["source_refs"]["template_id"] == (
        "research-adaptive.pilot.v1"
    )
    assert invoke.payload["source_refs"]["research_rollout"] == (
        "opt_in_pilot"
    )


def test_adaptive_research_fails_closed_without_read_only_agent_root(
    tmp_path: Path,
) -> None:
    _state_dir, writer, service = _service(tmp_path)
    root = next(
        role
        for role in service.config.roles
        if role.name == "research_root"
    )
    root.allowed_tools = ["Read", "Write", "Bash(zf emit *)"]

    result = _execute(service, writer, "research-start", {
        "task_id": "TASK-RESEARCH",
        "topic": "Unsafe adaptive route",
        "template_id": "research-adaptive.pilot.v1",
    })

    assert result["ok"] is False
    assert result["status"] == "preflight_blocked"
    assert "allowlisted Agent tool" in result["reason"]
    assert not any(
        event.type == "workflow.invoke.requested"
        for event in writer.event_log.read_all()
    )


def test_channel_research_requires_current_request_bound_to_task(
    tmp_path: Path,
) -> None:
    state_dir, writer, service = _service(tmp_path)
    missing = _execute(service, writer, "research-start", {
        "task_id": "TASK-RESEARCH",
        "topic": "Request-first research",
        "channel_id": "ch-research",
        "thread_id": "thread-1",
    })
    assert missing["status"] == "invalid_payload"
    assert "request_id" in missing["reason"]

    origin = build_workflow_origin_binding(
        source="kanban-agent",
        project_id="research-test",
        channel_id="ch-research",
        thread_id="thread-1",
    )
    request_dir = state_dir / "workflow-requests"
    request_dir.mkdir()
    (request_dir / "REQ-BOUND.json").write_text(
        json.dumps({
            "schema_version": "workflow.request.v1",
            "request_id": "REQ-BOUND",
            "project_id": "research-test",
            "kind": "prd",
            "status": "ready",
            "revision": 2,
            "origin_binding": origin,
        }),
        encoding="utf-8",
    )
    unbound = _execute(service, writer, "workflow-invoke", {
        "task_id": "TASK-RESEARCH",
        "pattern_id": "research-fanout",
        "request_id": "REQ-BOUND",
        "request_revision": 2,
    })
    assert unbound["status"] == "workflow_task_stale"

    store = TaskStore(state_dir / "kanban.json")
    task = store.get("TASK-RESEARCH")
    assert task is not None
    task = mark_workflow_managed_task(task)
    task = bind_workflow_request_to_task(
        task,
        request_id="REQ-BOUND",
        request_revision=2,
        origin_binding_digest=workflow_origin_digest(origin),
    )
    store.update(task.id, contract=task.contract)

    result = _execute(service, writer, "research-start", {
        "task_id": "TASK-RESEARCH",
        "topic": "Request-first research",
        "request_id": "REQ-BOUND",
        "request_revision": 2,
        "channel_id": "ch-research",
        "thread_id": "thread-1",
    })

    assert result["ok"] is True
    invoke = [
        event
        for event in writer.event_log.read_all()
        if event.type == "workflow.invoke.requested"
    ][-1]
    assert invoke.payload["request_id"] == "REQ-BOUND"
    assert invoke.payload["request_revision"] == 2
    assert invoke.payload["origin_binding"] == origin
    manifest = json.loads(
        (
            state_dir
            / str(invoke.payload["workflow_input_manifest_ref"])
        ).read_text(encoding="utf-8")
    )
    assert manifest["request_id"] == "REQ-BOUND"
    assert manifest["request_revision"] == 2
    assert manifest["origin_binding"] == origin


def test_unbound_invoke_cannot_promote_provenance_to_canonical_origin(
    tmp_path: Path,
) -> None:
    state_dir, writer, service = _service(tmp_path)
    supplied_origin = build_workflow_origin_binding(
        source="legacy-adapter",
        project_id="research-test",
        channel_id="ch-research",
        thread_id="thread-1",
    )

    result = _execute(service, writer, "workflow-invoke", {
        "task_id": "TASK-RESEARCH",
        "pattern_id": "research-fanout",
        "channel_id": "ch-research",
        "thread_id": "thread-1",
        "origin_binding": supplied_origin,
    })

    assert result["ok"] is True
    invoke = next(
        event
        for event in writer.event_log.read_all()
        if event.type == "workflow.invoke.requested"
    )
    assert invoke.payload["origin_binding"] == {}
    manifest = json.loads(
        (
            state_dir
            / str(invoke.payload["workflow_input_manifest_ref"])
        ).read_text(encoding="utf-8")
    )
    assert manifest["origin_binding"] == {}


def test_workflow_start_resolves_fixed_research_route(
    tmp_path: Path,
) -> None:
    state_dir, writer, service = _service(tmp_path)
    task = TaskStore(state_dir / "kanban.json").get("TASK-RESEARCH")
    assert task is not None
    catalog = workflow_route_catalog(service.config)

    result = _execute(service, writer, "workflow-start", {
        "task_id": task.id,
        "task_contract_digest": task_workflow_binding_digest(task),
        "route_id": "research:fixed",
        "config_digest": catalog["config_digest"],
        "objective": "Compare workflow orchestration approaches.",
        "parameters": {
            "expected_output": "Evidence-backed recommendation.",
        },
    })

    assert result["ok"] is True
    assert result["route_id"] == "research:fixed"
    assert result["template_id"] == "research-fanout.fixed.v1"
    invoke = next(
        event
        for event in writer.event_log.read_all()
        if event.type == "workflow.invoke.requested"
    )
    assert invoke.task_id == task.id
    assert invoke.payload["pattern_id"] == "research-fanout"
    assert invoke.payload["expected_output"] == (
        "Evidence-backed recommendation."
    )


def test_workflow_start_resolves_adaptive_research_route(
    tmp_path: Path,
) -> None:
    state_dir, writer, service = _service(tmp_path)
    task = TaskStore(state_dir / "kanban.json").get("TASK-RESEARCH")
    assert task is not None
    catalog = workflow_route_catalog(service.config)

    result = _execute(service, writer, "workflow-start", {
        "task_id": task.id,
        "task_contract_digest": task_workflow_binding_digest(task),
        "route_id": "research:adaptive-pilot",
        "config_digest": catalog["config_digest"],
        "objective": "Run one bounded adaptive research root.",
        "parameters": {
            "expected_output": "Evidence-backed recommendation.",
        },
    })

    assert result["ok"] is True
    assert result["route_id"] == "research:adaptive-pilot"
    assert result["template_id"] == "research-adaptive.pilot.v1"
    invoke = next(
        event
        for event in writer.event_log.read_all()
        if event.type == "workflow.invoke.requested"
    )
    assert invoke.payload["pattern_id"] == "research-adaptive"


def test_workflow_start_rejects_stale_task_or_config_binding(
    tmp_path: Path,
) -> None:
    state_dir, writer, service = _service(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    task = store.get("TASK-RESEARCH")
    assert task is not None
    task_digest = task_workflow_binding_digest(task)
    config_digest = workflow_route_catalog(service.config)["config_digest"]
    store.update(task.id, title="Changed research contract")
    changed_task = store.get(task.id)
    assert changed_task is not None

    stale_task = _execute(service, writer, "workflow-start", {
        "task_id": task.id,
        "task_contract_digest": task_digest,
        "route_id": "research:fixed",
        "config_digest": config_digest,
        "objective": "Do not start stale work.",
    })
    stale_config = _execute(service, writer, "workflow-start", {
        "task_id": task.id,
        "task_contract_digest": task_workflow_binding_digest(changed_task),
        "route_id": "research:fixed",
        "config_digest": "sha256:stale",
        "objective": "Do not start stale work.",
    })

    assert stale_task["status"] == "workflow_task_stale"
    assert stale_config["status"] == "workflow_route_unavailable"
    assert not any(
        event.type == "workflow.invoke.requested"
        for event in writer.event_log.read_all()
    )


def test_workflow_start_allows_only_registered_general_reader_entry(
    tmp_path: Path,
) -> None:
    config_ref = tmp_path / "zf.yaml"
    config_ref.write_text(
        """\
apiVersion: zaofu.dev/v1
kind: IssueFlow
metadata: {name: issue-demo}
spec:
  lanes: 1
  backend: mock
  issueRef: docs/issue.md
---
apiVersion: zaofu.dev/v1
kind: Workflow
metadata: {name: architecture-review}
spec:
  contractVersion: generic-workflow.v1
  intent: research
  template: evidence-synthesis-v1
  entry: scope
  completionProfile:
    id: artifact_delivery
    requiredArtifacts: [scope.report]
    independentVerify: true
  tasks:
  - name: scope
    operation: agent.read
    role: architecture-reader
    inputs:
    - {name: requirement, kind: requirement/spec, from: external.requirement}
    outputs:
    - {name: report, kind: report/markdown}
  - name: verify
    operation: agent.verify
    role: architecture-verifier
    dependencies: [scope]
    inputs:
    - {name: report, kind: report/markdown, from: scope.report}
    outputs:
    - {name: verdict, kind: verification/verdict}
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: general-start-test}
spec:
  version: "1.0"
  project: {name: general-start-test, state_dir: .zf}
  roles:
  - {name: orchestrator, instance_id: orchestrator, backend: mock, role_kind: reader}
  - {name: architecture-reader, instance_id: architecture-reader, backend: mock, role_kind: reader}
  - {name: architecture-verifier, instance_id: architecture-verifier, backend: mock, role_kind: reader}
""",
        encoding="utf-8",
    )
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    TaskStore(state_dir / "kanban.json").add(
        Task(id="TASK-GENERAL", title="Architecture review")
    )
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    config = load_config(config_ref)
    service = ControlledActionService(
        state_dir,
        writer,
        config=config,
        project_root=tmp_path,
        actor="web",
        source="kanban-agent",
        surface="web",
    )
    task = TaskStore(state_dir / "kanban.json").get("TASK-GENERAL")
    assert task is not None
    catalog = workflow_route_catalog(config)
    payload = {
        "task_id": task.id,
        "task_contract_digest": task_workflow_binding_digest(task),
        "config_digest": catalog["config_digest"],
        "objective": "Review the runtime authority boundary.",
        "parameters": {"allow_missing_env": True},
    }

    rejected = _execute(service, writer, "task-workflow-start", {
        **payload,
        "route_id": "general:unregistered-stage",
    })
    started = _execute(service, writer, "task-workflow-start", {
        **payload,
        "route_id": "general:scope",
    })

    assert rejected["status"] == "workflow_route_unavailable"
    assert started["ok"] is True, started
    assert started["action"] == "workflow-start"
    invoke = next(
        event
        for event in writer.event_log.read_all()
        if event.type == "workflow.invoke.requested"
    )
    assert invoke.payload["pattern_id"] == "scope"
    assert invoke.payload["flow_kind"] == "workflow"
    assert invoke.payload["request_kind"] == "workflow"
    assert invoke.task_id == task.id
    assert invoke.payload["workflow_proposal_digest"]
    assert invoke.payload["workflow_proposal_ref"]
    assert invoke.payload["effective_config_digest"]
    assert invoke.payload["effective_config_ref"]
    assert invoke.payload["run_contract_digest"]
    assert invoke.payload["run_contract_ref"]

    class _AliveTransport:
        def is_alive(self, _role_name: str) -> bool:
            return True

    restarted = Orchestrator(
        state_dir,
        load_config(config_ref),
        _AliveTransport(),
        project_root=tmp_path,
    )
    restarted._try_start_declared_workflow_fanout = lambda *args, **kwargs: True

    decision = restarted._on_workflow_invoke_requested(invoke)

    assert decision is not None
    assert decision.action == "workflow_invoke"
    events = writer.event_log.read_all()
    assert any(event.type == "flow.roles.activation.applied" for event in events)
    assert not any(event.type == "workflow.invoke.rejected" for event in events)


def test_research_start_fails_before_event_when_stage_is_missing(tmp_path: Path) -> None:
    _state_dir, writer, service = _service(tmp_path)
    service.config.workflow.stages.clear()

    result = _execute(service, writer, "research-start", {
        "task_id": "TASK-RESEARCH",
        "topic": "missing stage",
    })

    assert result["ok"] is False
    assert result["status"] == "preflight_blocked"
    assert not any(
        event.type == "workflow.invoke.requested"
        for event in writer.event_log.read_all()
    )


def test_research_config_drift_cancels_old_generation_before_dispatch_and_restarts_fresh(
    tmp_path: Path,
) -> None:
    state_dir, writer, service = _service(tmp_path)
    first = _execute(service, writer, "research-start", {
        "task_id": "TASK-RESEARCH",
        "topic": "Generation freshness",
    })
    assert first["ok"] is True
    first_invoke = next(
        event
        for event in writer.event_log.read_all()
        if event.type == "workflow.invoke.requested"
    )

    source_role = next(
        role
        for role in service.config.roles
        if role.name == "source_researcher"
    )
    source_role.model = "replacement-model"
    cancelled = reconcile_stale_research_generations(
        config=service.config,
        state_dir=state_dir,
        writer=writer,
    )

    assert cancelled == [first["workflow_run_id"]]
    events = writer.event_log.read_all()
    superseded = next(
        event for event in events
        if event.type == "workflow.generation.superseded"
    )
    assert superseded.payload["safe_resume_action"] == "restart_from_admission"
    assert superseded.payload["restart_boundary"] == "workflow_start"
    cancellation = next(event for event in events if event.type == "run.cancelled")
    assert cancellation.task_id is None
    assert cancellation.payload["root_task_id"] == "TASK-RESEARCH"
    assert TaskStore(state_dir / "kanban.json").get("TASK-RESEARCH").status == (
        "in_progress"
    )
    assert reconcile_stale_research_generations(
        config=service.config,
        state_dir=state_dir,
        writer=writer,
    ) == []

    class _AliveTransport:
        def is_alive(self, _role_name: str) -> bool:
            return True

    orchestrator = Orchestrator(
        state_dir,
        service.config,
        _AliveTransport(),
        project_root=tmp_path,
    )
    orchestrator._try_start_declared_workflow_fanout = (
        lambda *args, **kwargs: True
    )
    before = sum(
        event.type == "task.fanout.requested"
        for event in writer.event_log.read_all()
    )
    stale_decision = orchestrator._on_workflow_invoke_requested(first_invoke)
    assert stale_decision is not None
    assert stale_decision.action == "block"
    assert sum(
        event.type == "task.fanout.requested"
        for event in writer.event_log.read_all()
    ) == before

    second = _execute(service, writer, "research-start", {
        "task_id": "TASK-RESEARCH",
        "topic": "Generation freshness",
    })
    assert second["ok"] is True
    assert second["workflow_run_id"] != first["workflow_run_id"]
    assert second["workflow_generation"] != first["workflow_generation"]
    second_invoke = [
        event
        for event in writer.event_log.read_all()
        if event.type == "workflow.invoke.requested"
    ][-1]
    decision = orchestrator._on_workflow_invoke_requested(second_invoke)
    assert decision is not None
    assert decision.action == "workflow_invoke"
    events = writer.event_log.read_all()
    admissions = [
        event for event in events
        if event.type == "run.admission.admitted"
    ]
    assert len(admissions) == 1
    assert admissions[0].payload["workflow_run_id"] == second["workflow_run_id"]
    assert isinstance(admissions[0].payload["budget_snapshot"], dict)
    projection = build_run_admission_projection(events)
    assert projection.active_run_ids == [second["workflow_run_id"]]


def test_research_adoption_verifies_digest_revision_and_is_idempotent(
    tmp_path: Path,
) -> None:
    state_dir, writer, service = _service(tmp_path)
    request_dir = state_dir / "workflow-requests"
    request_dir.mkdir()
    projection = {
        "schema_version": "workflow.request.v1",
        "request_id": "REQ-1",
        "project_id": "research-test",
        "kind": "prd",
        "status": "ready",
        "revision": 2,
        "channel_id": "ch-research",
        "thread_id": "thread-1",
        "origin_binding": build_workflow_origin_binding(
            source="kanban-agent",
            project_id="research-test",
            channel_id="ch-research",
            thread_id="thread-1",
        ),
        "requirement_spec_ref": "workflow-requests/REQ-1/requirements/rev-2.json",
        "requirement_spec_digest": "a" * 64,
    }
    (request_dir / "REQ-1.json").write_text(
        json.dumps(projection),
        encoding="utf-8",
    )
    task = TaskStore(state_dir / "kanban.json").get("TASK-RESEARCH")
    assert task is not None
    task = bind_workflow_request_to_task(
        mark_workflow_managed_task(task),
        request_id="REQ-1",
        request_revision=2,
        origin_binding_digest=workflow_origin_digest(
            projection["origin_binding"]
        ),
    )
    TaskStore(state_dir / "kanban.json").update(
        task.id,
        contract=task.contract,
    )
    artifact = state_dir / "research" / "TASK-RESEARCH" / "result.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("# Research\n\nVerified result.\n", encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    payload = {
        "task_id": "TASK-RESEARCH",
        "request_id": "REQ-1",
        "request_revision": 1,
        "artifact_ref": "research/TASK-RESEARCH/result.md",
        "artifact_digest": digest,
        "summary": "Verified result.",
        "channel_id": "ch-research",
        "thread_id": "thread-1",
    }
    terminal_event = writer.emit(
        "fanout.aggregate.completed",
        actor="orchestrator",
        task_id="TASK-RESEARCH",
        payload={
            "fanout_id": "fanout-research-1",
            "stage_id": "research-fanout",
            "status": "completed",
            "artifact_refs": [{
                "kind": "research_report",
                "ref": "research/TASK-RESEARCH/result.md",
                "sha256": digest,
                "task_id": "TASK-RESEARCH",
                "workflow_run_id": "wf-research-1",
                "request_id": "REQ-1",
                "request_revision": 2,
            }],
        },
    )
    result_event = writer.emit(
        "workflow.result.available",
        actor="zf-cli",
        task_id="TASK-RESEARCH",
        causation_id=terminal_event.id,
        payload={
            "schema_version": "workflow-result.v1",
            "result_kind": "research_report",
            "status": "available",
            "project_id": "research-test",
            "origin_surface": "channel",
            "channel_id": "ch-research",
            "thread_id": "thread-1",
            "request_id": "REQ-1",
            "request_revision": 2,
            "task_id": "TASK-RESEARCH",
            "workflow_run_id": "wf-research-1",
            "terminal_event_id": terminal_event.id,
            "artifact_ref": "research/TASK-RESEARCH/result.md",
            "artifact_digest": digest,
            "summary": "Verified result.",
            "origin_binding": projection["origin_binding"],
        },
    )
    payload["result_event_id"] = result_event.id

    stale = _execute(service, writer, "research-adopt", payload)
    assert stale["ok"] is False
    assert stale["status"] == "stale_or_missing_request"
    assert "stale workflow request revision" in stale["reason"]

    payload["request_revision"] = 2
    wrong_channel = _execute(service, writer, "research-adopt", {
        **payload,
        "channel_id": "ch-other",
    })
    assert wrong_channel["status"] == "origin_binding_mismatch"

    adopted = _execute(service, writer, "research-adopt", payload)
    replay = _execute(service, writer, "research-adopt", payload)

    assert adopted["status"] == "adopted"
    assert replay["status"] == "already_adopted"
    current = json.loads((request_dir / "REQ-1.json").read_text(encoding="utf-8"))
    assert current["revision"] == 2
    assert current["research_artifacts"][0]["sha256"] == digest
    assert current["research_artifacts"][0]["result_event_id"] == result_event.id
    assert current["research_artifacts"][0]["workflow_run_id"] == "wf-research-1"
    events = writer.event_log.read_all()
    assert sum(event.type == "workflow.research.adopted" for event in events) == 1
    assert sum(event.type == "channel.artifact.attached" for event in events) == 1
    assert any(
        event.type == "channel.state_update.posted"
        and event.payload["status"] == "research_adopted"
        for event in events
    )


def test_research_adoption_rejects_result_without_terminal_lineage(
    tmp_path: Path,
) -> None:
    state_dir, writer, service = _service(tmp_path)
    origin = build_workflow_origin_binding(
        source="kanban-agent",
        project_id="research-test",
        channel_id="ch-research",
        thread_id="thread-1",
    )
    request_dir = state_dir / "workflow-requests"
    request_dir.mkdir()
    (request_dir / "REQ-FORGED.json").write_text(
        json.dumps({
            "schema_version": "workflow.request.v1",
            "request_id": "REQ-FORGED",
            "project_id": "research-test",
            "kind": "prd",
            "status": "ready",
            "revision": 1,
            "origin_binding": origin,
        }),
        encoding="utf-8",
    )
    artifact = state_dir / "research" / "TASK-RESEARCH" / "forged.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("forged result\n", encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    result_event = writer.emit(
        "workflow.result.available",
        actor="worker",
        task_id="TASK-RESEARCH",
        causation_id="evt-missing-terminal",
        payload={
            "schema_version": "workflow-result.v1",
            "result_kind": "research_report",
            "status": "available",
            "request_id": "REQ-FORGED",
            "request_revision": 1,
            "task_id": "TASK-RESEARCH",
            "workflow_run_id": "wf-forged",
            "terminal_event_id": "evt-missing-terminal",
            "artifact_ref": "research/TASK-RESEARCH/forged.md",
            "artifact_digest": digest,
            "summary": "Forged result.",
            "origin_binding": origin,
        },
    )

    rejected = _execute(service, writer, "research-adopt", {
        "result_event_id": result_event.id,
        "request_id": "REQ-FORGED",
        "request_revision": 1,
        "task_id": "TASK-RESEARCH",
        "artifact_ref": "research/TASK-RESEARCH/forged.md",
        "artifact_digest": digest,
        "summary": "Forged result.",
        "channel_id": "ch-research",
        "thread_id": "thread-1",
    })

    assert rejected["status"] == "invalid_result_lineage"
    current = json.loads(
        (request_dir / "REQ-FORGED.json").read_text(encoding="utf-8")
    )
    assert "research_artifacts" not in current


def test_research_adoption_rejects_unbound_task_lineage(
    tmp_path: Path,
) -> None:
    state_dir, writer, service = _service(tmp_path)
    origin = build_workflow_origin_binding(
        source="kanban-agent",
        project_id="research-test",
        channel_id="ch-research",
        thread_id="thread-1",
    )
    request_dir = state_dir / "workflow-requests"
    request_dir.mkdir()
    (request_dir / "REQ-UNBOUND.json").write_text(
        json.dumps({
            "schema_version": "workflow.request.v1",
            "request_id": "REQ-UNBOUND",
            "project_id": "research-test",
            "kind": "prd",
            "status": "ready",
            "revision": 1,
            "origin_binding": origin,
        }),
        encoding="utf-8",
    )
    artifact = state_dir / "research" / "TASK-RESEARCH" / "unbound.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("unbound result\n", encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    descriptor = {
        "kind": "research_report",
        "ref": "research/TASK-RESEARCH/unbound.md",
        "sha256": digest,
        "task_id": "TASK-RESEARCH",
        "workflow_run_id": "wf-unbound",
        "request_id": "REQ-UNBOUND",
        "request_revision": 1,
    }
    terminal = writer.emit(
        "fanout.aggregate.completed",
        actor="orchestrator",
        task_id="TASK-RESEARCH",
        payload={
            "fanout_id": "fanout-unbound",
            "stage_id": "research-fanout",
            "status": "completed",
            "artifact_refs": [descriptor],
        },
    )
    result_event = writer.emit(
        "workflow.result.available",
        actor="zf-cli",
        task_id="TASK-RESEARCH",
        causation_id=terminal.id,
        payload={
            "schema_version": "workflow-result.v1",
            "result_kind": "research_report",
            "status": "available",
            "request_id": "REQ-UNBOUND",
            "request_revision": 1,
            "task_id": "TASK-RESEARCH",
            "workflow_run_id": "wf-unbound",
            "terminal_event_id": terminal.id,
            "artifact_ref": descriptor["ref"],
            "artifact_digest": digest,
            "summary": "Unbound result.",
            "origin_binding": origin,
        },
    )

    rejected = _execute(service, writer, "research-adopt", {
        "result_event_id": result_event.id,
        "request_id": "REQ-UNBOUND",
        "request_revision": 1,
        "task_id": "TASK-RESEARCH",
        "artifact_ref": descriptor["ref"],
        "artifact_digest": digest,
        "summary": "Unbound result.",
    })

    assert rejected["status"] == "invalid_result_lineage"
    assert "Task is not bound" in rejected["reason"]
