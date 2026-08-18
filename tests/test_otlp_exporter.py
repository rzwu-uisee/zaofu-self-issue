from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

from zf.core.config.schema import OtlpExporterConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.otlp_exporter import (
    OtlpExporter,
    otlp_exporter_state_path,
    read_otlp_exporter_status,
    schedule_otlp_exporter,
)


class _CollectorHandler(BaseHTTPRequestHandler):
    status_codes: ClassVar[list[int]] = []
    requests: ClassVar[list[dict[str, object]]] = []

    def do_POST(self) -> None:  # noqa: N802
        size = int(self.headers.get("Content-Length", "0"))
        type(self).requests.append({
            "path": self.path,
            "headers": dict(self.headers.items()),
            "body": self.rfile.read(size).decode("utf-8"),
        })
        status = type(self).status_codes.pop(0) if type(self).status_codes else 200
        self.send_response(status)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        del format, args


@pytest.fixture
def collector() -> tuple[str, type[_CollectorHandler]]:
    _CollectorHandler.status_codes = []
    _CollectorHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CollectorHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}", _CollectorHandler
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def _config() -> OtlpExporterConfig:
    return OtlpExporterConfig(
        enabled=True,
        endpoint_env="ZF_TEST_OTLP_ENDPOINT",
        headers_env="ZF_TEST_OTLP_HEADERS",
        interval_seconds=1.0,
        request_timeout_seconds=1.0,
        batch_size=32,
        retry_initial_seconds=1.0,
        retry_max_seconds=4.0,
        healthy_sample_rate=0.0,
    )


def _event_log(state_dir: Path) -> EventLog:
    state_dir.mkdir(parents=True, exist_ok=True)
    return EventLog(state_dir / "events.jsonl")


def _span_rows(body: str) -> list[dict[str, object]]:
    payload = json.loads(body)
    return payload["resourceSpans"][0]["scopeSpans"][0]["spans"]


def test_otlp_http_exporter_redacts_and_joins_provider_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    collector: tuple[str, type[_CollectorHandler]],
) -> None:
    endpoint, receiver = collector
    monkeypatch.setenv("ZF_TEST_OTLP_ENDPOINT", endpoint)
    monkeypatch.setenv("ZF_TEST_OTLP_HEADERS", '{"Authorization":"Bearer collector-secret"}')
    state_dir = tmp_path / ".zf"
    event_log = _event_log(state_dir)
    original_events = [
        ZfEvent(
            id="evt-provider-context",
            type="provider.telemetry.context.bound",
            actor="claude-headless",
            task_id="TASK-1",
            correlation_id="trace-1",
            payload={
                "otel_trace_id": "a" * 32,
                "otel_parent_span_id": "b" * 16,
                "provider": "claude",
                "prompt": "never export this prompt",
                "tool_input": "never export this tool body",
            },
        ),
        ZfEvent(
            id="evt-dispatch",
            type="task.dispatched",
            actor="zf-orchestrator",
            task_id="TASK-1",
            correlation_id="trace-1",
            payload={"stage_id": "impl", "repo_path": "/private/repo"},
        ),
        ZfEvent(
            id="evt-thinking",
            type="agent.thinking",
            actor="claude-headless",
            task_id="TASK-1",
            payload={"text": "private chain of thought"},
        ),
    ]
    for event in original_events:
        event_log.append(event)

    exporter = OtlpExporter(
        state_dir=state_dir,
        event_log=event_log,
        config=_config(),
        project_id="demo",
    )
    result = exporter.run_once()

    assert result.status == "exported"
    assert len(receiver.requests) == 1
    request = receiver.requests[0]
    assert request["path"] == "/v1/traces"
    assert request["headers"]["Idempotency-Key"]
    assert request["headers"]["Authorization"] == "Bearer collector-secret"
    assert "never export" not in str(request["body"])
    assert "/private/repo" not in str(request["body"])
    assert "collector-secret" not in str(request["body"])

    spans = _span_rows(str(request["body"]))
    provider_parent = next(span for span in spans if span["name"] == "zaofu.provider.parent")
    dispatch = next(span for span in spans if span["name"] == "zaofu.workflow.dispatch")
    stage = next(span for span in spans if span["name"] == "zaofu.workflow.stage")
    assert provider_parent["traceId"] == "a" * 32
    assert provider_parent["spanId"] == "b" * 16
    assert provider_parent["parentSpanId"]
    assert dispatch["traceId"] == "a" * 32
    assert stage["traceId"] == "a" * 32
    assert all(span["name"] != "zaofu.event.agent.thinking" for span in spans)
    assert [event.id for event in event_log.read_all()] == [event.id for event in original_events]

    assert exporter.run_once().status == "idle"
    assert len(receiver.requests) == 1
    safe_status = read_otlp_exporter_status(state_dir, config=_config())
    assert "collector-secret" not in json.dumps(safe_status)


def test_otlp_exporter_retains_pending_batch_across_backoff_then_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    collector: tuple[str, type[_CollectorHandler]],
) -> None:
    endpoint, receiver = collector
    receiver.status_codes = [503, 200]
    monkeypatch.setenv("ZF_TEST_OTLP_ENDPOINT", endpoint)
    state_dir = tmp_path / ".zf"
    event_log = _event_log(state_dir)
    event_log.append(ZfEvent(id="evt-failure", type="agent.timeout", task_id="TASK-1"))

    writer = EventWriter(event_log)
    exporter = OtlpExporter(
        state_dir=state_dir,
        event_log=event_log,
        config=_config(),
        event_writer=writer,
    )
    failed = exporter.run_once()
    assert failed.status == "degraded"
    assert failed.failure_class == "otlp_http_503"
    assert exporter.run_once().status == "backoff"
    assert len(receiver.requests) == 1

    state_path = otlp_exporter_state_path(state_dir)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["next_attempt_at"] = 0
    state_path.write_text(json.dumps(state), encoding="utf-8")
    recovered = exporter.run_once()
    assert recovered.status == "exported"
    assert len(receiver.requests) == 2
    assert receiver.requests[0]["headers"]["Idempotency-Key"] == (
        receiver.requests[1]["headers"]["Idempotency-Key"]
    )
    assert [event.type for event in event_log.read_all()][-2:] == [
        "telemetry.exporter.degraded",
        "telemetry.exporter.recovered",
    ]
    status = read_otlp_exporter_status(state_dir, config=_config())
    assert status["health"] == "healthy"
    assert status["pending"]["event_count"] == 0
    assert status["counters"]["failed_attempts"] == 1


def test_otlp_exporter_filtered_batch_keeps_remaining_backlog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_TEST_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    state_dir = tmp_path / ".zf"
    event_log = _event_log(state_dir)
    for index in range(5):
        event_log.append(ZfEvent(
            id=f"evt-internal-{index}",
            type="telemetry.exporter.degraded",
        ))

    config = _config()
    config.batch_size = 2
    result = OtlpExporter(
        state_dir=state_dir,
        event_log=event_log,
        config=config,
    ).run_once()

    assert result.status == "filtered"
    assert result.backlog_events == 3
    assert read_otlp_exporter_status(state_dir, config=config)["backlog_events"] == 3


def test_enabled_exporter_with_missing_runtime_environment_degrades_fail_open(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    event_log = _event_log(state_dir)
    writer = EventWriter(event_log)

    result = OtlpExporter(
        state_dir=state_dir,
        event_log=event_log,
        config=_config(),
        event_writer=writer,
    ).run_once()

    assert result.status == "degraded"
    assert result.failure_class == "endpoint_env_missing"
    assert read_otlp_exporter_status(state_dir, config=_config())["health"] == "degraded"
    assert event_log.read_all()[-1].type == "telemetry.exporter.degraded"


def test_tick_scheduler_exports_in_background_without_event_log_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    collector: tuple[str, type[_CollectorHandler]],
) -> None:
    endpoint, receiver = collector
    monkeypatch.setenv("ZF_TEST_OTLP_ENDPOINT", endpoint)
    state_dir = tmp_path / ".zf"
    event_log = _event_log(state_dir)
    original = ZfEvent(id="evt-tick", type="task.dispatched", task_id="TASK-1")
    event_log.append(original)
    config = _config()

    assert schedule_otlp_exporter(
        state_dir=state_dir,
        event_log=event_log,
        config=config,
        event_writer=EventWriter(event_log),
        metrics=None,
        runtime_logs_enabled=False,
        project_id="demo",
    ) is True
    deadline = time.monotonic() + 2.0
    status = read_otlp_exporter_status(state_dir, config=config)
    while status["counters"].get("exported_batches", 0) != 1 and time.monotonic() < deadline:
        time.sleep(0.02)
        status = read_otlp_exporter_status(state_dir, config=config)

    assert status["counters"]["exported_batches"] == 1
    assert len(receiver.requests) == 1
    assert [event.id for event in event_log.read_all()] == [original.id]
