"""feishu W1: in-process always-on WS bridge (`zf feishu bridge --watch`, doc 99 §4.1).

Replaces the throwaway nohup receiver script from doc 98's live verification. Runs
the lark-oapi WS long-connection in-process and, per inbound chat:

  on_message → PendingQueue.push (W2 debounce)
    → flush(batch) → block(scope) → dispatch_inbound_async (B4 async, never blocks
      the WS ping) → on completion unblock(scope)

The transport is constructed ONCE at startup (a fixed FeishuHttpTransport), which
removes the "two replies merged" bug from doc 99 §3 (that came from swapping
transport mid-stream while reusing a state_dir). Session continuity across turns
is already provided by the channel HeadlessThreadStore (stable channel_id + thread
from bridge_inbound_message), so each turn resumes the previous provider session —
no separate session store here (doc 99 §4.3).

`BridgeWatch` (the queue+dispatch core) has no lark dependency so it tests without
a live WS. `run_bridge_watch` is the thin WS glue that lazily imports lark-oapi.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
from typing import Any, Callable

from zf.cli.feishu_consume import dispatch_inbound_async
from zf.integrations.feishu.thread_scope import feishu_debounce_scope
from zf.integrations.feishu.transport import MockFeishuTransport

DEFAULT_DEBOUNCE_MS = 600
DEFAULT_PROJECTION_INTERVAL_SECONDS = 1.0


def sdk_log_level(lark_module: Any) -> Any:
    """Avoid INFO logs because the SDK prints WS URL query parameters there."""
    log_level = getattr(lark_module, "LogLevel", None)
    return getattr(log_level, "WARNING", getattr(log_level, "ERROR", None))


def workspace_message_is_addressed(
    *,
    mention_ids: list[str],
    bot_open_id: str,
    chat_type: str,
    resolution: Any | None,
) -> bool:
    """Accept a group message for its primary responder when it has no @.

    Explicit @mentions always retain the existing Feishu behavior.  For a
    project collaboration group, a message with no bot mention is routed only
    to the binding's configured primary responder, preventing the Kanban and
    Run Manager bots from both treating it as an unscoped command.
    """
    from zf.integrations.feishu.catchup import addressed_to_bot

    if addressed_to_bot(mention_ids, bot_open_id, chat_type=chat_type):
        return True
    if resolution is None or chat_type == "p2p":
        return False
    return bool(
        not mention_ids
        and resolution.binding.primary_responder == resolution.index_route.purpose
    )


def should_ignore_inbound_message(
    *,
    text: str,
    message_type: str,
    sender_type: str,
) -> bool:
    """Reject bridge-owned card echoes before they enter a Channel turn.

    Feishu emits ``im.message.receive_v1`` for an app's own interactive result
    cards. Those cards carry no operator text, but are sometimes threaded and
    addressed to the bot, which otherwise creates a second empty headless turn.
    User text and attachments are deliberately left alone.
    """

    if str(sender_type or "").strip().lower() in {"app", "bot"}:
        return True
    return (
        not str(text or "").strip()
        and str(message_type or "").strip().lower() in {"interactive", "system"}
    )


def merge_batch(batch: list[dict[str, Any]]) -> dict[str, Any]:
    """Collapse a debounced batch of normalized inbound messages into one raw
    event dict (newline-joined text, last message's ids)."""
    texts = [str(m.get("text") or "") for m in batch if m.get("text")]
    last = batch[-1] if batch else {}
    return {
        "type": "message",
        "payload": {"text": "\n".join(texts),
                    "message_id": str(last.get("message_id") or ""),
                    "parent_message_id": str(last.get("parent_message_id") or ""),
                    "root_message_id": str(last.get("root_message_id") or ""),
                    "quote_message_id": str(last.get("quote_message_id") or ""),
                    "thread_id": str(last.get("thread_id") or ""),
                    "create_time": str(last.get("create_time") or ""),
                    "bot_open_id": str(last.get("bot_open_id") or ""),
                    "app_id": str(last.get("app_id") or ""),
                    "mention_ids": list(last.get("mention_ids") or [])},
        "user_id": str(last.get("user_id") or ""),
        "chat_id": str(last.get("chat_id") or ""),
    }


class BridgeWatch:
    """Queue + dispatch core. Inject `dispatch` in tests to avoid a live backend."""

    def __init__(self, context, transport, *, debounce_ms: int = DEFAULT_DEBOUNCE_MS,
                 dispatch: Callable | None = None,
                 inbound_resolver: Callable[[Any], Any | None] | None = None,
                 on_resolved: Callable[[Any, Any], None] | None = None) -> None:
        from zf.integrations.feishu.pending_queue import PendingQueue

        self.context = context
        self.transport = transport
        self._dispatch = dispatch or dispatch_inbound_async
        self._inbound_resolver = inbound_resolver
        self._on_resolved = on_resolved
        self._futures: set[Any] = set()
        self._futures_lock = threading.Lock()
        self.queue = PendingQueue(debounce_ms, self._on_flush)

    def on_message(self, normalized: dict[str, Any]) -> int:
        """Feed one normalized inbound message {text, message_id, user_id, chat_id}
        into the per-chat debounce queue. Returns queued count (0 if no chat_id)."""
        scope = feishu_debounce_scope(normalized)
        if not scope:
            return 0
        return self.queue.push(scope, normalized)

    def _on_flush(self, scope: str, batch: list[dict[str, Any]]) -> None:
        # block the scope so messages arriving during the run accumulate without
        # firing a second run; unblock when this run's future settles.
        self.queue.block(scope)
        print(f"[bridge] flushing chat={scope} batch={len(batch)}", flush=True)
        event = MockFeishuTransport().parse_webhook(merge_batch(batch))
        if event is None:
            self.queue.unblock(scope)
            return
        dispatch_context = self.context
        if self._inbound_resolver is not None:
            try:
                resolved = self._inbound_resolver(event)
            except Exception as exc:  # noqa: BLE001 - fail closed, WS stays live.
                print(
                    f"[bridge] route resolution failed chat={scope}: {exc!r}",
                    file=sys.stderr,
                    flush=True,
                )
                self.queue.unblock(scope)
                return
            if resolved is None:
                print(
                    f"[bridge] drop unmapped workspace route chat={scope}",
                    flush=True,
                )
                self.queue.unblock(scope)
                return
            dispatch_context = resolved.context
            setattr(event, "route", resolved.route)
            setattr(event, "feishu_binding_id", resolved.binding.binding_id)
            try:
                _record_workspace_route(dispatch_context, event, resolved)
                if self._on_resolved is not None:
                    self._on_resolved(resolved, event)
            except Exception as exc:  # noqa: BLE001 - routing audit must not kill WS.
                print(
                    f"[bridge] route projection failed chat={scope}: {exc!r}",
                    file=sys.stderr,
                    flush=True,
                )
        try:
            future = self._dispatch(
                event,
                context=dispatch_context,
                transport=self.transport,
            )
        except Exception as exc:  # noqa: BLE001 - keep the bridge observable and live.
            print(f"[bridge] dispatch submit failed chat={scope}: {exc!r}",
                  file=sys.stderr, flush=True)
            self.queue.unblock(scope)
            return
        with self._futures_lock:
            self._futures.add(future)

        def _done(settled: Any) -> None:
            try:
                result = settled.result()
                print(
                    f"[bridge] dispatch done chat={scope} "
                    f"status={result.get('status') if isinstance(result, dict) else type(result).__name__} "
                    f"kind={result.get('kind') if isinstance(result, dict) else ''}",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001 - background failures are otherwise invisible.
                print(f"[bridge] dispatch failed chat={scope}: {exc!r}",
                      file=sys.stderr, flush=True)
            with self._futures_lock:
                self._futures.discard(settled)
            self.queue.unblock(scope)

        future.add_done_callback(_done)

    def drain(self, timeout: float = 30.0) -> None:
        with self._futures_lock:
            pending = list(self._futures)
        for future in pending:
            try:
                future.result(timeout=timeout)
            except Exception:  # noqa: BLE001 — drain must not raise on a failed run
                pass

    def shutdown(self) -> None:
        self.queue.cancel_all()
        self.drain()


def _record_workspace_route(context, event, resolved) -> None:
    """Append route evidence in the selected project ledger, never globally."""
    from zf.core.events import EventWriter
    from zf.core.events.factory import event_log_from_project

    EventWriter(
        event_log_from_project(context.state_dir, config=context.config),
        default_origin="external",
    ).emit(
        "feishu.project_group.inbound.routed",
        actor="feishu-workspace-bridge",
        correlation_id=f"feishu-group:{resolved.binding.binding_id}",
        payload={
            "binding_id": resolved.binding.binding_id,
            "workspace_id": resolved.binding.workspace_id,
            "project_id": resolved.binding.project_id,
            "chat_id": event.chat_id,
            "app_id": str(event.payload.get("app_id") or ""),
            "purpose": resolved.index_route.purpose,
            "target": resolved.route.target,
            "message_id": str(event.payload.get("message_id") or ""),
        },
    )


class BridgeProjectionLoop:
    """Wakeable projection loop owned by the bridge process.

    The loop stores only delivery targets. Card state and idempotency remain in
    each restart-safe projector ledger.
    """

    def __init__(
        self,
        tick: Callable[[str], Any],
        *,
        interval_seconds: float = DEFAULT_PROJECTION_INTERVAL_SECONDS,
    ) -> None:
        self._tick = tick
        self._interval = max(0.1, float(interval_seconds))
        self._targets: set[str] = set()
        self._targets_lock = threading.Lock()
        self._last_target = ""
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def add_target(self, chat_id: str) -> None:
        target = str(chat_id or "").strip()
        if not target:
            return
        with self._targets_lock:
            self._targets.add(target)
        self._wake.set()

    def tick_once(self) -> bool:
        with self._targets_lock:
            targets = sorted(self._targets)
            if not targets:
                return False
            if self._last_target not in targets:
                target = targets[0]
            else:
                index = (targets.index(self._last_target) + 1) % len(targets)
                target = targets[index]
            self._last_target = target
        self._tick(target)
        return True

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            name="feishu-bridge-projection",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=max(5.0, self._interval * 2))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick_once()
            except Exception as exc:  # noqa: BLE001 - projection must not kill WS.
                print(
                    f"[bridge] projection tick failed: {exc!r}",
                    file=sys.stderr,
                    flush=True,
                )
            self._wake.wait(self._interval)
            self._wake.clear()


def push_bridge_projections_once(
    context,
    transport,
    fallback_receive_id: str,
    *,
    include_kanban_controls: bool = True,
    include_channel_controls: bool = True,
    member_id: str = "",
) -> dict[str, int]:
    """Push the control cards required to operate one bridge end to end."""

    from zf.integrations.feishu.channel_progress_card import (
        push_channel_progress_cards_once,
    )
    from zf.integrations.feishu.channel_question_card import (
        push_channel_question_cards_once,
    )
    from zf.integrations.feishu.channel_result_card import (
        push_channel_result_cards_once,
    )
    from zf.integrations.feishu.delivery_card import push_delivery_cards_once
    from zf.integrations.feishu.kanban_plan_card import push_kanban_plan_cards_once
    from zf.integrations.feishu.kanban_proposal_card import (
        push_kanban_proposal_cards_once,
    )
    from zf.integrations.feishu.stream_card import push_stream_card_once

    identity = getattr(
        getattr(context.config, "integrations", None),
        "feishu_identity",
        None,
    )
    secret_env = str(
        getattr(identity, "action_token_secret_env", "")
        or "ZF_FEISHU_ACTION_TOKEN_SECRET"
    )
    action_secret = os.environ.get(secret_env, "").encode() or None
    action_ttl = int(getattr(identity, "action_token_ttl_seconds", 86400) or 86400)
    state_dir = context.state_dir
    counts: dict[str, int] = {}

    def project(name: str, callback: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            result = callback()
            counts[name] = len(result.get("sent", [])) + len(result.get("updated", []))
            return result
        except Exception as exc:  # noqa: BLE001 - one card type must not starve others.
            counts[name] = 0
            print(
                f"[bridge] {name} projection failed: {exc!r}",
                file=sys.stderr,
                flush=True,
            )
            return {}

    if include_kanban_controls:
        project("plans", lambda: push_kanban_plan_cards_once(
            state_dir,
            transport,
            receive_id=fallback_receive_id,
            action_secret=action_secret,
            action_ttl_seconds=action_ttl,
        ))
        project("proposals", lambda: push_kanban_proposal_cards_once(
            state_dir,
            transport,
            receive_id=fallback_receive_id,
            action_secret=action_secret,
            action_ttl_seconds=action_ttl,
        ))
    else:
        counts["plans"] = 0
        counts["proposals"] = 0
    if include_channel_controls:
        project("questions", lambda: push_channel_question_cards_once(
            state_dir,
            transport,
            receive_id=fallback_receive_id,
            action_secret=action_secret,
            action_ttl_seconds=action_ttl,
        ))
        project("progress", lambda: push_channel_progress_cards_once(
            state_dir,
            transport,
            action_secret=action_secret,
            action_ttl_seconds=action_ttl,
        ))
        project("results", lambda: push_channel_result_cards_once(state_dir, transport))
    else:
        counts["questions"] = 0
        counts["progress"] = 0
        counts["results"] = 0
    stream_result = project("stream", lambda: push_stream_card_once(
        state_dir,
        transport,
        receive_id=fallback_receive_id,
        member=member_id,
    ))
    project("delivery", lambda: push_delivery_cards_once(
        state_dir,
        transport,
        receive_id=fallback_receive_id,
        action_secret=action_secret,
        action_ttl_seconds=action_ttl,
        skip_request_ids=set(stream_result.get("visible_request_ids", [])),
        member=member_id,
    ))
    if any(counts.values()):
        print(
            "[bridge] projected "
            + " ".join(f"{key}={value}" for key, value in counts.items()),
            flush=True,
        )
    return counts


def _catchup_chat_id(route_key: str) -> str:
    key = str(route_key or "").strip()
    if not key or key == "*":
        return ""
    if "#" in key:
        key = key.split("#", 1)[0]
    elif "@" in key:
        key = key.split("@", 1)[0]
    elif ":" in key:
        key = key.split(":", 1)[1]
    if key == "*" or key.startswith("__") or key.endswith("_unset__"):
        return ""
    return key


def _configured_projection_targets(context, app_id: str) -> list[str]:
    integrations = getattr(context.config, "integrations", None)
    routing = getattr(integrations, "feishu_routing", None)
    if not isinstance(routing, dict):
        return []
    targets: list[str] = []
    for route_key in routing:
        key = str(route_key or "")
        if ":" in key:
            route_app_id, _separator, _chat = key.partition(":")
            if route_app_id not in {"*", app_id}:
                continue
        chat_id = _catchup_chat_id(key)
        if chat_id and chat_id not in targets:
            targets.append(chat_id)
    return targets


def _catchup_on_start(context, transport, bridge: BridgeWatch,
                      bot_open_id: str = "", app_id: str = "") -> None:
    """W5: replay the restart gap for every explicitly-routed chat before the WS
    loop takes over. Wildcard "*" (p2p / dynamic chats) is skipped — we don't
    pre-scan unknown conversation history (doc 99 §4.5 boundary)."""
    from zf.integrations.feishu import catchup

    integrations = getattr(context.config, "integrations", None)
    routing = getattr(integrations, "feishu_routing", None)
    if not isinstance(routing, dict):
        return
    seen_chat_ids: set[str] = set()
    for route_key, route in routing.items():
        chat_id = _catchup_chat_id(str(route_key))
        if not chat_id or getattr(route, "target", "") not in (
            "channel",
            "agent",
            "kanban_agent",
            "run_manager",
        ):
            continue
        if chat_id in seen_chat_ids:
            continue
        seen_chat_ids.add(chat_id)
        try:
            def _dispatch_replay(raw: dict) -> Any:
                payload = raw.setdefault("payload", {})
                payload["bot_open_id"] = bot_open_id
                payload["app_id"] = app_id
                return bridge._dispatch(
                    MockFeishuTransport().parse_webhook(raw),
                    context=context,
                    transport=transport,
                )

            result = catchup.catchup_chat(
                context.state_dir, chat_id, bot_open_id=bot_open_id,
                list_recent=lambda cid: transport.list_recent(cid),
                dispatch=_dispatch_replay,
                app_id=app_id,
            )
            if result["replayed"]:
                print(f"[bridge] catchup chat={chat_id} replayed="
                      f"{result['replayed']}", flush=True)
        except Exception as exc:  # noqa: BLE001 — catchup must not block startup
            print(f"[bridge] catchup error chat={chat_id}: {exc!r}",
                  file=sys.stderr, flush=True)


def _catchup_routed_projects_on_start(
    inbound_resolver,
    routes,
    transport,
    bridge: BridgeWatch,
    bot_open_id: str,
    app_id: str,
) -> None:
    """Replay each exact routed project binding through its selected project.

    The cursor and idempotency sidecars stay project-scoped.  A single App WS
    therefore gains one connection without collapsing separate project thread
    histories into a workspace-global state file.
    """
    from zf.integrations.feishu import catchup

    seen: set[tuple[str, str]] = set()
    for route in routes:
        key = (route.project_id, route.chat_id)
        if key in seen:
            continue
        seen.add(key)
        resolved = inbound_resolver.resolve(
            app_id=route.app_id,
            chat_id=route.chat_id,
        )
        if resolved is None:
            continue
        try:
            def _dispatch_replay(raw: dict) -> Any:
                payload = raw.setdefault("payload", {})
                payload["bot_open_id"] = bot_open_id
                payload["app_id"] = app_id
                event = MockFeishuTransport().parse_webhook(raw)
                if event is None:
                    return None
                setattr(event, "route", resolved.route)
                setattr(event, "feishu_binding_id", resolved.binding.binding_id)
                return bridge._dispatch(
                    event,
                    context=resolved.context,
                    transport=transport,
                )

            result = catchup.catchup_chat(
                resolved.context.state_dir,
                route.chat_id,
                bot_open_id=bot_open_id,
                list_recent=lambda chat_id: transport.list_recent(chat_id),
                dispatch=_dispatch_replay,
                app_id=app_id,
                # Workspace bindings are collaboration groups. The REST list
                # endpoint can omit both this type and rich-text mentions;
                # catchup recovers the latter and mirrors the live route guard.
                fallback_chat_type="group",
                allow_unmentioned_group=(
                    str(resolved.binding.primary_responder or "")
                    == str(resolved.index_route.purpose or "")
                ),
            )
            if result["replayed"]:
                print(
                    f"[bridge] workspace catchup project={route.project_id} "
                    f"chat={route.chat_id} replayed={result['replayed']}",
                    flush=True,
                )
        except Exception as exc:  # noqa: BLE001 - catchup must not block WS.
            print(
                f"[bridge] workspace catchup error project={route.project_id} "
                f"chat={route.chat_id}: {exc!r}",
                file=sys.stderr,
                flush=True,
            )


def run_bridge_watch(args) -> int:
    """`zf feishu bridge --watch` — in-process WS long-connection that drives the
    real agent reply for every inbound message, debounced per chat."""
    from zf.core.config.loader import ConfigError
    from zf.core.config.project_context import resolve_project_context
    from zf.integrations.feishu.single_instance import (
        WS_LOCK_HEARTBEAT_SECONDS,
        acquire_provider_ws_lock,
    )
    from zf.integrations.feishu.transport import FeishuHttpTransport

    workspace = str(getattr(args, "workspace", "") or "").strip()
    all_workspaces = bool(getattr(args, "all_workspaces", False))
    if workspace and all_workspaces:
        print("Error: --workspace and --all-workspaces are mutually exclusive.", file=sys.stderr)
        return 2
    context = None
    if not workspace and not all_workspaces:
        try:
            context = resolve_project_context(
                explicit_state_dir=getattr(args, "state_dir", None),
                load_config_with_explicit=True,
            )
        except ConfigError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    env_app_id = os.environ.get("FEISHU_APP_ID", "") or os.environ.get(
        "LARKSUITE_CLI_APP_ID", "")
    requested_app_id = str(getattr(args, "app_id", "") or "").strip()
    if requested_app_id and env_app_id and requested_app_id != env_app_id:
        print(
            "Error: --app-id must match FEISHU_APP_ID for this bridge process.",
            file=sys.stderr,
        )
        return 1
    app_id = requested_app_id or env_app_id
    app_secret = os.environ.get("FEISHU_APP_SECRET", "") or os.environ.get(
        "LARKSUITE_CLI_APP_SECRET", "")
    if not app_id or not app_secret:
        print("Error: FEISHU_APP_ID / FEISHU_APP_SECRET must be set for --watch.",
              file=sys.stderr)
        return 1

    inbound_resolver = None
    routed_routes = []
    if all_workspaces:
        from zf.core.workspace.feishu_binding_index import (
            ProviderFeishuBindingConflict,
            ProviderFeishuInboundResolver,
        )

        inbound_resolver = ProviderFeishuInboundResolver()
        try:
            inbound_resolver.refresh()
        except ProviderFeishuBindingConflict as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        routed_routes = inbound_resolver.routes_for_app(app_id)
        if not routed_routes:
            print(
                "Error: no active project Feishu group binding for this "
                f"provider app ({app_id}).",
                file=sys.stderr,
            )
            return 1
    elif workspace:
        from zf.core.workspace.feishu_binding_index import (
            WorkspaceFeishuBindingConflict,
            WorkspaceFeishuInboundResolver,
        )

        inbound_resolver = WorkspaceFeishuInboundResolver(workspace=workspace)
        try:
            routes = inbound_resolver.refresh()
        except WorkspaceFeishuBindingConflict as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        routed_routes = [
            route for route in routes.values() if route.app_id == app_id
        ]
        if not routed_routes:
            print(
                "Error: no active project Feishu group binding for this "
                f"workspace/app ({workspace}/{app_id}).",
                file=sys.stderr,
            )
            return 1
    lock = acquire_provider_ws_lock(app_id)
    if lock is None:
        print("Error: another Feishu WS bridge is already running for this app "
              "(single-instance guard). Stop it first.", file=sys.stderr)
        return 1

    try:
        import lark_oapi as lark
    except ImportError:
        print("Error: lark-oapi is not installed. Run: pip install 'zf[feishu]'",
              file=sys.stderr)
        lock.release()
        return 1

    transport = FeishuHttpTransport()
    bot_open_id = transport.bot_open_id()
    lock_heartbeat_stop = threading.Event()
    lock_heartbeat_thread = threading.Thread(
        target=_refresh_ws_lock,
        args=(lock, lock_heartbeat_stop, WS_LOCK_HEARTBEAT_SECONDS),
        name="feishu-ws-lock-heartbeat",
        daemon=True,
    )
    lock_heartbeat_thread.start()
    projection_contexts: dict[str, Any] = {}
    projection_purposes: dict[str, str] = {}
    projection_members: dict[str, str] = {}

    def _route_resolve(event):
        assert inbound_resolver is not None
        return inbound_resolver.resolve(app_id=app_id, chat_id=event.chat_id)

    def _on_workspace_resolved(resolved, event) -> None:
        projection_contexts[event.chat_id] = resolved.context
        projection_purposes[event.chat_id] = resolved.index_route.purpose
        projection_members[event.chat_id] = str(
            getattr(resolved.route, "default_member", "") or ""
        )
        projection_loop.add_target(event.chat_id)

    bridge = BridgeWatch(
        context,
        transport,
        debounce_ms=int(getattr(args, "debounce_ms", DEFAULT_DEBOUNCE_MS)),
        inbound_resolver=_route_resolve if inbound_resolver else None,
        on_resolved=_on_workspace_resolved if inbound_resolver else None,
    )

    def _projection_tick(fallback: str) -> dict[str, int]:
        selected_context = (
            projection_contexts.get(fallback) if inbound_resolver else context
        )
        if selected_context is None:
            return {}
        return push_bridge_projections_once(
            selected_context,
            transport,
            fallback,
            include_kanban_controls=(
                not inbound_resolver
                or projection_purposes.get(fallback) == "kanban_agent"
            ),
            include_channel_controls=(
                not inbound_resolver
                or projection_purposes.get(fallback) == "kanban_agent"
            ),
            member_id=projection_members.get(fallback, ""),
        )

    projection_loop = BridgeProjectionLoop(
        _projection_tick,
        interval_seconds=float(
            getattr(args, "projection_interval", DEFAULT_PROJECTION_INTERVAL_SECONDS)
            or DEFAULT_PROJECTION_INTERVAL_SECONDS
        ),
    )
    if inbound_resolver is not None:
        for route in routed_routes:
            resolved = inbound_resolver.resolve(
                app_id=route.app_id,
                chat_id=route.chat_id,
            )
            if resolved is not None:
                projection_contexts[route.chat_id] = resolved.context
                projection_purposes[route.chat_id] = route.purpose
                projection_members[route.chat_id] = str(
                    getattr(resolved.route, "default_member", "") or ""
                )
                projection_loop.add_target(route.chat_id)
    else:
        assert context is not None
        for target in _configured_projection_targets(context, app_id):
            projection_loop.add_target(target)
    projection_loop.start()

    def _on_msg(data: Any) -> None:
        try:
            message = data.event.message
            text = ""
            if message.content:
                import json
                try:
                    text = json.loads(message.content).get("text", "")
                except (ValueError, TypeError):
                    text = message.content
            sender = getattr(data.event, "sender", None)
            user_id = ""
            if sender is not None and getattr(sender, "sender_id", None) is not None:
                user_id = getattr(sender.sender_id, "open_id", "") or ""
            message_type = str(
                getattr(message, "message_type", "")
                or getattr(message, "msg_type", "")
                or ""
            )
            sender_type = str(
                getattr(sender, "sender_type", "") or ""
            )
            if should_ignore_inbound_message(
                text=text,
                message_type=message_type,
                sender_type=sender_type,
            ):
                print(
                    "[bridge] skip (non-user card) "
                    f"chat={message.chat_id} type={message_type or 'unknown'} "
                    f"sender_type={sender_type or 'unknown'}",
                    flush=True,
                )
                return
            # multi-bot group: only answer when WE are the @-target (p2p always).
            chat_type = getattr(message, "chat_type", "") or ""
            mention_ids = []
            for mention in (getattr(message, "mentions", None) or []):
                mid = getattr(mention, "id", None)
                oid = getattr(mid, "open_id", "") if mid is not None else ""
                if oid:
                    mention_ids.append(oid)
            resolution = None
            if inbound_resolver is not None:
                resolution = inbound_resolver.resolve(
                    app_id=app_id,
                    chat_id=message.chat_id,
                )
            if not workspace_message_is_addressed(
                mention_ids=mention_ids,
                bot_open_id=bot_open_id,
                chat_type=chat_type,
                resolution=resolution,
            ):
                print(f"[bridge] skip (not @us) chat={message.chat_id} "
                      f"mentions={mention_ids}", flush=True)
                return
            queued = bridge.on_message({
                "text": text, "message_id": message.message_id,
                "user_id": user_id, "chat_id": message.chat_id,
                "parent_message_id": _message_ref(
                    message, "parent_message_id", "parent_id"),
                "root_message_id": _message_ref(
                    message, "root_message_id", "root_id"),
                "quote_message_id": _message_ref(
                    message, "quote_message_id", "quote_id", "quote_message_id"),
                "thread_id": _message_ref(message, "thread_id"),
                "create_time": getattr(message, "create_time", "") or "",
                "bot_open_id": bot_open_id,
                "app_id": app_id,
                "mention_ids": mention_ids})
            projection_loop.add_target(message.chat_id)
            print(f"[bridge] queued chat={message.chat_id} n={queued}", flush=True)
        except Exception as exc:  # noqa: BLE001 — a bad event must not kill the WS
            print(f"[bridge] on_message error: {exc!r}", file=sys.stderr, flush=True)

    def _on_card_action(data: Any) -> Any:
        """card.action.trigger (button click over the long-connection) → the
        same gated approve/reject path as a webhook (ingest → identity + A2 gate
        → ControlledAction). Returns a toast so the button doesn't spin."""
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            CallBackToast, P2CardActionTriggerResponse)

        from zf.cli.feishu_consume import ingest_feishu_event

        resp = P2CardActionTriggerResponse()
        toast = CallBackToast()
        try:
            ev = data.event
            action = getattr(ev, "action", None)
            value = getattr(action, "value", None) or {}
            operator = getattr(ev, "operator", None)
            open_id = getattr(operator, "open_id", "") or ""
            ctx = getattr(ev, "context", None)
            chat_id = getattr(ctx, "open_chat_id", "") or ""
            message_id = getattr(ctx, "open_message_id", "") or ""
            raw = {
                "header": {"event_type": "card.action.trigger"},
                "event": {
                    "action": {"value": value, "tag": getattr(action, "tag", "")},
                    "operator": {"operator_id": {"open_id": open_id}},
                    "open_chat_id": chat_id,
                    "open_message_id": message_id,
                    "context": {"open_chat_id": chat_id,
                                "open_message_id": message_id},
                },
            }
            action_context = context
            if inbound_resolver is not None:
                resolved = inbound_resolver.resolve(app_id=app_id, chat_id=chat_id)
                if resolved is None:
                    raise ValueError("unmapped workspace project-group chat")
                action_context = resolved.context
                projection_contexts[chat_id] = action_context
                projection_purposes[chat_id] = resolved.index_route.purpose
                projection_members[chat_id] = str(
                    getattr(resolved.route, "default_member", "") or ""
                )
            if action_context is None:
                raise ValueError("missing bridge project context")
            result = ingest_feishu_event(raw, context=action_context)
            projection_loop.add_target(chat_id)
            ok = bool(result.get("ok", result.get("status") not in ("rejected", "error")))
            toast.type = "success" if ok else "error"
            toast.content = str(result.get("message") or result.get("status") or "已处理")
            print(f"[bridge] card.action {value} by {open_id}: {result.get('status')}",
                  flush=True)
        except Exception as exc:  # noqa: BLE001 — a bad callback must not kill the WS
            toast.type = "error"
            toast.content = "处理失败"
            print(f"[bridge] card.action error: {exc!r}", file=sys.stderr, flush=True)
        resp.toast = toast
        return resp

    handler = (lark.EventDispatcherHandler.builder("", "")
               .register_p2_im_message_receive_v1(_on_msg)
               .register_p2_card_action_trigger(_on_card_action).build())
    client = lark.ws.Client(app_id, app_secret, event_handler=handler,
                            log_level=sdk_log_level(lark))

    def _stop(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    scope = (
        "all-workspaces " if all_workspaces
        else (f"workspace={workspace} " if workspace else "")
    )
    print(f"[bridge] watch starting ({scope}app={app_id[:12]}…, bot={bot_open_id[:18] or '?'}, "
          f"debounce={bridge.queue._delay * 1000:.0f}ms)", flush=True)
    if inbound_resolver is not None:
        _catchup_routed_projects_on_start(
            inbound_resolver,
            routed_routes,
            bridge.transport,
            bridge,
            bot_open_id,
            app_id,
        )
    else:
        assert context is not None
        _catchup_on_start(context, bridge.transport, bridge, bot_open_id, app_id)
    try:
        client.start()  # blocking WS loop with internal reconnect
    except KeyboardInterrupt:
        print("[bridge] shutdown signal — draining in-flight runs…", flush=True)
    finally:
        projection_loop.stop()
        bridge.shutdown()
        lock_heartbeat_stop.set()
        lock_heartbeat_thread.join(timeout=1.0)
        lock.release()
        print("[bridge] stopped.", flush=True)
    return 0


def _refresh_ws_lock(lock, stop: threading.Event, interval_seconds: float) -> None:
    """Keep the stale-lock TTL meaningful for a long-lived WS process."""
    interval = max(1.0, float(interval_seconds))
    while not stop.wait(interval):
        try:
            lock.refresh()
        except Exception:  # noqa: BLE001 - a failed refresh must not kill WS.
            pass


def _message_ref(message: Any, *names: str) -> str:
    """Best-effort read Feishu reply/thread refs across SDK versions."""

    for name in names:
        value = getattr(message, name, "") or ""
        if value:
            return str(value)
    return ""
