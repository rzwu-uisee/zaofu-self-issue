"""Stable Feishu thread identity helpers."""

from __future__ import annotations

from typing import Mapping


def feishu_thread_id(payload: Mapping[str, object] | None) -> str:
    values = payload or {}
    for key in ("thread_id", "root_message_id", "parent_message_id"):
        value = str(values.get(key) or "").strip()
        if value:
            return value
    return "main"


def feishu_debounce_scope(message: Mapping[str, object]) -> str:
    chat_id = str(message.get("chat_id") or "").strip()
    if not chat_id:
        return ""
    thread_id = feishu_thread_id(message)
    return f"{chat_id}:{thread_id}"


__all__ = ["feishu_debounce_scope", "feishu_thread_id"]
