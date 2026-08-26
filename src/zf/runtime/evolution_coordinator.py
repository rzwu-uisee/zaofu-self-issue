"""Deterministic coordinator for evolution identity, trials, and adoption."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.evolution_contracts import (
    LEARNING_ASSET_SCHEMA,
    EvolutionContractError,
    normalize_digest,
    stable_digest,
    validate_evaluator_generation,
    validate_evolution_attempt,
)
from zf.runtime.evolution_evaluator import (
    compare_repeated_trials,
    incomparable_comparison,
    validate_measurement,
)
from zf.runtime.evolution_store import CapabilityRegistry, EvolutionTrialStore
from zf.runtime.evolution_projection import build_evolution_projection
from zf.runtime.run_archive import RunArchiveError, verify_run_archive
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, verify_sidecar_ref


_ASSET_KINDS = frozenset({
    "memory_entry",
    "skill_prompt",
    "workflow_patch",
    "provider_route",
    "eval_rule",
    "runbook",
    "regression_fixture",
    "tool_capability",
})


class EvolutionCoordinator:
    """One lifecycle owner layered over EventLog, stores, and artifacts.

    It never edits source, config, memory, or skills. Object-specific apply
    services remain authoritative and must provide immutable receipt refs.
    """

    def __init__(
        self,
        state_dir: Path,
        *,
        writer: EventWriter | None = None,
    ) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve(strict=False)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.writer = writer or EventWriter(event_log_from_project(self.state_dir))
        self.trials = EvolutionTrialStore(
            self.state_dir / "evolution" / "trials.json"
        )
        self.capabilities = CapabilityRegistry(
            self.state_dir / "evolution" / "capabilities.json"
        )

    def materialize_attempt(
        self,
        raw: Mapping[str, Any],
        *,
        actor: str = "evolution-coordinator",
    ) -> dict[str, Any]:
        attempt = validate_evolution_attempt(raw)
        descriptor = write_immutable_json_sidecar(
            self.state_dir,
            attempt,
            root="evolution/attempts",
            kind="evolution_attempt",
            schema_version="evolution-attempt.v1",
            created_by=actor,
        )
        row, created = self.trials.register_attempt(
            attempt_id=str(attempt["attempt_id"]),
            artifact_ref=descriptor,
            idempotency_key=str(
                attempt["execution_policy"]["attempt_idempotency_key"]
            ),
            max_trial_attempts=int(
                attempt["execution_policy"]["max_trial_attempts"]
            ),
            created_at=_now(),
        )
        if created:
            self.writer.emit(
                "evolution.attempt.materialized",
                actor=actor,
                task_id=_first_task_id(attempt),
                correlation_id=str(attempt["campaign_id"]),
                payload={
                    "attempt_id": attempt["attempt_id"],
                    "campaign_id": attempt["campaign_id"],
                    "object_kind": attempt["mutation"]["object_kind"],
                    "evolution_time": attempt["evolution_time"],
                    "persistence_scope": attempt["persistence_scope"],
                    "adoption_claim": attempt["adoption_claim"],
                    "artifact_ref": descriptor,
                    "source_event_refs": list(
                        attempt.get("source_event_refs") or []
                    ),
                },
            )
        return {"attempt": row, "artifact_ref": descriptor, "created": created}

    def ensure_trial(
        self,
        *,
        attempt_id: str,
        arm: str,
        replicate: int,
    ) -> dict[str, Any]:
        row, created = self.trials.ensure_trial(
            attempt_id=attempt_id,
            arm=arm,
            replicate=replicate,
            created_at=_now(),
        )
        return {"trial": row, "created": created}

    def start_trial(
        self,
        trial_id: str,
        *,
        lease_owner: str,
        lease_expires_at: str,
        actor: str = "evolution-eval-runner",
    ) -> dict[str, Any]:
        row, claimed = self.trials.claim_trial(
            trial_id,
            lease_owner=lease_owner,
            now=_now(),
            lease_expires_at=lease_expires_at,
        )
        if claimed:
            self.writer.emit(
                "evolution.trial.started",
                actor=actor,
                correlation_id=str(row["attempt_id"]),
                payload={
                    "attempt_id": row["attempt_id"],
                    "trial_id": row["trial_id"],
                    "arm": row["arm"],
                    "replicate": row["replicate"],
                    "attempt_number": row["attempt_number"],
                    "lease_owner": row["lease_owner"],
                    "lease_expires_at": row["lease_expires_at"],
                },
            )
        return {"trial": row, "claimed": claimed}

    def settle_trial(
        self,
        trial_id: str,
        *,
        lease_owner: str,
        attempt_number: int,
        outcome: str,
        evaluator_generation: Mapping[str, Any] | None = None,
        measurement: Mapping[str, Any] | None = None,
        archive_ref: str,
        archive_digest: str,
        cost_receipt_refs: list[str] | None = None,
        failure_class: str = "",
        retryable: bool | None = None,
        actor: str = "evolution-eval-runner",
    ) -> dict[str, Any]:
        normalized_archive_digest = normalize_digest(
            archive_digest, field="archive_digest"
        )
        archive_path = Path(archive_ref).expanduser()
        if not archive_path.is_absolute():
            archive_path = self.state_dir / archive_path
        archive_path = archive_path.resolve(strict=False)
        archive_root = (self.state_dir / "runs").resolve(strict=False)
        try:
            archive_path.relative_to(archive_root)
        except ValueError as exc:
            raise EvolutionContractError(
                "trial archive must be stored under the configured state_dir/runs"
            ) from exc
        try:
            verify_run_archive(
                archive_path,
                expected_digest=normalized_archive_digest,
            )
        except (FileNotFoundError, RunArchiveError) as exc:
            raise EvolutionContractError(f"trial archive verification failed: {exc}") from exc
        descriptor: dict[str, Any] = {}
        if outcome != "infrastructure_failed":
            if evaluator_generation is None or measurement is None:
                raise EvolutionContractError(
                    "settled semantic trials require evaluator generation and measurement"
                )
            trial_row = next(
                (
                    row
                    for row in self.trials.load()["trials"].values()
                    if row.get("trial_id") == trial_id
                ),
                None,
            )
            if not isinstance(trial_row, dict):
                raise EvolutionContractError("trial identity is unavailable")
            self._assert_evaluator_bound(
                str(trial_row["attempt_id"]), evaluator_generation
            )
            normalized = validate_measurement(evaluator_generation, measurement)
            if str(normalized["trial_id"]) != trial_id:
                raise EvolutionContractError("measurement trial_id mismatch")
            descriptor = write_immutable_json_sidecar(
                self.state_dir,
                normalized,
                root="evolution/measurements",
                kind="evolution_measurement",
                schema_version="evolution-measurement.v1",
                created_by=actor,
            )
        row, settlement_status = self.trials.settle_trial(
            trial_id,
            lease_owner=lease_owner,
            attempt_number=attempt_number,
            outcome=outcome,
            settlement_ref=descriptor,
            archive_ref=archive_ref,
            archive_digest=normalized_archive_digest,
            cost_receipt_refs=list(cost_receipt_refs or []),
            failure_class=failure_class,
            retryable=retryable,
            settled_at=_now(),
        )
        if settlement_status == "accepted":
            self.writer.emit(
                "evolution.trial.completed",
                actor=actor,
                correlation_id=str(row["attempt_id"]),
                payload={
                    "attempt_id": row["attempt_id"],
                    "trial_id": trial_id,
                    "arm": row["arm"],
                    "replicate": row["replicate"],
                    "outcome": row["outcome"],
                    "settlement_id": row["accepted_settlement_id"],
                    "settlement_ref": descriptor,
                    "archive_ref": archive_ref,
                    "archive_digest": normalized_archive_digest,
                    "cost_receipt_refs": list(cost_receipt_refs or []),
                },
            )
        elif settlement_status == "retryable":
            self.writer.emit(
                "evolution.trial.retry.requested",
                actor=actor,
                correlation_id=str(row["attempt_id"]),
                payload={
                    "attempt_id": row["attempt_id"],
                    "trial_id": trial_id,
                    "attempt_number": attempt_number,
                    "failure_class": row["failure_class"],
                    "retry_policy": "infrastructure_only",
                },
            )
        elif settlement_status == "dead_letter":
            self.writer.emit(
                "evolution.trial.completed",
                actor=actor,
                correlation_id=str(row["attempt_id"]),
                payload={
                    "attempt_id": row["attempt_id"],
                    "trial_id": trial_id,
                    "arm": row["arm"],
                    "replicate": row["replicate"],
                    "outcome": row["outcome"],
                    "failure_class": row["failure_class"],
                    "retryable": False,
                    "settlement_id": row["accepted_settlement_id"],
                    "archive_ref": archive_ref,
                    "archive_digest": normalized_archive_digest,
                },
            )
        return {
            "trial": row,
            "settlement_status": settlement_status,
            "settlement_ref": descriptor,
        }

    def compare_attempt(
        self,
        attempt_id: str,
        *,
        evaluator_generation: Mapping[str, Any],
        actor: str = "evolution-evaluator",
    ) -> dict[str, Any]:
        evaluator = validate_evaluator_generation(evaluator_generation)
        attempt = self._attempt_body(attempt_id)
        mutation = attempt.get("mutation") or {}
        baseline: list[dict[str, Any]] = []
        candidate: list[dict[str, Any]] = []
        control: list[dict[str, Any]] = []
        if bool(mutation.get("tcb_affected")):
            comparison = incomparable_comparison(
                evaluator,
                attempt_id=attempt_id,
                reason=(
                    "evaluator/TCB mutation requires an independently admitted "
                    "evaluator generation N+1"
                ),
                attempt=attempt,
            )
        else:
            self._assert_evaluator_bound(attempt_id, evaluator)
            rows = self.trials.trials_for_attempt(attempt_id)
            for row in rows:
                if row.get("status") != "settled" or row.get("outcome") == "semantic_failed":
                    continue
                descriptor = row.get("settlement_ref")
                if not isinstance(descriptor, dict) or not descriptor:
                    continue
                hydrated = hydrate_sidecar_ref(
                    self.state_dir,
                    descriptor,
                    purpose="evolution-comparison",
                    actor=actor,
                )
                if not isinstance(hydrated.payload, dict):
                    raise EvolutionContractError("trial measurement is not an object")
                target = {
                    "control": control,
                    "baseline": baseline,
                    "candidate": candidate,
                }.get(str(row.get("arm") or ""))
                if target is None:
                    raise EvolutionContractError("trial has an unsupported arm")
                target.append(dict(hydrated.payload))
            comparison = compare_repeated_trials(
                evaluator,
                attempt_id=attempt_id,
                baseline=baseline,
                candidate=candidate,
                control=control,
                attempt=attempt,
            )
        descriptor = write_immutable_json_sidecar(
            self.state_dir,
            comparison,
            root="evolution/comparisons",
            kind="evolution_comparison",
            schema_version="evolution-comparison.v1",
            created_by=actor,
        )
        _row, created = self.trials.register_comparison(
            comparison,
            artifact_ref=descriptor,
            created_at=_now(),
        )
        if created:
            self.writer.emit(
                "evolution.comparison.completed",
                actor=actor,
                correlation_id=attempt_id,
                payload={
                    "attempt_id": attempt_id,
                    "comparison_id": comparison["comparison_id"],
                    "status": comparison["status"],
                    "adoption_eligible": comparison["adoption_eligible"],
                    "blocking_reasons": comparison.get("blocking_reasons", []),
                    "claim_scope": comparison.get("claim_scope", ""),
                    "object_kind": comparison.get("object_kind", ""),
                    "evaluator_generation_id": comparison[
                        "evaluator_generation_id"
                    ],
                    "evaluator_generation_digest": comparison[
                        "evaluator_generation_digest"
                    ],
                    "comparison_fingerprint": comparison[
                        "comparison_fingerprint"
                    ],
                    "artifact_ref": descriptor,
                },
            )
        return {
            "comparison": comparison,
            "artifact_ref": descriptor,
            "created": created,
        }

    def propose_asset(
        self,
        raw: Mapping[str, Any],
        *,
        comparison_id: str,
        actor: str = "evolution-coordinator",
    ) -> dict[str, Any]:
        trial_state = self.trials.load()
        comparison = trial_state["comparisons"].get(comparison_id)
        if not isinstance(comparison, dict) or not comparison.get("adoption_eligible"):
            raise EvolutionContractError(
                "learning asset requires a candidate_better comparison"
            )
        body = _validate_learning_asset(raw, comparison=comparison)
        descriptor = write_immutable_json_sidecar(
            self.state_dir,
            body,
            root="evolution/learning-assets",
            kind="learning_asset",
            schema_version=LEARNING_ASSET_SCHEMA,
            created_by=actor,
        )
        row, created = self.capabilities.propose(
            body,
            artifact_ref=descriptor,
            created_at=_now(),
        )
        if created:
            self.writer.emit(
                "evolution.adoption.proposed",
                actor=actor,
                correlation_id=str(comparison["attempt_id"]),
                payload={
                    "asset_id": row["asset_id"],
                    "asset_kind": row["asset_kind"],
                    "version": row["version"],
                    "comparison_id": comparison_id,
                    "artifact_ref": descriptor,
                    "apply_mode": "proposal_only",
                    "owner_approval_required": True,
                },
            )
        return {"asset": row, "artifact_ref": descriptor, "created": created}

    def transition_asset(
        self,
        *,
        asset_id: str,
        version: int,
        target_state: str,
        expected_revision: int,
        action_id: str,
        receipt_ref: Mapping[str, Any],
        actor: str = "controlled-action-service",
    ) -> dict[str, Any]:
        verify_sidecar_ref(self.state_dir, dict(receipt_ref))
        row, applied = self.capabilities.transition(
            asset_id=asset_id,
            version=version,
            target_state=target_state,
            expected_revision=expected_revision,
            action_id=action_id,
            receipt_ref=receipt_ref,
            updated_at=_now(),
        )
        if applied:
            self.writer.emit(
                _asset_event_type(target_state),
                actor=actor,
                correlation_id=asset_id,
                payload={
                    "asset_id": asset_id,
                    "asset_kind": row["asset_kind"],
                    "version": version,
                    "state": target_state,
                    "revision": row["revision"],
                    "action_id": action_id,
                    "receipt_ref": dict(receipt_ref),
                    "canary_scope_ref": str(
                        (row.get("activation") or {}).get("canary_scope_ref") or ""
                    ),
                },
            )
        return {"asset": row, "applied": applied}

    def record_asset_outcome(
        self,
        *,
        asset_id: str,
        version: int,
        usage_ref: str,
        matched: bool,
        outcome: str,
        cost: Mapping[str, Any],
        cohort: Mapping[str, Any] | None = None,
        evaluation: Mapping[str, Any] | None = None,
        actor: str = "evolution-observer",
    ) -> dict[str, Any]:
        row, recorded = self.capabilities.record_outcome(
            asset_id=asset_id,
            version=version,
            usage_ref=usage_ref,
            matched=matched,
            outcome=outcome,
            cost=cost,
            cohort=cohort,
            evaluation=evaluation,
            recorded_at=_now(),
        )
        if recorded:
            self.writer.emit(
                "evolution.asset.outcome.recorded",
                actor=actor,
                correlation_id=asset_id,
                payload={
                    "asset_id": asset_id,
                    "version": version,
                    "usage_ref": usage_ref,
                    "matched": matched,
                    "outcome": outcome,
                    "negative_transfer": bool(
                        matched and outcome in {"failed", "regressed"}
                    ),
                    "cohort": deepcopy(dict(cohort or {})),
                    "reuse_gain": (
                        (row.get("outcomes") or [{}])[-1]
                        .get("evaluation", {})
                        .get("reuse_gain")
                    ),
                },
            )
        return {"asset": row, "recorded": recorded}

    def record_skill_outcome(
        self,
        *,
        asset_id: str,
        version: int,
        skill_name: str,
        task_id: str,
        role_instance: str,
        outcome: str,
        cost: Mapping[str, Any],
        config: Any | None = None,
        project_root: Path | None = None,
    ) -> dict[str, Any]:
        """Credit a skill asset only when invocation evidence is observable."""

        from zf.runtime.skill_invocation_projection import project_skill_invocations

        key = f"{asset_id}@{int(version)}"
        asset = self.capabilities.load()["assets"].get(key)
        if not isinstance(asset, dict) or asset.get("asset_kind") != "skill_prompt":
            raise EvolutionContractError("skill outcome requires a skill_prompt asset")
        hydrated = hydrate_sidecar_ref(
            self.state_dir,
            dict(asset["artifact_ref"]),
            purpose="skill-evolution-outcome",
            actor="evolution-observer",
        )
        body = hydrated.payload if isinstance(hydrated.payload, Mapping) else {}
        declared = str(body.get("skill_name") or body.get("name") or "").strip()
        if declared and declared != skill_name:
            raise EvolutionContractError("skill asset name does not match invocation")
        projection = project_skill_invocations(
            self.state_dir,
            config=config,
            project_root=project_root or self.state_dir.parent,
            task_id=task_id,
            role_instance=role_instance,
        )
        invoked = [
            row
            for row in projection.get("skills") or []
            if row.get("skill") == skill_name and bool(row.get("invoked"))
        ]
        if not invoked:
            raise EvolutionContractError(
                "skill outcome has no observed invocation evidence"
            )
        evidence_ids = sorted({
            str(item.get("event_id") or "")
            for row in invoked
            for item in row.get("evidence") or []
            if str(item.get("event_id") or "")
        })
        usage_ref = "skill-invocation://" + stable_digest({
            "asset": key,
            "skill": skill_name,
            "task_id": task_id,
            "role_instance": role_instance,
            "evidence_ids": evidence_ids,
        })
        result = self.record_asset_outcome(
            asset_id=asset_id,
            version=version,
            usage_ref=usage_ref,
            matched=True,
            outcome=outcome,
            cost=cost,
            actor="skill-invocation-projector",
        )
        result["invocation"] = {
            "skill": skill_name,
            "task_id": task_id,
            "role_instance": role_instance,
            "evidence_event_ids": evidence_ids,
            "usage_ref": usage_ref,
        }
        return result

    def materialize_challenge(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        from zf.runtime.evolution_learning import ChallengeBank

        return ChallengeBank(
            self.state_dir / "evolution" / "challenges.json"
        ).materialize(self.state_dir, raw, writer=self.writer)

    def decide_challenge(
        self,
        *,
        challenge_id: str,
        expected_revision: int,
        verdict: str,
        evaluator_receipt_ref: Mapping[str, Any],
    ) -> dict[str, Any]:
        from zf.runtime.evolution_learning import ChallengeBank

        return ChallengeBank(
            self.state_dir / "evolution" / "challenges.json"
        ).decide(
            challenge_id=challenge_id,
            expected_revision=expected_revision,
            verdict=verdict,
            evaluator_receipt_ref=evaluator_receipt_ref,
            writer=self.writer,
        )

    def export_asset(self, *, asset_id: str, version: int) -> dict[str, Any]:
        from zf.runtime.evolution_learning import export_learning_asset

        result = export_learning_asset(
            self.state_dir,
            registry=self.capabilities,
            asset_id=asset_id,
            version=version,
        )
        self.writer.emit(
            "evolution.asset.exported",
            actor="evolution-coordinator",
            correlation_id=asset_id,
            payload={
                "asset_id": asset_id,
                "version": version,
                "artifact_ref": result["artifact_ref"],
            },
        )
        return result

    def import_asset(
        self,
        *,
        package_descriptor: Mapping[str, Any],
        target_project: str,
        source_state_dir: Path | None = None,
    ) -> dict[str, Any]:
        from zf.runtime.evolution_learning import import_learning_asset

        result = import_learning_asset(
            self.state_dir,
            registry=self.capabilities,
            package_descriptor=package_descriptor,
            target_project=target_project,
            imported_at=_now(),
            source_state_dir=source_state_dir,
        )
        if result["created"]:
            self.writer.emit(
                "evolution.asset.imported",
                actor="evolution-coordinator",
                correlation_id=str(result["asset"]["asset_id"]),
                payload={
                    "asset_id": result["asset"]["asset_id"],
                    "version": result["asset"]["version"],
                    "target_project": target_project,
                    "state": "candidate",
                    "target_validation": "pending",
                    "artifact_ref": result["artifact_ref"],
                },
            )
        return result

    def record_target_validation(
        self,
        *,
        asset_id: str,
        version: int,
        expected_revision: int,
        action_id: str,
        passed: bool,
        receipt_ref: Mapping[str, Any],
    ) -> dict[str, Any]:
        verify_sidecar_ref(self.state_dir, dict(receipt_ref))
        row, applied = self.capabilities.record_target_validation(
            asset_id=asset_id,
            version=version,
            expected_revision=expected_revision,
            action_id=action_id,
            passed=passed,
            receipt_ref=receipt_ref,
            updated_at=_now(),
        )
        if applied:
            self.writer.emit(
                "evolution.asset.target_validated",
                actor="controlled-action-service",
                correlation_id=asset_id,
                payload={
                    "asset_id": asset_id,
                    "version": version,
                    "passed": bool(passed),
                    "revision": row["revision"],
                    "action_id": action_id,
                    "receipt_ref": dict(receipt_ref),
                },
            )
        return {"asset": row, "applied": applied}

    def materialize_variant_comparison(
        self,
        *,
        variants: list[Mapping[str, Any]],
        dimensions: Mapping[str, str],
    ) -> dict[str, Any]:
        from zf.runtime.evolution_learning import (
            PROVIDER_ROUTE_VARIANT_SCHEMA,
            WORKFLOW_VARIANT_SCHEMA,
            build_provider_route_variant,
            build_workflow_variant,
            compare_variant_archive,
        )

        normalized: list[dict[str, Any]] = []
        for raw in variants:
            schema = str(raw.get("schema_version") or "")
            if schema == WORKFLOW_VARIANT_SCHEMA:
                normalized.append(build_workflow_variant(raw))
            elif schema == PROVIDER_ROUTE_VARIANT_SCHEMA:
                normalized.append(build_provider_route_variant(raw))
            else:
                raise EvolutionContractError(
                    f"unsupported evolution variant schema: {schema or 'missing'}"
                )
        comparison = compare_variant_archive(normalized, dimensions=dimensions)
        descriptor = write_immutable_json_sidecar(
            self.state_dir,
            comparison,
            root="evolution/variant-comparisons",
            kind="evolution_variant_comparison",
            schema_version="evolution-variant-comparison.v1",
            created_by="evolution-coordinator",
        )
        self.writer.emit(
            "evolution.variant.comparison.completed",
            actor="evolution-coordinator",
            correlation_id=str(comparison.get("task_family") or "evolution-variant"),
            payload={
                "status": comparison["status"],
                "task_family": comparison.get("task_family") or "",
                "pareto_frontier": list(comparison.get("pareto_frontier") or []),
                "artifact_ref": descriptor,
            },
        )
        return {"comparison": comparison, "artifact_ref": descriptor}

    def materialize_opportunity(self, insight: Mapping[str, Any]) -> dict[str, Any]:
        from zf.runtime.evolution_learning import opportunity_to_variant_proposal

        proposal = opportunity_to_variant_proposal(insight)
        descriptor = write_immutable_json_sidecar(
            self.state_dir,
            proposal,
            root="evolution/opportunities",
            kind="evolution_opportunity_proposal",
            schema_version="evolution-opportunity-proposal.v1",
            created_by="evolution-coordinator",
        )
        self.writer.emit(
            "evolution.opportunity.proposed",
            actor="evolution-coordinator",
            correlation_id=str(proposal["opportunity_id"]),
            payload={
                "opportunity_id": proposal["opportunity_id"],
                "task_family": proposal["task_family"],
                "kind": proposal["kind"],
                "artifact_ref": descriptor,
                "apply_mode": "proposal_only",
            },
        )
        return {"proposal": proposal, "artifact_ref": descriptor}

    def projection(self) -> dict[str, Any]:
        trial_state = self.trials.load()
        capability_state = self.capabilities.load()
        return build_evolution_projection(
            trial_state,
            capability_state,
            generated_at=_now(),
        )

    def _assert_evaluator_bound(
        self,
        attempt_id: str,
        evaluator_generation: Mapping[str, Any],
    ) -> None:
        evaluator = validate_evaluator_generation(evaluator_generation)
        attempt = self._attempt_body(attempt_id)
        frozen = attempt["frozen_inputs"]
        policy = attempt["evaluation_policy"]
        mismatches: list[str] = []
        if str(frozen["evaluator_digest"]) != str(evaluator["generation_digest"]):
            mismatches.append("evaluator_digest")
        if str(frozen["comparison_parser_digest"]) != str(evaluator["parser_digest"]):
            mismatches.append("comparison_parser_digest")
        if str(frozen["holdout_generation_digest"]) != str(
            evaluator["holdout_generation_digest"]
        ):
            mismatches.append("holdout_generation_digest")
        if set(policy["required_gates"]) != {
            str(item["id"]) for item in evaluator["required_gates"]
        }:
            mismatches.append("required_gates")
        if set(policy["required_score_dimensions"]) != {
            str(item["id"]) for item in evaluator["required_score_dimensions"]
        }:
            mismatches.append("required_score_dimensions")
        if str(policy["score_weights_digest"]) != str(evaluator["weights_digest"]):
            mismatches.append("score_weights_digest")
        if mismatches:
            raise EvolutionContractError(
                "attempt evaluator binding drift: " + ", ".join(mismatches)
            )

    def _attempt_body(self, attempt_id: str) -> dict[str, Any]:
        attempt_row = self.trials.load()["attempts"].get(attempt_id)
        if not isinstance(attempt_row, dict):
            raise EvolutionContractError(f"evolution attempt not found: {attempt_id}")
        hydrated = hydrate_sidecar_ref(
            self.state_dir,
            dict(attempt_row["artifact_ref"]),
            purpose="evolution-attempt-read",
            actor="evolution-coordinator",
        )
        if not isinstance(hydrated.payload, dict):
            raise EvolutionContractError("evolution attempt body is invalid")
        return dict(hydrated.payload)


def _validate_learning_asset(
    raw: Mapping[str, Any],
    *,
    comparison: Mapping[str, Any],
) -> dict[str, Any]:
    body = deepcopy(dict(raw))
    if str(body.get("schema_version") or "") != LEARNING_ASSET_SCHEMA:
        raise EvolutionContractError(f"schema_version must be {LEARNING_ASSET_SCHEMA}")
    if str(body.get("asset_kind") or "") not in _ASSET_KINDS:
        raise EvolutionContractError("unsupported learning asset kind")
    comparison_kind = str(comparison.get("object_kind") or "")
    if body.get("asset_kind") == "skill_prompt" and comparison_kind != "skill_prompt":
        raise EvolutionContractError(
            "skill_prompt asset requires a Skill-scoped comparison"
        )
    if comparison_kind == "skill_prompt" and body.get("asset_kind") != "skill_prompt":
        raise EvolutionContractError(
            "Skill-scoped comparison can only propose a skill_prompt asset"
        )
    if not str(body.get("asset_id") or "").strip() or int(body.get("version") or 0) < 1:
        raise EvolutionContractError("learning asset requires asset_id and version")
    body["digest"] = normalize_digest(body.get("digest"), field="learning asset digest")
    source_attempt_ids = [str(item) for item in body.get("source_attempt_ids") or [] if str(item)]
    if comparison.get("attempt_id") not in source_attempt_ids:
        raise EvolutionContractError("learning asset must cite the comparison attempt")
    body["source_attempt_ids"] = source_attempt_ids
    for key in ("applicability", "quality", "activation", "rollback", "provenance", "taint"):
        if not isinstance(body.get(key), Mapping):
            raise EvolutionContractError(f"learning asset {key} must be an object")
        body[key] = deepcopy(dict(body[key]))
    activation = body["activation"]
    if activation.get("mode") != "proposal_only" or not bool(
        activation.get("owner_approval_required")
    ):
        raise EvolutionContractError("learning asset activation must be owner-approved proposal_only")
    retain_policy = activation.get("retain_policy")
    if not isinstance(retain_policy, Mapping):
        raise EvolutionContractError(
            "learning asset activation requires a retain_policy"
        )
    try:
        minimum = int(retain_policy.get("min_matched_outcomes"))
        maximum_negative = int(retain_policy.get("max_negative_transfer"))
    except (TypeError, ValueError) as exc:
        raise EvolutionContractError(
            "learning asset retain_policy values must be integers"
        ) from exc
    if minimum < 1 or maximum_negative < 0:
        raise EvolutionContractError(
            "learning asset retain_policy requires min_matched_outcomes >= 1 "
            "and max_negative_transfer >= 0"
        )
    activation["retain_policy"] = {
        "min_matched_outcomes": minimum,
        "max_negative_transfer": maximum_negative,
    }
    body["evidence"] = {
        **deepcopy(dict(body.get("evidence") or {})),
        "comparison_id": comparison["comparison_id"],
        "comparison_ref": deepcopy(comparison["artifact_ref"]),
    }
    body["proposal_fingerprint"] = str(
        body.get("proposal_fingerprint")
        or stable_digest({
            "asset_kind": body["asset_kind"],
            "digest": body["digest"],
            "applicability": body["applicability"],
        })
    )
    return body


def _asset_event_type(target_state: str) -> str:
    return {
        "validated": "evolution.asset.validated",
        "approved": "evolution.adoption.approved",
        "canary_active": "evolution.asset.applied",
        "active_retained": "evolution.asset.retained",
        "revoked": "evolution.asset.revoked",
        "superseded": "evolution.asset.superseded",
        "rejected": "evolution.adoption.rejected",
        "expired": "evolution.asset.expired",
    }.get(target_state, "evolution.asset.transitioned")


def _first_task_id(attempt: Mapping[str, Any]) -> str | None:
    values = (attempt.get("source_identity") or {}).get("source_task_ids") or []
    return str(values[0]) if values else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["EvolutionCoordinator"]
