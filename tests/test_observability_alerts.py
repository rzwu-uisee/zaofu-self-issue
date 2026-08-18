from __future__ import annotations

import json
from pathlib import Path

from zf.core.config.schema import ObservabilityAlertConfig, OtlpExporterConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.observability_alerts import (
    emit_observability_attentions,
    observability_alert_state_path,
    record_sse_gap,
)
from zf.runtime.otlp_exporter import otlp_exporter_state_path


def _setup(tmp_path: Path) -> tuple[Path, EventLog, EventWriter]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]", encoding="utf-8")
    event_log = EventLog(state_dir / "events.jsonl")
    return state_dir, event_log, EventWriter(event_log)


def test_alerts_emit_observe_only_deduped_runtime_attention(tmp_path: Path) -> None:
    state_dir, event_log, writer = _setup(tmp_path)
    event_log.append(ZfEvent(
        id="evt-lag",
        type="runtime.watcher.lag_warning",
        task_id="TASK-1",
    ))
    event_log.append(ZfEvent(id="evt-api", type="agent.api_blocked", task_id="TASK-2"))
    config = ObservabilityAlertConfig(enabled=True, cooldown_seconds=60)

    first = emit_observability_attentions(
        state_dir=state_dir,
        event_log=event_log,
        event_writer=writer,
        config=config,
        project_id="demo",
        now_epoch=100,
    )
    assert first.emitted == 2
    attention = [event for event in event_log.read_all() if event.type == "runtime.attention.needed"]
    assert {event.payload["failure_class"] for event in attention} == {
        "watcher_lag",
        "provider_failure",
    }
    assert all(event.payload["actionability"] == "observe" for event in attention)
    assert all(event.payload["suggested_route"] == "observe_only" for event in attention)
    assert (state_dir / "kanban.json").read_text(encoding="utf-8") == "[]"

    second = emit_observability_attentions(
        state_dir=state_dir,
        event_log=event_log,
        event_writer=writer,
        config=config,
        project_id="demo",
        now_epoch=101,
    )
    assert second.emitted == 0
    assert len([event for event in event_log.read_all() if event.type == "runtime.attention.needed"]) == 2


def test_sse_gap_and_exporter_health_open_distinct_attention_episodes(
    tmp_path: Path,
) -> None:
    state_dir, event_log, writer = _setup(tmp_path)
    config = ObservabilityAlertConfig(enabled=True, cooldown_seconds=0)
    exporter_config = OtlpExporterConfig(enabled=True, endpoint_env="ZF_OTLP_ENDPOINT")
    exporter_state = otlp_exporter_state_path(state_dir)
    exporter_state.parent.mkdir(parents=True, exist_ok=True)
    exporter_state.write_text(json.dumps({
        "health": "degraded",
        "last_failure_class": "otlp_http_503",
    }), encoding="utf-8")
    record_sse_gap(state_dir=state_dir, cursor=5, current=20)

    first = emit_observability_attentions(
        state_dir=state_dir,
        event_log=event_log,
        event_writer=writer,
        config=config,
        exporter_config=exporter_config,
        project_id="demo",
        now_epoch=100,
    )
    assert first.emitted == 2
    attention = [event.payload for event in event_log.read_all() if event.type == "runtime.attention.needed"]
    assert {payload["failure_class"] for payload in attention} == {
        "sse_gap",
        "otlp_http_503",
    }

    exporter_state.write_text(json.dumps({"health": "healthy"}), encoding="utf-8")
    recovered = emit_observability_attentions(
        state_dir=state_dir,
        event_log=event_log,
        event_writer=writer,
        config=config,
        exporter_config=exporter_config,
        project_id="demo",
        now_epoch=101,
    )
    assert recovered.emitted == 0
    exporter_state.write_text(json.dumps({
        "health": "degraded",
        "last_failure_class": "otlp_timeout",
    }), encoding="utf-8")
    degraded_again = emit_observability_attentions(
        state_dir=state_dir,
        event_log=event_log,
        event_writer=writer,
        config=config,
        exporter_config=exporter_config,
        project_id="demo",
        now_epoch=102,
    )
    assert degraded_again.emitted == 1
    assert observability_alert_state_path(state_dir).exists()
