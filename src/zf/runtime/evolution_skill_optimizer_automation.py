"""Run Manager reconciliation for the bounded Skill Optimizer Agent loop."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.evolution_skill_optimizer import (
    SkillOptimizationService,
    SkillOptimizerError,
)
from zf.runtime.evolution_skill_optimizer_agent import (
    OPTIMIZER_AGENT_REQUESTED,
    request_skill_optimizer_proposal,
)
from zf.runtime.evolution_skill_optimizer_contracts import CAMPAIGN_SCHEMA
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


OPTIMIZER_INTAKE_SCHEMA = "skill-optimizer-intake.v1"
OPTIMIZER_SELECTION_COMPLETED = "evolution.skill_optimizer.selection.completed"
OPTIMIZER_SELECTION_REJECTED = "evolution.skill_optimizer.selection.rejected"
OPTIMIZER_SELECTION_SUPERSEDED = "evolution.skill_optimizer.selection.superseded"


@dataclass(frozen=True)
class SkillOptimizerAutomationResult:
    requests: int = 0
    steps: int = 0
    exports: int = 0
    rejected: int = 0

    @property
    def action_count(self) -> int:
        return self.requests + self.steps + self.exports + self.rejected


def is_skill_optimizer_intake(deposition: Mapping[str, Any]) -> bool:
    return isinstance(deposition.get("skill_optimizer"), Mapping)


def materialize_skill_optimizer_intake(
    *,
    state_dir: Path,
    writer: EventWriter,
    deposition: Mapping[str, Any],
    deposition_ref: Mapping[str, Any],
    source_event: ZfEvent,
    policy: Any,
) -> dict[str, Any]:
    """Initialize one v2 optimizer campaign from an Autoresearch deposition."""

    raw = deposition.get("skill_optimizer")
    if (
        not isinstance(raw, Mapping)
        or raw.get("schema_version") != OPTIMIZER_INTAKE_SCHEMA
    ):
        raise SkillOptimizerError(f"skill_optimizer must be {OPTIMIZER_INTAKE_SCHEMA}")
    campaign = raw.get("campaign")
    baseline = raw.get("baseline_evaluation")
    train_ref = raw.get("train_evidence_ref")
    failures = raw.get("failure_cluster_refs")
    if (
        not isinstance(campaign, Mapping)
        or campaign.get("schema_version") != CAMPAIGN_SCHEMA
    ):
        raise SkillOptimizerError(
            "Autoresearch optimizer intake requires a v2 campaign"
        )
    if not isinstance(baseline, Mapping):
        raise SkillOptimizerError(
            "Autoresearch optimizer intake requires baseline_evaluation"
        )
    if not isinstance(train_ref, Mapping):
        raise SkillOptimizerError(
            "Autoresearch optimizer intake requires train_evidence_ref"
        )
    if (
        not isinstance(failures, list)
        or not failures
        or not all(isinstance(item, Mapping) for item in failures)
    ):
        raise SkillOptimizerError(
            "Autoresearch optimizer intake requires failure_cluster_refs"
        )
    context = {
        "schema_version": OPTIMIZER_INTAKE_SCHEMA,
        "campaign": dict(campaign),
        "baseline_evaluation": dict(baseline),
        "train_evidence_ref": dict(train_ref),
        "failure_cluster_refs": [dict(item) for item in failures],
        "source_deposition_ref": dict(deposition_ref),
    }
    context_ref = write_immutable_json_sidecar(
        state_dir,
        context,
        root="evolution/skill-optimizer/intakes",
        kind="skill_optimizer_intake",
        schema_version=OPTIMIZER_INTAKE_SCHEMA,
        created_by="run-manager-evolution",
        source_event_id=source_event.id,
    )
    service = SkillOptimizationService(
        state_dir,
        event_log=writer.event_log,
        event_writer=writer,
        actor="run-manager-evolution",
    )
    initialized = service.initialize(
        campaign,
        baseline_evaluation=baseline,
        source_event_id=source_event.id,
        source_deposition_digest=str(deposition_ref.get("sha256") or ""),
        source_context_ref=context_ref,
    )
    request = request_skill_optimizer_proposal(
        state_dir=state_dir,
        writer=writer,
        state_ref=initialized["state_ref"],
        train_evidence_ref=train_ref,
        failure_cluster_refs=failures,
        backend=str(getattr(policy, "backend", "")),
        model=str(getattr(policy, "model", "")),
        reasoning_effort=str(getattr(policy, "model_reasoning_effort", "")),
        timeout_seconds=int(getattr(policy, "trial_timeout_seconds", 300)),
        source_event_id=source_event.id,
    )
    return {**initialized, "optimizer_request": request, "context_ref": context_ref}


def reconcile_skill_optimizer_automation(
    *,
    state_dir: Path,
    writer: EventWriter,
    policy: Any,
    events: list[ZfEvent] | None = None,
    max_actions: int = 4,
) -> SkillOptimizerAutomationResult:
    """Settle Selection facts, then resume or export the latest optimizer state."""

    state_dir = Path(state_dir)
    events = list(events) if events is not None else writer.event_log.read_all()
    service = SkillOptimizationService(
        state_dir,
        event_log=writer.event_log,
        event_writer=writer,
        actor="run-manager-evolution",
    )
    requests = steps = exports = rejected = 0
    settled_step_ids = {
        str(_payload(event).get("step_id") or "")
        for event in events
        if event.type == "evolution.skill_optimizer.step.completed"
    }
    handled_selection_ids = {
        str(_payload(event).get("selection_event_id") or "")
        for event in events
        if event.type in {OPTIMIZER_SELECTION_REJECTED, OPTIMIZER_SELECTION_SUPERSEDED}
    }
    for event in events:
        if steps + rejected >= max_actions:
            break
        if (
            event.type != OPTIMIZER_SELECTION_COMPLETED
            or event.id in handled_selection_ids
        ):
            continue
        payload = _payload(event)
        try:
            step = _hydrate(
                state_dir,
                payload.get("step_ref"),
                purpose="skill-optimizer-selection-step",
            )
            if str(step.get("step_id") or "") in settled_step_ids:
                continue
            evaluation = _hydrate(
                state_dir,
                payload.get("evaluation_ref"),
                purpose="skill-optimizer-selection-evaluation",
            )
            service.settle_step(
                payload.get("state_ref") or {},
                payload.get("step_ref") or {},
                evaluation=evaluation,
            )
            steps += 1
        except (SkillOptimizerError, OSError, ValueError) as exc:
            event_type = (
                OPTIMIZER_SELECTION_SUPERSEDED
                if "stale" in str(exc).lower()
                else OPTIMIZER_SELECTION_REJECTED
            )
            writer.append(
                ZfEvent(
                    type=event_type,
                    actor="run-manager",
                    causation_id=event.id,
                    correlation_id=event.correlation_id,
                    payload={
                        "schema_version": "skill-optimizer-selection-verdict.v1",
                        "selection_event_id": event.id,
                        "campaign_id": str(payload.get("campaign_id") or ""),
                        "reason": str(exc),
                        "state_ref": dict(payload.get("state_ref") or {}),
                        "step_ref": dict(payload.get("step_ref") or {}),
                    },
                )
            )
            rejected += 1
    if steps or rejected:
        events = writer.event_log.read_all()

    latest_states = _latest_state_events(events)
    for campaign_id, state_event in latest_states.items():
        if requests + steps + exports + rejected >= max_actions:
            break
        state_ref = _payload(state_event).get("state_ref")
        try:
            state = _hydrate(
                state_dir,
                state_ref,
                purpose="skill-optimizer-latest-state",
            )
            if state.get("status") == "completed":
                if not _candidate_exported(events, campaign_id):
                    service.export_best(state_ref or {})
                    exports += 1
                continue
            if state.get("status") != "running" or _state_has_request(
                events, state_ref
            ):
                continue
            context = _hydrate(
                state_dir,
                state.get("source_context_ref"),
                schema_version=OPTIMIZER_INTAKE_SCHEMA,
                purpose="skill-optimizer-resume-context",
            )
            created = request_skill_optimizer_proposal(
                state_dir=state_dir,
                writer=writer,
                state_ref=state_ref or {},
                train_evidence_ref=context["train_evidence_ref"],
                failure_cluster_refs=context["failure_cluster_refs"],
                backend=str(getattr(policy, "backend", "")),
                model=str(getattr(policy, "model", "")),
                reasoning_effort=str(getattr(policy, "model_reasoning_effort", "")),
                timeout_seconds=int(getattr(policy, "trial_timeout_seconds", 300)),
                source_event_id=state_event.id,
            )
            requests += int(bool(created["created"]))
        except (SkillOptimizerError, OSError, ValueError) as exc:
            writer.append(
                ZfEvent(
                    type=OPTIMIZER_SELECTION_REJECTED,
                    actor="run-manager",
                    causation_id=state_event.id,
                    correlation_id=campaign_id,
                    payload={
                        "schema_version": "skill-optimizer-selection-verdict.v1",
                        "selection_event_id": state_event.id,
                        "campaign_id": campaign_id,
                        "reason": f"optimizer resume failed: {exc}",
                        "state_ref": dict(state_ref or {}),
                        "step_ref": {},
                    },
                )
            )
            rejected += 1
    return SkillOptimizerAutomationResult(
        requests=requests,
        steps=steps,
        exports=exports,
        rejected=rejected,
    )


def submit_skill_optimizer_selection(
    *,
    state_dir: Path,
    writer: EventWriter,
    selection_request_event_id: str,
    evaluation: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and publish one sealed Selection result for Run Manager."""

    events = writer.event_log.read_all()
    request = next(
        (event for event in events if event.id == selection_request_event_id), None
    )
    if (
        request is None
        or request.type != "evolution.skill_optimizer.selection.requested"
    ):
        raise SkillOptimizerError("Skill optimizer Selection request not found")
    prior = next(
        (
            event
            for event in events
            if event.type == OPTIMIZER_SELECTION_COMPLETED
            and str(_payload(event).get("selection_request_event_id") or "")
            == selection_request_event_id
        ),
        None,
    )
    if prior is not None:
        return {
            "created": False,
            "event_id": prior.id,
            "evaluation_ref": dict(_payload(prior).get("evaluation_ref") or {}),
        }
    payload = _payload(request)
    service = SkillOptimizationService(
        state_dir,
        event_log=writer.event_log,
        event_writer=writer,
        actor="sealed-skill-evaluator",
    )
    normalized = service.validate_selection_evaluation(
        payload.get("state_ref") or {},
        candidate_digest=str(payload.get("candidate_content_digest") or ""),
        evaluation=evaluation,
    )
    evaluation_ref = write_immutable_json_sidecar(
        state_dir,
        normalized,
        root="evolution/skill-optimizer/evaluations",
        kind="skill_optimization_evaluation",
        schema_version="skill-optimization-evaluation.v1",
        created_by="sealed-skill-evaluator",
        source_event_id=request.id,
    )
    event = writer.append(
        ZfEvent(
            type=OPTIMIZER_SELECTION_COMPLETED,
            actor="sealed-skill-evaluator",
            causation_id=request.id,
            correlation_id=str(payload.get("campaign_id") or ""),
            payload={
                "schema_version": "skill-optimizer-selection-result.v1",
                "selection_request_event_id": request.id,
                "campaign_id": str(payload.get("campaign_id") or ""),
                "state_ref": dict(payload.get("state_ref") or {}),
                "step_ref": dict(payload.get("step_ref") or {}),
                "evaluation_ref": evaluation_ref,
            },
        )
    )
    return {"created": True, "event_id": event.id, "evaluation_ref": evaluation_ref}


def _latest_state_events(events: list[ZfEvent]) -> dict[str, ZfEvent]:
    result: dict[str, ZfEvent] = {}
    for event in events:
        if event.type not in {
            "evolution.skill_optimizer.started",
            "evolution.skill_optimizer.step.completed",
        }:
            continue
        campaign_id = str(_payload(event).get("campaign_id") or "")
        if campaign_id:
            result[campaign_id] = event
    return result


def _state_has_request(events: list[ZfEvent], state_ref: object) -> bool:
    if not isinstance(state_ref, Mapping):
        return False
    return any(
        event.type == OPTIMIZER_AGENT_REQUESTED
        and _same_ref(_payload(event).get("state_ref"), state_ref)
        for event in events
    )


def _candidate_exported(events: list[ZfEvent], campaign_id: str) -> bool:
    return any(
        event.type == "evolution.skill_optimizer.candidate.exported"
        and str(_payload(event).get("campaign_id") or "") == campaign_id
        for event in events
    )


def _hydrate(
    state_dir: Path,
    descriptor: object,
    *,
    purpose: str,
    schema_version: str = "",
) -> dict[str, Any]:
    if not isinstance(descriptor, Mapping):
        raise SkillOptimizerError(f"{purpose} descriptor is required")
    hydrated = hydrate_sidecar_ref(
        state_dir,
        dict(descriptor),
        purpose=purpose,
        actor="run-manager",
    )
    if not isinstance(hydrated.payload, Mapping):
        raise SkillOptimizerError(f"{purpose} payload is invalid")
    body = dict(hydrated.payload)
    if schema_version and body.get("schema_version") != schema_version:
        raise SkillOptimizerError(f"{purpose} schema drift")
    return body


def _same_ref(left: object, right: object) -> bool:
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return False
    return str(left.get("ref") or "") == str(right.get("ref") or "") and str(
        left.get("sha256") or ""
    ) == str(right.get("sha256") or "")


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


__all__ = [
    "OPTIMIZER_INTAKE_SCHEMA",
    "OPTIMIZER_SELECTION_COMPLETED",
    "SkillOptimizerAutomationResult",
    "is_skill_optimizer_intake",
    "materialize_skill_optimizer_intake",
    "reconcile_skill_optimizer_automation",
    "submit_skill_optimizer_selection",
]
