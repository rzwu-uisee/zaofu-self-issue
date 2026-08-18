"""Provider-native telemetry boundary for per-turn ZaoFu operations.

The EventLog remains the canonical business trace.  This module only derives
an opaque W3C-compatible correlation context, describes provider capability,
and records read-only observation sidecars.  It never receives provider
payloads or writes Task/Feature/Session state.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.security.redaction import redact_obj
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import FileLock


_SCHEMA_VERSION = "provider-telemetry.v1"
_OPERATION_KINDS = frozenset({
    "workflow_dispatch",
    "kanban_turn",
    "channel_turn",
    "sidecar_operation",
})
_MAX_BINDINGS = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_provider(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("_", "-")
    if normalized.startswith("claude"):
        return "claude"
    if normalized.startswith("codex"):
        return "codex"
    return normalized or "unknown"


@dataclass(frozen=True)
class TelemetryOperationContextV1:
    """Stable input for one provider operation, never a task-state mutation."""

    operation_kind: str
    correlation_id: str = ""
    project_id: str = ""
    workflow_run_id: str = ""
    task_id: str = ""
    dispatch_id: str = ""
    attempt_id: str = ""
    role_instance_id: str = ""
    provider: str = ""
    conversation_id: str = ""
    thread_id: str = ""
    provider_session_id: str = ""

    def __post_init__(self) -> None:
        if self.operation_kind not in _OPERATION_KINDS:
            raise ValueError(
                "operation_kind must be workflow_dispatch, kanban_turn, "
                "channel_turn, or sidecar_operation"
            )

    @classmethod
    def from_dispatch(cls, context: Any) -> "TelemetryOperationContextV1":
        """Build from DispatchContext without importing transport internals."""

        return cls(
            operation_kind="workflow_dispatch",
            correlation_id=str(getattr(context, "trace_id", "") or ""),
            project_id=str(getattr(context, "project_id", "") or ""),
            workflow_run_id=str(getattr(context, "run_id", "") or ""),
            task_id=str(getattr(context, "task_id", "") or ""),
            dispatch_id=str(getattr(context, "dispatch_id", "") or ""),
            attempt_id=str(getattr(context, "attempt_id", "") or ""),
            role_instance_id=str(getattr(context, "instance_id", "") or ""),
            provider=canonical_provider(str(getattr(context, "backend", "") or "")),
        )

    @classmethod
    def interaction(
        cls,
        *,
        operation_kind: str,
        correlation_id: str = "",
        project_id: str = "",
        workflow_run_id: str = "",
        task_id: str = "",
        dispatch_id: str = "",
        attempt_id: str = "",
        role_instance_id: str = "kanban-agent",
        provider: str = "",
        conversation_id: str = "",
        thread_id: str = "",
        provider_session_id: str = "",
    ) -> "TelemetryOperationContextV1":
        return cls(
            operation_kind=operation_kind,
            correlation_id=correlation_id,
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            dispatch_id=dispatch_id,
            attempt_id=attempt_id,
            role_instance_id=role_instance_id,
            provider=canonical_provider(provider),
            conversation_id=conversation_id,
            thread_id=thread_id,
            provider_session_id=provider_session_id,
        )

    def identity_fields(self) -> dict[str, str]:
        return {
            "operation_kind": self.operation_kind,
            "correlation_id": self.correlation_id,
            "project_id": self.project_id,
            "workflow_run_id": self.workflow_run_id,
            "task_id": self.task_id,
            "dispatch_id": self.dispatch_id,
            "attempt_id": self.attempt_id,
            "role_instance_id": self.role_instance_id,
            "provider": canonical_provider(self.provider),
            "conversation_id": self.conversation_id,
            "thread_id": self.thread_id,
            "provider_session_id": self.provider_session_id,
        }


@dataclass(frozen=True)
class TelemetryContextV1:
    """Opaque W3C parent context deterministically derived from an operation."""

    operation: TelemetryOperationContextV1
    otel_trace_id: str
    otel_parent_span_id: str
    sampling_mode: str = "01"
    schema_version: str = _SCHEMA_VERSION

    @classmethod
    def from_operation(
        cls,
        operation: TelemetryOperationContextV1,
    ) -> "TelemetryContextV1":
        identity = json.dumps(
            operation.identity_fields(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        trace_id = hashlib.sha256(
            f"{_SCHEMA_VERSION}|trace|{identity}".encode("utf-8")
        ).hexdigest()[:32]
        span_id = hashlib.sha256(
            f"{_SCHEMA_VERSION}|parent|{identity}".encode("utf-8")
        ).hexdigest()[:16]
        return cls(operation=operation, otel_trace_id=trace_id, otel_parent_span_id=span_id)

    @property
    def traceparent(self) -> str:
        return f"00-{self.otel_trace_id}-{self.otel_parent_span_id}-{self.sampling_mode}"

    def public_identity(self) -> dict[str, str]:
        operation = self.operation
        return {
            "schema_version": self.schema_version,
            "zaofu_correlation_id": operation.correlation_id,
            "project_id": operation.project_id,
            "workflow_run_id": operation.workflow_run_id,
            "task_id": operation.task_id,
            "dispatch_id": operation.dispatch_id,
            "attempt_id": operation.attempt_id,
            "role_instance_id": operation.role_instance_id,
            "provider": canonical_provider(operation.provider),
            "provider_session_id": operation.provider_session_id,
            "operation_kind": operation.operation_kind,
            "otel_trace_id": self.otel_trace_id,
            "otel_parent_span_id": self.otel_parent_span_id,
            "sampling_mode": self.sampling_mode,
        }


@dataclass(frozen=True)
class TelemetryCapabilityV1:
    provider: str
    route: str
    requested: str
    detected: str
    effective: str
    join_kind: str
    w3c_inbound: bool
    signals: dict[str, bool]
    collector_delivery: str = "unobserved"
    provider_version: str = ""
    evidence_ref: str = ""
    observed_at: str = ""
    failure_class: str = ""
    schema_version: str = _SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return redact_obj(asdict(self))


@dataclass(frozen=True)
class TelemetryLaunch:
    context: TelemetryContextV1
    capability: TelemetryCapabilityV1
    env: dict[str, str]


class ProviderTelemetryRuntime:
    """Build per-turn launch env and persist capability/binding observations."""

    def __init__(self, state_dir: Path, config: Any | None = None) -> None:
        self.state_dir = Path(state_dir)
        self.config = config

    @property
    def mode(self) -> str:
        return str(getattr(self.config, "mode", "off") or "off").strip().lower()

    @property
    def requested(self) -> str:
        if self.mode == "managed":
            profile = str(getattr(self.config, "profile_id", "") or "zaofu-managed-v1")
            return f"profile:{profile}"
        return self.mode if self.mode in {"off", "host_managed"} else "off"

    def launch(
        self,
        operation: TelemetryOperationContextV1,
        *,
        route: str,
    ) -> TelemetryLaunch:
        context = TelemetryContextV1.from_operation(operation)
        provider = canonical_provider(operation.provider)
        capability, env = self._capability_and_env(
            provider=provider,
            route=route,
            context=context,
        )
        self._record_capability(capability)
        if capability.effective == "active":
            self._record_binding(context, capability)
        return TelemetryLaunch(context=context, capability=capability, env=env)

    def _capability_and_env(
        self,
        *,
        provider: str,
        route: str,
        context: TelemetryContextV1,
    ) -> tuple[TelemetryCapabilityV1, dict[str, str]]:
        now = _now()
        signals = {"logs": False, "metrics": False, "traces": False}
        if route == "tmux":
            return TelemetryCapabilityV1(
                provider=provider,
                route=route,
                requested=self.requested,
                detected="absent",
                effective="unsupported" if self.mode != "off" else "disabled",
                join_kind="derived_only",
                w3c_inbound=False,
                signals=signals,
                observed_at=now,
                failure_class="tmux_route_not_per_turn" if self.mode != "off" else "",
            ), {}
        if self.mode == "off":
            detected = "configured_externally" if _host_otel_configured() else "absent"
            return TelemetryCapabilityV1(
                provider=provider,
                route=route,
                requested="off",
                detected=detected,
                effective="disabled",
                join_kind="derived_only",
                w3c_inbound=False,
                signals=signals,
                observed_at=now,
            ), {}
        if self.mode == "host_managed":
            detected = "configured_externally" if _host_otel_configured() else "absent"
            return TelemetryCapabilityV1(
                provider=provider,
                route=route,
                requested="host_managed",
                detected=detected,
                effective="partial" if detected == "configured_externally" else "disabled",
                join_kind="unobserved",
                w3c_inbound=False,
                signals=signals,
                observed_at=now,
                failure_class="host_profile_not_linked" if detected == "configured_externally" else "",
            ), {}

        endpoint_env = str(getattr(self.config, "endpoint_env", "") or "").strip()
        endpoint = os.environ.get(endpoint_env, "").strip() if endpoint_env else ""
        if not endpoint:
            return TelemetryCapabilityV1(
                provider=provider,
                route=route,
                requested=self.requested,
                detected="probe_failed",
                effective="disabled",
                join_kind="derived_only",
                w3c_inbound=False,
                signals=signals,
                observed_at=now,
                failure_class="endpoint_env_missing",
            ), {}
        if provider != "claude":
            return TelemetryCapabilityV1(
                provider=provider,
                route=route,
                requested=self.requested,
                detected="probe_failed",
                effective="unsupported",
                join_kind="unobserved",
                w3c_inbound=False,
                signals=signals,
                observed_at=now,
                failure_class="provider_native_probe_required",
            ), {}

        traces_enabled = bool(getattr(self.config, "enable_traces", False))
        signals = {"logs": True, "metrics": True, "traces": traces_enabled}
        env = {
            "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
            "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint,
            "OTEL_LOGS_EXPORTER": "otlp",
            "OTEL_METRICS_EXPORTER": "otlp",
            "OTEL_TRACES_EXPORTER": "otlp" if traces_enabled else "none",
            "OTEL_SERVICE_NAME": "zaofu-claude-headless",
            "TRACEPARENT": context.traceparent,
            # Provider defaults can carry high-cardinality account/session attrs.
            "OTEL_METRICS_INCLUDE_SESSION_ID": "false",
            "OTEL_METRICS_INCLUDE_ACCOUNT_UUID": "false",
            "OTEL_METRICS_INCLUDE_RESOURCE_ATTRIBUTES": "false",
            "OTEL_LOG_USER_PROMPTS": "0",
            "OTEL_LOG_ASSISTANT_RESPONSES": "0",
            "OTEL_LOG_TOOL_DETAILS": "0",
            "OTEL_LOG_TOOL_CONTENT": "0",
            "OTEL_LOG_RAW_API_BODIES": "0",
        }
        if traces_enabled:
            env["CLAUDE_CODE_ENHANCED_TELEMETRY_BETA"] = "1"
        return TelemetryCapabilityV1(
            provider=provider,
            route=route,
            requested=self.requested,
            detected="observed",
            effective="active",
            join_kind="parent_child",
            w3c_inbound=True,
            signals=signals,
            observed_at=now,
        ), env

    def snapshot(self) -> dict[str, Any]:
        path = _state_path(self.state_dir)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        return {
            "schema_version": _SCHEMA_VERSION,
            "requested_mode": self.requested,
            "updated_at": str(raw.get("updated_at") or ""),
            "capabilities": list(raw.get("capabilities") or []),
            "bindings": list(raw.get("bindings") or []),
        }

    def _record_capability(self, capability: TelemetryCapabilityV1) -> None:
        def update(payload: dict[str, Any]) -> None:
            rows = [item for item in payload.get("capabilities", []) if isinstance(item, dict)]
            key = (capability.provider, capability.route)
            rows = [
                item for item in rows
                if (str(item.get("provider") or ""), str(item.get("route") or "")) != key
            ]
            rows.append(capability.to_dict())
            payload["capabilities"] = sorted(
                rows,
                key=lambda item: (str(item.get("provider") or ""), str(item.get("route") or "")),
            )

        self._update_state(update)

    def _record_binding(
        self,
        context: TelemetryContextV1,
        capability: TelemetryCapabilityV1,
    ) -> None:
        binding = {
            "bound_at": _now(),
            "provider": capability.provider,
            "route": capability.route,
            "join_kind": capability.join_kind,
            **context.public_identity(),
        }

        def update(payload: dict[str, Any]) -> None:
            rows = [item for item in payload.get("bindings", []) if isinstance(item, dict)]
            rows.append(binding)
            payload["bindings"] = rows[-_MAX_BINDINGS:]

        self._update_state(update)

    def _update_state(self, update: Any) -> None:
        path = _state_path(self.state_dir)
        lock = path.with_name(path.name + ".lock")
        with FileLock(lock):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            payload["schema_version"] = _SCHEMA_VERSION
            update(payload)
            payload["updated_at"] = _now()
            atomic_write_text(
                path,
                json.dumps(redact_obj(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )


def _state_path(state_dir: Path) -> Path:
    return Path(state_dir) / "projections" / "provider_telemetry.json"


def _host_otel_configured() -> bool:
    return any(
        str(os.environ.get(name) or "").strip()
        for name in (
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "OTEL_TRACES_EXPORTER",
            "OTEL_METRICS_EXPORTER",
            "CLAUDE_CODE_ENABLE_TELEMETRY",
        )
    )


def read_provider_telemetry_snapshot(
    state_dir: Path,
    *,
    config: Any | None = None,
) -> dict[str, Any]:
    return ProviderTelemetryRuntime(state_dir, config).snapshot()


__all__ = [
    "ProviderTelemetryRuntime",
    "TelemetryCapabilityV1",
    "TelemetryContextV1",
    "TelemetryLaunch",
    "TelemetryOperationContextV1",
    "canonical_provider",
    "read_provider_telemetry_snapshot",
]
