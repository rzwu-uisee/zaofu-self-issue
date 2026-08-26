"""Bounded single-Skill optimizer feeding the trusted evolution lifecycle."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.evolution_contracts import stable_digest
from zf.runtime.evolution_skill import validate_skill_candidate
from zf.runtime.evolution_skill_optimizer_contracts import (
    CAMPAIGN_SCHEMA,
    EVALUATION_SCHEMA,
    MATERIAL_SCHEMA,
    PROPOSAL_SCHEMA,
    STATE_SCHEMA,
    STEP_SCHEMA,
    SkillOptimizerError,
    apply_edits,
    normalize_campaign,
    normalize_evaluation,
    normalize_proposal,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


_STATE_EVENT_TYPES = frozenset({
    "evolution.skill_optimizer.started",
    "evolution.skill_optimizer.step.completed",
})

class SkillOptimizationService:
    """Apply Agent-proposed edits and select only strict held-out improvements."""

    def __init__(
        self,
        state_dir: Path,
        *,
        event_log: EventLog,
        event_writer: EventWriter,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.event_log = event_log
        self.event_writer = event_writer

    def initialize(
        self,
        campaign: Mapping[str, Any],
        *,
        baseline_evaluation: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized, base_content = normalize_campaign(campaign)
        campaign_id = str(normalized["campaign_id"])
        if self._latest_state_ref(campaign_id) is not None:
            raise SkillOptimizerError(f"Skill optimizer campaign already exists: {campaign_id}")

        base_material = _write_material(
            self.state_dir,
            campaign_id=campaign_id,
            skill_name=str(normalized["skill_name"]),
            content=base_content,
            parent_digest="",
            epoch=0,
            source_event_id="",
        )
        normalized["base_material_ref"] = base_material
        campaign_ref = _write_sidecar(
            self.state_dir,
            normalized,
            root="evolution/skill-optimizer/campaigns",
            kind="skill_optimization_campaign",
            schema_version=CAMPAIGN_SCHEMA,
        )
        evaluation = normalize_evaluation(
            baseline_evaluation,
            campaign=normalized,
            expected_candidate_digest=str(base_material["content_digest"]),
        )
        evaluation_ref = _write_sidecar(
            self.state_dir,
            evaluation,
            root="evolution/skill-optimizer/evaluations",
            kind="skill_optimization_evaluation",
            schema_version=EVALUATION_SCHEMA,
        )
        state = {
            "schema_version": STATE_SCHEMA,
            "campaign_id": campaign_id,
            "campaign_ref": campaign_ref,
            "epoch": 0,
            "status": "running",
            "stop_reason": "",
            "best_material_ref": base_material,
            "best_content_digest": str(base_material["content_digest"]),
            "best_evaluation_ref": evaluation_ref,
            "best_total_score": evaluation["total_score"],
            "best_scores": dict(evaluation["scores"]),
            "accepted_step_count": 0,
            "consecutive_no_improvement": 0,
            "rejection_buffer": [],
            "slow_meta_state": {},
            "slow_meta_revision": 0,
            "last_step_ref": {},
        }
        state_ref = self._write_state(state)
        event = self.event_writer.append(ZfEvent(
            type="evolution.skill_optimizer.started",
            actor="zf-cli",
            correlation_id=campaign_id,
            payload={
                "schema_version": STATE_SCHEMA,
                "campaign_id": campaign_id,
                "campaign_ref": campaign_ref,
                "state_ref": state_ref,
                "best_content_digest": state["best_content_digest"],
                "epoch": 0,
            },
        ))
        return {
            "campaign": normalized,
            "campaign_ref": campaign_ref,
            "state": state,
            "state_ref": state_ref,
            "event_id": event.id,
        }

    def prepare_step(
        self,
        state_descriptor: Mapping[str, Any],
        *,
        proposal: Mapping[str, Any],
    ) -> dict[str, Any]:
        state = self._current_state(state_descriptor)
        if state["status"] != "running":
            raise SkillOptimizerError("Skill optimizer campaign is not running")
        campaign = self._campaign(state)
        next_epoch = int(state["epoch"]) + 1
        if next_epoch > int(campaign["max_epochs"]):
            raise SkillOptimizerError("Skill optimizer epoch budget is exhausted")
        normalized_proposal = normalize_proposal(
            proposal,
            campaign=campaign,
            state=state,
            next_epoch=next_epoch,
        )
        base = _hydrate_payload(
            self.state_dir,
            state["best_material_ref"],
            schema_version=MATERIAL_SCHEMA,
        )
        candidate_content = apply_edits(
            str(base["content"]),
            normalized_proposal["edits"],
        )
        candidate = validate_skill_candidate({
            **dict(campaign["candidate_metadata"]),
            "schema_version": "skill-candidate.v1",
            "skill_name": campaign["skill_name"],
            "content": candidate_content,
        })
        proposal_ref = _write_sidecar(
            self.state_dir,
            normalized_proposal,
            root="evolution/skill-optimizer/proposals",
            kind="skill_edit_proposal",
            schema_version=PROPOSAL_SCHEMA,
        )
        material_ref = _write_material(
            self.state_dir,
            campaign_id=str(campaign["campaign_id"]),
            skill_name=str(campaign["skill_name"]),
            content=candidate_content,
            parent_digest=str(state["best_content_digest"]),
            epoch=next_epoch,
            source_event_id="",
        )
        step = {
            "schema_version": STEP_SCHEMA,
            "step_id": stable_digest({
                "campaign_id": campaign["campaign_id"],
                "parent_state_digest": state_descriptor.get("sha256"),
                "proposal_digest": proposal_ref["sha256"],
            }),
            "campaign_id": campaign["campaign_id"],
            "campaign_ref": state["campaign_ref"],
            "epoch": next_epoch,
            "parent_state_ref": dict(state_descriptor),
            "base_content_digest": state["best_content_digest"],
            "proposal_ref": proposal_ref,
            "candidate_material_ref": material_ref,
            "candidate_content_digest": candidate["content_digest"],
            "status": "prepared",
        }
        step_ref = _write_sidecar(
            self.state_dir,
            step,
            root="evolution/skill-optimizer/steps",
            kind="skill_optimization_step",
            schema_version=STEP_SCHEMA,
        )
        event = self.event_writer.append(ZfEvent(
            type="evolution.skill_optimizer.step.prepared",
            actor="zf-cli",
            correlation_id=str(campaign["campaign_id"]),
            payload={
                "schema_version": STEP_SCHEMA,
                "campaign_id": campaign["campaign_id"],
                "step_id": step["step_id"],
                "epoch": next_epoch,
                "step_ref": step_ref,
                "candidate_content_digest": candidate["content_digest"],
            },
        ))
        return {
            "step": step,
            "step_ref": step_ref,
            "candidate": candidate,
            "event_id": event.id,
        }

    def settle_step(
        self,
        state_descriptor: Mapping[str, Any],
        step_descriptor: Mapping[str, Any],
        *,
        evaluation: Mapping[str, Any],
    ) -> dict[str, Any]:
        state = self._current_state(state_descriptor)
        campaign = self._campaign(state)
        step = _hydrate_payload(
            self.state_dir,
            step_descriptor,
            schema_version=STEP_SCHEMA,
        )
        if str(step.get("campaign_id") or "") != str(campaign["campaign_id"]):
            raise SkillOptimizerError("Skill optimization step belongs to another campaign")
        if not _same_ref(step.get("parent_state_ref"), state_descriptor):
            raise SkillOptimizerError("Skill optimization step parent state is stale")
        if int(step.get("epoch") or 0) != int(state["epoch"]) + 1:
            raise SkillOptimizerError("Skill optimization step epoch is stale")

        candidate_digest = str(step["candidate_content_digest"])
        normalized_evaluation = normalize_evaluation(
            evaluation,
            campaign=campaign,
            expected_candidate_digest=candidate_digest,
        )
        evaluation_ref = _write_sidecar(
            self.state_dir,
            normalized_evaluation,
            root="evolution/skill-optimizer/evaluations",
            kind="skill_optimization_evaluation",
            schema_version=EVALUATION_SCHEMA,
        )
        proposal = _hydrate_payload(
            self.state_dir,
            step["proposal_ref"],
            schema_version=PROPOSAL_SCHEMA,
        )
        blocking_regressions = [
            dimension["id"]
            for dimension in campaign["score_dimensions"]
            if dimension["blocking"]
            and normalized_evaluation["scores"][dimension["id"]]
            < float(state["best_scores"][dimension["id"]])
        ]
        delta = round(
            float(normalized_evaluation["total_score"])
            - float(state["best_total_score"]),
            6,
        )
        accepted = delta > 0 and not blocking_regressions
        if blocking_regressions:
            reason = "blocking_dimension_regression"
        elif delta == 0:
            reason = "strict_selection_tie"
        elif delta < 0:
            reason = "score_not_improved"
        else:
            reason = "strict_improvement"

        rejection_buffer = [dict(item) for item in state["rejection_buffer"]]
        if not accepted:
            rejection_buffer.append({
                "step_ref": dict(step_descriptor),
                "proposal_ref": dict(step["proposal_ref"]),
                "candidate_content_digest": candidate_digest,
                "evaluation_ref": evaluation_ref,
                "score_delta": delta,
                "reason": reason,
                "blocking_regressions": blocking_regressions,
            })
            rejection_buffer = rejection_buffer[-int(campaign["rejection_buffer_size"]):]

        slow_meta_state = deepcopy(dict(state["slow_meta_state"]))
        slow_meta_revision = int(state["slow_meta_revision"])
        slow_meta_update = proposal.get("slow_meta_update")
        if isinstance(slow_meta_update, Mapping):
            slow_meta_state = deepcopy(dict(slow_meta_update))
            slow_meta_revision += 1

        epoch = int(step["epoch"])
        no_improvement = 0 if accepted else int(state["consecutive_no_improvement"]) + 1
        status = "running"
        stop_reason = ""
        if epoch >= int(campaign["max_epochs"]):
            status = "completed"
            stop_reason = "max_epochs_reached"
        elif no_improvement >= int(campaign["max_consecutive_no_improvement"]):
            status = "completed"
            stop_reason = "no_improvement_budget_reached"

        new_state = {
            **state,
            "epoch": epoch,
            "status": status,
            "stop_reason": stop_reason,
            "best_material_ref": (
                dict(step["candidate_material_ref"])
                if accepted
                else dict(state["best_material_ref"])
            ),
            "best_content_digest": (
                candidate_digest if accepted else state["best_content_digest"]
            ),
            "best_evaluation_ref": (
                evaluation_ref if accepted else dict(state["best_evaluation_ref"])
            ),
            "best_total_score": (
                normalized_evaluation["total_score"]
                if accepted
                else state["best_total_score"]
            ),
            "best_scores": (
                dict(normalized_evaluation["scores"])
                if accepted
                else dict(state["best_scores"])
            ),
            "accepted_step_count": int(state["accepted_step_count"]) + int(accepted),
            "consecutive_no_improvement": no_improvement,
            "rejection_buffer": rejection_buffer,
            "slow_meta_state": slow_meta_state,
            "slow_meta_revision": slow_meta_revision,
            "last_step_ref": dict(step_descriptor),
        }
        state_ref = self._write_state(new_state)
        completed = self.event_writer.append(ZfEvent(
            type="evolution.skill_optimizer.step.completed",
            actor="zf-cli",
            correlation_id=str(campaign["campaign_id"]),
            payload={
                "schema_version": STATE_SCHEMA,
                "campaign_id": campaign["campaign_id"],
                "step_id": step["step_id"],
                "epoch": epoch,
                "selection": "accepted" if accepted else "rejected",
                "selection_reason": reason,
                "score_delta": delta,
                "blocking_regressions": blocking_regressions,
                "evaluation_ref": evaluation_ref,
                "state_ref": state_ref,
                "status": status,
                "semantic_attempt_incremented": False,
            },
        ))
        if status == "completed":
            self.event_writer.append(ZfEvent(
                type="evolution.skill_optimizer.completed",
                actor="zf-cli",
                causation_id=completed.id,
                correlation_id=str(campaign["campaign_id"]),
                payload={
                    "schema_version": STATE_SCHEMA,
                    "campaign_id": campaign["campaign_id"],
                    "state_ref": state_ref,
                    "best_content_digest": new_state["best_content_digest"],
                    "best_total_score": new_state["best_total_score"],
                    "stop_reason": stop_reason,
                },
            ))
        return {
            "selection": "accepted" if accepted else "rejected",
            "selection_reason": reason,
            "score_delta": delta,
            "blocking_regressions": blocking_regressions,
            "evaluation": normalized_evaluation,
            "evaluation_ref": evaluation_ref,
            "state": new_state,
            "state_ref": state_ref,
            "event_id": completed.id,
        }

    def export_best(
        self,
        state_descriptor: Mapping[str, Any],
    ) -> dict[str, Any]:
        state = self._current_state(state_descriptor)
        if state["status"] != "completed":
            raise SkillOptimizerError("Skill optimizer must complete before export")
        campaign = self._campaign(state)
        material = _hydrate_payload(
            self.state_dir,
            state["best_material_ref"],
            schema_version=MATERIAL_SCHEMA,
        )
        candidate = validate_skill_candidate({
            **dict(campaign["candidate_metadata"]),
            "schema_version": "skill-candidate.v1",
            "skill_name": campaign["skill_name"],
            "content": material["content"],
            "content_digest": state["best_content_digest"],
            "candidate_version": state["best_content_digest"],
            "optimizer_provenance": {
                "campaign_ref": state["campaign_ref"],
                "state_ref": dict(state_descriptor),
                "accepted_step_count": state["accepted_step_count"],
                "best_evaluation_ref": state["best_evaluation_ref"],
            },
        })
        candidate_ref = _write_sidecar(
            self.state_dir,
            candidate,
            root="evolution/skill-candidates",
            kind="skill_candidate",
            schema_version="skill-candidate.v1",
        )
        event = self.event_writer.append(ZfEvent(
            type="evolution.skill_optimizer.candidate.exported",
            actor="zf-cli",
            correlation_id=str(campaign["campaign_id"]),
            payload={
                "schema_version": "skill-candidate.v1",
                "campaign_id": campaign["campaign_id"],
                "skill_name": campaign["skill_name"],
                "candidate_digest": candidate["content_digest"],
                "candidate_ref": candidate_ref,
                "next_lifecycle": "design-179-evaluation-and-adoption",
            },
        ))
        return {
            "candidate": candidate,
            "candidate_ref": candidate_ref,
            "event_id": event.id,
        }

    def _campaign(self, state: Mapping[str, Any]) -> dict[str, Any]:
        return _hydrate_payload(
            self.state_dir,
            state["campaign_ref"],
            schema_version=CAMPAIGN_SCHEMA,
        )

    def _current_state(
        self,
        descriptor: Mapping[str, Any],
    ) -> dict[str, Any]:
        state = _hydrate_payload(
            self.state_dir,
            descriptor,
            schema_version=STATE_SCHEMA,
        )
        latest = self._latest_state_ref(str(state.get("campaign_id") or ""))
        if latest is None or not _same_ref(latest, descriptor):
            raise SkillOptimizerError("Skill optimizer state is stale")
        return state

    def _latest_state_ref(self, campaign_id: str) -> dict[str, Any] | None:
        for event in reversed(self.event_log.read_all()):
            if event.type not in _STATE_EVENT_TYPES:
                continue
            body = event.payload if isinstance(event.payload, Mapping) else {}
            if str(body.get("campaign_id") or "") != campaign_id:
                continue
            descriptor = body.get("state_ref")
            if isinstance(descriptor, Mapping):
                return dict(descriptor)
        return None

    def _write_state(self, state: Mapping[str, Any]) -> dict[str, Any]:
        return _write_sidecar(
            self.state_dir,
            state,
            root="evolution/skill-optimizer/states",
            kind="skill_optimizer_state",
            schema_version=STATE_SCHEMA,
        )


def _write_material(
    state_dir: Path,
    *,
    campaign_id: str,
    skill_name: str,
    content: str,
    parent_digest: str,
    epoch: int,
    source_event_id: str,
) -> dict[str, Any]:
    import hashlib

    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    descriptor = _write_sidecar(
        state_dir,
        {
            "schema_version": MATERIAL_SCHEMA,
            "campaign_id": campaign_id,
            "skill_name": skill_name,
            "epoch": epoch,
            "parent_digest": parent_digest,
            "content": content,
            "content_digest": digest,
        },
        root="evolution/skill-optimizer/materials",
        kind="skill_optimization_material",
        schema_version=MATERIAL_SCHEMA,
        source_event_id=source_event_id,
    )
    descriptor["content_digest"] = digest
    return descriptor


def _write_sidecar(
    state_dir: Path,
    payload: Mapping[str, Any],
    *,
    root: str,
    kind: str,
    schema_version: str,
    source_event_id: str = "",
) -> dict[str, Any]:
    return write_immutable_json_sidecar(
        Path(state_dir),
        dict(payload),
        root=root,
        kind=kind,
        schema_version=schema_version,
        created_by="skill-optimizer",
        source_event_id=source_event_id,
    )


def _hydrate_payload(
    state_dir: Path,
    descriptor: Mapping[str, Any],
    *,
    schema_version: str,
) -> dict[str, Any]:
    if not isinstance(descriptor, Mapping):
        raise SkillOptimizerError("Skill optimizer sidecar descriptor is invalid")
    hydrated = hydrate_sidecar_ref(
        Path(state_dir),
        dict(descriptor),
        purpose="skill-optimizer",
        actor="zf-cli",
    )
    payload = hydrated.payload
    if not isinstance(payload, Mapping) or payload.get("schema_version") != schema_version:
        raise SkillOptimizerError(f"Skill optimizer sidecar must be {schema_version}")
    return deepcopy(dict(payload))


def _same_ref(left: object, right: object) -> bool:
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return False
    return (
        str(left.get("ref") or "") == str(right.get("ref") or "")
        and str(left.get("sha256") or "") == str(right.get("sha256") or "")
    )


__all__ = [
    "CAMPAIGN_SCHEMA",
    "EVALUATION_SCHEMA",
    "MATERIAL_SCHEMA",
    "PROPOSAL_SCHEMA",
    "STATE_SCHEMA",
    "STEP_SCHEMA",
    "SkillOptimizationService",
    "SkillOptimizerError",
]
