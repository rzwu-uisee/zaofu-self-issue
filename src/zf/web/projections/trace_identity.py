"""Canonical Trace v2 identity and membership helpers.

Keep every Web projection that links into Trace on one identity contract:
correlation id first, nested payload ``trace_id`` second, and the existing
task-event fallback last.  Oversized identities are represented by a stable
opaque wire ref that remains resolvable against source events.
"""

from __future__ import annotations

import hashlib

from zf.web.projections.common import _payload_ref


MAX_WIRE_TRACE_ID_CHARS = 240
OPAQUE_TRACE_REF_PREFIX = "trace-ref:sha256:"


def event_trace_id(event: object) -> str:
    """Return the canonical Trace v2 membership key for one source event."""

    trace_id = getattr(event, "correlation_id", None)
    if not trace_id:
        payload_trace = _payload_ref(getattr(event, "payload", {}), "trace_id")
        trace_id = str(payload_trace) if payload_trace else None
    if trace_id:
        return str(trace_id)
    task_id = str(getattr(event, "task_id", "") or "").strip()
    return f"task:{task_id}" if task_id else ""


def wire_trace_id(trace_id: object) -> tuple[str, bool]:
    """Return a bounded wire identity plus whether it is opaque."""

    value = str(trace_id or "")
    if (
        len(value) <= MAX_WIRE_TRACE_ID_CHARS
        and not value.startswith(OPAQUE_TRACE_REF_PREFIX)
    ):
        return value, False
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{OPAQUE_TRACE_REF_PREFIX}{digest}", True


def resolve_trace_id(
    events: list[tuple[int, object]],
    *,
    trace_ref: str,
    as_of_seq: int,
) -> str:
    """Resolve an opaque wire ref to its canonical source-event identity."""

    if not trace_ref.startswith(OPAQUE_TRACE_REF_PREFIX):
        return trace_ref
    for seq, event in events:
        if seq > as_of_seq:
            break
        candidate = event_trace_id(event)
        if candidate and wire_trace_id(candidate)[0] == trace_ref:
            return candidate
    return trace_ref


def trace_events(
    events: list[tuple[int, object]],
    *,
    trace_id: str,
    as_of_seq: int,
) -> list[tuple[int, object]]:
    """Select events using exactly the canonical Trace v2 membership key."""

    return [
        (seq, event)
        for seq, event in events
        if seq <= as_of_seq and event_trace_id(event) == trace_id
    ]


__all__ = [
    "MAX_WIRE_TRACE_ID_CHARS",
    "OPAQUE_TRACE_REF_PREFIX",
    "event_trace_id",
    "resolve_trace_id",
    "trace_events",
    "wire_trace_id",
]
