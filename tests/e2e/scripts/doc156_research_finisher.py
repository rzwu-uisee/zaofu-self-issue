#!/usr/bin/env python3
"""Complete the model-output portion of the Doc 156 live Research fanout."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

from zf.cli.flow import build_flow_intake
from zf.core.events import EventWriter, ZfEvent
from zf.core.events.factory import event_log_from_project
from zf.core.config.loader import load_config
from zf.core.task.store import TaskStore
from zf.runtime.workflow_anchor import (
    bind_workflow_request_to_task,
    mark_workflow_managed_task,
)
from zf.runtime.workflow_origin import workflow_origin_digest
from zf.runtime.workflow_requests import load_workflow_request


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare-request", "finish-research", "report"))
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--channel-id", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--timeout", type=float, default=120.0)
    return parser


def _writer(project_root: Path, state_dir: Path) -> EventWriter:
    config = load_config(project_root / "zf.yaml")
    return EventWriter(event_log_from_project(state_dir, config=config))


def _prepare_request(args: argparse.Namespace) -> int:
    result = build_flow_intake(
        kind="issue",
        objective="Use Doc 156 research evidence before delivery starts.",
        project_id="doc156-kanban-collaboration-live",
        project_name="doc156-kanban-collaboration-live",
        acceptance=("Research artifact is adopted before workflow invocation.",),
        constraints=("Use the registered delivery-smoke workflow.",),
        request_id=args.request_id,
        source="doc156-playwright",
        created_by="doc156-e2e",
        channel_id=args.channel_id,
        thread_id="main",
        output=args.project_root / "docs" / "intake" / f"{args.request_id}.md",
    )
    if not result.get("request_projection_ref"):
        raise SystemExit("workflow request projection was not created")
    projection = load_workflow_request(args.state_dir, args.request_id)
    task_store = TaskStore(args.state_dir / "kanban.json")
    task = task_store.get(args.task_id)
    if task is None:
        raise SystemExit(f"research task does not exist: {args.task_id}")
    origin_binding = dict(projection.get("origin_binding") or {})
    bind_workflow_request_to_task(
        mark_workflow_managed_task(task),
        request_id=args.request_id,
        request_revision=int(projection.get("revision") or 0),
        origin_binding_digest=workflow_origin_digest(origin_binding),
    )
    task_store.update(task.id, contract=task.contract)
    _writer(args.project_root, args.state_dir).emit(
        "task.contract.update",
        actor="doc156-e2e",
        task_id=task.id,
        payload={
            "source": "doc156_prepare_request",
            "contract": asdict(task.contract),
            "execution_owner": "workflow",
            "request_id": args.request_id,
            "request_revision": int(projection.get("revision") or 0),
            "origin_binding_digest": workflow_origin_digest(
                origin_binding
            ),
        },
    )
    print(json.dumps(result, sort_keys=True))
    return 0


def _wait_for(
    writer: EventWriter,
    predicate,
    *,
    timeout: float,
    label: str,
):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events = writer.event_log.read_all()
        result = predicate(events)
        if result:
            return result
        time.sleep(0.1)
    raise TimeoutError(f"timed out waiting for {label}")


def _finish_research(args: argparse.Namespace) -> int:
    writer = _writer(args.project_root, args.state_dir)
    started = _wait_for(
        writer,
        lambda events: next((
            event
            for event in reversed(events)
            if event.type == "fanout.started"
            and event.payload.get("stage_id") == "research-fanout"
            and event.payload.get("pdd_id") == args.task_id
        ), None),
        timeout=args.timeout,
        label="research fanout start",
    )
    fanout_id = str(started.payload["fanout_id"])
    dispatches = _wait_for(
        writer,
        lambda events: (
            items
            if len(items := [
                event
                for event in events
                if event.type == "fanout.child.dispatched"
                and event.payload.get("fanout_id") == fanout_id
            ]) == 4
            else None
        ),
        timeout=args.timeout,
        label="four research child dispatches",
    )
    for event in dispatches:
        writer.append(ZfEvent(
            type="research.child.completed",
            actor=str(event.payload["role_instance"]),
            task_id=args.task_id,
            causation_id=event.id,
            correlation_id=args.channel_id,
            payload={
                "fanout_id": fanout_id,
                "stage_id": "research-fanout",
                "child_id": event.payload["child_id"],
                "run_id": event.payload["run_id"],
                "role_instance": event.payload["role_instance"],
                "status": "completed",
                "report": {
                    "summary": f"{event.payload['child_id']} browser evidence",
                    "evidence_refs": ["e2e:doc156-deterministic-research"],
                },
            },
        ))

    synth = _wait_for(
        writer,
        lambda events: next((
            event
            for event in reversed(events)
            if event.type == "fanout.synth.dispatched"
            and event.payload.get("fanout_id") == fanout_id
        ), None),
        timeout=args.timeout,
        label="research synthesis dispatch",
    )
    writer.append(ZfEvent(
        type="fanout.synth.completed",
        actor="synthesizer",
        task_id=args.task_id,
        causation_id=synth.id,
        correlation_id=args.channel_id,
        payload={
            "fanout_id": fanout_id,
            "stage_id": "research-fanout",
            "run_id": synth.payload["run_id"],
            "role_instance": "synthesizer",
            "status": "completed",
            "research_summary": "Doc 156 deterministic browser research completed.",
            "evidence_refs": ["e2e:doc156-deterministic-research"],
            "open_questions": [],
            "report": {
                "summary": "Doc 156 deterministic browser research completed.",
                "recommendation": "approve",
            },
        },
    ))
    aggregate = _wait_for(
        writer,
        lambda events: next((
            event
            for event in reversed(events)
            if event.type == "fanout.aggregate.completed"
            and event.payload.get("fanout_id") == fanout_id
            and event.payload.get("research_artifact_ref")
        ), None),
        timeout=args.timeout,
        label="research aggregate artifact",
    )
    result = _wait_for(
        writer,
        lambda events: next((
            event
            for event in reversed(events)
            if event.type == "workflow.result.available"
            and event.payload.get("terminal_event_id") == aggregate.id
        ), None),
        timeout=args.timeout,
        label="research result return",
    )
    workflow_run_id = str(
        aggregate.payload.get("workflow_run_id")
        or started.payload.get("workflow_run_id")
        or ""
    )
    if not workflow_run_id:
        raise RuntimeError("research aggregate is missing workflow_run_id")
    if not any(
        event.type == "run.completed"
        and (
            event.payload.get("workflow_run_id") == workflow_run_id
            or event.payload.get("run_id") == workflow_run_id
        )
        for event in writer.event_log.read_all()
    ):
        writer.append(ZfEvent(
            type="run.completed",
            actor="orchestrator",
            task_id=args.task_id,
            causation_id=aggregate.id,
            correlation_id=workflow_run_id,
            payload={
                "workflow_run_id": workflow_run_id,
                "run_id": workflow_run_id,
                "status": "completed",
                "source_event_id": aggregate.id,
            },
        ))
    print(json.dumps({
        "fanout_id": fanout_id,
        "artifact_ref": aggregate.payload["research_artifact_ref"],
        "artifact_digest": aggregate.payload["research_artifact_digest"],
        "result_event_id": result.id,
        "workflow_run_id": workflow_run_id,
    }, sort_keys=True))
    return 0


def _report(args: argparse.Namespace) -> int:
    events = _writer(args.project_root, args.state_dir).event_log.read_all()
    relevant = {
        "channel_created": sum(event.type == "channel.created" for event in events),
        "channel_replies": sum(
            event.type == "channel.agent.reply.completed"
            for event in events
        ),
        "channel_synthesis": sum(
            event.type == "channel.synthesis.proposed"
            for event in events
        ),
        "research_fanouts": sum(
            event.type == "fanout.started"
            and event.payload.get("stage_id") == "research-fanout"
            for event in events
        ),
        "research_adoptions": sum(
            event.type == "workflow.research.adopted"
            for event in events
        ),
        "delivery_dispatches": sum(
            event.type == "fanout.child.dispatched"
            and event.payload.get("stage_id") == "delivery-smoke"
            for event in events
        ),
        "codex_prompt_hooks": sum(
            event.type == "codex.hook.user_prompt_submit"
            and event.actor == "delivery_worker"
            for event in events
        ),
    }
    print(json.dumps(relevant, sort_keys=True))
    return 0


def main() -> int:
    args = _parser().parse_args()
    args.project_root = args.project_root.resolve()
    args.state_dir = args.state_dir.resolve()
    if args.mode == "prepare-request":
        return _prepare_request(args)
    if args.mode == "finish-research":
        return _finish_research(args)
    return _report(args)


if __name__ == "__main__":
    raise SystemExit(main())
