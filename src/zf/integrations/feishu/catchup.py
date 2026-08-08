"""feishu W5: catchup-on-restart (doc 99 §4.5, backlog 2026-06-22-1130).

A WS bridge process restart (Ctrl-C / OOM / deploy) loses every message the
operator sent during the gap — the live long-connection only resumes from
re-attach. Persist a per-chat cursor (last processed message + epoch-ms) and,
on restart, replay the gap via the transport's message-list REST endpoint into
the SAME inbound path, deduped.  The cursor is App/chat-scoped: two bot Apps in
one project group must not advance each other's replay high-water mark.

Three gotchas covered by regression tests:
- epoch-ms vs TZ-ambiguous string: lark renders local-time strings; store the
  epoch-ms reading at event time so a TZ change across restart can't shift the
  cutoff by hours. Readers prefer create_time_ms, fall back to parsing.
- REST minute-precision vs live ms-precision + reorder: floor the cutoff to the
  minute (so same-minute REST rows survive) AND step back a lookback margin (so a
  cross-minute out-of-order/silent-drop miss survives); dedup absorbs re-applies.
- bot-self回环: list returns the bot's own cards too — skip app/bot-sent rows or
  every restart re-fires the bot's replies as inbound.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any, Callable

from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path

_DEFAULT_LOOKBACK_MS = 120_000  # observed WS silent-drop / reorder window
_RICH_TEXT_MENTION_RE = re.compile(
    r"<at\b[^>]*\buser_id\s*=\s*[\"']([^\"']+)[\"'][^>]*>",
    re.IGNORECASE,
)


def _cursor_path(state_dir) -> Path:
    return Path(state_dir) / "integrations" / "feishu" / "bridge_cursor.json"


def _read_all(state_dir) -> dict:
    try:
        data = json.loads(_cursor_path(state_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _cursor_key(chat_id: str, app_id: str = "") -> str:
    """Return an App-scoped cursor key while retaining legacy no-App callers."""
    normalized_chat = str(chat_id or "").strip()
    normalized_app = str(app_id or "").strip()
    return f"{normalized_app}:{normalized_chat}" if normalized_app else normalized_chat


def read_cursor(state_dir, chat_id: str, *, app_id: str = "") -> dict:
    """Return the persisted cursor for one App/chat pair, or {}.

    Older files use the raw chat id.  We deliberately do not fall back from an
    App-scoped read to that legacy value: two Apps in one group must never
    inherit and advance each other's replay high-water mark.
    """
    entry = _read_all(state_dir).get(_cursor_key(chat_id, app_id))
    return entry if isinstance(entry, dict) else {}


def record(
    state_dir,
    chat_id: str,
    message_id: str,
    create_time: object,
    *,
    app_id: str = "",
) -> None:
    """Advance one App/chat cursor. No-op if message id/time is empty."""
    if not chat_id or not message_id or not create_time:
        return
    path = _cursor_path(state_dir)
    with locked_path(path):
        data = _read_all(state_dir)
        data[_cursor_key(chat_id, app_id)] = {
            "message_id": str(message_id),
            "create_time": str(create_time),
            "create_time_ms": _to_epoch_ms(create_time),
        }
        atomic_write_text(
            path,
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        )


def _to_epoch_ms(create_time: object) -> int:
    """Coerce a create_time into epoch ms. Accepts an epoch(-ms) numeric string
    or "YYYY-MM-DD HH:MM[:SS]" (lark REST local-time shape). 0 if uninterpretable
    → _newer_than treats it as older than any non-zero cursor (skip safely)."""
    if not create_time:
        return 0
    s = str(create_time).strip()
    if s.isdigit():
        return int(s)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return int(_dt.datetime.strptime(s, fmt).timestamp() * 1000)
        except ValueError:
            continue
    return 0


def addressed_to_bot(mention_ids, bot_open_id: str, *, chat_type: str = "") -> bool:
    """True when a message should be answered by THIS bot.

    A group can hold several bots; with broad read scope our app receives every
    message, so we must only reply when we're the @-target (the user's bug: an
    @other-bot message got a reply from us). p2p (DM) is always for us. When our
    own open_id is unknown we fail OPEN (reply) rather than black-hole everything.
    """
    if chat_type == "p2p":
        return True
    ids = [str(m) for m in (mention_ids or []) if m]
    if ids:
        return (not bot_open_id) or (bot_open_id in ids)
    # no mentions: a DM-like message (reply); in a group a no-mention message is
    # ambient chatter not addressed to us (skip only when we know it's a group).
    return chat_type != "group"


def _is_bot(msg: dict) -> bool:
    """True when a listed row was sent by the app/bot itself (skip on replay so
    the bridge never re-ingests its own reply cards)."""
    sender = msg.get("sender") if isinstance(msg.get("sender"), dict) else {}
    kind = str(sender.get("sender_type") or sender.get("id_type") or "").lower()
    return kind in {"app", "bot"}


def _mention_ids(msg: dict) -> list[str]:
    """Normalize REST mentions and recover rich-text @ tags when omitted.

    ``GET /im/v1/messages`` currently omits ``mentions`` and ``chat_type`` for
    some group rows. The message content still contains Lark's
    ``<at user_id=\"ou_...\"/>`` marker, so using it as a replay fallback keeps
    multi-Bot catchup aligned with the live WebSocket route guard.
    """

    ids: list[str] = []
    for mention in msg.get("mentions") or []:
        if isinstance(mention, dict):
            nested = mention.get("id")
            value = (
                nested.get("open_id") if isinstance(nested, dict) else nested
            ) or mention.get("open_id") or mention.get("user_id")
        else:
            value = mention
        value = str(value or "").strip()
        if value and value not in ids:
            ids.append(value)
    content = str(msg.get("content") or "")
    for match in _RICH_TEXT_MENTION_RE.finditer(content):
        value = match.group(1).strip()
        if value and value not in ids:
            ids.append(value)
    return ids


def _newer_than(messages, cursor_create_time: str, *, cursor_ms: int = 0,
                lookback_ms: int = _DEFAULT_LOOKBACK_MS) -> list[dict]:
    """Keep rows at-or-after (cursor minute floor − lookback), oldest-first."""
    raw_cutoff = cursor_ms or _to_epoch_ms(cursor_create_time)
    minute_floor = (raw_cutoff // 60_000) * 60_000
    cutoff = minute_floor - max(0, lookback_ms)

    def keep(m: dict) -> bool:
        ts = _to_epoch_ms(m.get("create_time"))
        return ts > 0 and ts >= cutoff

    fresh = [m for m in messages if keep(m)]
    fresh.sort(key=lambda m: _to_epoch_ms(m.get("create_time")))
    return fresh


def _msg_to_raw_event(msg: dict) -> dict:
    """A listed message row → the raw event dict parse_webhook(...) consumes
    (the local-fixture `type=message` shape), carrying create_time for cursor
    advance on the replay's way through bridge_inbound_message."""
    sender = msg.get("sender") if isinstance(msg.get("sender"), dict) else {}
    return {
        "type": "message",
        "payload": {
            "text": str(msg.get("content") or ""),
            "message_id": str(msg.get("message_id") or ""),
            "create_time": str(msg.get("create_time") or ""),
            "mentions": list(msg.get("mentions") or []),
            "chat_type": str(msg.get("chat_type") or ""),
        },
        "user_id": str(sender.get("id") or sender.get("open_id") or ""),
        "chat_id": str(msg.get("chat_id") or ""),
    }


def pending_events(
    state_dir,
    chat_id: str,
    *,
    list_fn: Callable[[], list[dict]],
    bot_open_id: str = "",
    app_id: str = "",
    lookback_ms: int = _DEFAULT_LOOKBACK_MS,
    fallback_chat_type: str = "",
    allow_unmentioned_group: bool = False,
) -> list[dict]:
    """Raw event dicts for messages newer than the saved cursor, oldest-first.

    No cursor (fresh deploy) → [] (never replay arbitrary history — the live
    stream picks up from now and writes the first cursor). Bot-self rows skipped,
    and rows that @-target a DIFFERENT bot are skipped (addressed_to_bot).
    `list_fn` is injectable for tests (production: transport.list_recent)."""
    cursor = read_cursor(state_dir, chat_id, app_id=app_id)
    cursor_ct = str(cursor.get("create_time") or "")
    if not cursor_ct:
        return []
    try:
        cursor_ms = int(cursor.get("create_time_ms") or 0)
    except (TypeError, ValueError):
        cursor_ms = 0
    messages = list_fn() or []
    fresh = _newer_than(messages, cursor_ct, cursor_ms=cursor_ms, lookback_ms=lookback_ms)
    out = []
    for m in fresh:
        if _is_bot(m):
            continue
        mention_ids = _mention_ids(m)
        chat_type = str(m.get("chat_type") or fallback_chat_type or "")
        addressed = addressed_to_bot(
            mention_ids,
            bot_open_id,
            chat_type=chat_type,
        )
        if (
            not addressed
            and allow_unmentioned_group
            and chat_type == "group"
            and not mention_ids
        ):
            addressed = True
        if not addressed:
            continue
        out.append(_msg_to_raw_event({
            **m,
            "mentions": mention_ids,
            "chat_type": chat_type,
        }))
    return out


def newest_marker(messages) -> tuple[str, str]:
    """(message_id, create_time) of the newest listed row — the monotonic
    high-water mark to advance the cursor to after a catchup pass (covers
    bot-self rows that were skipped, so they aren't re-listed forever)."""
    best, best_ms = None, -1
    for m in messages or []:
        ts = _to_epoch_ms(m.get("create_time"))
        if ts > best_ms:
            best, best_ms = m, ts
    if not best:
        return ("", "")
    return (str(best.get("message_id") or ""), str(best.get("create_time") or ""))


def catchup_chat(
    state_dir,
    chat_id: str,
    *,
    list_recent: Callable[[str], list[dict]],
    dispatch: Callable[[dict], Any],
    bot_open_id: str = "",
    app_id: str = "",
    lookback_ms: int = _DEFAULT_LOOKBACK_MS,
    fallback_chat_type: str = "",
    allow_unmentioned_group: bool = False,
) -> dict:
    """Replay the gap for one chat: list → filter newer-than-cursor → dispatch
    each (dedup is enforced downstream by IdempotencyStore) → advance cursor to
    the high-water mark. Returns {replayed, chat_id}."""
    messages = list_recent(chat_id) or []
    events = pending_events(state_dir, chat_id, list_fn=lambda: messages,
                            bot_open_id=bot_open_id, app_id=app_id,
                            lookback_ms=lookback_ms,
                            fallback_chat_type=fallback_chat_type,
                            allow_unmentioned_group=allow_unmentioned_group)
    for raw in events:
        dispatch(raw)
    marker_id, marker_ct = newest_marker(messages)
    if marker_id and marker_ct:
        record(state_dir, chat_id, marker_id, marker_ct, app_id=app_id)
    return {"chat_id": chat_id, "replayed": len(events)}
