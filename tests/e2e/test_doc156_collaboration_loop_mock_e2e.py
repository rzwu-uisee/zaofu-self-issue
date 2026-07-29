from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace

from zf.core.config.loader import load_config
from zf.core.config.schema import RoleConfig, WorkflowStageConfig
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.channel_discussion import advance_discussion
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.orchestrator_reactor import EventReactorMixin


RESEARCH_CONFIG = (
    Path(__file__).parents[1] / "fixtures" / "research_fanout.yaml"
)


class _RecordingTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Path, str, object]] = []

    def send_task(
        self,
        role_name,
        briefing_path,
        prompt,
        *,
        context=None,
    ):  # noqa: ANN001
        self.sent.append((role_name, briefing_path, prompt, context))

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


def _approved_action(
    service: ControlledActionService,
    writer: EventWriter,
    action: str,
    payload: dict,
) -> dict:
    proposal = writer.emit(
        "operator.action.proposed",
        actor="web",
        task_id=str(payload.get("task_id") or "") or None,
        payload={
            "source": "web",
            "proposal": {
                "proposal_id": f"proposal-{action}-{time.time_ns()}",
                "proposal_digest": hashlib.sha256(
                    json.dumps(
                        {"action": action, "payload": payload},
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest(),
                "revision": 1,
                "action": action,
                "requested_action": action,
                "reason": "doc156 deterministic mock",
                "valid": True,
                "validation_error": "",
                "payload": payload,
            },
        },
    )
    requested = writer.emit(
        "web.action.requested",
        actor="operator",
        task_id=str(payload.get("task_id") or "") or None,
        causation_id=proposal.id,
        payload={
            "action": action,
            "request": payload,
            "proposal_event_id": proposal.id,
        },
    )
    return service.execute(
        action=action,
        requested_action=action,
        payload={**payload, "proposal_event_id": proposal.id},
        requested=requested,
    )


def test_doc156_channel_research_adoption_and_workflow_start(
    tmp_path: Path,
) -> None:
    config = load_config(RESEARCH_CONFIG)
    config.roles.append(
        RoleConfig(
            name="delivery_worker",
            backend="mock",
            role_kind="writer",
        )
    )
    config.workflow.stages.append(
        WorkflowStageConfig(
            id="delivery-smoke",
            trigger="workflow.invoke.requested",
            topology="fanout_reader",
            roles=["delivery_worker"],
        )
    )
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    TaskStore(state_dir / "kanban.json").add(
        Task(
            id="TASK-DOC156",
            title="Doc 156 collaboration loop",
            status="in_progress",
            active_dispatch_id="dispatch-doc156",
        )
    )
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    service = ControlledActionService(
        state_dir,
        writer,
        config=config,
        project_root=tmp_path,
        actor="operator",
        source="kanban-agent",
        surface="web",
    )

    channel = _approved_action(
        service,
        writer,
        "channel-create-from-template",
        {
            "template_id": "quick-change",
            "channel_id": "doc156-review",
            "task_id": "TASK-DOC156",
            "overrides": {"backend": "fake"},
        },
    )
    assert channel["status"] == "created"
    channel_id = str(channel["channel_id"])
    discussion = _approved_action(
        service,
        writer,
        "channel-discussion-start",
        {
            "channel_id": channel_id,
            "thread_id": "main",
            "task_id": "TASK-DOC156",
            "objective": "Assess delivery constraints before research.",
        },
    )
    assert discussion["status"] == "started"
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        replies = [
            event
            for event in log.read_all()
            if event.type == "channel.agent.reply.completed"
            and event.payload.get("thread_id") == "main"
        ]
        if len(replies) >= 3:
            break
        time.sleep(0.02)
    assert len(replies) == 3

    advance_discussion(
        state_dir,
        writer,
        channel_id=channel_id,
        thread_id="main",
        project_root=tmp_path,
    )
    advance_discussion(
        state_dir,
        writer,
        channel_id=channel_id,
        thread_id="main",
        project_root=tmp_path,
    )
    synthesis_request = next(
        event
        for event in reversed(log.read_all())
        if event.type == "channel.synthesis.requested"
        and event.payload.get("channel_id") == channel_id
    )
    host = SimpleNamespace(
        state_dir=state_dir,
        event_log=log,
        event_writer=writer,
        project_root=tmp_path,
        config=config,
        openclaw_client=None,
    )
    EventReactorMixin._on_channel_synthesis_requested(
        host,
        synthesis_request,
    )
    assert any(
        event.type == "channel.synthesis.proposed"
        and event.payload.get("request_id")
        == synthesis_request.payload["request_id"]
        for event in log.read_all()
    )

    request_dir = state_dir / "workflow-requests"
    request_dir.mkdir()
    (request_dir / "REQ-DOC156.json").write_text(
        json.dumps(
            {
                "request_id": "REQ-DOC156",
                "revision": 1,
                "status": "ready",
                "channel_id": channel_id,
                "thread_id": "main",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    research = _approved_action(
        service,
        writer,
        "research-start",
        {
            "task_id": "TASK-DOC156",
            "topic": "Collect evidence for the delivery decision.",
            "channel_id": channel_id,
            "thread_id": "main",
            "dispatch_id": "dispatch-doc156",
        },
    )
    assert research["status"] == "requested"
    invoke = next(
        event
        for event in reversed(log.read_all())
        if event.id == research["event_id"]
        and event.type == "workflow.invoke.requested"
    )

    transport = _RecordingTransport()
    orchestrator = Orchestrator(
        state_dir,
        config,
        transport,  # type: ignore[arg-type]
    )
    orchestrator.run_once(events=[invoke])
    fanout = next(
        event
        for event in log.read_all()
        if event.type == "fanout.started"
        and event.payload.get("stage_id") == "research-fanout"
    )
    child_dispatches = [
        event
        for event in log.read_all()
        if event.type == "fanout.child.dispatched"
        and event.payload.get("fanout_id") == fanout.payload["fanout_id"]
    ]
    assert len(child_dispatches) == 4
    orchestrator.run_once(events=[
        ZfEvent(
            type="research.child.completed",
            actor=str(event.payload["role_instance"]),
            task_id="TASK-DOC156",
            correlation_id=channel_id,
            payload={
                "fanout_id": fanout.payload["fanout_id"],
                "stage_id": "research-fanout",
                "child_id": event.payload["child_id"],
                "run_id": event.payload["run_id"],
                "role_instance": event.payload["role_instance"],
                "status": "completed",
                "report": {
                    "summary": f"{event.payload['child_id']} evidence",
                    "evidence_refs": ["source:mock"],
                },
            },
        )
        for event in child_dispatches
    ])
    synth_dispatch = next(
        event
        for event in reversed(log.read_all())
        if event.type == "fanout.synth.dispatched"
        and event.payload.get("fanout_id") == fanout.payload["fanout_id"]
    )
    orchestrator.run_once(events=[ZfEvent(
        type="fanout.synth.completed",
        actor="synthesizer",
        task_id="TASK-DOC156",
        correlation_id=channel_id,
        payload={
            "fanout_id": fanout.payload["fanout_id"],
            "stage_id": "research-fanout",
            "run_id": synth_dispatch.payload["run_id"],
            "role_instance": "synthesizer",
            "status": "completed",
            "research_summary": "Evidence-backed mock synthesis.",
            "evidence_refs": ["source:mock"],
            "open_questions": [],
            "report": {
                "summary": "Evidence-backed mock synthesis.",
                "recommendation": "approve",
            },
        },
    )])
    aggregate = next(
        event
        for event in reversed(log.read_all())
        if event.type == "fanout.aggregate.completed"
        and event.payload.get("fanout_id") == fanout.payload["fanout_id"]
    )
    artifact_ref = str(aggregate.payload["research_artifact_ref"])
    artifact_digest = str(
        aggregate.payload["research_artifact_digest"]
    )
    assert (state_dir / artifact_ref).is_file()

    adoption = _approved_action(
        service,
        writer,
        "research-adopt",
        {
            "task_id": "TASK-DOC156",
            "request_id": "REQ-DOC156",
            "request_revision": 1,
            "artifact_ref": artifact_ref,
            "artifact_digest": artifact_digest,
            "summary": "Adopt the mock evidence into the workflow request.",
            "channel_id": channel_id,
            "thread_id": "main",
        },
    )
    assert adoption["status"] == "adopted"

    delivery = _approved_action(
        service,
        writer,
        "workflow-invoke",
        {
            "task_id": "TASK-DOC156",
            "pattern_id": "delivery-smoke",
            "dispatch_id": "dispatch-doc156",
            "channel_id": channel_id,
            "thread_id": "main",
            "reason": "approved post-research delivery start",
        },
    )
    delivery_invoke = next(
        event
        for event in reversed(log.read_all())
        if event.id == delivery["event_id"]
    )
    orchestrator.run_once(events=[delivery_invoke])
    events = log.read_all()
    assert any(
        event.type == "workflow.invoke.accepted"
        and event.payload.get("pattern_id") == "delivery-smoke"
        for event in events
    )
    assert any(
        event.type == "fanout.child.dispatched"
        and event.payload.get("stage_id") == "delivery-smoke"
        and event.payload.get("role_instance") == "delivery_worker"
        for event in events
    )
    assert sum(
        event.type == "operator.action.resolved"
        for event in events
    ) == 5
