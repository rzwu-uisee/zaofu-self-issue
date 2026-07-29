"""Cleanup-safe real Feishu transport smoke."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid
from argparse import Namespace
from datetime import datetime, timezone
from typing import Any, Callable

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import load_project_env, resolve_project_context
from zf.integrations.feishu.bot_credentials import credential_for_purpose
from zf.integrations.feishu.transport import (
    FeishuHttpTransport,
    FeishuMessage,
    FeishuTransport,
)


class FeishuLiveSmokeError(RuntimeError):
    """A live-smoke stage failed after cleanup was attempted."""

    def __init__(self, stage: str, detail: str, result: dict[str, Any]) -> None:
        super().__init__(detail)
        self.stage = stage
        self.result = result


def _digest(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]}"


def _card(*, marker: str, status: str, message: str) -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue" if status == "running" else "green",
            "title": {
                "tag": "plain_text",
                "content": "ZaoFu Feishu live smoke",
            },
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**Status:** {status}\n{message}",
                },
            },
            {
                "tag": "note",
                "elements": [{
                    "tag": "plain_text",
                    "content": marker,
                }],
            },
        ],
    }


def execute_live_smoke(
    transport: FeishuTransport,
    *,
    target: str,
    receive_id_type: str = "chat_id",
    message: str = "ZaoFu real transport verification",
    keep_message: bool = False,
    poll_attempts: int = 5,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Exercise auth, send, read, update, and recall against one transport."""

    marker = f"zf-live-smoke:{uuid.uuid4().hex}"
    result: dict[str, Any] = {
        "ok": False,
        "target_digest": _digest(target),
        "message_digest": "",
        "stages": {},
    }
    message_id = ""
    failed_stage = ""
    failure: Exception | None = None
    try:
        bot_id = transport.bot_open_id()
        if not bot_id:
            raise RuntimeError("bot identity was empty")
        result["bot_digest"] = _digest(bot_id)
        result["stages"]["auth"] = "passed"

        message_id = str(transport.send_card(FeishuMessage(
            chat_id=target,
            content=json.dumps(
                _card(marker=marker, status="running", message=message),
                ensure_ascii=False,
            ),
            msg_type="interactive",
            receive_id_type=receive_id_type,
        )) or "")
        if not message_id:
            raise RuntimeError("send_card returned no message id")
        result["message_digest"] = _digest(message_id)
        result["stages"]["send"] = "passed"

        found = False
        for attempt in range(max(1, poll_attempts)):
            rows = transport.list_recent(target, page_size=20)
            found = any(
                str(row.get("message_id") or "") == message_id
                for row in rows
                if isinstance(row, dict)
            )
            if found:
                break
            if attempt + 1 < max(1, poll_attempts):
                sleep(0.5)
        if not found:
            raise RuntimeError("sent message was not visible in recent messages")
        result["stages"]["list"] = "passed"

        updated = transport.update_card(
            message_id,
            _card(marker=marker, status="verified", message=message),
        )
        if not updated:
            raise RuntimeError("update_card returned false")
        result["stages"]["update"] = "passed"
    except Exception as exc:  # cleanup below owns the final verdict
        failure = exc
        failed_stage = next(
            (
                stage
                for stage in ("auth", "send", "list", "update")
                if stage not in result["stages"]
            ),
            "unknown",
        )
        result["stages"][failed_stage] = "failed"
    finally:
        if message_id and not keep_message:
            try:
                deleted = transport.delete_message(message_id)
                if not deleted:
                    raise RuntimeError("delete_message returned false")
                still_live = True
                for attempt in range(max(1, poll_attempts)):
                    rows = transport.list_recent(target, page_size=20)
                    matching = [
                        row
                        for row in rows
                        if isinstance(row, dict)
                        and str(row.get("message_id") or "") == message_id
                    ]
                    still_live = any(
                        not bool(row.get("deleted"))
                        for row in matching
                    )
                    if not still_live:
                        break
                    if attempt + 1 < max(1, poll_attempts):
                        sleep(0.5)
                if still_live:
                    raise RuntimeError("recalled message remained live")
                result["stages"]["cleanup"] = "recalled"
                result["message_deleted_after_cleanup"] = True
            except Exception as cleanup_exc:
                result["stages"]["cleanup"] = "failed"
                if failure is None:
                    failure = cleanup_exc
                    failed_stage = "cleanup"
        elif message_id:
            result["stages"]["cleanup"] = "kept"
        else:
            result["stages"]["cleanup"] = "not_needed"

    if failure is not None:
        raise FeishuLiveSmokeError(
            failed_stage,
            str(failure),
            result,
        ) from failure
    result["ok"] = True
    return result


def run_live_smoke(args: Namespace) -> int:
    if not bool(getattr(args, "confirm_real_api", False)):
        print(
            "Error: --confirm-real-api is required for external Feishu calls",
            file=sys.stderr,
        )
        return 2
    target = str(getattr(args, "to", "") or "").strip()
    if not target:
        print("Error: --to is required", file=sys.stderr)
        return 2
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
        load_project_env(context.project_root)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    purpose = str(getattr(args, "purpose", "kanban_agent") or "kanban_agent")
    credential = credential_for_purpose(
        purpose,
        env=os.environ,
        allow_fallback=False,
    )
    if credential is None:
        print(
            f"Error: exact Feishu credential for purpose {purpose!r} is unavailable",
            file=sys.stderr,
        )
        return 1
    transport = FeishuHttpTransport(
        app_id=credential.app_id,
        app_secret=credential.app_secret,
    )
    try:
        result = execute_live_smoke(
            transport,
            target=target,
            receive_id_type=str(
                getattr(args, "receive_id_type", "chat_id") or "chat_id"
            ),
            message=str(
                getattr(args, "message", "")
                or "ZaoFu real transport verification"
            ),
            keep_message=bool(getattr(args, "keep_message", False)),
        )
    except FeishuLiveSmokeError as exc:
        message_digest = str(exc.result.get("message_digest") or "")
        print(json.dumps({
            **exc.result,
            "error": {
                "stage": exc.stage,
                "type": type(exc.__cause__).__name__,
                "detail": "external Feishu smoke stage failed",
            },
            "message_digest": message_digest,
            "purpose": purpose,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False, sort_keys=True))
        return 1

    result.update({
        "purpose": purpose,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    })
    if str(getattr(args, "format", "json") or "json") == "json":
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        stages = ", ".join(
            f"{name}={status}"
            for name, status in result["stages"].items()
        )
        print(f"Feishu live smoke passed ({stages})")
    return 0
