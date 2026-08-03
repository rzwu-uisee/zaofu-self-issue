"""Per-message serialization for Channel routing ownership."""

from __future__ import annotations

from functools import wraps
from pathlib import Path
from typing import Any, Callable, ParamSpec, TypeVar

from zf.core.state.locks import locked_path
from zf.runtime.control_actions_helpers import _stable_control_id


P = ParamSpec("P")
R = TypeVar("R")


def channel_route_lock_path(
    state_dir: Path,
    *,
    channel_id: str,
    thread_id: str,
    message_id: str,
) -> Path:
    lock_id = _stable_control_id(
        "channel-route",
        channel_id,
        thread_id or "main",
        message_id,
    )
    return Path(state_dir) / "locks" / lock_id


def serialized_channel_route(func: Callable[P, R]) -> Callable[P, R]:
    """Make every routing surface share one owner for the same message."""

    @wraps(func)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        payload = kwargs.get("message_payload")
        event = kwargs.get("message_event")
        if not isinstance(payload, dict) or event is None:
            return func(*args, **kwargs)
        channel_id = str(payload.get("channel_id") or "")
        thread_id = str(payload.get("thread_id") or "main") or "main"
        message_id = str(payload.get("message_id") or getattr(event, "id", ""))
        state_dir = Path(kwargs["state_dir"])
        with locked_path(channel_route_lock_path(
            state_dir,
            channel_id=channel_id,
            thread_id=thread_id,
            message_id=message_id,
        )):
            return func(*args, **kwargs)

    return wrapped
