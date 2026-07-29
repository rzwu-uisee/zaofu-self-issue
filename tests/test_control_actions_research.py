from __future__ import annotations

import hashlib
import json
from pathlib import Path

from zf.core.config.schema import (
    FanoutAggregateConfig,
    FanoutChildConfig,
    ProjectConfig,
    RoleConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.events import EventLog, EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.task_workflow_plans import task_workflow_binding_digest
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
    stage = WorkflowStageConfig(
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
    return ZfConfig(
        project=ProjectConfig(name="research-test"),
        roles=roles,
        workflow=WorkflowConfig(stages=[stage]),
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
        "channel_id": "ch-research",
        "thread_id": "thread-1",
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
    state_dir, writer, service = _service(tmp_path)
    service.config.roles.append(
        RoleConfig(
            name="architecture_reader",
            backend="mock",
            role_kind="reader",
        )
    )
    service.config.workflow.stages.append(
        WorkflowStageConfig(
            id="architecture-review",
            trigger="workflow.invoke.requested",
            topology="single_reader",
            roles=["architecture_reader"],
        )
    )
    task = TaskStore(state_dir / "kanban.json").get("TASK-RESEARCH")
    assert task is not None
    catalog = workflow_route_catalog(service.config)
    payload = {
        "task_id": task.id,
        "task_contract_digest": task_workflow_binding_digest(task),
        "config_digest": catalog["config_digest"],
        "objective": "Review the runtime authority boundary.",
    }

    rejected = _execute(service, writer, "task-workflow-start", {
        **payload,
        "route_id": "general:unregistered-stage",
    })
    started = _execute(service, writer, "task-workflow-start", {
        **payload,
        "route_id": "general:architecture-review",
    })

    assert rejected["status"] == "workflow_route_unavailable"
    assert started["ok"] is True
    assert started["action"] == "workflow-start"
    invoke = next(
        event
        for event in writer.event_log.read_all()
        if event.type == "workflow.invoke.requested"
    )
    assert invoke.payload["pattern_id"] == "architecture-review"
    assert invoke.task_id == task.id


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
        "requirement_spec_ref": "workflow-requests/REQ-1/requirements/rev-2.json",
        "requirement_spec_digest": "a" * 64,
    }
    (request_dir / "REQ-1.json").write_text(
        json.dumps(projection),
        encoding="utf-8",
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

    stale = _execute(service, writer, "research-adopt", payload)
    assert stale["ok"] is False
    assert stale["status"] == "stale_or_missing_request"
    assert "stale workflow request revision" in stale["reason"]

    payload["request_revision"] = 2
    adopted = _execute(service, writer, "research-adopt", payload)
    replay = _execute(service, writer, "research-adopt", payload)

    assert adopted["status"] == "adopted"
    assert replay["status"] == "already_adopted"
    current = json.loads((request_dir / "REQ-1.json").read_text(encoding="utf-8"))
    assert current["revision"] == 2
    assert current["research_artifacts"][0]["sha256"] == digest
    events = writer.event_log.read_all()
    assert sum(event.type == "workflow.research.adopted" for event in events) == 1
    assert sum(event.type == "channel.artifact.attached" for event in events) == 1
    assert any(
        event.type == "channel.state_update.posted"
        and event.payload["status"] == "research_adopted"
        for event in events
    )
