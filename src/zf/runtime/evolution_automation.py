"""Run Manager reconciliation for unattended, evidence-bound evolution.

Provider execution stays with the Autoresearch resident; this module only
folds immutable facts into the next request or controlled action.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.evolution_contracts import (
    EvolutionContractError,
    stable_digest,
)
from zf.runtime.evolution_environment import (
    EnvironmentSnapshotter,
    capture_evolution_environment,
    freeze_campaign_environment,
)
from zf.runtime.evolution_coordinator import EvolutionCoordinator
from zf.runtime.evolution_automation_support import (
    asset_for_attempt as _asset_for_attempt,
    campaign_terminal as _campaign_terminal,
    canary_failure_count as _canary_failure_count,
    canary_request_open as _canary_request_open,
    canary_terminal as _canary_terminal,
    complete_campaign as _complete_campaign,
    controlled_outcome as _controlled_outcome,
    controlled_transition as _controlled_transition,
    hydrate_campaign as _hydrate_campaign,
    latest_campaigns as _latest_campaigns,
    trial_request_open as _trial_request_open,
)
from zf.runtime.evolution_intake import (
    deposition_from_archive as _deposition_from_archive,
    validate_candidate as _validate_candidate,
)
from zf.runtime.run_archive import RunArchiveError
from zf.runtime.run_scope import event_run_id, run_aliases


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
        ))


def reconcile_evolution_automation(
    *,
    state_dir: Path,
    writer: EventWriter,
    config: Any,
    project_root: Path,
    events: list[ZfEvent] | None = None,
    environment_snapshotter: EnvironmentSnapshotter = capture_evolution_environment,
) -> EvolutionAutomationResult:
    """Advance all enabled campaigns by a bounded number of mechanical steps."""

    policy = getattr(getattr(config, "runtime", None), "evolution", None)
    if policy is None or not bool(getattr(policy, "enabled", False)):
        return EvolutionAutomationResult()
    state_dir = Path(state_dir).resolve(strict=False)
    project_root = Path(project_root).resolve(strict=False)
    events = list(events) if events is not None else writer.event_log.read_all()
    aliases = run_aliases(events)
    remaining = max(1, int(getattr(policy, "max_actions_per_tick", 4) or 4))
    counts = {
        "intake_materialized": 0,
        "intake_declined": 0,
        "trials_requested": 0,
        "comparisons_completed": 0,
        "assets_proposed": 0,
        "controlled_actions": 0,
        "campaigns_completed": 0,
    }

    handled_sources = {
        str(_payload(event).get("source_event_id") or "")
        for event in events
        if event.type in {CAMPAIGN_MATERIALIZED, CAMPAIGN_DECLINED}
    }
    handled_deposition_digests = {
        str(_payload(event).get("deposition_digest") or "")
        for event in events
        if event.type in {CAMPAIGN_MATERIALIZED, CAMPAIGN_DECLINED}
        and str(_payload(event).get("deposition_digest") or "")
    }
    for event in events:
        if remaining <= 0:
            break
        if event.type != "autoresearch.loop.completed" or event.id in handled_sources:
            continue
        payload = _payload(event)
        if str(payload.get("mode") or "") != "learn":
            continue
        deposition_ref: dict[str, Any] | None = None
        try:
            deposition, deposition_ref = _deposition_from_archive(
                state_dir=state_dir,
                event=event,
            )
            deposition_digest = str(deposition_ref["sha256"])
            if deposition_digest in handled_deposition_digests:
                writer.emit(
                    CAMPAIGN_DECLINED,
                    actor="run-manager",
                    task_id=event.task_id,
                    causation_id=event.id,
                    correlation_id=(
                        event.correlation_id
                        or event_run_id(event, aliases=aliases)
                        or str(payload.get("loop_request_id") or "")
                    ),
                    payload={
                        "schema_version": "evolution-campaign-declined.v1",
                        "source_event_id": event.id,
                        "deposition_ref": deposition_ref,
                        "deposition_digest": deposition_digest,
                        "reason": "capability deposition was already consumed",
                        "disposition": "stale_duplicate",
                        "terminal": True,
                        "human_action_required": False,
                    },
                )
                handled_sources.add(event.id)
                counts["intake_declined"] += 1
                remaining -= 1
                continue
            candidate = _validate_candidate(
                deposition,
                state_dir=state_dir,
                policy=policy,
            )
            requested = writer.emit(
                CAMPAIGN_REQUESTED,
                actor="run-manager",
                task_id=event.task_id,
                causation_id=event.id,
                correlation_id=(
                    event.correlation_id
                    or event_run_id(event, aliases=aliases)
                    or str(payload.get("loop_request_id") or "")
                ),
                payload={
                    "schema_version": "evolution-campaign-request.v1",
                    "source_event_id": event.id,
                    "deposition_digest": deposition_digest,
                    "deposition_ref": deposition_ref,
                    "candidate_digest": stable_digest(candidate),
                    "asset_kind": candidate["asset_kind"],
                    "task_family": candidate["task_family"],
                    "policy_mode": str(getattr(policy, "mode", "evaluate_only")),
                },
            )
            campaign = _materialize_campaign(
                state_dir=state_dir,
                project_root=project_root,
                source_event=event,
                source_payload=payload,
                deposition=deposition,
                deposition_ref=deposition_ref,
                candidate=candidate,
                policy=policy,
                environment_snapshotter=environment_snapshotter,
                workflow_run_id=(
                    event_run_id(event, aliases=aliases)
                    or str(payload.get("loop_request_id") or "")
                ),
            )
            coordinator = EvolutionCoordinator(state_dir, writer=writer)
            materialized = coordinator.materialize_attempt(
                campaign["attempt"], actor="run-manager-evolution"
            )
            descriptor = write_immutable_json_sidecar(
                state_dir,
                campaign,
                root="evolution/campaigns",
                kind="evolution_campaign",
                schema_version="evolution-campaign.v1",
                created_by="run-manager",
                source_event_id=event.id,
            )
            writer.emit(
                CAMPAIGN_MATERIALIZED,
                actor="run-manager",
                task_id=event.task_id,
                causation_id=requested.id,
                correlation_id=str(campaign["campaign_id"]),
                payload={
                    "schema_version": "evolution-campaign-materialized.v1",
                    "source_event_id": event.id,
                    "deposition_ref": deposition_ref,
                    "deposition_digest": deposition_digest,
                    "campaign_id": campaign["campaign_id"],
                    "attempt_id": campaign["attempt"]["attempt_id"],
                    "asset_kind": candidate["asset_kind"],
                    "campaign_ref": descriptor,
                    "attempt_ref": materialized["artifact_ref"],
                    "policy_digest": campaign["policy_digest"],
                },
            )
            counts["intake_materialized"] += 1
        except (EvolutionContractError, RunArchiveError, OSError, ValueError) as exc:
            decline_payload: dict[str, Any] = {
                "schema_version": "evolution-campaign-declined.v1",
                "source_event_id": event.id,
                "reason": str(exc),
                "disposition": getattr(exc, "disposition", "rejected"),
                "terminal": True,
                "human_action_required": False,
            }
            if deposition_ref is not None:
                decline_payload["deposition_ref"] = deposition_ref
                decline_payload["deposition_digest"] = str(
                    deposition_ref.get("sha256") or ""
                )
            asset_kind = str(getattr(exc, "asset_kind", "") or "")
            if asset_kind:
                decline_payload["asset_kind"] = asset_kind
            writer.emit(
                CAMPAIGN_DECLINED,
                actor="run-manager",
                task_id=event.task_id,
                causation_id=event.id,
                correlation_id=(
                    event.correlation_id
                    or event_run_id(event, aliases=aliases)
                    or str(payload.get("loop_request_id") or "")
                ),
                payload=decline_payload,
            )
            counts["intake_declined"] += 1
        remaining -= 1

    if counts["intake_materialized"] or counts["intake_declined"]:
        events = writer.event_log.read_all()

    coordinator = EvolutionCoordinator(state_dir, writer=writer)
    for materialized_event in _latest_campaigns(events):
        if remaining <= 0:
            break
        campaign = _hydrate_campaign(state_dir, materialized_event)
        if _campaign_terminal(events, str(campaign["campaign_id"])):
            continue
        outcome = _advance_campaign(
            state_dir=state_dir,
            project_root=project_root,
            writer=writer,
            coordinator=coordinator,
            config=config,
            policy=policy,
            campaign=campaign,
            campaign_event=materialized_event,
            events=events,
            remaining=remaining,
        )
        for key, value in outcome.items():
            counts[key] += value
        used = sum(outcome.values())
        remaining -= max(1, used) if used else 0
        if used:
            events = writer.event_log.read_all()

    return EvolutionAutomationResult(**counts)


def _advance_campaign(
    *,
    state_dir: Path,
    project_root: Path,
    writer: EventWriter,
    coordinator: EvolutionCoordinator,
    config: Any,
    policy: Any,
    campaign: dict[str, Any],
    campaign_event: ZfEvent,
    events: list[ZfEvent],
    remaining: int,
) -> dict[str, int]:
    counts = {
        "trials_requested": 0,
        "comparisons_completed": 0,
        "assets_proposed": 0,
        "controlled_actions": 0,
        "campaigns_completed": 0,
    }
    attempt_id = str(campaign["attempt"]["attempt_id"])
    repetitions = int(campaign["trial_repetitions"])
    trial_rows: list[dict[str, Any]] = []
    for replicate in range(1, repetitions + 1):
        for arm in ("baseline", "candidate"):
            row = coordinator.ensure_trial(
                attempt_id=attempt_id,
                arm=arm,
                replicate=replicate,
            )["trial"]
            trial_rows.append(row)
            if remaining - sum(counts.values()) <= 0:
                continue
            if _trial_ready_for_request(row) and not _trial_request_open(
                events, str(row["trial_id"])
            ):
                writer.emit(
                    TRIAL_REQUESTED,
                    actor="run-manager",
                    causation_id=campaign_event.id,
                    correlation_id=str(campaign["campaign_id"]),
                    payload={
                        "schema_version": "evolution-trial-request.v1",
                        "campaign_id": campaign["campaign_id"],
                        "attempt_id": attempt_id,
                        "trial_id": row["trial_id"],
                        "arm": arm,
                        "replicate": replicate,
                        "campaign_ref": _payload(campaign_event)["campaign_ref"],
                        "backend": str(getattr(policy, "backend", "")),
                        "model": str(getattr(policy, "model", "")),
                        "model_reasoning_effort": str(
                            getattr(policy, "model_reasoning_effort", "")
                        ),
                        "timeout_seconds": int(
                            getattr(policy, "trial_timeout_seconds", 300)
                        ),
                    },
                )
                counts["trials_requested"] += 1

    if any(row["status"] not in {"settled", "dead_letter"} for row in trial_rows):
        return counts
    if any(row["status"] == "dead_letter" for row in trial_rows):
        counts["campaigns_completed"] += _complete_campaign(
            writer,
            campaign_event,
            campaign,
            outcome=_terminal_trial_outcome(trial_rows),
            adoption="rejected",
        )
        return counts

    trial_state = coordinator.trials.load()
    comparison = next((
        row for row in trial_state["comparisons"].values()
        if row.get("attempt_id") == attempt_id
    ), None)
    if not isinstance(comparison, dict):
        result = coordinator.compare_attempt(
            attempt_id,
            evaluator_generation=campaign["evaluator"],
            actor="run-manager-evolution",
        )
        comparison = result["comparison"]
        counts["comparisons_completed"] += 1

    if str(comparison.get("status")) != "candidate_better":
        counts["campaigns_completed"] += _complete_campaign(
            writer,
            campaign_event,
            campaign,
            outcome=str(comparison.get("status") or "inconclusive"),
            adoption="rejected",
            comparison_id=str(comparison.get("comparison_id") or ""),
        )
        return counts

    asset = _asset_for_attempt(coordinator, attempt_id)
    if asset is None:
        proposed = coordinator.propose_asset(
            campaign["asset"],
            comparison_id=str(comparison["comparison_id"]),
            actor="run-manager-evolution",
        )
        asset = proposed["asset"]
        counts["assets_proposed"] += 1

    mode = str(getattr(policy, "mode", "evaluate_only"))
    if mode != "auto_low_risk" or str(asset["asset_kind"]) not in set(
        getattr(policy, "auto_asset_kinds", []) or []
    ):
        counts["campaigns_completed"] += _complete_campaign(
            writer,
            campaign_event,
            campaign,
            outcome="candidate_better",
            adoption="proposal_only",
            comparison_id=str(comparison["comparison_id"]),
            asset=asset,
        )
        return counts

    while asset["state"] in {"candidate", "validated", "approved"}:
        if remaining - sum(counts.values()) <= 0:
            return counts
        target = {
            "candidate": "validated",
            "validated": "approved",
            "approved": "canary_active",
        }[str(asset["state"])]
        result = _controlled_transition(
            state_dir=state_dir,
            project_root=project_root,
            writer=writer,
            config=config,
            campaign=campaign,
            asset=asset,
            target_state=target,
        )
        asset = result["asset"]
        counts["controlled_actions"] += int(bool(result.get("applied")))

    if asset["state"] == "canary_active":
        canary = _canary_terminal(events, str(asset["asset_id"]), int(asset["version"]))
        if canary is None:
            if not _canary_request_open(
                events, str(asset["asset_id"]), int(asset["version"])
            ):
                writer.emit(
                    CANARY_REQUESTED,
                    actor="run-manager",
                    causation_id=campaign_event.id,
                    correlation_id=str(campaign["campaign_id"]),
                    payload={
                        "schema_version": "evolution-canary-request.v1",
                        "campaign_id": campaign["campaign_id"],
                        "attempt_id": attempt_id,
                        "asset_id": asset["asset_id"],
                        "version": asset["version"],
                        "campaign_ref": _payload(campaign_event)["campaign_ref"],
                        "backend": str(getattr(policy, "backend", "")),
                        "model": str(getattr(policy, "model", "")),
                        "model_reasoning_effort": str(
                            getattr(policy, "model_reasoning_effort", "")
                        ),
                        "timeout_seconds": int(
                            getattr(policy, "trial_timeout_seconds", 300)
                        ),
                    },
                )
                counts["trials_requested"] += 1
            return counts
        if canary.type == CANARY_FAILED:
            canary_payload = _payload(canary)
            retryable = bool(canary_payload.get("retryable", True))
            failed_attempts = _canary_failure_count(
                events, str(asset["asset_id"]), int(asset["version"])
            )
            if retryable and failed_attempts < int(getattr(policy, "max_trial_attempts", 2)):
                if not _canary_request_open(
                    events, str(asset["asset_id"]), int(asset["version"])
                ):
                    writer.emit(
                        CANARY_REQUESTED,
                        actor="run-manager",
                        causation_id=canary.id,
                        correlation_id=str(campaign["campaign_id"]),
                        payload={
                            "schema_version": "evolution-canary-request.v1",
                            "campaign_id": campaign["campaign_id"],
                            "attempt_id": attempt_id,
                            "asset_id": asset["asset_id"],
                            "version": asset["version"],
                            "campaign_ref": _payload(campaign_event)["campaign_ref"],
                            "backend": str(getattr(policy, "backend", "")),
                            "model": str(getattr(policy, "model", "")),
                            "model_reasoning_effort": str(
                                getattr(policy, "model_reasoning_effort", "")
                            ),
                            "timeout_seconds": int(
                                getattr(policy, "trial_timeout_seconds", 300)
                            ),
                            "retry_attempt": failed_attempts + 1,
                        },
                    )
                    counts["trials_requested"] += 1
                return counts
            asset = _controlled_transition(
                state_dir=state_dir,
                project_root=project_root,
                writer=writer,
                config=config,
                campaign=campaign,
                asset=asset,
                target_state="revoked",
            )["asset"]
            counts["controlled_actions"] += 1
            counts["campaigns_completed"] += _complete_campaign(
                writer,
                campaign_event,
                campaign,
                outcome=_canary_terminal_outcome(canary_payload),
                adoption="revoked",
                comparison_id=str(comparison["comparison_id"]),
                asset=asset,
            )
            return counts

        canary_payload = _payload(canary)
        outcome = str(canary_payload.get("outcome") or "failed")
        outcome_result = _controlled_outcome(
            state_dir=state_dir,
            project_root=project_root,
            writer=writer,
            config=config,
            campaign=campaign,
            asset=asset,
            canary_event=canary,
        )
        asset = outcome_result["asset"]
        counts["controlled_actions"] += int(bool(outcome_result.get("recorded")))
        target = "active_retained" if outcome == "passed" else "revoked"
        transition = _controlled_transition(
            state_dir=state_dir,
            project_root=project_root,
            writer=writer,
            config=config,
            campaign=campaign,
            asset=asset,
            target_state=target,
        )
        asset = transition["asset"]
        counts["controlled_actions"] += int(bool(transition.get("applied")))
        counts["campaigns_completed"] += _complete_campaign(
            writer,
            campaign_event,
            campaign,
            outcome=f"canary_{outcome}",
            adoption="retained" if target == "active_retained" else "revoked",
            comparison_id=str(comparison["comparison_id"]),
            asset=asset,
        )
    return counts


def _trial_ready_for_request(row: Mapping[str, Any]) -> bool:
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


def _materialize_campaign(
    *,
    state_dir: Path,
    project_root: Path,
    source_event: ZfEvent,
    source_payload: Mapping[str, Any],
    deposition: Mapping[str, Any],
    deposition_ref: Mapping[str, Any],
    candidate: Mapping[str, Any],
    policy: Any,
    environment_snapshotter: EnvironmentSnapshotter,
    workflow_run_id: str,
) -> dict[str, Any]:
    source_digest = str(deposition_ref["sha256"])
    campaign_id = "evocamp-" + stable_digest({
        "source_event_id": source_event.id,
        "deposition_digest": source_digest,
        "candidate": candidate,
    })[:20]
    attempt_id = "evoattempt-" + stable_digest({
        "campaign_id": campaign_id,
        "candidate_digest": stable_digest(candidate),
    })[:20]
    evaluator = dict(candidate["evaluator"])
    task_family = str(candidate["task_family"])
    asset_id = str(candidate["asset_id"])
    content = str(candidate["content"])
    coordinator = EvolutionCoordinator(state_dir)
    registry = coordinator.capabilities.load()
    active_key = str(registry["active_versions"].get(asset_id) or "")
    active = registry["assets"].get(active_key) if active_key else None
    base_digest = str((active or {}).get("digest") or stable_digest({
        "asset_id": asset_id,
        "base": "none",
    }))
    candidate_digest = stable_digest({
        "asset_id": asset_id,
        "asset_kind": candidate["asset_kind"],
        "content": content,
        "task_family": task_family,
    })
    config_snapshot = _snapshot(
        state_dir,
        "config",
        {
            "enabled": bool(getattr(policy, "enabled", False)),
            "mode": str(getattr(policy, "mode", "evaluate_only")),
            "backend": str(getattr(policy, "backend", "")),
            "model": str(getattr(policy, "model", "")),
            "model_reasoning_effort": str(
                getattr(policy, "model_reasoning_effort", "")
            ),
            "trial_repetitions": int(getattr(policy, "trial_repetitions", 2)),
            "auto_asset_kinds": list(getattr(policy, "auto_asset_kinds", []) or []),
        },
        source_event.id,
    )
    environment_facts = freeze_campaign_environment(
        project_root=project_root,
        state_dir=state_dir,
        source_event_id=source_event.id,
        backend=str(getattr(policy, "backend", "")),
        model=str(getattr(policy, "model", "")),
        reasoning_effort=str(getattr(policy, "model_reasoning_effort", "")),
        token_env=str(getattr(policy, "access_token_env", "")),
        sealed_root=str(getattr(policy, "sealed_root", "")),
        snapshotter=environment_snapshotter,
    )
    environment_capability = environment_facts.capability
    capability_snapshot = environment_facts.capability_snapshot
    provider_snapshot = environment_facts.provider_snapshot
    toolchain_snapshot = environment_facts.toolchain_snapshot
    environment_snapshot = environment_facts.environment_snapshot
    sandbox_snapshot = environment_facts.sandbox_snapshot
    network_snapshot = environment_facts.network_snapshot
    credential_snapshot = environment_facts.credential_snapshot
    environment_digests = environment_facts.digests
    diff_snapshot = _snapshot(
        state_dir,
        "mutation",
        {"base_digest": base_digest, "candidate_digest": candidate_digest},
        source_event.id,
    )
    budget = {
        "max_cost_usd": float(getattr(policy, "max_cost_usd", 2.0)),
        "max_wall_seconds": float(getattr(policy, "trial_timeout_seconds", 300))
        * int(getattr(policy, "trial_repetitions", 2)) * 2,
        "max_tokens": float(getattr(policy, "max_tokens", 50_000)),
    }
    comparison_identity = {
        "scenario_set_digest": evaluator["scenario_set_digest"],
        "config_generation": config_snapshot["sha256"],
        "provider_capability_digest": provider_snapshot["sha256"],
        "toolchain_digest": toolchain_snapshot["sha256"],
        "environment_digest": environment_snapshot["sha256"],
        "sandbox_policy_digest": sandbox_snapshot["sha256"],
        "network_policy_digest": network_snapshot["sha256"],
        "credential_policy_digest": credential_snapshot["sha256"],
        "budget_digest": stable_digest(budget),
        "seed_policy_digest": stable_digest({"seed": "provider-managed-counterbalanced"}),
        "task_family": task_family,
    }
    manifest_ref = str(
        (source_payload.get("archive_refs") or {}).get("manifest") or ""
    )
    manifest_digest = str(
        (source_payload.get("archive_refs") or {}).get("manifest_digest") or ""
    )
    source_task = str(source_event.task_id or source_payload.get("task_id") or "")
    if not source_task:
        source_task = "autoresearch:" + str(
            source_payload.get("loop_request_id") or source_event.id
        )
    source_ref = str(deposition_ref["ref"])
    attempt = {
        "schema_version": "evolution-attempt.v1",
        "attempt_id": attempt_id,
        "campaign_id": campaign_id,
        "evolution_time": "post_task",
        "persistence_scope": str(candidate.get("persistence_scope") or "project"),
        "adoption_claim": "persistent_capability",
        "evidence_kinds": ["outcome", "environmental", "trajectory"],
        "objective": {
            "kind": "capability_accumulation",
            "summary": str(deposition["capability"]),
            "task_family": task_family,
        },
        "mutation": {
            "object_kind": "memory_entry",
            "identity_kind": "artifact_digest",
            "object_ref": f"learning-asset://{asset_id}",
            "base_version": base_digest,
            "candidate_version": candidate_digest,
            "diff_ref": diff_snapshot["ref"],
            "diff_digest": diff_snapshot["sha256"],
            "hypothesis_ref": source_ref,
        },
        "source_identity": {
            "workflow_run_id": workflow_run_id or str(
                source_payload.get("loop_request_id") or campaign_id
            ),
            "source_task_ids": [source_task],
            "briefing_ref": source_ref,
            "briefing_digest": source_digest,
            "context_read_set_ref": source_ref,
            "context_read_set_digest": source_digest,
            "skill_lock_ref": source_ref,
            "skill_lock_digest": source_digest,
            "memory_snapshot_ref": source_ref,
            "memory_snapshot_digest": source_digest,
            "tool_policy_ref": sandbox_snapshot["ref"],
            "tool_policy_digest": sandbox_snapshot["sha256"],
        },
        "frozen_inputs": {
            "config_ref": config_snapshot["ref"],
            "config_digest": config_snapshot["sha256"],
            "workflow_generation": config_snapshot["sha256"],
            "evaluator_ref": candidate["evaluator_ref"]["ref"],
            "evaluator_digest": evaluator["generation_digest"],
            "evaluation_harness_digest": evaluator["tcb_digest"],
            "comparison_parser_digest": evaluator["parser_digest"],
            "scenario_set_ref": candidate["evaluator_ref"]["ref"],
            "scenario_set_digest": evaluator["scenario_set_digest"],
            "holdout_authority_ref": evaluator["holdout_authority_ref"],
            "holdout_generation_digest": evaluator["holdout_generation_digest"],
            "provider_capability_ref": provider_snapshot["ref"],
            "provider_capability_digest": provider_snapshot["sha256"],
            "provider": str(getattr(policy, "backend", "")),
            "model": str(getattr(policy, "model", "") or "provider-default"),
            "toolchain_ref": toolchain_snapshot["ref"],
            "toolchain_digest": toolchain_snapshot["sha256"],
            "environment_ref": environment_snapshot["ref"],
            "environment_digest": environment_snapshot["sha256"],
            "sandbox_policy_ref": sandbox_snapshot["ref"],
            "sandbox_policy_digest": sandbox_snapshot["sha256"],
            "network_policy_ref": network_snapshot["ref"],
            "network_policy_digest": network_snapshot["sha256"],
            "credential_policy_ref": credential_snapshot["ref"],
            "credential_policy_digest": credential_snapshot["sha256"],
            "run_archive_manifest_ref": manifest_ref,
            "run_archive_manifest_digest": manifest_digest,
        },
        "evaluation_policy": {
            "pairing_key": stable_digest(comparison_identity),
            "min_trials": int(evaluator["min_trials"]),
            "min_delta": float(evaluator["min_delta"]),
            "required_gates": [str(item["id"]) for item in evaluator["required_gates"]],
            "required_score_dimensions": [
                str(item["id"]) for item in evaluator["required_score_dimensions"]
            ],
            "score_weights_digest": evaluator["weights_digest"],
            "numeric_policy": "finite_bounded",
            "trial_order": "counterbalanced",
            "selection": "pareto_then_policy",
        },
        "execution_policy": {
            "attempt_idempotency_key": stable_digest({
                "campaign_id": campaign_id,
                "candidate_digest": candidate_digest,
            }),
            "lease_seconds": int(getattr(policy, "lease_seconds", 600)),
            "max_trial_attempts": int(getattr(policy, "max_trial_attempts", 2)),
            "retry_policy": "infrastructure_only",
        },
        "budget": budget,
        "policy": {
            "apply_mode": "proposal_only",
            "owner_approval_required": True,
            "canary_required": True,
        },
    }
    trial_repetitions = max(
        int(evaluator["min_trials"]),
        int(getattr(policy, "trial_repetitions", 2)),
    )
    canary_evaluator = dict(candidate.get("canary_evaluator") or {})
    policy_body = {
        "mode": str(getattr(policy, "mode", "evaluate_only")),
        "auto_asset_kinds": list(getattr(policy, "auto_asset_kinds", []) or []),
        "trial_repetitions": trial_repetitions,
        "max_trial_attempts": int(getattr(policy, "max_trial_attempts", 2)),
        "budget": budget,
    }
    asset = {
        "schema_version": "learning-asset.v1",
        "asset_id": asset_id,
        "asset_kind": candidate["asset_kind"],
        "version": _next_asset_version(registry, asset_id),
        "digest": candidate_digest,
        "source_attempt_ids": [attempt_id],
        "content": content,
        "applicability": {
            "task_families": [task_family],
            **dict(candidate.get("applicability") or {}),
        },
        "quality": {
            "confidence": str(candidate.get("confidence") or "medium"),
            "expires_at": str(candidate.get("expires_at") or "2099-01-01T00:00:00+00:00"),
        },
        "activation": {
            "mode": "proposal_only",
            "owner_approval_required": True,
            "automation_policy": policy_body["mode"],
            "automation_policy_digest": stable_digest(policy_body),
            "canary_scope_ref": str(
                canary_evaluator.get("holdout_authority_ref")
                or evaluator["holdout_authority_ref"]
            ),
            "expected_active_key": active_key,
            "retain_policy": {
                "min_matched_outcomes": 1,
                "max_negative_transfer": 0,
            },
        },
        "rollback": {
            "previous_version_ref": active_key,
            "conditions": ["canary_failed", "negative_transfer"],
        },
        "dependencies": list(candidate.get("dependencies") or []),
        "provenance": {
            "project": project_root.name,
            "source_event_id": source_event.id,
            "deposition_ref": dict(deposition_ref),
            "target_validation": "passed",
        },
        "taint": {
            "blocked": False,
            "secret": False,
            "pii": False,
            "license_unknown": False,
            **dict(candidate.get("taint") or {}),
        },
    }
    return {
        "schema_version": "evolution-campaign.v1",
        "campaign_id": campaign_id,
        "source_event_id": source_event.id,
        "deposition_ref": dict(deposition_ref),
        "attempt": attempt,
        "asset": asset,
        "evaluator": evaluator,
        "evaluator_ref": dict(candidate["evaluator_ref"]),
        "canary_evaluator": canary_evaluator,
        "canary_evaluator_ref": dict(candidate.get("canary_evaluator_ref") or {}),
        "comparison_identity": comparison_identity,
        "trial_repetitions": trial_repetitions,
        "policy": policy_body,
        "policy_digest": stable_digest(policy_body),
        "environment_capability": {
            "schema_version": str(environment_capability.get("schema_version") or ""),
            "snapshot_ref": capability_snapshot,
            "snapshot_digest": capability_snapshot["sha256"],
            "frozen_digests": environment_digests,
        },
    }


def _snapshot(
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


def _terminal_trial_outcome(rows: list[Mapping[str, Any]]) -> str:
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


def _canary_terminal_outcome(payload: Mapping[str, Any]) -> str:
    failure_class = str(payload.get("failure_class") or "")
    if failure_class == "evolution_environment_comparison_drift":
        return "environment_comparison_drift"
    if failure_class.startswith("evolution_environment_"):
        return "environment_preflight_failed"
    return "canary_infrastructure_exhausted"


def _next_asset_version(registry: Mapping[str, Any], asset_id: str) -> int:
    versions = [
        int(row.get("version") or 0)
        for row in (registry.get("assets") or {}).values()
        if isinstance(row, Mapping) and str(row.get("asset_id") or "") == asset_id
    ]
    return max(versions, default=0) + 1


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


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
    "reconcile_evolution_automation",
]
