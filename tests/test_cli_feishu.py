"""Tests for zf feishu CLI bridge."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
import yaml

from zf.cli.main import main
from zf.cli import feishu as cli_feishu
from zf.cli.feishu import _parse_channel_targets
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.integrations.feishu.approval import ApprovalStore
from zf.integrations.feishu.mock_clients import MockFeishuBitableClient
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    config = {
        "version": "1.0",
        "project": {"name": "feishu-cli-test", "state_dir": "runtime-state"},
        "roles": [{"name": "dev", "backend": "mock"}],
    }
    (tmp_path / "zf.yaml").write_text(yaml.dump(config), encoding="utf-8")
    assert main(["init"]) == 0
    return tmp_path


def _message(text: str, *, message_id: str = "m1", user_id: str = "u1") -> dict:
    return {
        "type": "message",
        "user_id": user_id,
        "chat_id": "c1",
        "payload": {
            "text": text,
            "message_id": message_id,
        },
    }


def test_feishu_help_is_registered(capsys):
    with pytest.raises(SystemExit):
        main(["feishu", "--help"])
    out = capsys.readouterr().out
    assert "handle" in out
    assert "project-kanban" in out


def test_projection_backend_and_legacy_transport_are_mutually_exclusive(
    project: Path,
    capsys,
):
    result = main([
        "feishu",
        "sync-kanban-table",
        "--dry-run",
        "--backend",
        "lark-cli",
        "--transport",
        "mock",
    ])

    assert result == 1
    assert "mutually exclusive" in capsys.readouterr().err


def test_legacy_real_projection_transport_routes_to_lark_cli(
    project: Path,
    monkeypatch,
):
    selected = []
    monkeypatch.setattr(
        cli_feishu,
        "_build_bitable_client",
        lambda backend: selected.append(backend) or MockFeishuBitableClient(),
    )

    result = main([
        "feishu",
        "sync-kanban-table",
        "--dry-run",
        "--transport",
        "real",
    ])

    assert result == 0
    assert selected == ["lark-cli"]


def test_project_kanban_once_uses_registered_cli_entrypoint(
    project: Path,
    monkeypatch,
    capsys,
):
    state_dir = project / "runtime-state"
    TaskStore(state_dir / "kanban.json").add(
        Task(id="TASK-PROJECTION", title="Project me"),
    )
    client = MockFeishuBitableClient()
    monkeypatch.setenv("FEISHU_BITABLE_APP_TOKEN", "app")
    monkeypatch.setenv("FEISHU_BITABLE_TABLE_ID", "tbl")
    monkeypatch.setattr(
        "zf.cli.feishu_projection.LarkCliBitableClient",
        lambda: client,
    )

    result = main([
        "feishu",
        "project-kanban",
        "--once",
        "--backend",
        "lark-cli",
    ])

    assert result == 0
    assert len(client.created) == 1
    assert "reconciled=True" in capsys.readouterr().out
    event_types = {
        event.type
        for event in EventLog(state_dir / "events.jsonl").read_all()
    }
    assert {
        "feishu.kanban_projection.started",
        "feishu.kanban_projection.reconciled",
        "feishu.kanban_projection.stopped",
    } <= event_types


def test_project_kanban_can_create_project_scoped_target(
    project: Path,
    monkeypatch,
    capsys,
):
    state_dir = project / "runtime-state"
    TaskStore(state_dir / "kanban.json").add(
        Task(id="TASK-BOOTSTRAP", title="Bootstrap target"),
    )
    for key in (
        "FEISHU_BITABLE_APP_TOKEN",
        "FEISHU_BITABLE_TABLE_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("FEISHU_FOLDER_TOKEN", "fld-project")
    client = MockFeishuBitableClient()
    monkeypatch.setattr(
        "zf.cli.feishu_projection.LarkCliBitableClient",
        lambda: client,
    )

    result = main([
        "feishu",
        "project-kanban",
        "--once",
        "--create-target-if-missing",
    ])

    assert result == 0
    assert client.created_bases[0]["folder_token"] == "fld-project"
    assert client.created_tables[0]["name"] == "Kanban"
    assert len(client.created) == 1
    target = json.loads(
        (
            state_dir
            / "integrations"
            / "feishu"
            / "kanban-target.json"
        ).read_text(encoding="utf-8")
    )
    assert target["app_token"] == "app-mock-1"
    assert target["table_id"] == "tbl-mock-1"
    assert "reconciled=True" in capsys.readouterr().out
    events = EventLog(state_dir / "events.jsonl").read_all()
    event_types = {event.type for event in events}
    assert "feishu.kanban_projection.target_created" in event_types
    created_event = next(
        event
        for event in events
        if event.type == "feishu.kanban_projection.target_created"
    )
    assert created_event.payload["app_token"] == "app-...ck-1"
    assert created_event.payload["base_url"].endswith("/app-...ck-1")


def test_project_kanban_explicit_state_dir_still_loads_project_config(
    project: Path,
    monkeypatch,
    capsys,
):
    state_dir = project / "isolated-state"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(
        Task(id="TASK-ISOLATED-PROJECTION", title="Project isolated state"),
    )
    client = MockFeishuBitableClient()
    monkeypatch.setenv("FEISHU_BITABLE_APP_TOKEN", "app")
    monkeypatch.setenv("FEISHU_BITABLE_TABLE_ID", "tbl")
    monkeypatch.setattr(
        "zf.cli.feishu_projection.LarkCliBitableClient",
        lambda: client,
    )

    result = main([
        "feishu",
        "project-kanban",
        "--state-dir",
        str(state_dir),
        "--once",
        "--backend",
        "lark-cli",
    ])

    assert result == 0
    assert len(client.created) == 1
    assert "reconciled=True" in capsys.readouterr().out


def test_handle_status_query_uses_project_state_dir(
    project: Path,
    monkeypatch,
    capsys,
):
    state_dir = project / "runtime-state"
    TaskStore(state_dir / "kanban.json").add(Task(title="Feishu status task"))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_message("/zf status"))))

    result = main(["feishu", "handle", "--event-json", "-"])

    assert result == 0
    out = capsys.readouterr().out
    assert "1 total" in out
    assert not (project / ".zf").exists()


def test_handle_read_only_queries(project: Path, monkeypatch, capsys):
    state_dir = project / "runtime-state"
    TaskStore(state_dir / "kanban.json").add(
        Task(id="TASK-FEISHU", title="Blocked Feishu task", status="blocked"),
    )
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(type="human.escalate", actor="test", task_id="TASK-FEISHU"),
    )
    capsys.readouterr()

    cases = [
        ("/zf status", "1 total"),
        ("/zf tasks", "Blocked Feishu task"),
        ("/zf task TASK-FEISHU", "ID: TASK-FEISHU"),
        ("/zf blockers", "Blocked Feishu task"),
        ("/zf cost", "Total cost: $"),
        ("/zf handoff", "Recent events:"),
    ]
    for index, (command, expected) in enumerate(cases):
        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(json.dumps(_message(command, message_id=f"m-q-{index}"))),
        )
        result = main(["feishu", "handle", "--event-json", "-"])
        assert result == 0
        assert expected in capsys.readouterr().out


def test_handle_ask_emits_feishu_user_message(project: Path, monkeypatch, capsys):
    event = _message("/zf ask 当前 blocker 是什么？", message_id="m-ask")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))

    result = main(["feishu", "handle", "--event-json", "-"])

    assert result == 0
    out = capsys.readouterr().out
    assert "user.message" in out
    log = EventLog(project / "runtime-state" / "events.jsonl")
    message = next(e for e in log.read_all() if e.type == "user.message")
    assert message.actor == "feishu:u1"
    assert message.payload["source"] == "feishu"
    assert message.payload["chat_id"] == "c1"
    assert message.payload["message_id"] == "m-ask"
    assert "blocker" in message.payload["message"]


def test_handle_duplicate_message_is_not_reexecuted(
    project: Path,
    monkeypatch,
    capsys,
):
    event = _message("/zf note important info", message_id="m-note")

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    first = main([
        "feishu",
        "handle",
        "--event-json",
        "-",
        "--user-level",
        "u1=operator",
    ])
    assert first == 0

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    second = main([
        "feishu",
        "handle",
        "--event-json",
        "-",
        "--user-level",
        "u1=operator",
    ])

    assert second == 0
    out = capsys.readouterr().out
    assert "Duplicate" in out
    events = EventLog(project / "runtime-state" / "events.jsonl").read_all()
    notes = [event for event in events if event.type == "human.note"]
    assert len(notes) == 1


def test_handle_limited_control_actions_emit_events(
    project: Path,
    monkeypatch,
    capsys,
):
    cases = [
        ("/zf pause", "loop.pause_requested"),
        ("/zf resume", "loop.resume_requested"),
        ("/zf retry TASK-FEISHU", "task.retry_requested"),
        ("/zf cancel TASK-FEISHU", "task.cancel_requested"),
        ("/zf note ready for review", "human.note"),
    ]
    for index, (command, _event_type) in enumerate(cases):
        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(json.dumps(_message(command, message_id=f"m-c-{index}"))),
        )
        result = main([
            "feishu",
            "handle",
            "--event-json",
            "-",
            "--user-level",
            "u1=operator",
        ])
        assert result == 0
    capsys.readouterr()

    events = EventLog(project / "runtime-state" / "events.jsonl").read_all()
    event_types = {event.type for event in events}
    for _command, event_type in cases:
        assert event_type in event_types


def test_handle_rejects_operator_command_for_viewer(
    project: Path,
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps(_message("/zf pause", message_id="m-pause"))),
    )

    result = main(["feishu", "handle", "--event-json", "-"])

    assert result == 0
    assert "not authorized" in capsys.readouterr().out
    events = EventLog(project / "runtime-state" / "events.jsonl").read_all()
    assert not any(event.type == "loop.pause_requested" for event in events)


def test_handle_create_and_update_use_controlled_action_service(
    project: Path,
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps(_message("/zf create Feishu created task", message_id="m-create"))),
    )
    result = main([
        "feishu",
        "handle",
        "--event-json",
        "-",
        "--user-level",
        "u1=operator",
    ])
    assert result == 0
    out = capsys.readouterr().out
    assert "create-task: completed" in out

    store = TaskStore(project / "runtime-state" / "kanban.json")
    created = next(task for task in store.list_all() if task.title == "Feishu created task")

    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps(_message(
            f"/zf update {created.id} status=blocked reason='need human input'",
            message_id="m-update",
        ))),
    )
    result = main([
        "feishu",
        "handle",
        "--event-json",
        "-",
        "--user-level",
        "u1=operator",
    ])
    assert result == 0

    updated = store.get(created.id)
    assert updated is not None
    assert updated.status == "blocked"
    assert updated.blocked_reason == "need human input"
    events = EventLog(project / "runtime-state" / "events.jsonl").read_all()
    assert any(event.type == "task.created" and event.actor == "feishu:u1" for event in events)
    assert any(event.type == "task.updated" and event.actor == "feishu:u1" for event in events)
    assert any(event.type == "feishu.action.completed" for event in events)


def test_handle_attention_command_uses_controlled_action_service(
    project: Path,
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps(_message(
            "/zf attention ack attn-123 reason='operator saw it'",
            message_id="m-attn",
        ))),
    )

    result = main([
        "feishu",
        "handle",
        "--event-json",
        "-",
        "--user-level",
        "u1=operator",
    ])

    assert result == 0
    assert "attention-ack: recorded" in capsys.readouterr().out
    events = EventLog(project / "runtime-state" / "events.jsonl").read_all()
    attention = [event for event in events if event.type == "runtime.attention.acknowledged"][0]
    assert attention.actor == "feishu:u1"
    assert attention.payload["attention_id"] == "attn-123"
    assert attention.payload["reason"] == "operator saw it"


def test_push_once_routes_events_and_persists_offset(project: Path, capsys):
    log = EventLog(project / "runtime-state" / "events.jsonl")
    log.append(ZfEvent(type="human.escalate", actor="test", payload={"reason": "blocked"}))

    result = main([
        "feishu",
        "push",
        "--once",
        "--from-beginning",
        "--to",
        "ou_07bf51fbbd81df6de99e2f327bbc2d59",
        "--receive-id-type",
        "open_id",
    ])
    assert result == 0
    assert "Pushed 1" in capsys.readouterr().out

    result = main([
        "feishu",
        "push",
        "--once",
        "--to",
        "ou_07bf51fbbd81df6de99e2f327bbc2d59",
        "--receive-id-type",
        "open_id",
    ])
    assert result == 0
    assert "Pushed 0" in capsys.readouterr().out


def test_push_once_delivers_owner_visible_message(project: Path, capsys):
    log = EventLog(project / "runtime-state" / "events.jsonl")
    log.append(ZfEvent(
        type="owner.visible_message.requested",
        actor="zf-supervisor",
        task_id="TASK-ATTN",
        payload={
            "message_id": "omsg-cli-1",
            "attention_id": "attn-cli-1",
            "severity": "high",
            "human_action_required": True,
            "title": "needs owner",
            "summary": "manual review required",
            "delivery_targets": ["feishu"],
        },
    ))

    first = main([
        "feishu",
        "push",
        "--once",
        "--from-beginning",
        "--to",
        "ou_owner",
        "--receive-id-type",
        "open_id",
    ])
    assert first == 0
    assert "owner_visible_delivered=1" in capsys.readouterr().out

    second = main([
        "feishu",
        "push",
        "--once",
        "--to",
        "ou_owner",
        "--receive-id-type",
        "open_id",
    ])
    assert second == 0
    assert "owner_visible_delivered=0" in capsys.readouterr().out

    events = EventLog(project / "runtime-state" / "events.jsonl").read_all()
    assert [event.type for event in events].count("owner.visible_message.delivery_attempted") == 1
    assert [event.type for event in events].count("owner.visible_message.delivered") == 1


def test_parse_channel_targets_supports_per_route_receive_id_type():
    channels, receive_id_types = _parse_channel_targets(
        [
            "progress=chat_id:oc_project",
            "approval=open_id:ou_owner",
            "alert=oc_alert",
        ],
        default_receive_id_type="chat_id",
    )

    assert channels == {
        "progress": "oc_project",
        "approval": "ou_owner",
        "alert": "oc_alert",
    }
    assert receive_id_types == {
        "progress": "chat_id",
        "approval": "open_id",
    }


def test_approval_command_transitions_requested_approval(
    project: Path,
    monkeypatch,
    capsys,
):
    state_dir = project / "runtime-state"
    ApprovalStore(state_dir / "integrations" / "feishu" / "approvals.json").request(
        approval_id="APR-1",
        kind="escalation",
        task_id="TASK-X",
        reason="needs approval",
    )

    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps(_message("/zf approve APR-1", message_id="m-approve"))),
    )
    result = main([
        "feishu",
        "handle",
        "--event-json",
        "-",
        "--user-level",
        "u1=approver",
    ])

    assert result == 0
    assert "approved" in capsys.readouterr().out
    record = ApprovalStore(
        state_dir / "integrations" / "feishu" / "approvals.json",
    ).get("APR-1")
    assert record is not None
    assert record.status == "approved"
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert any(event.type == "feishu.approval.approved" for event in events)
