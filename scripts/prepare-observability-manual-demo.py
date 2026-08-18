#!/usr/bin/env python3
"""Create isolated, redacted state for the observability manual media.

The result is intentionally a projection-only demonstration. It uses the
same EventLog, telemetry, runtime-log, and metrics writers as production, but
never starts a provider, collector, or workflow.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from zf.core.config.loader import load_config
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.operations_metrics import OperationsMetricsRegistry
from zf.runtime.provider_telemetry import (
    ProviderTelemetryRuntime,
    TelemetryOperationContextV1,
)
from zf.runtime.runtime_logs import write_runtime_log


_CONFIG = """version: \"1.0\"
project:
  name: observability-manual-demo
  state_dir: .zf
observability:
  provider_telemetry:
    mode: managed
    endpoint_env: ZF_MANUAL_CLAUDE_OTLP_ENDPOINT
    enable_traces: true
  runtime_logs:
    enabled: true
  metrics:
    enabled: true
    access_token_env: ZF_MANUAL_METRICS_TOKEN
  otlp_exporter:
    enabled: true
    endpoint_env: ZF_MANUAL_OTLP_ENDPOINT
    interval_seconds: 15
    request_timeout_seconds: 3
    batch_size: 16
    retry_initial_seconds: 5
    retry_max_seconds: 300
    healthy_sample_rate: 0.1
  alerts:
    enabled: true
    cooldown_seconds: 300
"""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        required=True,
        help="Empty demo project directory to create",
    )
    return parser.parse_args()


def _ensure_empty(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise SystemExit(f"refusing non-empty output root: {path}")
    path.mkdir(parents=True, exist_ok=True)


def _append_demo_events(state_dir: Path) -> None:
    event_log = EventLog(state_dir / "events.jsonl")
    rows = (
        ("workflow.invoke.requested", "zf-cli", "task-observe-demo", {"run_id": "run-observe-demo", "route": "prd"}),
        ("agent.session.run.started", "kanban-agent", "task-observe-demo", {"provider": "claude", "route": "headless"}),
        ("runtime.watcher.lag_warning", "zf-runtime", "task-observe-demo", {"failure_class": "watcher_lag"}),
        ("agent.timeout", "zf-runtime", "task-observe-demo", {"provider": "codex", "failure_class": "provider_timeout"}),
        ("telemetry.exporter.degraded", "zf-otlp-exporter", None, {"failure_class": "otlp_http_503"}),
    )
    for event_type, actor, task_id, payload in rows:
        event_log.append(ZfEvent(
            type=event_type,
            actor=actor,
            task_id=task_id,
            correlation_id="trace-observe-demo",
            payload=payload,
            origin="kernel",
        ))


def _write_demo_exporter_projection(state_dir: Path) -> None:
    projection = {
        "schema_version": "otlp-exporter.v1",
        "health": "degraded",
        "cursor_event_id": "evt-observe-demo",
        "backlog_events": 3,
        "last_failure_at": "2026-08-18T00:00:00Z",
        "last_failure_class": "otlp_http_503",
        "pending": {
            "batch_id": "manual-demo-batch",
            "event_ids": ["evt-observe-demo-1", "evt-observe-demo-2", "evt-observe-demo-3"],
            "span_count": 7,
            "attempt": 2,
            "created_at": "2026-08-18T00:00:00Z",
        },
        "counters": {
            "sampled_out": 2,
            "dropped_by_policy": 1,
            "redacted_fields": 6,
        },
    }
    atomic_write_text(
        state_dir / "projections" / "otlp_exporter.json",
        json.dumps(projection, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _record_demo_telemetry(state_dir: Path, config: object) -> None:
    telemetry = ProviderTelemetryRuntime(
        state_dir,
        getattr(config, "observability").provider_telemetry,
    )
    base = {
        "correlation_id": "trace-observe-demo",
        "project_id": "observability-manual-demo",
        "task_id": "task-observe-demo",
        "conversation_id": "manual-demo",
        "thread_id": "manual-demo-thread",
    }
    telemetry.launch(
        TelemetryOperationContextV1.interaction(
            operation_kind="kanban_turn",
            provider="claude-headless",
            **base,
        ),
        route="headless",
    )
    telemetry.launch(
        TelemetryOperationContextV1.interaction(
            operation_kind="workflow_dispatch",
            provider="codex-cli",
            **base,
        ),
        route="stream-json",
    )
    telemetry.launch(
        TelemetryOperationContextV1.interaction(
            operation_kind="sidecar_operation",
            provider="claude-code",
            **base,
        ),
        route="tmux",
    )


def _record_demo_logs_and_metrics(state_dir: Path) -> None:
    rows = (
        ("INFO", "provider-turn", "Managed provider telemetry context bound", "", {"provider": "claude", "task_id": "task-observe-demo", "route": "headless"}),
        ("WARN", "sse", "Stream replay gap recovered from projection", "sse_gap", {"task_id": "task-observe-demo", "route": "web-sse"}),
        ("ERROR", "otlp-exporter", "OTLP exporter is retrying after collector response", "otlp_http_503", {"provider": "claude", "route": "otlp-http"}),
    )
    for level, component, message, failure_class, fields in rows:
        write_runtime_log(
            state_dir,
            level=level,
            component=component,
            message=message,
            failure_class=failure_class,
            fields=fields,
        )

    metrics = OperationsMetricsRegistry(state_dir, enabled=True)
    metrics.increment(
        "zf_provider_operations_total",
        labels={"provider": "claude", "operation": "kanban_turn", "result": "completed"},
    )
    metrics.increment(
        "zf_otlp_export_batches_total",
        labels={"component": "otlp_exporter", "result": "degraded", "failure_class": "otlp_http_503"},
    )
    metrics.observe(
        "zf_provider_operation_duration_seconds",
        1.2,
        labels={"provider": "claude", "operation": "kanban_turn", "result": "completed"},
    )


def main() -> int:
    args = _parse_args()
    root = args.output_root.resolve()
    _ensure_empty(root)
    state_dir = root / ".zf"
    state_dir.mkdir(parents=True)
    atomic_write_text(root / "zf.yaml", _CONFIG)
    atomic_write_text(state_dir / "kanban.json", "[]\n")
    atomic_write_text(state_dir / "feature_list.json", "[]\n")

    # The values are local placeholders used only to establish a safe capability
    # projection. No provider or collector is contacted by this script.
    os.environ.setdefault("ZF_MANUAL_CLAUDE_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    os.environ.setdefault("ZF_MANUAL_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    os.environ.setdefault("ZF_MANUAL_METRICS_TOKEN", "manual-metrics-token")
    config = load_config(root / "zf.yaml")

    _append_demo_events(state_dir)
    _record_demo_telemetry(state_dir, config)
    _record_demo_logs_and_metrics(state_dir)
    _write_demo_exporter_projection(state_dir)
    print(json.dumps({"project_root": str(root), "state_dir": str(state_dir)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
