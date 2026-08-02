from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from zf.core.config.loader import load_config
from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.kanban_proposals import pending_kanban_proposals
from zf.web.server import create_app


ROOT = Path(__file__).resolve().parents[2]


def test_plan_answer_resumes_original_intent_then_approves_exact_proposal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]", encoding="utf-8")
    script = tmp_path / "fake_plan_then_proposal.py"
    script.write_text(
        "\n".join([
            "import json, sys",
            "request = json.loads(sys.stdin.readline())",
            "prompt = request['message']['content'][0]['text']",
            "if 'Answer:' in prompt:",
            "    reply = {'action_proposal': {",
            "        'action': 'create-task',",
            "        'intent': {",
            "            'decision': 'propose_action',",
            "            'source_quote': 'Create a task',",
            "        },",
            "        'payload': {",
            "            'title': 'Implement route-aware delivery',",
            "            'contract': {",
            "                'behavior': 'Implement the selected direct route.',",
            "                'verification': 'uv run pytest tests/test_route.py -q',",
            "            },",
            "        },",
            "        'reason': 'The owner selected the direct route.',",
            "    }}",
            "else:",
            "    reply = {'plan_request': {",
            "        'header': 'Delivery route',",
            "        'id': 'route',",
            "        'question': 'Which route should create the task?',",
            "        'options': [",
            "            {'id': 'direct', 'label': 'Direct (Recommended)', 'description': 'Create one tracked task.'},",
            "            {'id': 'research', 'label': 'Research', 'description': 'Gather evidence first.'},",
            "        ],",
            "        'allow_other': True,",
            "    }}",
            "print(json.dumps({'type':'system','session_id':'mock-plan-session'}), flush=True)",
            "print(json.dumps({'type':'result','session_id':'mock-plan-session','result':json.dumps(reply)}), flush=True)",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setenv(
        "ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD",
        f"{sys.executable} {script}",
    )
    client = TestClient(create_app(state_dir, project_root=tmp_path))
    headers = {"x-zf-web-token": "test-token"}
    chat_route = "/api/actions/chat-orchestrator"

    first = client.post(
        chat_route,
        headers=headers,
        json={
            "backend": "claude-headless",
            "project_id": "project-plan-e2e",
            "conversation_id": "kanban:project-plan-e2e",
            "thread_key": "main",
            "sync": True,
            "message": (
                "Create a task for route-aware delivery, but ask me which "
                "route to use first."
            ),
        },
    )
    assert first.status_code == 200, first.text
    plan = first.json()["reply"]["plan_request"]

    second = client.post(
        chat_route,
        headers=headers,
        json={
            "backend": "claude-headless",
            "project_id": "project-plan-e2e",
            "conversation_id": "kanban:project-plan-e2e",
            "thread_key": "main",
            "sync": True,
            "plan_response": {
                "request_event_id": plan["request_event_id"],
                "request_id": plan["request_id"],
                "revision": plan["revision"],
                "question_id": plan["question_id"],
                "option_id": "direct",
                "answer": "forged",
            },
        },
    )
    assert second.status_code == 200, second.text
    proposal = second.json()["reply"]["action_proposal"]
    assert proposal["action"] == "create-task"
    assert proposal["valid"] is True
    assert second.json()["reply"]["resumed"] is True

    approved = client.post(
        "/api/actions/create-task",
        headers=headers,
        json={
            **proposal["payload"],
            "proposal_event_id": proposal["proposal_event_id"],
        },
    )
    assert approved.status_code in {200, 201, 202}, approved.text
    assert approved.json()["ok"] is True

    events = EventLog(state_dir / "events.jsonl").read_all()
    assert len([
        event for event in events
        if event.type == "kanban.agent.plan.answered"
    ]) == 1
    assert len([
        event for event in events
        if event.type == "operator.action.proposed"
    ]) == 1
    assert len([
        event for event in events
        if event.type == "task.created"
        and event.payload.get("request", {}).get("proposal_event_id")
        == proposal["proposal_event_id"]
    ]) == 1
    assert pending_kanban_proposals(events) == []


def test_channel_prd_context_enters_task_workflow_plan_before_ignition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]", encoding="utf-8")
    (tmp_path / "zf.yaml").write_text(
        (ROOT / "zf.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "examples").symlink_to(
        ROOT / "examples",
        target_is_directory=True,
    )
    (tmp_path / "skills").symlink_to(
        ROOT / "skills",
        target_is_directory=True,
    )
    task = Task(
        id="TASK-CHANNEL-PLAN",
        title="Deliver the canonical Channel PRD",
        contract=TaskContract(
            behavior="Implement the accepted Channel requirement.",
            verification="Run the Task acceptance checks.",
            spec_ref="channel-artifacts/ch-prd/prd.md",
            source_ref="channel-artifacts/ch-prd/prd.md",
            source_revision="1",
            source_mode="channel_prd",
            evidence_contract={
                "channel_id": "ch-prd",
                "thread_id": "main",
                "channel_member_id": "product-pm",
                "leader_revision": 1,
                "prd_revision": 1,
                "source_digest": "sha256:canonical-prd",
            },
            handoff_artifacts=["channel-artifacts/ch-prd/prd.md"],
        ),
    )
    TaskStore(state_dir / "kanban.json").add(task)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.emit(
        "channel.created",
        actor="web",
        correlation_id="ch-prd",
        payload={
            "channel_id": "ch-prd",
            "name": "Canonical PRD",
            "owner_actor_ref": "owner:operator",
            "leader_member_id": "product-pm",
            "leader_revision": 1,
            "source": "test",
        },
    )
    writer.emit(
        "channel.member.added",
        actor="web",
        correlation_id="ch-prd",
        payload={
            "channel_id": "ch-prd",
            "member_id": "product-pm",
            "member_type": "provider_agent",
            "channel_role": "product_pm",
            "provider": "deterministic",
            "permission_profile": "read_only",
            "permissions": [
                "read",
                "message",
                "summarize",
                "propose_workflow",
            ],
            "source": "test",
        },
    )
    writer.emit(
        "channel.consensus.proposed",
        actor="product-pm",
        correlation_id="ch-prd",
        payload={
            "channel_id": "ch-prd",
            "thread_id": "main",
            "prd_ref": "channel-artifacts/ch-prd/prd.md",
            "prd_digest": "canonical-prd",
            "prd_revision": 1,
            "owner_actor_ref": "owner:operator",
            "proposed_by": "product-pm",
            "source": "test",
        },
    )
    writer.emit(
        "channel.consensus.reached",
        actor="owner:operator",
        correlation_id="ch-prd",
        payload={
            "channel_id": "ch-prd",
            "thread_id": "main",
            "prd_ref": "channel-artifacts/ch-prd/prd.md",
            "prd_digest": "canonical-prd",
            "prd_revision": 1,
            "confirmed_by": "owner:operator",
            "source": "test",
        },
    )
    script = tmp_path / "fake_task_workflow_plan.py"
    script.write_text(
        "\n".join([
            "import json, sys",
            "json.loads(sys.stdin.readline())",
            "reply = {'plan_request': {",
            "  'subject_type': 'task_workflow',",
            "  'header': 'Workflow route',",
            "  'id': 'workflow-route',",
            "  'question': 'How should TASK-CHANNEL-PLAN run?',",
            "  'options': [",
            "    {'id': 'research', 'label': 'Research (Recommended)', 'recommended': True,",
            "     'description': 'Collect evidence first.',",
            "     'effect': {'mode': 'propose', 'action': 'workflow-start', 'payload': {",
            "       'task_id': 'TASK-CHANNEL-PLAN', 'route_id': 'research:fixed',",
            "       'objective': 'Research the canonical PRD.', 'parameters': {}}}},",
            "    {'id': 'delivery', 'label': 'PRD delivery',",
            "     'description': 'Run the configured delivery route.',",
            "     'effect': {'mode': 'propose', 'action': 'workflow-start', 'payload': {",
            "       'task_id': 'TASK-CHANNEL-PLAN', 'route_id': 'delivery:prd:standard',",
            "       'objective': 'Deliver the canonical PRD.', 'parameters': {}}}},",
            "    {'id': 'defer', 'label': 'No workflow yet',",
            "     'description': 'Keep the Task tracked.', 'effect': {'mode': 'continue'}}",
            "  ],",
            "  'allow_other': True",
            "}}",
            "print(json.dumps({'type':'system','session_id':'mock-workflow-session'}), flush=True)",
            "print(json.dumps({'type':'result','session_id':'mock-workflow-session','result':json.dumps(reply)}), flush=True)",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setenv(
        "ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD",
        f"{sys.executable} {script}",
    )
    client = TestClient(create_app(
        state_dir,
        config=load_config(tmp_path / "zf.yaml"),
        project_root=tmp_path,
    ))
    headers = {"x-zf-web-token": "test-token"}
    expected_authority = {
        "channel_id": "ch-prd",
        "thread_id": "main",
        "channel_member_id": "product-pm",
        "leader_revision": 1,
        "prd_revision": 1,
        "source_ref": "channel-artifacts/ch-prd/prd.md",
        "source_digest": "sha256:canonical-prd",
    }

    planned = client.post(
        "/api/actions/chat-orchestrator",
        headers=headers,
        json={
            "backend": "claude-headless",
            "project_id": "project-channel-plan",
            "conversation_id": "kanban:project-channel-plan",
            "thread_key": "main",
            "task_id": task.id,
            "sync": True,
            "message": "Plan a workflow for the canonical Channel PRD.",
        },
    )

    assert planned.status_code == 200, planned.text
    plan = planned.json()["reply"]["plan_request"]
    assert plan["valid"] is True, plan["validation_error"]
    selected = plan["options"][0]["submit_payload"]
    assert {
        key: selected["parameters"][key]
        for key in expected_authority
    } == expected_authority
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert not any(
        event.type == "workflow.invoke.requested"
        for event in events
    )

    proposed = client.post(
        "/api/actions/kanban-plan-apply",
        headers=headers,
        json={
            "plan_response": {
                "request_event_id": plan["request_event_id"],
                "request_id": plan["request_id"],
                "revision": plan["revision"],
                "question_id": plan["question_id"],
                "option_id": "research",
                "answer": "forged",
            },
        },
    )

    assert proposed.status_code == 202, proposed.text
    assert proposed.json()["status"] == "proposal_ready"
    assert proposed.json()["proposed_action"] == "workflow-start"
    final_events = EventLog(state_dir / "events.jsonl").read_all()
    proposal = next(
        event.payload["proposal"]
        for event in final_events
        if event.type == "operator.action.proposed"
    )
    assert proposal["action"] == "workflow-start"
    assert {
        key: proposal["payload"]["parameters"][key]
        for key in expected_authority
    } == expected_authority
    assert not any(
        event.type == "workflow.invoke.requested"
        for event in final_events
    )
