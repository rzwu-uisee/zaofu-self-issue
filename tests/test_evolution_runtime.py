from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zf.cli.main import main
from zf.core.events.factory import event_log_from_project
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.evolution_contracts import (
    EvolutionContractError,
    stable_digest,
    validate_evaluator_generation,
    validate_evolution_attempt,
)
from zf.runtime.evolution_coordinator import EvolutionCoordinator
from zf.runtime.evolution_evaluator import (
    SealedEvaluatorAuthority,
    compare_repeated_trials,
    pareto_frontier,
    validate_measurement,
)
from zf.runtime.evolution_learning import (
    PROVIDER_ROUTE_VARIANT_SCHEMA,
    WORKFLOW_VARIANT_SCHEMA,
    build_provider_route_variant,
    build_workflow_variant,
    compare_variant_archive,
    evolution_economics,
    learning_context_projection,
    opportunity_to_variant_proposal,
    provider_comparison_is_current,
)
from zf.runtime.evolution_store import EvolutionConflictError
from zf.runtime.run_archive import archive_run
from zf.web.server import create_app


SHA = {
    letter: letter * 64
    for letter in "abcdef0123456789"
}


def _state(tmp_path: Path) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    return state_dir


def _evaluator(*, min_trials: int = 2, generation_id: str = "eval-1") -> dict:
    return validate_evaluator_generation({
        "schema_version": "evaluator-generation.v1",
        "generation_id": generation_id,
        "parser_digest": SHA["a"],
        "tcb_digest": SHA["b"],
        "scenario_set_digest": SHA["c"],
        "holdout_generation_digest": SHA["d"],
        "holdout_authority_ref": f"sealed-evaluator://generation/{generation_id}",
        "required_gates": [
            {"id": "correctness", "blocking": True},
            {"id": "secrets", "blocking": True},
        ],
        "required_score_dimensions": [
            {
                "id": "correctness",
                "weight": 0.7,
                "min": 0,
                "max": 100,
                "blocking_regression": True,
            },
            {"id": "cost", "weight": 0.3, "min": 0, "max": 100},
        ],
        "min_trials": min_trials,
        "min_delta": 2,
        "max_spread": 20,
    })


def _attempt(evaluator: dict, *, code: bool = False) -> dict:
    mutation = {
        "object_kind": "framework_code" if code else "workflow_config",
        "identity_kind": "git_commit" if code else "artifact_digest",
        "object_ref": "refs/heads/evolution/candidate" if code else "artifact://workflow/candidate",
        "base_version": "base-commit" if code else SHA["1"],
        "candidate_version": "candidate-commit" if code else SHA["2"],
        "diff_ref": "artifact://diff/1",
        "diff_digest": SHA["3"],
        "hypothesis_ref": "artifact://hypothesis/1",
    }
    source = {
        "workflow_run_id": "workflow-1",
        "source_task_ids": ["TASK-1"],
        "briefing_ref": "artifact://briefing/1",
        "briefing_digest": SHA["4"],
        "context_read_set_ref": "artifact://context/1",
        "context_read_set_digest": SHA["5"],
        "skill_lock_ref": "artifact://skill-lock/1",
        "skill_lock_digest": SHA["6"],
        "memory_snapshot_ref": "artifact://memory/1",
        "memory_snapshot_digest": SHA["7"],
        "tool_policy_ref": "artifact://tool-policy/1",
        "tool_policy_digest": SHA["8"],
    }
    if code:
        source.update({
            "base_commit": "base-commit",
            "candidate_commit": "candidate-commit",
            "candidate_ref": "refs/heads/evolution/candidate",
            "candidate_verification_authority_ref": "artifact://candidate-authority/1",
        })
    frozen = {
        "config_ref": "artifact://config/1",
        "config_digest": SHA["9"],
        "workflow_generation": "workflow-generation-1",
        "evaluator_ref": "artifact://evaluator/1",
        "evaluator_digest": evaluator["generation_digest"],
        "evaluation_harness_digest": SHA["b"],
        "comparison_parser_digest": evaluator["parser_digest"],
        "scenario_set_ref": "artifact://scenario/1",
        "scenario_set_digest": evaluator["scenario_set_digest"],
        "holdout_authority_ref": evaluator["holdout_authority_ref"],
        "holdout_generation_digest": evaluator["holdout_generation_digest"],
        "provider_capability_ref": "artifact://provider/1",
        "provider_capability_digest": SHA["a"],
        "provider": "codex",
        "model": "recorded-model",
        "toolchain_ref": "artifact://toolchain/1",
        "toolchain_digest": SHA["b"],
        "environment_ref": "artifact://environment/1",
        "environment_digest": SHA["c"],
        "sandbox_policy_ref": "artifact://sandbox/1",
        "sandbox_policy_digest": SHA["d"],
        "network_policy_ref": "artifact://network/1",
        "network_policy_digest": SHA["e"],
        "credential_policy_ref": "artifact://credential-policy/1",
        "credential_policy_digest": SHA["f"],
        "run_archive_manifest_ref": "artifact://run-archive/1",
        "run_archive_manifest_digest": SHA["0"],
    }
    return {
        "schema_version": "evolution-attempt.v1",
        "attempt_id": "evo-1-code" if code else "evo-1",
        "campaign_id": "campaign-1",
        "evolution_time": "post_task",
        "persistence_scope": "project",
        "adoption_claim": "persistent_capability",
        "evidence_kinds": ["outcome", "environmental", "trajectory"],
        "objective": {
            "kind": "recovery_quality",
            "summary": "Improve recovery without regressions",
            "task_family": "long_horizon_provider_run",
        },
        "mutation": mutation,
        "source_identity": source,
        "frozen_inputs": frozen,
        "evaluation_policy": {
            "pairing_key": SHA["1"],
            "min_trials": evaluator["min_trials"],
            "min_delta": evaluator["min_delta"],
            "required_gates": [item["id"] for item in evaluator["required_gates"]],
            "required_score_dimensions": [
                item["id"] for item in evaluator["required_score_dimensions"]
            ],
            "score_weights_digest": evaluator["weights_digest"],
            "numeric_policy": "finite_bounded",
            "trial_order": "counterbalanced",
            "selection": "pareto_then_policy",
        },
        "execution_policy": {
            "attempt_idempotency_key": SHA["2"],
            "lease_seconds": 300,
            "max_trial_attempts": 2,
            "retry_policy": "infrastructure_only",
        },
        "budget": {
            "max_cost_usd": 10,
            "max_wall_seconds": 600,
            "max_tokens": 10000,
        },
        "policy": {
            "apply_mode": "proposal_only",
            "owner_approval_required": True,
            "canary_required": True,
        },
    }


def _measurement(
    evaluator: dict,
    *,
    trial_id: str,
    arm: str,
    correctness: float,
    cost: float,
    config_generation: str = "config-1",
) -> dict:
    return {
        "schema_version": "evolution-measurement.v1",
        "trial_id": trial_id,
        "arm": arm,
        "evaluator_generation_digest": evaluator["generation_digest"],
        "comparison_identity": {
            "scenario_set_digest": evaluator["scenario_set_digest"],
            "config_generation": config_generation,
            "provider_capability_digest": SHA["a"],
            "toolchain_digest": SHA["b"],
            "environment_digest": SHA["c"],
            "sandbox_policy_digest": SHA["d"],
            "network_policy_digest": SHA["e"],
            "credential_policy_digest": SHA["f"],
            "budget_digest": SHA["d"],
            "seed_policy_digest": SHA["e"],
            "task_family": "long_horizon_provider_run",
        },
        "gates": {"correctness": "passed", "secrets": "passed"},
        "scores": {"correctness": correctness, "cost": cost},
    }


def _asset(attempt_id: str, *, asset_id: str = "learn-recovery", version: int = 1) -> dict:
    return {
        "schema_version": "learning-asset.v1",
        "asset_id": asset_id,
        "asset_kind": "memory_entry",
        "version": version,
        "digest": SHA["f"],
        "source_attempt_ids": [attempt_id],
        "content": "Use settlement evidence before redispatching a provider call.",
        "applicability": {
            "task_families": ["long_horizon_provider_run"],
            "providers": ["codex"],
        },
        "quality": {"confidence": "medium", "expires_at": "2099-01-01T00:00:00+00:00"},
        "activation": {
            "mode": "proposal_only",
            "owner_approval_required": True,
            "canary_scope_ref": "artifact://canary/recovery",
            "expected_active_key": "",
            "retain_policy": {
                "min_matched_outcomes": 1,
                "max_negative_transfer": 0,
            },
        },
        "rollback": {"previous_version_ref": "", "conditions": ["critical_regression"]},
        "dependencies": [],
        "provenance": {"project": "demo", "target_validation": "passed"},
        "taint": {"blocked": False, "secret": False, "pii": False},
    }


def _receipt(state_dir: Path, action: str) -> dict:
    return write_immutable_json_sidecar(
        state_dir,
        {"schema_version": "controlled-action-receipt.v1", "action": action},
        root="evolution/receipts",
        kind="controlled_action_receipt",
        schema_version="controlled-action-receipt.v1",
        created_by="test",
    )


def _trial_archive(state_dir: Path, trial_id: str) -> tuple[str, str]:
    run_id = f"trial-{stable_digest(trial_id)[:16]}"
    live = state_dir / "evolution" / "test-live" / run_id
    live.mkdir(parents=True, exist_ok=True)
    (live / "events.jsonl").write_text("", encoding="utf-8")
    result = archive_run(
        project_root=state_dir.parent,
        state_dir=state_dir,
        live_state_dir=live,
        run_id=run_id,
        status="passed",
        command="mock-evolution-trial",
        provider={"provider": "mock", "model": "deterministic"},
    )
    return str(result.manifest_path), result.manifest_digest


def _settle_pair(
    coordinator: EvolutionCoordinator,
    evaluator: dict,
    *,
    replicate: int,
    baseline_score: float,
    candidate_score: float,
) -> None:
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    for arm, score in (("baseline", baseline_score), ("candidate", candidate_score)):
        trial = coordinator.ensure_trial(
            attempt_id="evo-1", arm=arm, replicate=replicate
        )["trial"]
        started = coordinator.start_trial(
            trial["trial_id"],
            lease_owner="eval-runner",
            lease_expires_at=future,
        )["trial"]
        archive_ref, archive_digest = _trial_archive(
            coordinator.state_dir, trial["trial_id"]
        )
        coordinator.settle_trial(
            trial["trial_id"],
            lease_owner="eval-runner",
            attempt_number=started["attempt_number"],
            outcome="passed",
            evaluator_generation=evaluator,
            measurement=_measurement(
                evaluator,
                trial_id=trial["trial_id"],
                arm=arm,
                correctness=score,
                cost=80,
            ),
            archive_ref=archive_ref,
            archive_digest=archive_digest,
            cost_receipt_refs=[f"cost://{trial['trial_id']}"],
        )


def test_attempt_contract_supports_code_and_non_code_identity() -> None:
    evaluator = _evaluator()
    assert validate_evolution_attempt(_attempt(evaluator))["mutation"]["identity_kind"] == "artifact_digest"
    assert validate_evolution_attempt(_attempt(evaluator, code=True))["mutation"]["identity_kind"] == "git_commit"


def test_tcb_mutation_is_n_plus_one_only_and_cannot_self_approve(
    tmp_path: Path,
) -> None:
    evaluator = _evaluator(min_trials=1)
    raw = _attempt(evaluator)
    raw["attempt_id"] = "evo-tcb"
    raw["adoption_claim"] = "experiment_only"
    raw["mutation"].update({
        "object_kind": "evaluator_challenge",
        "tcb_affected": True,
        "proposed_evaluator_generation_ref": "artifact://evaluator/eval-2",
        "proposed_evaluator_generation_digest": SHA["f"],
    })
    normalized = validate_evolution_attempt(raw)
    assert normalized["mutation"]["tcb_affected"] is True
    coordinator = EvolutionCoordinator(_state(tmp_path))
    coordinator.materialize_attempt(raw)
    comparison = coordinator.compare_attempt(
        "evo-tcb", evaluator_generation=evaluator
    )["comparison"]
    assert comparison["status"] == "incomparable"
    assert comparison["adoption_eligible"] is False
    assert "generation N+1" in comparison["reason"]

    invalid = _attempt(evaluator)
    invalid["mutation"].update({
        "tcb_affected": True,
        "proposed_evaluator_generation_ref": "artifact://evaluator/eval-2",
        "proposed_evaluator_generation_digest": SHA["f"],
    })
    with pytest.raises(EvolutionContractError, match="experiment_only"):
        validate_evolution_attempt(invalid)


def test_strict_evaluator_rejects_missing_extra_nan_and_identity_drift() -> None:
    evaluator = _evaluator(min_trials=1)
    row = _measurement(
        evaluator,
        trial_id="trial-b",
        arm="baseline",
        correctness=80,
        cost=80,
    )
    missing = {**row, "scores": {"correctness": 80}}
    with pytest.raises(EvolutionContractError, match="exact required dimension"):
        validate_measurement(evaluator, missing)
    with pytest.raises(EvolutionContractError, match="finite"):
        validate_measurement(evaluator, {**row, "scores": {"correctness": float("nan"), "cost": 80}})

    candidate = _measurement(
        evaluator,
        trial_id="trial-c",
        arm="candidate",
        correctness=90,
        cost=80,
        config_generation="config-2",
    )
    comparison = compare_repeated_trials(
        evaluator,
        attempt_id="evo",
        baseline=[row],
        candidate=[candidate],
    )
    assert comparison["status"] == "incomparable"
    assert comparison["adoption_eligible"] is False


def test_sealed_evaluator_projection_never_exposes_cases(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    authority = SealedEvaluatorAuthority(
        tmp_path / "sealed",
        access_token="0123456789abcdef",
    )
    public_spec = _evaluator(min_trials=1)
    public_spec.pop("holdout_authority_ref")
    public_spec.pop("holdout_generation_digest")
    public_spec.pop("generation_digest")
    public, descriptor = authority.register_generation(
        state_dir=state_dir,
        public_spec=public_spec,
        sealed_cases=[{"command": "hidden-command", "expected": "secret-answer"}],
    )

    public_text = (state_dir / descriptor["ref"]).read_text(encoding="utf-8")
    assert "hidden-command" not in public_text
    assert "secret-answer" not in public_text
    assert not (state_dir / "sealed").exists()
    with pytest.raises(PermissionError):
        authority.evaluate(
            public["holdout_authority_ref"],
            generation_digest=public["generation_digest"],
            access_token="wrong-token-value",
            trusted_runner=lambda cases: {"count": len(cases)},
        )
    result = authority.evaluate(
        public["holdout_authority_ref"],
        generation_digest=public["generation_digest"],
        access_token="0123456789abcdef",
        trusted_runner=lambda cases: {"count": len(cases)},
    )
    assert result["count"] == 1


def test_trial_restart_is_effectively_once_and_only_infra_retries(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    evaluator = _evaluator(min_trials=1)
    coordinator = EvolutionCoordinator(state_dir)
    coordinator.materialize_attempt(_attempt(evaluator))
    trial = coordinator.ensure_trial(
        attempt_id="evo-1", arm="baseline", replicate=1
    )["trial"]
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    started = coordinator.start_trial(
        trial["trial_id"], lease_owner="runner", lease_expires_at=future
    )["trial"]
    archive_ref, archive_digest = _trial_archive(state_dir, trial["trial_id"])
    settlement = coordinator.settle_trial(
        trial["trial_id"],
        lease_owner="runner",
        attempt_number=started["attempt_number"],
        outcome="passed",
        evaluator_generation=evaluator,
        measurement=_measurement(
            evaluator,
            trial_id=trial["trial_id"],
            arm="baseline",
            correctness=80,
            cost=80,
        ),
        archive_ref=archive_ref,
        archive_digest=archive_digest,
        cost_receipt_refs=["cost://baseline"],
    )
    assert settlement["settlement_status"] == "accepted"

    restarted = EvolutionCoordinator(state_dir)
    duplicate = restarted.settle_trial(
        trial["trial_id"],
        lease_owner="runner",
        attempt_number=started["attempt_number"],
        outcome="passed",
        evaluator_generation=evaluator,
        measurement=_measurement(
            evaluator,
            trial_id=trial["trial_id"],
            arm="baseline",
            correctness=80,
            cost=80,
        ),
        archive_ref=archive_ref,
        archive_digest=archive_digest,
        cost_receipt_refs=["cost://baseline"],
    )
    assert duplicate["settlement_status"] == "duplicate"
    assert restarted.start_trial(
        trial["trial_id"], lease_owner="runner-2", lease_expires_at=future
    )["claimed"] is False
    events = event_log_from_project(state_dir, config=None, warn=False).read_all()
    assert sum(event.type == "evolution.trial.completed" for event in events) == 1

    retry_trial = restarted.ensure_trial(
        attempt_id="evo-1", arm="candidate", replicate=2
    )["trial"]
    retry_archive_ref, retry_archive_digest = _trial_archive(
        state_dir, retry_trial["trial_id"]
    )
    for expected_attempt in (1, 2):
        running = restarted.start_trial(
            retry_trial["trial_id"],
            lease_owner=f"retry-runner-{expected_attempt}",
            lease_expires_at=future,
        )
        assert running["claimed"] is True
        assert running["trial"]["attempt_number"] == expected_attempt
        failed = restarted.settle_trial(
            retry_trial["trial_id"],
            lease_owner=f"retry-runner-{expected_attempt}",
            attempt_number=expected_attempt,
            outcome="infrastructure_failed",
            archive_ref=retry_archive_ref,
            archive_digest=retry_archive_digest,
            failure_class="provider_transport",
        )
        assert failed["settlement_status"] == "retryable"
    exhausted = restarted.start_trial(
        retry_trial["trial_id"],
        lease_owner="retry-runner-3",
        lease_expires_at=future,
    )
    assert exhausted["claimed"] is False
    assert exhausted["trial"]["status"] == "dead_letter"

    tampered_trial = restarted.ensure_trial(
        attempt_id="evo-1", arm="candidate", replicate=3
    )["trial"]
    tampered_running = restarted.start_trial(
        tampered_trial["trial_id"],
        lease_owner="tamper-runner",
        lease_expires_at=future,
    )["trial"]
    tampered_ref, tampered_digest = _trial_archive(
        state_dir, tampered_trial["trial_id"]
    )
    Path(tampered_ref).parent.joinpath("command.txt").write_text(
        "tampered\n", encoding="utf-8"
    )
    with pytest.raises(EvolutionContractError, match="archive verification failed"):
        restarted.settle_trial(
            tampered_trial["trial_id"],
            lease_owner="tamper-runner",
            attempt_number=tampered_running["attempt_number"],
            outcome="infrastructure_failed",
            archive_ref=tampered_ref,
            archive_digest=tampered_digest,
            failure_class="provider_transport",
        )


def test_tie_cannot_propose_asset_and_rejection_fingerprint_is_suppressed(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    evaluator = _evaluator()
    coordinator = EvolutionCoordinator(state_dir)
    coordinator.materialize_attempt(_attempt(evaluator))
    _settle_pair(
        coordinator,
        evaluator,
        replicate=1,
        baseline_score=80,
        candidate_score=80,
    )
    _settle_pair(
        coordinator,
        evaluator,
        replicate=2,
        baseline_score=81,
        candidate_score=81,
    )
    comparison = coordinator.compare_attempt(
        "evo-1", evaluator_generation=evaluator
    )["comparison"]
    assert comparison["status"] == "tie"
    assert comparison["adoption_eligible"] is False
    with pytest.raises(EvolutionContractError, match="candidate_better"):
        coordinator.propose_asset(
            _asset("evo-1"), comparison_id=comparison["comparison_id"]
        )

    registry = coordinator.capabilities
    body = _asset("evo-1", asset_id="rejected-method")
    body["proposal_fingerprint"] = stable_digest({
        "asset_kind": body["asset_kind"],
        "digest": body["digest"],
        "applicability": body["applicability"],
    })
    descriptor = _receipt(state_dir, "rejected-method")
    row, _ = registry.propose(
        body,
        artifact_ref=descriptor,
        created_at="2026-01-01T00:00:00+00:00",
    )
    rejected, _ = registry.transition(
        asset_id=row["asset_id"],
        version=row["version"],
        target_state="rejected",
        expected_revision=row["revision"],
        action_id="reject-method",
        receipt_ref=descriptor,
        updated_at="2026-01-01T00:00:01+00:00",
    )
    assert rejected["state"] == "rejected"
    repeated = {**body, "version": 2}
    with pytest.raises(EvolutionConflictError, match="already rejected"):
        registry.propose(
            repeated,
            artifact_ref=descriptor,
            created_at="2026-01-01T00:00:02+00:00",
        )


def test_comparison_asset_canary_retain_and_applicability(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    evaluator = _evaluator()
    coordinator = EvolutionCoordinator(state_dir)
    coordinator.materialize_attempt(_attempt(evaluator))
    _settle_pair(coordinator, evaluator, replicate=1, baseline_score=70, candidate_score=90)
    _settle_pair(coordinator, evaluator, replicate=2, baseline_score=72, candidate_score=91)
    comparison = coordinator.compare_attempt(
        "evo-1", evaluator_generation=evaluator
    )["comparison"]
    assert comparison["status"] == "candidate_better"
    assert comparison["adoption_eligible"] is True

    proposed = coordinator.propose_asset(
        _asset("evo-1"), comparison_id=comparison["comparison_id"]
    )["asset"]
    current = proposed
    for target in ("validated", "approved"):
        current = coordinator.transition_asset(
            asset_id=current["asset_id"],
            version=current["version"],
            target_state=target,
            expected_revision=current["revision"],
            action_id=f"action-{target}",
            receipt_ref=_receipt(state_dir, target),
        )["asset"]
    current = coordinator.transition_asset(
        asset_id=current["asset_id"],
        version=current["version"],
        target_state="canary_active",
        expected_revision=current["revision"],
        action_id="action-canary",
        receipt_ref=_receipt(state_dir, "canary"),
    )["asset"]

    outside = learning_context_projection(
        state_dir,
        context={"task_family": "issue", "provider": "codex"},
    )
    assert outside["selected"] == []
    assert any(item["reason"].startswith("outside_") for item in outside["excluded"])
    canary = learning_context_projection(
        state_dir,
        context={
            "task_family": "long_horizon_provider_run",
            "provider": "codex",
            "canary_scope_refs": ["artifact://canary/recovery"],
        },
    )
    assert canary["selected"][0]["content"].startswith("Use settlement evidence")

    current = coordinator.record_asset_outcome(
        asset_id=current["asset_id"],
        version=current["version"],
        usage_ref="task://canary-1",
        matched=True,
        outcome="passed",
        cost={"cost_usd": 0.01},
    )["asset"]

    retained = coordinator.transition_asset(
        asset_id=current["asset_id"],
        version=current["version"],
        target_state="active_retained",
        expected_revision=current["revision"],
        action_id="action-retain",
        receipt_ref=_receipt(state_dir, "retain"),
    )["asset"]
    assert retained["state"] == "active_retained"
    assert coordinator.projection()["active_versions"][retained["asset_id"]].endswith("@1")


def test_concurrent_canary_and_tainted_import_fail_closed(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    registry = EvolutionCoordinator(state_dir).capabilities
    descriptor = _receipt(state_dir, "asset-body")
    first, _ = registry.propose(
        _asset("evo", asset_id="same", version=1),
        artifact_ref=descriptor,
        created_at="2026-01-01T00:00:00+00:00",
    )
    second_body = _asset("evo", asset_id="same", version=2)
    second_body["digest"] = SHA["e"]
    second, _ = registry.propose(
        second_body,
        artifact_ref=descriptor,
        created_at="2026-01-01T00:00:01+00:00",
    )
    for row, prefix in ((first, "one"), (second, "two")):
        for state in ("validated", "approved"):
            row, _ = registry.transition(
                asset_id=row["asset_id"],
                version=row["version"],
                target_state=state,
                expected_revision=row["revision"],
                action_id=f"{prefix}-{state}",
                receipt_ref=descriptor,
                updated_at="2026-01-01T00:00:02+00:00",
            )
        if prefix == "one":
            first = row
        else:
            second = row
    registry.transition(
        asset_id="same",
        version=1,
        target_state="canary_active",
        expected_revision=first["revision"],
        action_id="one-canary",
        receipt_ref=descriptor,
        updated_at="2026-01-01T00:00:03+00:00",
    )
    with pytest.raises(EvolutionConflictError, match="another canary"):
        registry.transition(
            asset_id="same",
            version=2,
            target_state="canary_active",
            expected_revision=second["revision"],
            action_id="two-canary",
            receipt_ref=descriptor,
            updated_at="2026-01-01T00:00:03+00:00",
        )

    tainted = _asset("evo", asset_id="tainted", version=1)
    tainted["taint"]["pii"] = True
    tainted_row, _ = registry.propose(
        tainted,
        artifact_ref=descriptor,
        created_at="2026-01-01T00:00:00+00:00",
    )
    for state in ("validated", "approved"):
        tainted_row, _ = registry.transition(
            asset_id="tainted",
            version=1,
            target_state=state,
            expected_revision=tainted_row["revision"],
            action_id=f"tainted-{state}",
            receipt_ref=descriptor,
            updated_at="2026-01-01T00:00:02+00:00",
        )
    with pytest.raises(EvolutionConflictError, match="tainted"):
        registry.transition(
            asset_id="tainted",
            version=1,
            target_state="canary_active",
            expected_revision=tainted_row["revision"],
            action_id="tainted-canary",
            receipt_ref=descriptor,
            updated_at="2026-01-01T00:00:03+00:00",
        )


def test_workflow_provider_variants_pareto_staleness_and_economics() -> None:
    workflow = build_workflow_variant({
        "schema_version": WORKFLOW_VARIANT_SCHEMA,
        "variant_id": "workflow-a",
        "task_family": "prd-medium",
        "config_ref": "artifact://config/a",
        "config_digest": SHA["a"],
        "hypothesis_ref": "artifact://hypothesis/a",
        "hypothesis_digest": SHA["b"],
        "objective": {"kind": "coordination", "summary": "Reduce rework"},
        "comparison_identity": {"scenario_set": SHA["c"]},
        "evidence_refs": ["artifact://run/workflow-a"],
        "metrics": {"correctness": 95, "cost": 8},
        "stage_graph": ["plan", "impl", "verify"],
        "policy": {"apply_mode": "proposal_only"},
    })
    assert workflow["variant_digest"]
    providers = []
    for name, model, correctness, cost in (
        ("codex", "model-a", 95, 8),
        ("claude", "model-b", 93, 5),
        ("slow", "model-c", 80, 12),
    ):
        row = build_provider_route_variant({
            "schema_version": PROVIDER_ROUTE_VARIANT_SCHEMA,
            "variant_id": name,
            "task_family": "prd-medium",
            "provider": name,
            "model": model,
            "capability_digest": SHA["a"],
            "route_policy_digest": SHA["b"],
            "health_ref": f"artifact://health/{name}",
            "health_digest": SHA["c"],
            "cost_receipt_refs": [f"cost://{name}"],
            "outcome_refs": [f"run://{name}"],
            "objective": {"kind": "provider_route", "summary": "Compare route"},
            "comparison_identity": {"scenario_set": SHA["d"]},
            "evidence_refs": [f"artifact://run/{name}"],
            "metrics": {"correctness": correctness, "cost": cost},
            "allowed_by_config": True,
        })
        providers.append(row)
    comparison = compare_variant_archive(
        providers,
        dimensions={"correctness": "maximize", "cost": "minimize"},
    )
    assert comparison["pareto_frontier"] == ["claude", "codex"]
    current = {row["provider"]: row["provider_fingerprint"] for row in providers}
    assert provider_comparison_is_current(comparison, current_fingerprints=current)[0]
    current["codex"] = SHA["f"]
    assert provider_comparison_is_current(comparison, current_fingerprints=current) == (
        False,
        "provider fingerprint changed: codex:model-a",
    )
    opportunity = opportunity_to_variant_proposal({
        "opportunity_id": "opp-1",
        "kind": "coordination_overhead",
        "task_family": "prd-medium",
        "summary": "Two lanes have lower merge overhead",
        "evidence_refs": ["artifact://trace/1"],
    })
    assert opportunity["policy"]["apply_mode"] == "proposal_only"
    assert evolution_economics(
        candidate_generation={}, evaluation={}
    )["status"] == "unknown"
    measured = evolution_economics(
        candidate_generation={"cost_usd": 2},
        evaluation={"cost_usd": 3, "score_delta": 10},
    )
    assert measured["marginal_gain_per_usd"] == 2


def test_pareto_frontier_preserves_non_dominated_candidates() -> None:
    frontier = pareto_frontier(
        [
            {"candidate_id": "a", "metrics": {"quality": 10, "cost": 10}},
            {"candidate_id": "b", "metrics": {"quality": 9, "cost": 5}},
            {"candidate_id": "c", "metrics": {"quality": 8, "cost": 12}},
        ],
        dimensions={"quality": "maximize", "cost": "minimize"},
    )
    assert [item["candidate_id"] for item in frontier] == ["a", "b"]


def test_cli_exposes_economics_and_provider_currentness(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    candidate_path = tmp_path / "candidate.json"
    evaluation_path = tmp_path / "evaluation.json"
    candidate_path.write_text(json.dumps({"cost_usd": 2}), encoding="utf-8")
    evaluation_path.write_text(
        json.dumps({"cost_usd": 3, "score_delta": 10}), encoding="utf-8"
    )
    assert main([
        "evolution",
        "economics",
        "--candidate-generation-file",
        str(candidate_path),
        "--evaluation-file",
        str(evaluation_path),
    ]) == 0
    assert json.loads(capsys.readouterr().out)["marginal_gain_per_usd"] == 2

    variant = build_provider_route_variant({
        "schema_version": PROVIDER_ROUTE_VARIANT_SCHEMA,
        "variant_id": "codex-current",
        "task_family": "issue",
        "provider": "codex",
        "model": "model-a",
        "capability_digest": SHA["a"],
        "route_policy_digest": SHA["b"],
        "health_ref": "artifact://health/codex",
        "health_digest": SHA["c"],
        "cost_receipt_refs": ["cost://codex"],
        "outcome_refs": ["run://codex"],
        "objective": {"kind": "route", "summary": "Check current route"},
        "comparison_identity": {"scenario_set": SHA["d"]},
        "evidence_refs": ["artifact://run/codex"],
        "metrics": {"quality": 90, "cost": 5},
        "allowed_by_config": True,
    })
    comparison_path = tmp_path / "comparison.json"
    comparison_path.write_text(json.dumps({
        "schema_version": "evolution-variant-comparison.v1",
        "status": "comparable",
        "variants": [variant],
    }), encoding="utf-8")
    fingerprints_path = tmp_path / "fingerprints.json"
    fingerprints_path.write_text(json.dumps({
        "codex:model-a": variant["provider_fingerprint"],
    }), encoding="utf-8")
    assert main([
        "evolution",
        "variant-current",
        "--comparison-file",
        str(comparison_path),
        "--fingerprints-file",
        str(fingerprints_path),
    ]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "current": True,
        "reason": "current",
    }


def test_cli_and_web_projection_are_read_only_and_redacted(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_dir = _state(tmp_path)
    evaluator = _evaluator(min_trials=1)
    attempt_path = tmp_path / "attempt.json"
    attempt_path.write_text(
        json.dumps(_attempt(evaluator)), encoding="utf-8"
    )
    assert main([
        "evolution",
        "attempt",
        "--state-dir",
        str(state_dir),
        "--file",
        str(attempt_path),
    ]) == 0
    materialized = json.loads(capsys.readouterr().out)
    assert materialized["attempt"]["attempt_id"] == "evo-1"
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")

    assert main(["evolution", "status", "--state-dir", str(state_dir)]) == 0
    cli_body = json.loads(capsys.readouterr().out)
    assert cli_body["attempts"][0]["attempt_id"] == "evo-1"

    client = TestClient(create_app(state_dir))
    response = client.get("/api/projects/default/evolution")
    assert response.status_code == 200
    body = response.json()
    assert body["authority"]["projection_only"] is True
    serialized = json.dumps(body)
    assert "hidden-command" not in serialized
    assert "secret-answer" not in serialized
