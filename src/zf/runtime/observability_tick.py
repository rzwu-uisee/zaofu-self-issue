"""Non-blocking optional observability services driven by runtime ticks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zf.runtime.observability_alerts import emit_observability_attentions
from zf.runtime.operations_metrics import OperationsMetricsRegistry
from zf.runtime.otlp_exporter import schedule_otlp_exporter
from zf.runtime.runtime_logs import write_runtime_log


@dataclass(frozen=True)
class ObservabilityTickResult:
    exporter_scheduled: bool = False
    alerts_emitted: int = 0
    alerts_status: str = "disabled"


def run_observability_tick(
    *,
    state_dir: Path,
    event_log: Any,
    event_writer: Any,
    config: Any,
    project_id: str = "",
) -> ObservabilityTickResult:
    """Schedule export and project alerts without entering workflow control flow."""

    observability = getattr(config, "observability", None)
    if observability is None:
        return ObservabilityTickResult()
    metrics_config = getattr(observability, "metrics", None)
    runtime_logs_config = getattr(observability, "runtime_logs", None)
    exporter_config = getattr(observability, "otlp_exporter", None)
    alert_config = getattr(observability, "alerts", None)
    metrics = OperationsMetricsRegistry(
        state_dir,
        enabled=bool(getattr(metrics_config, "enabled", False)),
    )
    runtime_logs_enabled = bool(getattr(runtime_logs_config, "enabled", True))

    exporter_scheduled = False
    try:
        exporter_scheduled = schedule_otlp_exporter(
            state_dir=state_dir,
            event_log=event_log,
            config=exporter_config,
            event_writer=event_writer,
            metrics=metrics,
            runtime_logs_enabled=runtime_logs_enabled,
            project_id=project_id,
        )
    except Exception as exc:
        write_runtime_log(
            state_dir,
            level="ERROR",
            component="observability-tick",
            message="OTLP exporter scheduling failed",
            failure_class=type(exc).__name__.lower(),
            enabled=runtime_logs_enabled,
        )

    try:
        alert_result = emit_observability_attentions(
            state_dir=state_dir,
            event_log=event_log,
            event_writer=event_writer,
            config=alert_config,
            exporter_config=exporter_config,
            project_id=project_id,
        )
    except Exception as exc:
        write_runtime_log(
            state_dir,
            level="ERROR",
            component="observability-tick",
            message="Observability attention projection failed",
            failure_class=type(exc).__name__.lower(),
            enabled=runtime_logs_enabled,
        )
        return ObservabilityTickResult(exporter_scheduled=exporter_scheduled)

    if alert_result.emitted:
        metrics.increment(
            "zf_observability_attention_total",
            labels={"component": "observability_alerts", "result": "emitted"},
            value=float(alert_result.emitted),
        )
    if alert_result.suppressed:
        metrics.increment(
            "zf_observability_attention_total",
            labels={"component": "observability_alerts", "result": "suppressed"},
            value=float(alert_result.suppressed),
        )
    return ObservabilityTickResult(
        exporter_scheduled=exporter_scheduled,
        alerts_emitted=alert_result.emitted,
        alerts_status=alert_result.status,
    )


__all__ = ["ObservabilityTickResult", "run_observability_tick"]
