from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.config.loader import ConfigError, load_config
from zf.core.config.schema import ProviderTelemetryConfig, RoleConfig
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.operations_metrics import OperationsMetricsRegistry
from zf.runtime.provider_telemetry import (
    ProviderTelemetryRuntime,
    TelemetryContextV1,
    TelemetryOperationContextV1,
)
from zf.runtime.runtime_logs import read_runtime_logs, write_runtime_log
from zf.runtime.transport import DispatchContext
from zf.runtime.transport_stream_json import StreamJsonTransport
from zf.web.headless_agent import HeadlessTurnResult, KanbanHeadlessAgent


def test_operation_context_is_stable_and_chat_does_not_invent_task() -> None:
    workflow = TelemetryOperationContextV1.from_dispatch(DispatchContext(
        trace_id="trace-1",
        run_id="run-1",
        task_id="task-1",
        dispatch_id="dispatch-1",
        attempt_id="attempt-1",
        instance_id="dev-1",
        backend="claude-code",
    ))
    chat = TelemetryOperationContextV1.interaction(
        operation_kind="kanban_turn",
        correlation_id="trace-1",
        project_id="demo",
        conversation_id="conversation-1",
        thread_id="thread-1",
        provider="claude-headless",
    )

    workflow_context = TelemetryContextV1.from_operation(workflow)
    assert workflow.operation_kind == "workflow_dispatch"
    assert workflow_context.otel_trace_id == TelemetryContextV1.from_operation(workflow).otel_trace_id
    assert len(workflow_context.otel_trace_id) == 32
    assert len(workflow_context.otel_parent_span_id) == 16
    assert chat.task_id == ""
    assert chat.dispatch_id == ""
    assert TelemetryContextV1.from_operation(chat).otel_trace_id != workflow_context.otel_trace_id


def test_managed_claude_profile_injects_safe_per_turn_env_and_records_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_TEST_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    runtime = ProviderTelemetryRuntime(
        tmp_path / ".zf",
        ProviderTelemetryConfig(
            mode="managed",
            endpoint_env="ZF_TEST_OTLP_ENDPOINT",
            enable_traces=True,
        ),
    )
    launch = runtime.launch(
        TelemetryOperationContextV1.interaction(
            operation_kind="kanban_turn",
            correlation_id="trace-1",
            project_id="demo",
            conversation_id="chat-1",
            thread_id="thread-1",
            provider="claude-headless",
        ),
        route="headless",
    )

    assert launch.capability.effective == "active"
    assert launch.capability.join_kind == "parent_child"
    assert launch.env["TRACEPARENT"] == launch.context.traceparent
    assert launch.env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://127.0.0.1:4318"
    assert launch.env["OTEL_LOG_USER_PROMPTS"] == "0"
    snapshot = runtime.snapshot()
    assert snapshot["capabilities"][0]["provider"] == "claude"
    assert snapshot["bindings"][0]["task_id"] == ""
    assert "http://127.0.0.1" not in json.dumps(snapshot)


def test_managed_profile_without_endpoint_is_disabled(tmp_path: Path) -> None:
    runtime = ProviderTelemetryRuntime(
        tmp_path / ".zf",
        ProviderTelemetryConfig(mode="managed", endpoint_env="MISSING_OTLP_ENDPOINT"),
    )
    launch = runtime.launch(
        TelemetryOperationContextV1.interaction(
            operation_kind="kanban_turn",
            provider="claude-headless",
        ),
        route="headless",
    )
    assert launch.env == {}
    assert launch.capability.effective == "disabled"
    assert launch.capability.failure_class == "endpoint_env_missing"


def test_tmux_route_is_never_given_per_task_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_TEST_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    runtime = ProviderTelemetryRuntime(
        tmp_path / ".zf",
        ProviderTelemetryConfig(mode="managed", endpoint_env="ZF_TEST_OTLP_ENDPOINT"),
    )
    launch = runtime.launch(
        TelemetryOperationContextV1.interaction(
            operation_kind="sidecar_operation",
            provider="claude-code",
        ),
        route="tmux",
    )
    assert launch.env == {}
    assert launch.capability.join_kind == "derived_only"
    assert launch.capability.failure_class == "tmux_route_not_per_turn"


def test_stream_json_uses_telemetry_env_and_emits_observability_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_TEST_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    seen: dict[str, str] = {}

    async def query_fn(*, prompt, options):  # type: ignore[no-untyped-def]
        del prompt
        seen.update(options.env)
        if False:
            yield None

    state_dir = tmp_path / ".zf"
    transport = StreamJsonTransport(
        state_dir,
        RoleSessionRegistry(state_dir / "role_sessions.yaml", project_root=tmp_path),
        query_fn=query_fn,
        telemetry=ProviderTelemetryRuntime(
            state_dir,
            ProviderTelemetryConfig(mode="managed", endpoint_env="ZF_TEST_OTLP_ENDPOINT"),
        ),
    )
    transport.register_role(RoleConfig(name="dev", backend="claude-code"))
    transport.send_task(
        "dev",
        tmp_path / "briefing.md",
        "inspect",
        context=DispatchContext(
            trace_id="trace-1",
            task_id="task-1",
            dispatch_id="dispatch-1",
            backend="claude-code",
        ),
    )

    assert seen["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"
    assert seen["TRACEPARENT"].startswith("00-")
    event_types = {event.type for event in transport.poll_events()}
    assert "provider.telemetry.capability.observed" in event_types
    assert "provider.telemetry.context.bound" in event_types


def test_kanban_headless_turn_receives_only_per_turn_managed_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_TEST_OTLP_ENDPOINT", "http://127.0.0.1:4318")

    class RecordingBackend:
        backend_id = "claude-headless"

        def __init__(self) -> None:
            self.env: dict[str, str] = {}

        def available(self) -> bool:
            return True

        def set_telemetry_env(self, env: dict[str, str]) -> None:
            self.env = dict(env)

        def run_turn(self, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["on_session_id"]("provider-session-1")
            return HeadlessTurnResult(
                ok=True,
                status="completed",
                backend=self.backend_id,
                thread_id=str(kwargs["thread_id"]),
                provider_session_id="provider-session-1",
                reply="done",
                messages=[],
                usage={},
            )

    state_dir = tmp_path / ".zf"
    backend = RecordingBackend()
    agent = KanbanHeadlessAgent(
        state_dir=state_dir,
        project_root=tmp_path,
        backends={"claude-headless": backend},
        telemetry=ProviderTelemetryRuntime(
            state_dir,
            ProviderTelemetryConfig(mode="managed", endpoint_env="ZF_TEST_OTLP_ENDPOINT"),
        ),
    )
    result = agent.run_turn(
        backend="claude",
        message="inspect",
        context={
            "trace_id": "trace-1",
            "project_id": "demo",
            "turn_id": "turn-1",
            "conversation_id": "chat-1",
        },
    )

    assert backend.env["TRACEPARENT"].startswith("00-")
    assert backend.env["OTEL_LOG_TOOL_CONTENT"] == "0"
    assert result.telemetry["capability"]["effective"] == "active"


@pytest.mark.parametrize("forbidden_label", ["task_id", "workflow_run_id", "provider_session_id"])
def test_runtime_logs_redact_and_metrics_reject_high_cardinality(
    tmp_path: Path,
    forbidden_label: str,
) -> None:
    state_dir = tmp_path / ".zf"
    write_runtime_log(
        state_dir,
        level="ERROR",
        component="test",
        message="token=super-secret-value",
        failure_class="test_failure",
        fields={"task_id": "task-1", "provider": "claude"},
    )
    rows = read_runtime_logs(state_dir)
    assert rows[0]["message"].endswith("[REDACTED_SECRET]")
    registry = OperationsMetricsRegistry(state_dir, enabled=True)
    registry.increment("zf_provider_operations_total", labels={"provider": "claude", "result": "completed"})
    registry.observe("zf_provider_operation_duration_seconds", 0.2, labels={"provider": "claude", "result": "completed"})
    assert "zf_provider_operations_total" in registry.prometheus_text()
    with pytest.raises(ValueError, match="high-cardinality"):
        registry.increment("zf_bad_total", labels={forbidden_label: "unique-1"})


def test_observability_config_is_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "zf.yaml"
    path.write_text(
        """version: '1.0'
project:
  name: demo
observability:
  provider_telemetry:
    mode: managed
    endpoint_env: ZF_TEST_OTLP_ENDPOINT
  metrics:
    enabled: true
    access_token_env: ZF_TEST_METRICS_TOKEN
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.observability.provider_telemetry.mode == "managed"
    assert config.observability.metrics.enabled is True
    assert config.observability.otlp_exporter.enabled is False
    assert config.observability.alerts.enabled is False

    path.write_text(
        """version: '1.0'
project:
  name: demo
observability:
  metrics:
    enabled: true
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="access_token_env"):
        load_config(path)


def test_otlp_exporter_config_accepts_only_environment_references(tmp_path: Path) -> None:
    path = tmp_path / "zf.yaml"
    path.write_text(
        """version: '1.0'
project:
  name: demo
observability:
  otlp_exporter:
    enabled: true
    endpoint_env: ZF_TEST_OTLP_ENDPOINT
    headers_env: ZF_TEST_OTLP_HEADERS
    batch_size: 16
    healthy_sample_rate: 0.25
  alerts:
    enabled: true
    cooldown_seconds: 60
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.observability.otlp_exporter.enabled is True
    assert config.observability.otlp_exporter.batch_size == 16
    assert config.observability.alerts.cooldown_seconds == 60

    path.write_text(
        """version: '1.0'
project:
  name: demo
observability:
  otlp_exporter:
    enabled: true
    endpoint_env: http://collector.invalid
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="environment variable name"):
        load_config(path)


def test_otlp_exporter_config_loads_from_kind_envelope_spec(tmp_path: Path) -> None:
    path = tmp_path / "zf.yaml"
    path.write_text(
        """apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata:
  name: demo
spec:
  version: "1.0"
  project:
    name: demo
  observability:
    otlp_exporter:
      enabled: true
      endpoint_env: ZF_TEST_OTLP_ENDPOINT
    alerts:
      enabled: true
      cooldown_seconds: 60
""",
        encoding="utf-8",
    )

    config = load_config(path)
    assert config.observability.otlp_exporter.endpoint_env == "ZF_TEST_OTLP_ENDPOINT"
    assert config.observability.alerts.enabled is True
