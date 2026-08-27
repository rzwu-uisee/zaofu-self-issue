"""Small controlled-action and event helpers for evolution automation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.evolution_contracts import EvolutionContractError, stable_digest
from zf.runtime.evolution_coordinator import EvolutionCoordinator
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


CAMPAIGN_REQUESTED = "evolution.campaign.requested"
CAMPAIGN_MATERIALIZED = "evolution.campaign.materialized"
CAMPAIGN_DECLINED = "evolution.campaign.declined"
CAMPAIGN_COMPLETED = "evolution.campaign.completed"
TRIAL_REQUESTED = "evolution.trial.requested"
TRIAL_EXECUTION_COMPLETED = "evolution.trial.execution.completed"
TRIAL_EXECUTION_FAILED = "evolution.trial.execution.failed"
CANARY_REQUESTED = "evolution.canary.requested"
CANARY_FAILED = "evolution.canary.failed"


@dataclass(frozen=True)
class EvolutionAutomationResult:
    intake_materialized: int = 0
    intake_declined: int = 0
    trials_requested: int = 0
    comparisons_completed: int = 0
    assets_proposed: int = 0
    controlled_actions: int = 0
    campaigns_completed: int = 0
    optimizer_requests: int = 0
    optimizer_steps: int = 0
    optimizer_exports: int = 0
    optimizer_rejected: int = 0

    @property
    def changed(self) -> bool:
        return any((
            self.intake_materialized,
            self.intake_declined,
            self.trials_requested,
            self.comparisons_completed,
            self.assets_proposed,
            self.controlled_actions,
            self.campaigns_completed,
            self.optimizer_requests,
            self.optimizer_steps,
            self.optimizer_exports,
            self.optimizer_rejected,
        ))

    @property
    def action_count(self) -> int:
        return sum((
            self.intake_materialized,
            self.intake_declined,
            self.trials_requested,
            self.comparisons_completed,
            self.assets_proposed,
            self.controlled_actions,
            self.campaigns_completed,
            self.optimizer_requests,
            self.optimizer_steps,
            self.optimizer_exports,
            self.optimizer_rejected,
        ))


def handled_evolution_sources(
    events: list[ZfEvent],
) -> tuple[set[str], set[str]]:
    handled_types = {
        CAMPAIGN_MATERIALIZED,
        CAMPAIGN_DECLINED,
        "evolution.skill_optimizer.started",
    }
    source_ids = {
        str(_payload(event).get("source_event_id") or "")
        for event in events
        if event.type in handled_types
    }
    deposition_digests = {
        str(_payload(event).get("deposition_digest") or "")
        for event in events
        if event.type in handled_types
        and str(_payload(event).get("deposition_digest") or "")
    }
    return source_ids, deposition_digests


def skill_maintenance_decision(
    comparison: Mapping[str, Any],
) -> tuple[str, str]:
    current_vs_raw = (
        (comparison.get("paired_lifts") or {})
        .get("comparisons", {})
        .get("current_vs_raw", {})
    )
    delta = current_vs_raw.get("median_delta")
    if isinstance(delta, (int, float)) and float(delta) < 0:
        return (
            "deactivate",
            "matched raw treatment outperformed the current Skill; propose "
            "owner-reviewed deactivation",
        )
    return (
        "optimize",
        "candidate did not satisfy trusted adoption; retain evidence and "
        "propose another bounded optimization",
    )


def trial_ready_for_request(row: Mapping[str, Any]) -> bool:
    status = str(row.get("status") or "")
    if status in {"prepared", "failed"}:
        return True
    if status != "running":
        return False
    expires = str(row.get("lease_expires_at") or "").strip()
    if not expires:
        return True
    try:
        parsed = datetime.fromisoformat(expires.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= datetime.now(timezone.utc)


def snapshot(
    state_dir: Path,
    label: str,
    body: Mapping[str, Any],
    source_event_id: str,
) -> dict[str, Any]:
    return write_immutable_json_sidecar(
        state_dir,
        dict(body),
        root=f"evolution/snapshots/{label}",
        kind=f"evolution_{label}_snapshot",
        schema_version=f"evolution-{label}-snapshot.v1",
        created_by="run-manager",
        source_event_id=source_event_id,
    )


def terminal_trial_outcome(rows: list[Mapping[str, Any]]) -> str:
    failure_classes = {
        str(row.get("failure_class") or "")
        for row in rows
        if str(row.get("failure_class") or "")
    }
    if "evolution_environment_comparison_drift" in failure_classes:
        return "environment_comparison_drift"
    if any(value.startswith("evolution_environment_") for value in failure_classes):
        return "environment_preflight_failed"
    return "trial_attempts_exhausted"


def canary_terminal_outcome(payload: Mapping[str, Any]) -> str:
    failure_class = str(payload.get("failure_class") or "")
    if failure_class == "evolution_environment_comparison_drift":
        return "environment_comparison_drift"
    if failure_class.startswith("evolution_environment_"):
        return "environment_preflight_failed"
    return "canary_infrastructure_exhausted"


def next_asset_version(registry: Mapping[str, Any], asset_id: str) -> int:
    versions = [
        int(row.get("version") or 0)
        for row in (registry.get("assets") or {}).values()
        if isinstance(row, Mapping) and str(row.get("asset_id") or "") == asset_id
    ]
    return max(versions, default=0) + 1


def controlled_transition(
    *,
    state_dir: Path,
    project_root: Path,
    writer: EventWriter,
    config: Any,
    campaign: Mapping[str, Any],
    asset: Mapping[str, Any],
    target_state: str,
    reason: str = "",
) -> dict[str, Any]:
    from zf.runtime.control_actions import ControlledActionService

    action_id = "evoact-" + stable_digest({
        "campaign_id": campaign["campaign_id"],
        "asset_id": asset["asset_id"],
        "version": asset["version"],
        "target_state": target_state,
    })[:20]
    requested = writer.emit(
        "run.manager.action.planned",
        actor="run-manager",
        correlation_id=str(campaign["campaign_id"]),
        payload={
            "schema_version": "run-manager.action.v1",
            "action": "evolution-asset-transition",
            "action_id": action_id,
            "policy_digest": campaign["policy_digest"],
        },
    )
    result = ControlledActionService(
        state_dir,
        writer,
        config=config,
        project_root=project_root,
        actor="run-manager",
        source="self-evolution",
        surface="run-manager",
    ).execute(
        action="evolution-asset-transition",
        requested_action="evolution-asset-transition",
        payload={
            "asset_id": asset["asset_id"],
            "version": asset["version"],
            "target_state": target_state,
            "expected_revision": asset["revision"],
            "action_id": action_id,
            "campaign_id": campaign["campaign_id"],
            "policy_digest": campaign["policy_digest"],
            "reason": reason,
            "previous_digest": str(
                (asset.get("activation") or {}).get("previous_digest") or ""
            ),
            "source_mutated": False,
        },
        requested=requested,
    )
    if not result.get("ok"):
        raise EvolutionContractError(
            f"controlled evolution transition failed: {result.get('reason') or result}"
        )
    return dict(result)


def controlled_outcome(
    *,
    state_dir: Path,
    project_root: Path,
    writer: EventWriter,
    config: Any,
    campaign: Mapping[str, Any],
    asset: Mapping[str, Any],
    canary_event: ZfEvent,
) -> dict[str, Any]:
    from zf.runtime.control_actions import ControlledActionService

    payload = _payload(canary_event)
    requested = writer.emit(
        "run.manager.action.planned",
        actor="run-manager",
        causation_id=canary_event.id,
        correlation_id=str(campaign["campaign_id"]),
        payload={
            "schema_version": "run-manager.action.v1",
            "action": "evolution-asset-outcome",
            "policy_digest": campaign["policy_digest"],
        },
    )
    result = ControlledActionService(
        state_dir,
        writer,
        config=config,
        project_root=project_root,
        actor="run-manager",
        source="self-evolution",
        surface="run-manager",
    ).execute(
        action="evolution-asset-outcome",
        requested_action="evolution-asset-outcome",
        payload={
            "asset_id": asset["asset_id"],
            "version": asset["version"],
            "usage_ref": str(payload.get("usage_ref") or f"event://{canary_event.id}"),
            "outcome": str(payload.get("outcome") or "failed"),
            "cost": dict(payload.get("cost") or {}),
            "cohort": dict(payload.get("cohort") or {}),
            "evaluation": dict(payload.get("evaluation") or {}),
            "campaign_id": campaign["campaign_id"],
            "policy_digest": campaign["policy_digest"],
            "source_event_id": canary_event.id,
        },
        requested=requested,
    )
    if not result.get("ok"):
        raise EvolutionContractError(
            f"controlled evolution outcome failed: {result.get('reason') or result}"
        )
    return dict(result)


def complete_campaign(
    writer: EventWriter,
    campaign_event: ZfEvent,
    campaign: Mapping[str, Any],
    *,
    outcome: str,
    adoption: str,
    comparison_id: str = "",
    asset: Mapping[str, Any] | None = None,
    action_ref: Mapping[str, Any] | None = None,
    human_action_required: bool = False,
) -> int:
    writer.emit(
        "evolution.campaign.completed",
        actor="run-manager",
        causation_id=campaign_event.id,
        correlation_id=str(campaign["campaign_id"]),
        payload={
            "schema_version": "evolution-campaign-result.v1",
            "campaign_id": campaign["campaign_id"],
            "attempt_id": campaign["attempt"]["attempt_id"],
            "outcome": outcome,
            "adoption": adoption,
            "comparison_id": comparison_id,
            "asset_id": str((asset or {}).get("asset_id") or ""),
            "asset_version": int((asset or {}).get("version") or 0),
            "asset_state": str((asset or {}).get("state") or ""),
            "action_ref": dict(action_ref or {}),
            "policy_digest": campaign["policy_digest"],
            "human_action_required": bool(human_action_required),
        },
    )
    return 1


def hydrate_campaign(state_dir: Path, event: ZfEvent) -> dict[str, Any]:
    descriptor = _payload(event).get("campaign_ref")
    if not isinstance(descriptor, Mapping):
        raise EvolutionContractError("campaign materialization lacks campaign_ref")
    hydrated = hydrate_sidecar_ref(
        state_dir,
        dict(descriptor),
        purpose="evolution-campaign-reconcile",
        actor="run-manager",
    )
    if not isinstance(hydrated.payload, Mapping):
        raise EvolutionContractError("evolution campaign body is invalid")
    body = dict(hydrated.payload)
    if str(body.get("schema_version") or "") != "evolution-campaign.v1":
        raise EvolutionContractError("unsupported evolution campaign schema")
    return body


def latest_campaigns(events: list[ZfEvent]) -> list[ZfEvent]:
    out: dict[str, ZfEvent] = {}
    for event in events:
        if event.type != "evolution.campaign.materialized":
            continue
        campaign_id = str(_payload(event).get("campaign_id") or "")
        if campaign_id:
            out[campaign_id] = event
    return list(out.values())


def campaign_terminal(events: list[ZfEvent], campaign_id: str) -> bool:
    return any(
        event.type == "evolution.campaign.completed"
        and str(_payload(event).get("campaign_id") or "") == campaign_id
        for event in events
    )


def trial_request_open(events: list[ZfEvent], trial_id: str) -> bool:
    request: ZfEvent | None = None
    for event in events:
        payload = _payload(event)
        if event.type == "evolution.trial.requested":
            if str(payload.get("trial_id") or "") == trial_id:
                request = event
        elif event.type in {
            "evolution.trial.execution.completed",
            "evolution.trial.execution.failed",
        } and request is not None and (
            str(payload.get("request_event_id") or "") == request.id
            or str(payload.get("trial_id") or "") == trial_id
        ):
            request = None
    return request is not None


def canary_request_open(events: list[ZfEvent], asset_id: str, version: int) -> bool:
    request: ZfEvent | None = None
    for event in events:
        payload = _payload(event)
        if (
            str(payload.get("asset_id") or "") != asset_id
            or int(payload.get("version") or 0) != version
        ):
            continue
        if event.type == "evolution.canary.requested":
            request = event
        elif event.type in {"evolution.canary.completed", "evolution.canary.failed"}:
            request = None
    return request is not None


def canary_terminal(
    events: list[ZfEvent], asset_id: str, version: int
) -> ZfEvent | None:
    for event in reversed(events):
        payload = _payload(event)
        if event.type not in {"evolution.canary.completed", "evolution.canary.failed"}:
            continue
        if (
            str(payload.get("asset_id") or "") == asset_id
            and int(payload.get("version") or 0) == version
        ):
            return event
    return None


def canary_failure_count(events: list[ZfEvent], asset_id: str, version: int) -> int:
    return sum(
        1 for event in events
        if event.type == "evolution.canary.failed"
        and str(_payload(event).get("asset_id") or "") == asset_id
        and int(_payload(event).get("version") or 0) == version
    )


def asset_for_attempt(
    coordinator: EvolutionCoordinator, attempt_id: str
) -> dict[str, Any] | None:
    for row in coordinator.capabilities.load()["assets"].values():
        if attempt_id in list(row.get("source_attempt_ids") or []):
            return dict(row)
    return None


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


event_payload = _payload


__all__ = [
    "CAMPAIGN_COMPLETED",
    "CAMPAIGN_DECLINED",
    "CAMPAIGN_MATERIALIZED",
    "CAMPAIGN_REQUESTED",
    "CANARY_FAILED",
    "CANARY_REQUESTED",
    "EvolutionAutomationResult",
    "TRIAL_EXECUTION_COMPLETED",
    "TRIAL_EXECUTION_FAILED",
    "TRIAL_REQUESTED",
    "asset_for_attempt",
    "campaign_terminal",
    "canary_failure_count",
    "canary_request_open",
    "canary_terminal",
    "canary_terminal_outcome",
    "complete_campaign",
    "controlled_outcome",
    "controlled_transition",
    "event_payload",
    "handled_evolution_sources",
    "hydrate_campaign",
    "latest_campaigns",
    "next_asset_version",
    "skill_maintenance_decision",
    "snapshot",
    "terminal_trial_outcome",
    "trial_ready_for_request",
    "trial_request_open",
]
