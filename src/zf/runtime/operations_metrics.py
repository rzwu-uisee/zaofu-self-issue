"""Persistent low-cardinality operations metrics with Prometheus rendering."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import FileLock


_SCHEMA_VERSION = "operations-metrics.v1"
_FORBIDDEN_LABELS = frozenset({
    "task_id", "project_id", "correlation_id", "trace_id", "event_id",
    "workflow_run_id", "run_id", "dispatch_id", "attempt_id",
    "role_instance_id", "session_id", "provider_session_id", "thread_id",
    "channel_id", "event_ref", "workdir", "path", "url", "prompt",
    "message", "error",
})
_ALLOWED_LABELS = frozenset({
    "component", "result", "provider", "operation", "failure_class",
    "role_type", "stage", "action_kind", "integration", "route",
})
_DEFAULT_BUCKETS = (0.1, 0.5, 1.0, 5.0, 30.0, 120.0, 600.0)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def operations_metrics_path(state_dir: Path) -> Path:
    return Path(state_dir) / "projections" / "operations_metrics.json"


class OperationsMetricsRegistry:
    """State-dir scoped registry; metrics are projections, never gate input."""

    def __init__(self, state_dir: Path, *, enabled: bool = False) -> None:
        self.state_dir = Path(state_dir)
        self.enabled = enabled

    def increment(
        self,
        name: str,
        *,
        labels: dict[str, str] | None = None,
        value: float = 1.0,
    ) -> None:
        if not self.enabled:
            return
        normalized = _normalized_labels(labels)
        self._update(lambda payload: _increment(payload, name, normalized, value))

    def observe(
        self,
        name: str,
        seconds: float,
        *,
        labels: dict[str, str] | None = None,
    ) -> None:
        if not self.enabled:
            return
        normalized = _normalized_labels(labels)
        self._update(
            lambda payload: _observe(payload, name, max(0.0, float(seconds)), normalized)
        )

    def snapshot(self) -> dict[str, Any]:
        path = operations_metrics_path(self.state_dir)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        counters = list(payload.get("counters") or [])
        histograms = list(payload.get("histograms") or [])
        return {
            "schema_version": _SCHEMA_VERSION,
            "enabled": self.enabled,
            "updated_at": str(payload.get("updated_at") or ""),
            "counter_series": len(counters),
            "histogram_series": len(histograms),
            "counters": counters,
            "histograms": histograms,
        }

    def prometheus_text(self) -> str:
        snapshot = self.snapshot()
        lines = ["# ZaoFu operations metrics are low-cardinality projections."]
        for row in snapshot["counters"]:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"{row.get('name', '')}{_label_text(row.get('labels'))} {float(row.get('value') or 0):g}"
            )
        for row in snapshot["histograms"]:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "")
            labels = dict(row.get("labels") or {})
            buckets = row.get("buckets") or {}
            for boundary, count in sorted(buckets.items(), key=lambda item: float(item[0])):
                lines.append(f"{name}_bucket{_label_text({**labels, 'le': boundary})} {float(count):g}")
            lines.append(f"{name}_bucket{_label_text({**labels, 'le': '+Inf'})} {float(row.get('count') or 0):g}")
            lines.append(f"{name}_sum{_label_text(labels)} {float(row.get('sum') or 0):g}")
            lines.append(f"{name}_count{_label_text(labels)} {float(row.get('count') or 0):g}")
        return "\n".join(lines) + "\n"

    def _update(self, updater: Any) -> None:
        path = operations_metrics_path(self.state_dir)
        lock = path.with_name(path.name + ".lock")
        with FileLock(lock):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            payload.setdefault("counters", [])
            payload.setdefault("histograms", [])
            updater(payload)
            payload["schema_version"] = _SCHEMA_VERSION
            payload["updated_at"] = _now()
            atomic_write_text(
                path,
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )


def _normalized_labels(labels: dict[str, str] | None) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in (labels or {}).items():
        if key in _FORBIDDEN_LABELS:
            raise ValueError(f"high-cardinality operations metric label is forbidden: {key}")
        if key not in _ALLOWED_LABELS:
            raise ValueError(f"operations metric label is not allowlisted: {key}")
        normalized[key] = str(value or "unknown").strip().lower()[:64] or "unknown"
    return dict(sorted(normalized.items()))


def _series(payload: dict[str, Any], collection: str, name: str, labels: dict[str, str]) -> dict[str, Any]:
    rows = payload.setdefault(collection, [])
    for row in rows:
        if row.get("name") == name and row.get("labels") == labels:
            return row
    row = {"name": name, "labels": labels}
    rows.append(row)
    return row


def _increment(payload: dict[str, Any], name: str, labels: dict[str, str], value: float) -> None:
    row = _series(payload, "counters", name, labels)
    row["value"] = float(row.get("value") or 0.0) + value


def _observe(payload: dict[str, Any], name: str, seconds: float, labels: dict[str, str]) -> None:
    row = _series(payload, "histograms", name, labels)
    row["count"] = int(row.get("count") or 0) + 1
    row["sum"] = float(row.get("sum") or 0.0) + seconds
    buckets = row.setdefault("buckets", {str(item): 0 for item in _DEFAULT_BUCKETS})
    for boundary in _DEFAULT_BUCKETS:
        if seconds <= boundary:
            key = str(boundary)
            buckets[key] = int(buckets.get(key) or 0) + 1


def _label_text(labels: Any) -> str:
    if not isinstance(labels, dict) or not labels:
        return ""
    parts = [f'{key}="{str(value).replace(chr(34), chr(92) + chr(34))}"' for key, value in sorted(labels.items())]
    return "{" + ",".join(parts) + "}"


__all__ = ["OperationsMetricsRegistry", "operations_metrics_path"]
