from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zf.cli.main import main
from zf.core.events.factory import event_log_from_project
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.evolution_contracts import EvolutionContractError, stable_digest
from zf.runtime.evolution_coordinator import EvolutionCoordinator
from zf.runtime.evolution_evaluator import SealedEvaluatorAuthority
from zf.runtime.evolution_learning import (
    PROVIDER_ROUTE_VARIANT_SCHEMA,
    build_provider_route_variant,
)
from zf.runtime.evolution_store import EvolutionConflictError
from zf.runtime.run_archive import archive_run, verify_run_archive_manifest
from zf.web.server import create_app


def _sha(value: str) -> str:
    return stable_digest({"value": value})


def _public_evaluator() -> dict:
    return {
        "schema_version": "evaluator-generation.v1",
        "generation_id": "mock-evaluator-1",
        "parser_digest": _sha("parser"),
        "tcb_digest": _sha("tcb"),
        "scenario_set_digest": _sha("scenarios"),
        "required_gates": [
            {"id": "correctness", "blocking": True},
            {"id": "secrets", "blocking": True},
        ],
        "required_score_dimensions": [
            {
                "id": "correctness",
                "weight": 0.8,
                "min": 0,
                "max": 100,
                "blocking_regression": True,
            },
            {"id": "efficiency", "weight": 0.2, "min": 0, "max": 100},
        ],
        "min_trials": 2,
        "min_delta": 2,
        "max_spread": 20,
    }


def _attempt(evaluator: dict) -> dict:
    return {
        "schema_version": "evolution-attempt.v1",
        "attempt_id": "mock-evolution-1",
        "campaign_id": "mock-campaign-1",
        "evolution_time": "post_task",
        "persistence_scope": "project",
        "adoption_claim": "persistent_capability",
        "evidence_kinds": ["outcome", "environmental", "trajectory"],
        "objective": {
            "kind": "recovery_quality",
            "summary": "Improve recovery without duplicate provider calls",
            "task_family": "long_horizon_provider_run",
        },
        "mutation": {
            "object_kind": "skill_prompt",
            "identity_kind": "artifact_digest",
            "object_ref": "artifact://skill/recovery-candidate",
            "base_version": _sha("skill-base"),
            "candidate_version": _sha("skill-candidate"),
            "diff_ref": "artifact://diff/recovery",
            "diff_digest": _sha("skill-diff"),
            "hypothesis_ref": "artifact://hypothesis/recovery",
        },
        "source_identity": {
            "workflow_run_id": "workflow-mock-1",
            "source_task_ids": ["TASK-MOCK-1"],
            "briefing_ref": "artifact://briefing/mock",
            "briefing_digest": _sha("briefing"),
            "context_read_set_ref": "artifact://context/mock",
            "context_read_set_digest": _sha("context"),
            "skill_lock_ref": "artifact://skills/mock",
            "skill_lock_digest": _sha("skills"),
            "memory_snapshot_ref": "artifact://memory/mock",
            "memory_snapshot_digest": _sha("memory"),
            "tool_policy_ref": "artifact://tools/mock",
            "tool_policy_digest": _sha("tools"),
        },
        "frozen_inputs": {
            "config_ref": "artifact://config/mock",
            "config_digest": _sha("config"),
            "workflow_generation": "workflow-generation-1",
            "evaluator_ref": "artifact://evaluator/mock-evaluator-1",
            "evaluator_digest": evaluator["generation_digest"],
            "evaluation_harness_digest": evaluator["tcb_digest"],
            "comparison_parser_digest": evaluator["parser_digest"],
            "scenario_set_ref": "artifact://scenario/mock",
            "scenario_set_digest": evaluator["scenario_set_digest"],
            "holdout_authority_ref": evaluator["holdout_authority_ref"],
            "holdout_generation_digest": evaluator["holdout_generation_digest"],
            "provider_capability_ref": "artifact://provider/mock",
            "provider_capability_digest": _sha("provider"),
            "provider": "mock",
            "model": "deterministic",
            "toolchain_ref": "artifact://toolchain/mock",
            "toolchain_digest": _sha("toolchain"),
            "environment_ref": "artifact://environment/mock",
            "environment_digest": _sha("environment"),
            "sandbox_policy_ref": "artifact://sandbox/mock",
            "sandbox_policy_digest": _sha("sandbox"),
            "network_policy_ref": "artifact://network/mock",
            "network_policy_digest": _sha("network"),
            "credential_policy_digest": _sha("credentials"),
            "run_archive_manifest_ref": "artifact://run-archive/mock",
            "run_archive_manifest_digest": _sha("archive-policy"),
        },
        "evaluation_policy": {
            "pairing_key": _sha("pairing"),
            "min_trials": 2,
            "min_delta": 2,
            "required_gates": ["correctness", "secrets"],
            "required_score_dimensions": ["correctness", "efficiency"],
            "score_weights_digest": evaluator["weights_digest"],
            "numeric_policy": "finite_bounded",
            "trial_order": "counterbalanced",
            "selection": "pareto_then_policy",
        },
        "execution_policy": {
            "attempt_idempotency_key": _sha("attempt-idempotency"),
            "lease_seconds": 300,
            "max_trial_attempts": 2,
            "retry_policy": "infrastructure_only",
        },
        "budget": {
            "max_cost_usd": 1,
            "max_wall_seconds": 120,
            "max_tokens": 1000,
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
) -> dict:
    return {
        "schema_version": "evolution-measurement.v1",
        "trial_id": trial_id,
        "arm": arm,
        "evaluator_generation_digest": evaluator["generation_digest"],
        "comparison_identity": {
            "scenario_set_digest": evaluator["scenario_set_digest"],
            "config_generation": "config-generation-1",
            "provider_capability_digest": _sha("provider"),
            "toolchain_digest": _sha("toolchain"),
            "environment_digest": _sha("environment"),
            "budget_digest": _sha("budget"),
            "seed_policy_digest": _sha("seed-policy"),
            "task_family": "long_horizon_provider_run",
        },
        "gates": {"correctness": "passed", "secrets": "passed"},
        "scores": {"correctness": correctness, "efficiency": 90},
    }


def _asset(*, version: int, digest: str, expected_active_key: str = "") -> dict:
    return {
        "schema_version": "learning-asset.v1",
        "asset_id": "recovery-method",
        "asset_kind": "memory_entry",
        "version": version,
        "digest": digest,
        "source_attempt_ids": ["mock-evolution-1"],
        "content": "Inspect settlement evidence before redispatching a provider call.",
        "applicability": {
            "task_families": ["long_horizon_provider_run"],
            "providers": ["mock"],
        },
        "quality": {
            "confidence": "medium",
            "expires_at": "2099-01-01T00:00:00+00:00",
        },
        "activation": {
            "mode": "proposal_only",
            "owner_approval_required": True,
            "canary_scope_ref": "artifact://canary/recovery",
            "expected_active_key": expected_active_key,
            "retain_policy": {
                "min_matched_outcomes": 1,
                "max_negative_transfer": 0,
            },
        },
        "rollback": {
            "previous_version_ref": expected_active_key,
            "conditions": ["critical_regression"],
        },
        "dependencies": [],
        "provenance": {"project": "mock", "target_validation": "passed"},
        "taint": {"blocked": False, "secret": False, "pii": False},
    }


def _receipt(state_dir: Path, action: str) -> dict:
    return write_immutable_json_sidecar(
        state_dir,
        {"schema_version": "controlled-action-receipt.v1", "action": action},
        root="evolution/receipts",
        kind="controlled_action_receipt",
        schema_version="controlled-action-receipt.v1",
        created_by="mock-e2e",
    )


def _archive_trial(
    project_root: Path,
    state_dir: Path,
    *,
    run_id: str,
    arm: str,
) -> tuple[str, str]:
    live = project_root / ".live" / run_id
    live.mkdir(parents=True)
    (live / "events.jsonl").write_text("", encoding="utf-8")
    (live / "cost.jsonl").write_text(
        json.dumps({"backend": "mock", "tokens": 10}) + "\n",
        encoding="utf-8",
    )
    result = archive_run(
        project_root=project_root,
        state_dir=state_dir,
        live_state_dir=live,
        run_id=run_id,
        status="passed",
        command=f"mock-provider --arm {arm}",
        provider={
            "provider": "mock",
            "model": "deterministic",
            "session_id": run_id,
            "usage": {"input_tokens": 5, "output_tokens": 5},
        },
        summary={"arm": arm, "mock": True},
    )
    assert verify_run_archive_manifest(result.manifest) == result.manifest_digest
    return str(result.manifest_path), result.manifest_digest


def _advance(
    coordinator: EvolutionCoordinator,
    row: dict,
    *states: str,
) -> dict:
    current = row
    for state in states:
        current = coordinator.transition_asset(
            asset_id=current["asset_id"],
            version=current["version"],
            target_state=state,
            expected_revision=current["revision"],
            action_id=f"{current['asset_id']}-{current['version']}-{state}",
            receipt_ref=_receipt(coordinator.state_dir, state),
        )["asset"]
    return current


def test_self_evolution_mock_e2e_closes_integrity_learning_and_variants(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=project_root, check=True)
    (project_root / "README.md").write_text("# mock evolution\n", encoding="utf-8")
    state_dir = project_root / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")

    authority = SealedEvaluatorAuthority(
        tmp_path / "sealed-evaluator",
        access_token="mock-evaluator-token",
    )
    evaluator, public_ref = authority.register_generation(
        state_dir=state_dir,
        public_spec=_public_evaluator(),
        sealed_cases=[{
            "case": "duplicate-settlement",
            "expected": "sealed-answer-do-not-project",
        }],
    )
    assert "sealed-answer-do-not-project" not in (
        state_dir / public_ref["ref"]
    ).read_text(encoding="utf-8")

    coordinator = EvolutionCoordinator(state_dir)
    coordinator.materialize_attempt(_attempt(evaluator))
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    first_settlement: dict | None = None
    for replicate in (1, 2):
        for arm, score in (("baseline", 70 + replicate), ("candidate", 90 + replicate)):
            trial = coordinator.ensure_trial(
                attempt_id="mock-evolution-1", arm=arm, replicate=replicate
            )["trial"]
            running = coordinator.start_trial(
                trial["trial_id"],
                lease_owner="mock-evaluator",
                lease_expires_at=future,
            )["trial"]
            archive_ref, archive_digest = _archive_trial(
                project_root,
                state_dir,
                run_id=f"{arm}-{replicate}",
                arm=arm,
            )
            settled = coordinator.settle_trial(
                trial["trial_id"],
                lease_owner="mock-evaluator",
                attempt_number=running["attempt_number"],
                outcome="passed",
                evaluator_generation=evaluator,
                measurement=_measurement(
                    evaluator,
                    trial_id=trial["trial_id"],
                    arm=arm,
                    correctness=score,
                ),
                archive_ref=archive_ref,
                archive_digest=archive_digest,
                cost_receipt_refs=[f"cost://{arm}-{replicate}"],
            )
            if first_settlement is None:
                first_settlement = {
                    "trial": trial,
                    "running": running,
                    "archive_ref": archive_ref,
                    "archive_digest": archive_digest,
                    "score": score,
                    "arm": arm,
                }

    assert first_settlement is not None
    restarted = EvolutionCoordinator(state_dir)
    duplicate = restarted.settle_trial(
        first_settlement["trial"]["trial_id"],
        lease_owner="mock-evaluator",
        attempt_number=first_settlement["running"]["attempt_number"],
        outcome="passed",
        evaluator_generation=evaluator,
        measurement=_measurement(
            evaluator,
            trial_id=first_settlement["trial"]["trial_id"],
            arm=first_settlement["arm"],
            correctness=first_settlement["score"],
        ),
        archive_ref=first_settlement["archive_ref"],
        archive_digest=first_settlement["archive_digest"],
        cost_receipt_refs=["cost://duplicate-must-not-apply"],
    )
    assert duplicate["settlement_status"] == "stale"

    comparison = restarted.compare_attempt(
        "mock-evolution-1", evaluator_generation=evaluator
    )["comparison"]
    assert comparison["status"] == "candidate_better"
    assert comparison["adoption_eligible"] is True

    version_one = restarted.propose_asset(
        _asset(version=1, digest=_sha("asset-v1")),
        comparison_id=comparison["comparison_id"],
    )["asset"]
    version_one = _advance(restarted, version_one, "validated", "approved", "canary_active")
    version_one = restarted.record_asset_outcome(
        asset_id="recovery-method",
        version=1,
        usage_ref="task://mock-canary-success",
        matched=True,
        outcome="passed",
        cost={"tokens": 10},
        cohort={
            "task_family": "long_horizon_provider_run",
            "provider": "mock",
            "language": "python",
        },
        evaluation={
            "baseline_ref": "run://baseline-canary-1",
            "candidate_ref": "run://candidate-canary-1",
            "holdout_matched": True,
            "baseline_score": 70,
            "candidate_score": 90,
        },
    )["asset"]
    version_one = _advance(restarted, version_one, "active_retained")
    assert version_one["state"] == "active_retained"

    version_two = restarted.propose_asset(
        _asset(
            version=2,
            digest=_sha("asset-v2"),
            expected_active_key="recovery-method@1",
        ),
        comparison_id=comparison["comparison_id"],
    )["asset"]
    version_two = _advance(restarted, version_two, "validated", "approved", "canary_active")
    version_two = restarted.record_asset_outcome(
        asset_id="recovery-method",
        version=2,
        usage_ref="task://mock-canary-regression",
        matched=True,
        outcome="regressed",
        cost={"tokens": 12},
        cohort={
            "task_family": "long_horizon_provider_run",
            "provider": "mock",
            "language": "python",
        },
        evaluation={
            "baseline_ref": "run://baseline-canary-2",
            "candidate_ref": "run://candidate-canary-2",
            "holdout_matched": True,
            "baseline_score": 90,
            "candidate_score": 60,
        },
    )["asset"]
    with pytest.raises(EvolutionConflictError, match="retain policy"):
        _advance(restarted, version_two, "active_retained")
    version_two = _advance(restarted, version_two, "revoked")
    assert version_two["state"] == "revoked"
    evolution_projection = restarted.projection()
    assert evolution_projection["active_versions"]["recovery-method"] == "recovery-method@1"
    cohort = evolution_projection["metrics"]["generalization_cohorts"][0]
    assert cohort["matched_outcomes"] == 2
    assert cohort["mean_reuse_gain"] == -5

    with pytest.raises(EvolutionConflictError, match="stale"):
        restarted.transition_asset(
            asset_id="recovery-method",
            version=1,
            target_state="superseded",
            expected_revision=1,
            action_id="stale-cas",
            receipt_ref=_receipt(state_dir, "stale-cas"),
        )

    challenge = restarted.materialize_challenge({
        "schema_version": "challenge-case.v1",
        "challenge_id": "mock-challenge-1",
        "source_event_ref": "event://mock-failure",
        "run_ref": "run://mock",
        "trace_ref": "trace://mock",
        "reproduction_ref": "artifact://reproduction/mock",
        "expected_invariant": "settlement remains effectively once",
        "visibility_policy": "shadow_visible",
        "secret_status": "redacted",
        "stability_observations": [
            {"run_ref": "run://mock-1", "reproduced": True},
            {"run_ref": "run://mock-2", "reproduced": True},
        ],
    })
    promoted = restarted.decide_challenge(
        challenge_id=challenge["challenge_id"],
        expected_revision=challenge["revision"],
        verdict="promoted",
        evaluator_receipt_ref=_receipt(state_dir, "challenge-promoted"),
    )
    assert promoted["status"] == "promoted"

    variants = []
    for name, quality, cost in (("fast", 92, 8), ("cheap", 88, 4), ("dominated", 70, 12)):
        variants.append(build_provider_route_variant({
            "schema_version": PROVIDER_ROUTE_VARIANT_SCHEMA,
            "variant_id": name,
            "task_family": "long_horizon_provider_run",
            "provider": "mock",
            "model": name,
            "capability_digest": _sha(f"capability-{name}"),
            "route_policy_digest": _sha(f"route-{name}"),
            "health_ref": f"artifact://health/{name}",
            "health_digest": _sha(f"health-{name}"),
            "cost_receipt_refs": [f"cost://{name}"],
            "outcome_refs": [f"run://{name}"],
            "objective": {"kind": "route", "summary": "Compare route"},
            "comparison_identity": {"scenario_set": _sha("variant-scenario")},
            "evidence_refs": [f"artifact://variant/{name}"],
            "metrics": {"quality": quality, "cost": cost},
            "allowed_by_config": True,
        }))
    variant_result = restarted.materialize_variant_comparison(
        variants=variants,
        dimensions={"quality": "maximize", "cost": "minimize"},
    )["comparison"]
    assert variant_result["pareto_frontier"] == ["cheap", "fast"]
    opportunity = restarted.materialize_opportunity({
        "opportunity_id": "mock-opportunity-1",
        "kind": "coordination_overhead",
        "task_family": "long_horizon_provider_run",
        "summary": "Try a smaller topology",
        "evidence_refs": ["artifact://trace/mock"],
    })["proposal"]
    assert opportunity["policy"]["apply_mode"] == "proposal_only"

    exported = restarted.export_asset(asset_id="recovery-method", version=1)
    target_state = project_root / ".zf-target"
    target_state.mkdir()
    target = EvolutionCoordinator(target_state)
    imported = target.import_asset(
        package_descriptor=exported["artifact_ref"],
        target_project="target-project",
        source_state_dir=state_dir,
    )["asset"]
    assert imported["provenance"]["target_validation"] == "pending"

    assert main(["evolution", "status", "--state-dir", str(state_dir)]) == 0
    cli_projection = json.loads(capsys.readouterr().out)
    web_projection = TestClient(create_app(state_dir)).get(
        "/api/projects/default/evolution"
    ).json()
    assert cli_projection["active_versions"] == web_projection["active_versions"]
    assert web_projection["authority"]["projection_only"] is True
    serialized = json.dumps(web_projection)
    assert "duplicate-settlement" not in serialized
    assert "sealed-answer-do-not-project" not in serialized

    event_types = [
        event.type
        for event in event_log_from_project(state_dir, config=None, warn=False).read_all()
    ]
    assert event_types.count("evolution.trial.completed") == 4
    assert "evolution.asset.retained" in event_types
    assert "evolution.asset.revoked" in event_types
    assert "evolution.variant.comparison.completed" in event_types
    assert "evolution.opportunity.proposed" in event_types
