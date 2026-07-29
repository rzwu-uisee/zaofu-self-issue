"""Cleanup and redaction contract for the real Feishu smoke command."""

from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace

import pytest

import zf.cli.feishu_live_smoke as live_smoke
from zf.cli.feishu_live_smoke import (
    FeishuLiveSmokeError,
    execute_live_smoke,
    run_live_smoke,
)
from zf.integrations.feishu.transport import FeishuMessage, MockFeishuTransport


class LiveSmokeTransport(MockFeishuTransport):
    def __init__(
        self,
        *,
        fail_update: bool = False,
        fail_delete: bool = False,
        retain_deleted: bool = False,
        tombstone_deleted: bool = False,
    ) -> None:
        super().__init__()
        self.bot_open_id_value = "ou_test_bot"
        self.fail_update = fail_update
        self.fail_delete = fail_delete
        self.retain_deleted = retain_deleted
        self.tombstone_deleted = tombstone_deleted

    def send_card(self, message: FeishuMessage) -> str | None:
        message_id = super().send_card(message)
        self.recent_messages.append({
            "message_id": message_id,
            "chat_id": message.chat_id,
            "msg_type": "interactive",
        })
        return message_id

    def update_card(self, message_id: str, card: dict, sequence: int = 0) -> bool:
        if self.fail_update:
            raise RuntimeError("simulated update failure")
        return super().update_card(message_id, card, sequence)

    def delete_message(self, message_id: str) -> bool:
        if self.fail_delete:
            return False
        if self.retain_deleted:
            self.deleted_message_ids.append(message_id)
            return True
        if self.tombstone_deleted:
            self.deleted_message_ids.append(message_id)
            for row in self.recent_messages:
                if row.get("message_id") == message_id:
                    row["deleted"] = True
            return True
        return super().delete_message(message_id)


def test_execute_live_smoke_closes_all_stages_and_redacts_ids():
    transport = LiveSmokeTransport()

    result = execute_live_smoke(
        transport,
        target="oc_private_target",
        poll_attempts=1,
        sleep=lambda _: None,
    )

    assert result["ok"] is True
    assert result["stages"] == {
        "auth": "passed",
        "send": "passed",
        "list": "passed",
        "update": "passed",
        "cleanup": "recalled",
    }
    assert transport.deleted_message_ids == ["mock-msg-1"]
    assert result["message_deleted_after_cleanup"] is True
    encoded = json.dumps(result)
    assert "oc_private_target" not in encoded
    assert "mock-msg-1" not in encoded


def test_execute_live_smoke_recalls_message_after_update_failure():
    transport = LiveSmokeTransport(fail_update=True)

    with pytest.raises(FeishuLiveSmokeError) as caught:
        execute_live_smoke(
            transport,
            target="oc_private_target",
            poll_attempts=1,
            sleep=lambda _: None,
        )

    assert caught.value.stage == "update"
    assert caught.value.result["stages"]["cleanup"] == "recalled"
    assert transport.deleted_message_ids == ["mock-msg-1"]


def test_execute_live_smoke_fails_when_recall_fails():
    transport = LiveSmokeTransport(fail_delete=True)

    with pytest.raises(FeishuLiveSmokeError) as caught:
        execute_live_smoke(
            transport,
            target="oc_private_target",
            poll_attempts=1,
            sleep=lambda _: None,
        )

    assert caught.value.stage == "cleanup"
    assert caught.value.result["stages"]["cleanup"] == "failed"


def test_execute_live_smoke_fails_when_recalled_message_remains_visible():
    transport = LiveSmokeTransport(retain_deleted=True)

    with pytest.raises(FeishuLiveSmokeError) as caught:
        execute_live_smoke(
            transport,
            target="oc_private_target",
            poll_attempts=1,
            sleep=lambda _: None,
        )

    assert caught.value.stage == "cleanup"
    assert caught.value.result["stages"]["cleanup"] == "failed"


def test_execute_live_smoke_accepts_provider_deleted_tombstone():
    transport = LiveSmokeTransport(tombstone_deleted=True)

    result = execute_live_smoke(
        transport,
        target="oc_private_target",
        poll_attempts=1,
        sleep=lambda _: None,
    )

    assert result["ok"] is True
    assert result["message_deleted_after_cleanup"] is True


def test_live_smoke_requires_explicit_real_api_confirmation(capsys):
    rc = run_live_smoke(Namespace(
        confirm_real_api=False,
        to="oc_private_target",
    ))

    assert rc == 2
    assert "--confirm-real-api is required" in capsys.readouterr().err


def test_live_smoke_disallows_purpose_credential_fallback(
    tmp_path,
    monkeypatch,
    capsys,
):
    observed = {}
    monkeypatch.setattr(
        live_smoke,
        "resolve_project_context",
        lambda **_: SimpleNamespace(project_root=tmp_path),
    )
    monkeypatch.setattr(live_smoke, "load_project_env", lambda _: {})

    def resolve_credential(purpose, *, env, allow_fallback):
        observed.update({
            "purpose": purpose,
            "allow_fallback": allow_fallback,
        })
        return None

    monkeypatch.setattr(
        live_smoke,
        "credential_for_purpose",
        resolve_credential,
    )

    rc = run_live_smoke(Namespace(
        confirm_real_api=True,
        to="oc_private_target",
        purpose="kanban_agent",
        state_dir=None,
    ))

    assert rc == 1
    assert observed == {
        "purpose": "kanban_agent",
        "allow_fallback": False,
    }
    assert "exact Feishu credential" in capsys.readouterr().err
