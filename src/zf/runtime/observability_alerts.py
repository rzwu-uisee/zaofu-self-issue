"""Projection-only health attention for the optional observability plane.

The evaluator consumes canonical events and safe state-dir projections.  It
does not decide recovery, change a task, or feed a Gate.  Its only canonical
write is an ``actionability=observe`` runtime attention emitted through the
normal EventWriter boundary.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.security.redaction import redact_obj
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import FileLock
from zf.runtime.otlp_exporter import read_otlp_exporter_status
from zf.runtime.problem_taxonomy import problem_envelope_from_attention


_SCHEMA_VERSION = "observability-alerts.v1"
_SSE_GAP_SCHEMA_VERSION = "observability-sse-gap.v1"
_MAX_FINGERPRINTS = 256

_EVENT_SIGNAL_TYPES: dict[str, tuple[str, str, str]] = {
    "runtime.watcher.lag_warning": (
        "watcher_lag",
        "high",
        "Event watcher replay lag is above its configured threshold",
    ),
    "agent.api_blocked": (
        "provider_failure",
        "warn",
        "Provider API operation was blocked",
    ),
    "agent.timeout": (
        "provider_failure",
        "warn",
        "Provider operation timed out",
    ),
    "agent.session.run.failed": (
        "provider_failure",
        "warn",
        "Provider session run failed",
    ),
    "agent.session.part.failed": (
        "provider_failure",
        "warn",
        "Provider session part failed",
    ),
    "worker.stuck.recovery_failed": (
        "recovery_failure",
        "high",
        "Worker recovery failed",
    ),
    "cost.exceeded": (
        "cost_anomaly",
        "warn",
        "Cost budget was exceeded",
    ),
    "task.rework.capped": (
        "rework_anomaly",
        "warn",
        "Task rework reached its configured cap",
    ),
    "candidate.rework.capped": (
        "rework_anomaly",
        "warn",
        "Candidate rework reached its configured cap",
    ),
    "replan.adoption.redrive_failed": (
        "rework_anomaly",
        "warn",
        "Replan adoption redrive failed",
    ),
}


@dataclass(frozen=True)
class ObservabilityAlertResult:
    status: str
    emitted: int = 0
    scanned_events: int = 0
    suppressed: int = 0


def observability_alert_state_path(state_dir: Path) -> Path:
    return Path(state_dir) / "projections" / "observability_alerts.json"


def observability_sse_gap_path(state_dir: Path) -> Path:
    return Path(state_dir) / "projections" / "observability_sse_gaps.json"


def record_sse_gap(*, state_dir: Path, cursor: int, current: int) -> None:
    """Record an SSE replay gap as a safe projection, never a Web write event."""

    path = observability_sse_gap_path(state_dir)
    lock = path.with_name(path.name + ".lock")
    with FileLock(lock):
        state = _load_state(path, schema_version=_SSE_GAP_SCHEMA_VERSION)
        sequence = int(state.get("sequence") or 0) + 1
        state.update({
            "schema_version": _SSE_GAP_SCHEMA_VERSION,
            "sequence": sequence,
            "count": int(state.get("count") or 0) + 1,
            "latest": {
                "cursor": max(0, int(cursor)),
                "current": max(0, int(current)),
                "observed_at": _now_iso(),
            },
        })
        _write_state(path, state)


def read_observability_alert_status(
    state_dir: Path,
    *,
    config: Any | None = None,
) -> dict[str, Any]:
    state = _load_state(
        observability_alert_state_path(state_dir),
        schema_version=_SCHEMA_VERSION,
    )
    return redact_obj({
        "schema_version": _SCHEMA_VERSION,
        "enabled": bool(getattr(config, "enabled", False)),
        "last_scan_at": str(state.get("last_scan_at") or ""),
        "cursor_event_id": str(state.get("cursor_event_id") or ""),
        "emitted_total": int(state.get("emitted_total") or 0),
        "suppressed_total": int(state.get("suppressed_total") or 0),
        "last_sse_gap_sequence": int(state.get("last_sse_gap_sequence") or 0),
        "last_exporter_health": str(state.get("last_exporter_health") or ""),
    })


def emit_observability_attentions(
    *,
    state_dir: Path,
    event_log: EventLog,
    event_writer: EventWriter | None,
    config: Any | None,
    exporter_config: Any | None = None,
    project_id: str = "",
    now_epoch: float | None = None,
) -> ObservabilityAlertResult:
    """Emit bounded observation-only attention for new health evidence."""

    if not bool(getattr(config, "enabled", False)):
        return ObservabilityAlertResult(status="disabled")
    if event_writer is None:
        return ObservabilityAlertResult(status="unavailable")

    now = float(now_epoch if now_epoch is not None else time.time())
    cooldown = max(0.0, float(getattr(config, "cooldown_seconds", 300.0) or 0.0))
    path = observability_alert_state_path(state_dir)
    lock = path.with_name(path.name + ".lock")
    with FileLock(lock):
        state = _load_state(path, schema_version=_SCHEMA_VERSION)
        prior_cursor = str(state.get("cursor_event_id") or "")
        source_events = _events_after_cursor(event_log.read_all(), prior_cursor)
        candidates = _event_candidates(source_events)
        candidates.extend(_sse_gap_candidates(state_dir, state))
        candidates.extend(_exporter_candidates(state_dir, state, exporter_config))

        emitted = 0
        suppressed = 0
        write_failed = False
        emitted_at = state.setdefault("emitted", {})
        if not isinstance(emitted_at, dict):
            emitted_at = {}
            state["emitted"] = emitted_at
        for candidate in candidates:
            fingerprint = str(candidate["fingerprint"])
            if _in_cooldown(emitted_at, fingerprint, now=now, cooldown=cooldown):
                suppressed += 1
                continue
            try:
                event_writer.append(_attention_event(candidate, project_id=project_id))
            except Exception:
                write_failed = True
                continue
            emitted_at[fingerprint] = now
            emitted += 1

        if source_events and not write_failed:
            state["cursor_event_id"] = str(source_events[-1].id or prior_cursor)
        if not write_failed:
            _advance_auxiliary_cursors(state_dir, state, exporter_config)
        state["last_scan_at"] = _now_iso()
        state["emitted_total"] = int(state.get("emitted_total") or 0) + emitted
        state["suppressed_total"] = int(state.get("suppressed_total") or 0) + suppressed
        _prune_fingerprints(emitted_at)
        _write_state(path, state)

    return ObservabilityAlertResult(
        status="degraded" if emitted else "healthy",
        emitted=emitted,
        scanned_events=len(source_events),
        suppressed=suppressed,
    )


def _event_candidates(events: list[ZfEvent]) -> list[dict[str, str | list[str]]]:
    rows: list[dict[str, str | list[str]]] = []
    for event in events:
        details = _EVENT_SIGNAL_TYPES.get(str(event.type or ""))
        if details is None:
            continue
        signal, severity, title = details
        rows.append(_candidate(
            signal=signal,
            severity=severity,
            title=title,
            failure_class=signal,
            fingerprint=f"observability:{signal}:{event.id}",
            task_id=str(event.task_id or ""),
            source_event_ids=[str(event.id)] if event.id else [],
            source_ref=f"events.jsonl#{event.id}" if event.id else "events.jsonl",
        ))
    return rows


def _sse_gap_candidates(state_dir: Path, state: dict[str, Any]) -> list[dict[str, str | list[str]]]:
    gap = _load_state(
        observability_sse_gap_path(state_dir),
        schema_version=_SSE_GAP_SCHEMA_VERSION,
    )
    sequence = int(gap.get("sequence") or 0)
    if sequence <= int(state.get("last_sse_gap_sequence") or 0):
        return []
    return [_candidate(
        signal="sse_gap",
        severity="warn",
        title="Web stream replay gap observed",
        failure_class="sse_gap",
        fingerprint=f"observability:sse_gap:{sequence}",
        source_ref="projections/observability_sse_gaps.json",
    )]


def _exporter_candidates(
    state_dir: Path,
    state: dict[str, Any],
    exporter_config: Any | None,
) -> list[dict[str, str | list[str]]]:
    exporter = read_otlp_exporter_status(state_dir, config=exporter_config)
    health = str(exporter.get("health") or "")
    previous = str(state.get("last_exporter_health") or "")
    if not exporter.get("enabled") or health != "degraded" or previous == "degraded":
        return []
    failure_class = str(exporter.get("last_failure_class") or "otlp_exporter")
    return [_candidate(
        signal="otlp_exporter",
        severity="warn",
        title="OTLP exporter is degraded",
        failure_class=failure_class,
        fingerprint=f"observability:otlp_exporter:{failure_class}",
        source_ref="projections/otlp_exporter.json",
    )]


def _advance_auxiliary_cursors(
    state_dir: Path,
    state: dict[str, Any],
    exporter_config: Any | None,
) -> None:
    gap = _load_state(
        observability_sse_gap_path(state_dir),
        schema_version=_SSE_GAP_SCHEMA_VERSION,
    )
    state["last_sse_gap_sequence"] = int(gap.get("sequence") or 0)
    exporter = read_otlp_exporter_status(state_dir, config=exporter_config)
    state["last_exporter_health"] = str(exporter.get("health") or "")


def _candidate(
    *,
    signal: str,
    severity: str,
    title: str,
    failure_class: str,
    fingerprint: str,
    task_id: str = "",
    source_event_ids: list[str] | None = None,
    source_ref: str = "",
) -> dict[str, str | list[str]]:
    return {
        "signal": signal,
        "severity": severity,
        "title": title,
        "failure_class": failure_class,
        "fingerprint": fingerprint,
        "task_id": task_id,
        "source_event_ids": source_event_ids or [],
        "source_ref": source_ref,
    }


def _attention_event(candidate: dict[str, str | list[str]], *, project_id: str) -> ZfEvent:
    fingerprint = str(candidate["fingerprint"])
    attention_id = "attn-" + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:12]
    payload: dict[str, Any] = {
        "schema_version": "runtime.attention.needed.v0",
        "is_derived_projection": True,
        "actionability": "observe",
        "project_id": project_id,
        "attention_id": attention_id,
        "fingerprint": fingerprint,
        "severity": str(candidate["severity"]),
        "source": "observability",
        "title": str(candidate["title"]),
        "summary": "Operational signal observed; inspect its canonical evidence before taking action.",
        "task_id": str(candidate.get("task_id") or ""),
        "source_event_ids": list(candidate.get("source_event_ids") or []),
        "source_ref": str(candidate.get("source_ref") or ""),
        "suggested_route": "observe_only",
        "suggested_action": {},
        "failure_class": str(candidate["failure_class"]),
        "projection_ref": {
            "observability_alerts": "projections/observability_alerts.json",
        },
    }
    payload["problem_envelope"] = problem_envelope_from_attention(payload)
    return ZfEvent(
        type="runtime.attention.needed",
        actor="zf-observability",
        task_id=payload["task_id"] or None,
        causation_id=(payload["source_event_ids"] or [None])[0],
        payload=redact_obj(payload),
    )


def _events_after_cursor(events: list[ZfEvent], cursor_event_id: str) -> list[ZfEvent]:
    if not cursor_event_id:
        return list(events)
    for index, event in enumerate(events):
        if str(event.id or "") == cursor_event_id:
            return events[index + 1:]
    return list(events)


def _in_cooldown(
    emitted: dict[str, Any],
    fingerprint: str,
    *,
    now: float,
    cooldown: float,
) -> bool:
    try:
        previous = float(emitted.get(fingerprint) or 0.0)
    except (TypeError, ValueError):
        return False
    return previous > 0.0 and now - previous < cooldown


def _prune_fingerprints(emitted: dict[str, Any]) -> None:
    if len(emitted) <= _MAX_FINGERPRINTS:
        return
    rows = sorted(
        ((key, float(value or 0.0)) for key, value in emitted.items()),
        key=lambda item: item[1],
        reverse=True,
    )[:_MAX_FINGERPRINTS]
    emitted.clear()
    emitted.update(rows)


def _load_state(path: Path, *, schema_version: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    state = value if isinstance(value, dict) else {}
    state.setdefault("schema_version", schema_version)
    return state


def _write_state(path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = _now_iso()
    atomic_write_text(
        path,
        json.dumps(redact_obj(state), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "ObservabilityAlertResult",
    "emit_observability_attentions",
    "observability_alert_state_path",
    "observability_sse_gap_path",
    "read_observability_alert_status",
    "record_sse_gap",
]
