"""Task Pipeline adapters and admission bindings for typed call results."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.integration_acceptance_result import (
    IntegrationAcceptanceResultError,
    PROFILE_ID,
    PROFILE_REVISION,
    SCHEMA_VERSION,
    bind_required_read_ledger,
    normalize_integration_acceptance_result,
)


def integration_acceptance_adapter() -> Any:
    from zf.runtime.call_result_adapters import ControlResultAdapter

    return ControlResultAdapter(
        adapter_id="task-integration-acceptance-result-v1",
        schema_version=SCHEMA_VERSION,
        accepts=_is_integration_acceptance_event,
        normalize=_normalize_integration_acceptance,
    )


def integration_acceptance_profile() -> Any:
    from zf.runtime.call_result_adapters import CallResultProfile

    return CallResultProfile(
        profile_id=PROFILE_ID,
        revision=PROFILE_REVISION,
        schema_version=SCHEMA_VERSION,
        adapter_id="task-integration-acceptance-result-v1",
        semantic_field="integration_acceptance_result",
        allowed_event_types=(
            "task.pipeline.acceptance.completed",
            "task.pipeline.acceptance.failed",
        ),
    )


def bind_integration_acceptance_ledger(
    adapted: Any,
    ledger_descriptor: Mapping[str, Any],
    *,
    state_dir: Path,
    source_event_id: str,
) -> Any:
    """Replace worker ledger claims with the Kernel-sealed descriptor."""

    if str(adapted.schema_version) != SCHEMA_VERSION:
        return adapted
    try:
        bound = bind_required_read_ledger(adapted.payload, ledger_descriptor)
    except IntegrationAcceptanceResultError as exc:
        return replace(
            adapted,
            issues=(
                *adapted.issues,
                {
                    "field": "control_result.required_read_ledger_ref",
                    "code": "required_read_ledger_invalid",
                    "message": str(exc),
                },
            ),
        )
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar

    descriptor = write_immutable_json_sidecar(
        state_dir,
        bound,
        root=f"call-results/control/{SCHEMA_VERSION}",
        kind="call_control_result",
        schema_version=adapted.schema_version,
        created_by="call-result-admission:required-read-binding",
        source_event_id=source_event_id,
    )
    return replace(adapted, payload=bound, descriptor=descriptor)


def _is_integration_acceptance_event(event: ZfEvent) -> bool:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return (
        event.type
        in {
            "task.pipeline.acceptance.completed",
            "task.pipeline.acceptance.failed",
        }
        and isinstance(payload.get("integration_acceptance_result"), Mapping)
    )


def _normalize_integration_acceptance(
    event: ZfEvent,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    try:
        return normalize_integration_acceptance_result(payload), []
    except IntegrationAcceptanceResultError as exc:
        raw = payload.get("integration_acceptance_result")
        result = dict(raw) if isinstance(raw, Mapping) else {
            "schema_version": SCHEMA_VERSION,
        }
        return result, [{
            "field": "control_result",
            "code": "schema_invalid",
            "message": str(exc),
        }]


__all__ = [
    "bind_integration_acceptance_ledger",
    "integration_acceptance_adapter",
    "integration_acceptance_profile",
]
