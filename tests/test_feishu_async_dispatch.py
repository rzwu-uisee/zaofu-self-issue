"""feishu B4: async dispatch — WS handler never blocks on the agent run."""

from __future__ import annotations

import json
import time
from pathlib import Path

import yaml

from zf.cli.feishu_consume import dispatch_inbound_async
from zf.cli.main import main
from zf.core.config.project_context import resolve_project_context
from zf.core.events.log import EventLog
from zf.integrations.feishu.channel_progress_card import _dispatch_guided_kanban
from zf.integrations.feishu.transport import MockFeishuTransport


def _project(tmp_path: Path):
    (tmp_path / "zf.yaml").write_text(yaml.dump({
        "version": "1.0", "project": {"name": "t", "state_dir": ".zf"},
        "roles": [{"name": "dev", "backend": "mock"}],
        "integrations": {"feishu_routing": {
            "oc_x": {"target": "agent", "backend": "fake",
                     "channel_id": "ch-a", "default_member": "dev-1"}}}}))
    main(["init"])


def _event():
    return MockFeishuTransport().parse_webhook({
        "type": "message", "payload": {"text": "@dev-1 hi", "message_id": "m1"},
        "user_id": "ou_u", "chat_id": "oc_x"})


def test_caller_returns_immediately_thread_does_the_work(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)
    ctx = resolve_project_context()
    t = MockFeishuTransport()

    t0 = time.monotonic()
    fut = dispatch_inbound_async(_event(), context=ctx, transport=t)
    # the caller returned without waiting for the (background) reply
    assert (time.monotonic() - t0) < 0.5
    result = fut.result(timeout=10)  # now drain the background work
    assert result.get("status") == "replied"

    # A fake backend has no stream deltas, so the same bridge turn must still
    # project the committed assistant body as one readable exact-thread card.
    events = EventLog(ctx.state_dir / "events.jsonl").read_all()
    assert [e for e in events if e.type == "channel.message.posted"
            and e.payload.get("member_id") == "dev-1"]
    assert len(t.sent_messages) == 1
    message = t.sent_messages[0]
    assert (message.chat_id, message.thread_id) == ("oc_x", "m1")
    rendered = json.dumps(json.loads(message.content), ensure_ascii=False)
    assert "已回复" in rendered
    assert "dev-1 received the channel request" in rendered
    assert "member: " not in rendered and " provider: " not in rendered
    ledger_dir = ctx.state_dir / "integrations" / "feishu"
    assert (ledger_dir / "stream_ledger-dev-1.json").is_file()
    assert (ledger_dir / "delivery_ledger-dev-1.json").is_file()


def test_dispatch_uses_injected_executor(tmp_path, monkeypatch):
    import concurrent.futures
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)
    ctx = resolve_project_context()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = dispatch_inbound_async(_event(), context=ctx, transport=None,
                                     executor=ex)
        assert fut.result(timeout=10).get("status") == "replied"


def test_guided_kanban_dispatch_resolves_app_scoped_route(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(yaml.dump({
        "version": "1.0", "project": {"name": "t", "state_dir": ".zf"},
        "roles": [{"name": "dev", "backend": "mock"}],
        "integrations": {"feishu_routing": {
            "cli-kanban:oc_x": {
                "target": "kanban_agent",
                "backend": "fake",
                "default_member": "kanban-agent",
            },
        }},
    }))
    main(["init"])
    monkeypatch.setenv("FEISHU_APP_ID", "cli-kanban")
    ctx = resolve_project_context()

    future = _dispatch_guided_kanban(
        "Create a Task from the confirmed PRD.",
        {
            "context": ctx,
            "command": "channel-progress-create-task",
            "channel_id": "ch-prd-1",
            "thread_id": "main",
            "task_id": "",
            "user_id": "ou_u",
            "chat_id": "oc_x",
            "origin_binding": {"origin_message_id": "om_requirement"},
        },
    )

    result = future.result(timeout=10)
    assert result["status"] == "replied"
    assert result["kind"] == "kanban_agent_conversation"
