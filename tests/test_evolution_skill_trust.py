from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.cli.main import main
from zf.runtime.evolution_contracts import (
    EvolutionContractError,
    stable_digest,
    validate_evaluator_generation,
    validate_evolution_attempt,
)
from zf.runtime.control_actions_evolution import EvolutionActionsMixin
from zf.runtime.evolution_coordinator import EvolutionCoordinator
from zf.runtime.evolution_evaluator import compare_repeated_trials
from zf.runtime.evolution_skill import (
    build_skill_trial_spec,
    materialize_skill_trial_arm,
)
from zf.runtime.evolution_skill_eval import (
    build_skill_treatment_identity,
    classify_skill_treatment,
    compare_skill_treatment_identities,
    validate_skill_eval_suite,
)
from zf.runtime.run_archive import archive_run


SHA = {letter: letter * 64 for letter in "abcdef0123456789"}


def _skill_source(name: str, marker: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {marker} method\n"
        "---\n\n"
        f"# {marker}\n\nUse the {marker} procedure.\n"
    )


def _suite(*, purpose: str = "adoption_lift") -> dict:
    return validate_skill_eval_suite({
        "schema_version": "skill-eval-suite.v1",
        "suite_id": f"suite-{purpose}",
        "evaluation_purpose": purpose,
        "cases": [
            {
                "case_id": "explicit-1",
                "case_kind": "explicit",
                "treatment": "optional" if purpose == "adoption_lift" else "required",
            },
            {
                "case_id": "implicit-1",
                "case_kind": "implicit",
                "treatment": "optional" if purpose == "adoption_lift" else "required",
            },
            {
                "case_id": "negative-1",
                "case_kind": "negative",
                "treatment": "forbidden",
            },
        ],
    })


def _candidate(suite: dict) -> dict:
    return {
        "schema_version": "skill-candidate.v1",
        "skill_name": "demo-method",
        "content": _skill_source("demo-method", "candidate"),
        "task_families": ["issue"],
        "source_trajectories": [
            {"ref": "run://success-1", "digest": SHA["b"], "outcome": "passed"},
            {"ref": "run://failure-1", "digest": SHA["c"], "outcome": "failed"},
        ],
        "applicability_ref": "artifact://applicability/demo-method",
        "applicability_digest": SHA["a"],
        "public_eval_suite_ref": "artifact://eval/demo-method",
        "public_eval_suite_digest": suite["suite_digest"],
        "sealed_eval_generation_ref": "sealed-evaluator://generation/skill-1",
        "evaluation_purpose": suite["evaluation_purpose"],
    }


def _common_identity(support_digest: str) -> dict[str, str]:
    return {
        "support_skill_inventory_digest": support_digest,
        "role_profile_digest": SHA["1"],
        "briefing_digest": SHA["2"],
        "prompt_digest": SHA["3"],
        "workspace_fixture_digest": SHA["4"],
        "tool_policy_digest": SHA["5"],
        "eval_suite_generation_digest": SHA["6"],
    }


def _trial_spec() -> dict:
    suite = _suite()
    support = {
        "name": "shared-method",
        "content": _skill_source("shared-method", "shared"),
    }
    import hashlib

    support_digest = hashlib.sha256(
        support["content"].encode("utf-8")
    ).hexdigest()
    support["digest"] = support_digest
    inventory_digest = stable_digest([
        {"name": support["name"], "digest": support_digest}
    ])
    return build_skill_trial_spec(
        _candidate(suite),
        current={
            "skill_name": "demo-method",
            "content": _skill_source("demo-method", "current"),
            "version": "current-v1",
        },
        common_identity=_common_identity(inventory_digest),
        eval_suite=suite,
        support_skills=[support],
    )


def test_three_arm_spec_and_normal_materialization_are_treatment_fair(
    tmp_path: Path,
) -> None:
    spec = _trial_spec()
    comparison = compare_skill_treatment_identities(
        list(spec["treatment_identities"].values())
    )

    assert comparison["comparable"] is True
    assert spec["arm_map"] == {
        "control": "raw",
        "baseline": "current",
        "candidate": "candidate",
    }

    results = {
        arm: materialize_skill_trial_arm(
            workdir=tmp_path / arm,
            backend="codex",
            spec=spec,
            arm=arm,
        )
        for arm in ("control", "baseline", "candidate")
    }
    assert results["control"]["target_materialized"] is False
    assert results["baseline"]["target_materialized"] is True
    assert results["candidate"]["target_materialized"] is True
    assert {row["support_skill_count"] for row in results.values()} == {1}
    assert all(Path(row["manifest_path"]).is_file() for row in results.values())


def test_skill_candidate_and_suite_refs_require_immutable_digests() -> None:
    suite = _suite()
    candidate = _candidate(suite)
    del candidate["source_trajectories"][0]["digest"]
    with pytest.raises(EvolutionContractError, match="source trajectory"):
        build_skill_trial_spec(
            candidate,
            common_identity=_common_identity(stable_digest([])),
            eval_suite=suite,
        )

    raw_suite = {
        "schema_version": "skill-eval-suite.v1",
        "suite_id": "mutable-fixture",
        "evaluation_purpose": "content_lift",
        "cases": [{
            "case_id": "explicit-1",
            "case_kind": "explicit",
            "treatment": "required",
            "fixture_ref": "artifact://fixture/1",
        }],
    }
    with pytest.raises(EvolutionContractError, match="fixture_ref/fixture_digest"):
        validate_skill_eval_suite(raw_suite)


def test_non_target_treatment_drift_is_incomparable() -> None:
    spec = _trial_spec()
    identities = [deepcopy(row) for row in spec["treatment_identities"].values()]
    candidate = next(row for row in identities if row["arm"] == "candidate")
    candidate["prompt_digest"] = SHA["f"]

    result = compare_skill_treatment_identities(identities)

    assert result["comparable"] is False
    assert result["reason"] == "non-target Skill treatment identity differs"


def test_itt_retains_natural_non_use_and_flags_overtrigger() -> None:
    spec = _trial_spec()
    cases = spec["eval_suite"]["cases"]
    candidate = spec["treatment_identities"]["candidate"]

    unused = classify_skill_treatment(
        identity=candidate,
        cases=cases,
    )
    overtriggered = classify_skill_treatment(
        identity=candidate,
        cases=cases,
        loaded_case_ids=["negative-1"],
    )

    assert unused["intent_to_treat"] is True
    assert unused["admission_valid"] is True
    assert unused["observation"] == "available_not_loaded"
    assert unused["applied"] is None
    assert overtriggered["observation"] == "overtriggered"
    assert overtriggered["overtrigger_count"] == 1


def test_forced_required_case_must_load_but_raw_keeps_same_common_identity() -> None:
    common = _common_identity(SHA["a"])
    identity = build_skill_treatment_identity(
        arm="candidate",
        target_skill={
            "name": "demo-method",
            "available": True,
            "version": "candidate",
            "digest": SHA["b"],
            "materialized_path_digest": SHA["c"],
        },
        common_identity=common,
        evaluation_purpose="content_lift",
    )
    result = classify_skill_treatment(
        identity=identity,
        cases=[{
            "case_id": "required-1",
            "case_kind": "explicit",
            "treatment": "required",
        }],
    )

    assert result["admission_valid"] is False
    assert result["observation"] == "treatment_not_applied"


def test_routing_stress_requires_real_pool_and_negative_cases() -> None:
    raw = {
        "schema_version": "skill-eval-suite.v1",
        "suite_id": "routing-1",
        "evaluation_purpose": "routing_stress",
        "cases": [{
            "case_id": "positive-1",
            "case_kind": "explicit",
            "treatment": "optional",
        }],
    }
    with pytest.raises(EvolutionContractError, match="routing pool"):
        validate_skill_eval_suite(raw)

    raw["cases"].append({
        "case_id": "confusable-1",
        "case_kind": "confusable",
        "treatment": "forbidden",
    })
    raw["routing_pool"] = {
        "sizes": [1, 5, 20, 50],
        "decoy_skills": [{"name": "other-method", "digest": SHA["d"]}],
    }
    suite = validate_skill_eval_suite(raw)

    assert suite["routing_pool"]["sizes"] == [1, 5, 20, 50]


def _evaluator(*, min_trials: int) -> dict:
    return validate_evaluator_generation({
        "schema_version": "evaluator-generation.v1",
        "generation_id": "skill-eval-1",
        "parser_digest": SHA["a"],
        "tcb_digest": SHA["b"],
        "scenario_set_digest": SHA["c"],
        "holdout_generation_digest": SHA["d"],
        "holdout_authority_ref": "sealed-evaluator://generation/skill-eval-1",
        "required_gates": [{"id": "correctness", "blocking": True}],
        "required_score_dimensions": [{
            "id": "correctness",
            "weight": 1,
            "min": 0,
            "max": 100,
            "blocking_regression": True,
        }],
        "comparison_identity_fields": ["scenario", "skill_treatment_spec_digest"],
        "min_trials": min_trials,
        "min_delta": 2,
        "max_spread": 20,
    })


def _claim_attempt(*, purpose: str = "adoption_lift") -> dict:
    return {
        "mutation": {"object_kind": "skill_prompt"},
        "adoption_claim": "persistent_capability",
        "frozen_inputs": {"skill_treatment_spec_digest": SHA["0"]},
        "evaluation_policy": {
            "evaluation_purpose": purpose,
            "skill_adoption": {
                "min_distinct_cases": 3,
                "min_replicates_per_case": 2,
                "required_arms": ["raw", "current", "candidate"],
                "routing_stress_ref": "artifact://routing/1",
                "routing_stress_digest": SHA["e"],
                "routing_stress_status": "passed",
            },
        },
    }


def _measurement(
    evaluator: dict,
    *,
    generic_arm: str,
    identity: dict,
    replicate: int,
    score: float,
    overtrigger: bool = False,
    trial_id: str | None = None,
    treatment_spec_digest: str = SHA["0"],
) -> dict:
    cases = [
        {"case_id": "explicit-1", "case_kind": "explicit", "treatment": "optional"},
        {"case_id": "implicit-1", "case_kind": "implicit", "treatment": "optional"},
        {"case_id": "negative-1", "case_kind": "negative", "treatment": "forbidden"},
    ]
    loaded = []
    if identity["arm"] != "raw":
        loaded = ["explicit-1", "implicit-1"]
        if overtrigger:
            loaded.append("negative-1")
    treatment = classify_skill_treatment(
        identity=identity,
        cases=cases,
        loaded_case_ids=loaded,
    )
    return {
        "schema_version": "evolution-measurement.v1",
        "trial_id": trial_id or f"trial-{generic_arm}-{replicate}",
        "arm": generic_arm,
        "pairing": {"replicate": replicate},
        "evaluator_generation_digest": evaluator["generation_digest"],
        "comparison_identity": {
            "scenario": SHA["f"],
            "skill_treatment_spec_digest": treatment_spec_digest,
        },
        "gates": {"correctness": "passed"},
        "scores": {"correctness": score},
        "case_results": [
            {
                "case_id": case["case_id"],
                "case_kind": case["case_kind"],
                "score": score,
                "gate_passed": True,
            }
            for case in cases
        ],
        "treatment": treatment,
    }


def _comparison_rows(*, min_trials: int, replicates: int, overtrigger: bool = False):
    evaluator = _evaluator(min_trials=min_trials)
    spec = _trial_spec()
    identities = spec["treatment_identities"]
    control = [
        _measurement(
            evaluator,
            generic_arm="control",
            identity=identities["raw"],
            replicate=replicate,
            score=60,
        )
        for replicate in range(1, replicates + 1)
    ]
    baseline = [
        _measurement(
            evaluator,
            generic_arm="baseline",
            identity=identities["current"],
            replicate=replicate,
            score=70,
        )
        for replicate in range(1, replicates + 1)
    ]
    candidate = [
        _measurement(
            evaluator,
            generic_arm="candidate",
            identity=identities["candidate"],
            replicate=replicate,
            score=90,
            overtrigger=overtrigger,
        )
        for replicate in range(1, replicates + 1)
    ]
    return evaluator, control, baseline, candidate


def test_single_replicate_skill_win_remains_non_adoptable() -> None:
    evaluator, control, baseline, candidate = _comparison_rows(
        min_trials=1,
        replicates=1,
    )
    result = compare_repeated_trials(
        evaluator,
        attempt_id="skill-attempt-1",
        control=control,
        baseline=baseline,
        candidate=candidate,
        attempt=_claim_attempt(),
    )

    assert result["status"] == "candidate_better"
    assert result["adoption_eligible"] is False
    assert "insufficient_replicates:raw" in result["blocking_reasons"]
    assert "insufficient_replicates:current" in result["blocking_reasons"]
    assert "insufficient_replicates:candidate" in result["blocking_reasons"]


def test_complete_repeated_skill_evidence_can_propose_but_overtrigger_blocks() -> None:
    evaluator, control, baseline, candidate = _comparison_rows(
        min_trials=2,
        replicates=2,
    )
    accepted = compare_repeated_trials(
        evaluator,
        attempt_id="skill-attempt-2",
        control=control,
        baseline=baseline,
        candidate=candidate,
        attempt=_claim_attempt(),
    )

    assert accepted["status"] == "candidate_better"
    assert accepted["adoption_eligible"] is True
    assert accepted["blocking_reasons"] == []
    assert accepted["paired_lifts"]["comparisons"][
        "candidate_vs_current"
    ]["matched_pair_count"] == 6

    evaluator, control, baseline, candidate = _comparison_rows(
        min_trials=2,
        replicates=2,
        overtrigger=True,
    )
    blocked = compare_repeated_trials(
        evaluator,
        attempt_id="skill-attempt-3",
        control=control,
        baseline=baseline,
        candidate=candidate,
        attempt=_claim_attempt(),
    )
    assert blocked["adoption_eligible"] is False
    assert "routing_overtrigger_observed" in blocked["blocking_reasons"]


def test_measurements_matching_each_other_but_not_attempt_are_incomparable() -> None:
    evaluator, control, baseline, candidate = _comparison_rows(
        min_trials=2,
        replicates=2,
    )
    for row in [*control, *baseline, *candidate]:
        row["comparison_identity"]["skill_treatment_spec_digest"] = SHA["9"]

    result = compare_repeated_trials(
        evaluator,
        attempt_id="skill-attempt-stale-treatment",
        control=control,
        baseline=baseline,
        candidate=candidate,
        attempt=_claim_attempt(),
    )

    assert result["status"] == "incomparable"
    assert result["adoption_eligible"] is False
    assert result["reason"] == (
        "Skill measurement treatment spec digest differs from attempt"
    )


def test_unmatched_or_duplicate_case_replicates_block_skill_adoption() -> None:
    evaluator, control, baseline, candidate = _comparison_rows(
        min_trials=2,
        replicates=2,
    )
    for row in baseline:
        row["pairing"]["replicate"] += 1
    unmatched = compare_repeated_trials(
        evaluator,
        attempt_id="skill-attempt-unmatched-pairs",
        control=control,
        baseline=baseline,
        candidate=candidate,
        attempt=_claim_attempt(),
    )

    assert unmatched["status"] == "candidate_better"
    assert unmatched["adoption_eligible"] is False
    assert any(
        reason.startswith("unmatched_pair_set:current:")
        for reason in unmatched["blocking_reasons"]
    )

    evaluator, control, baseline, candidate = _comparison_rows(
        min_trials=2,
        replicates=2,
    )
    candidate[1]["pairing"]["replicate"] = 1
    duplicate = compare_repeated_trials(
        evaluator,
        attempt_id="skill-attempt-duplicate-pair",
        control=control,
        baseline=baseline,
        candidate=candidate,
        attempt=_claim_attempt(),
    )

    assert duplicate["adoption_eligible"] is False
    assert any(
        reason.startswith("duplicate_case_replicate:candidate:")
        for reason in duplicate["blocking_reasons"]
    )
    assert duplicate["paired_lifts"]["status"] == "invalid"


def test_treatment_summary_cannot_hide_negative_case_overtrigger() -> None:
    evaluator, control, baseline, candidate = _comparison_rows(
        min_trials=2,
        replicates=2,
        overtrigger=True,
    )
    candidate[0]["treatment"]["overtrigger_count"] = 0

    result = compare_repeated_trials(
        evaluator,
        attempt_id="skill-attempt-forged-treatment-summary",
        control=control,
        baseline=baseline,
        candidate=candidate,
        attempt=_claim_attempt(),
    )

    assert result["status"] == "incomparable"
    assert result["adoption_eligible"] is False
    assert "overtrigger_count is inconsistent" in result["reason"]


def test_legacy_or_non_adoption_skill_measurement_cannot_adopt() -> None:
    evaluator = _evaluator(min_trials=1)
    plain = {
        "schema_version": "evolution-measurement.v1",
        "trial_id": "plain-baseline",
        "arm": "baseline",
        "evaluator_generation_digest": evaluator["generation_digest"],
        "comparison_identity": {
            "scenario": SHA["f"],
            "skill_treatment_spec_digest": SHA["0"],
        },
        "gates": {"correctness": "passed"},
        "scores": {"correctness": 60},
    }
    candidate = {**plain, "trial_id": "plain-candidate", "arm": "candidate"}
    candidate["scores"] = {"correctness": 90}
    legacy = compare_repeated_trials(
        evaluator,
        attempt_id="legacy-skill",
        baseline=[plain],
        candidate=[candidate],
        attempt=_claim_attempt(),
    )

    assert legacy["status"] == "candidate_better"
    assert legacy["adoption_eligible"] is False
    assert "legacy_skill_measurement" in legacy["blocking_reasons"]


def _full_attempt(evaluator: dict) -> dict:
    refs = {
        "config_ref": "artifact://config/1",
        "config_digest": SHA["1"],
        "evaluator_ref": "artifact://evaluator/1",
        "evaluator_digest": evaluator["generation_digest"],
        "scenario_set_ref": "artifact://scenario/1",
        "scenario_set_digest": evaluator["scenario_set_digest"],
        "provider_capability_ref": "artifact://provider/1",
        "provider_capability_digest": SHA["2"],
        "toolchain_ref": "artifact://toolchain/1",
        "toolchain_digest": SHA["3"],
        "environment_ref": "artifact://environment/1",
        "environment_digest": SHA["4"],
        "sandbox_policy_ref": "artifact://sandbox/1",
        "sandbox_policy_digest": SHA["5"],
        "network_policy_ref": "artifact://network/1",
        "network_policy_digest": SHA["6"],
        "credential_policy_ref": "artifact://credential/1",
        "credential_policy_digest": SHA["7"],
        "run_archive_manifest_ref": "artifact://archive/1",
        "run_archive_manifest_digest": SHA["8"],
    }
    return {
        "schema_version": "evolution-attempt.v1",
        "attempt_id": "skill-attempt-contract",
        "campaign_id": "skill-campaign",
        "evolution_time": "post_task",
        "persistence_scope": "project",
        "adoption_claim": "persistent_capability",
        "evidence_kinds": ["outcome", "environmental", "trajectory"],
        "objective": {"kind": "skill_uplift", "summary": "improve planning", "task_family": "issue"},
        "mutation": {
            "object_kind": "skill_prompt",
            "identity_kind": "artifact_digest",
            "object_ref": "skill://demo-method",
            "skill_name": "demo-method",
            "base_version": SHA["9"],
            "candidate_version": SHA["a"],
            "diff_ref": "artifact://skill-diff/1",
            "diff_digest": SHA["b"],
        },
        "source_identity": {
            "workflow_run_id": "run-1",
            "source_task_ids": ["TASK-1"],
            "briefing_ref": "artifact://briefing/1",
            "briefing_digest": SHA["c"],
            "context_read_set_ref": "artifact://context/1",
            "context_read_set_digest": SHA["d"],
            "skill_lock_ref": "artifact://skill-lock/1",
            "skill_lock_digest": SHA["e"],
            "memory_snapshot_ref": "artifact://memory/1",
            "memory_snapshot_digest": SHA["f"],
            "tool_policy_ref": "artifact://tool-policy/1",
            "tool_policy_digest": SHA["0"],
        },
        "frozen_inputs": {
            **refs,
            "workflow_generation": "workflow-1",
            "provider": "codex",
            "model": "model-1",
            "holdout_authority_ref": evaluator["holdout_authority_ref"],
            "evaluation_harness_digest": evaluator["tcb_digest"],
            "comparison_parser_digest": evaluator["parser_digest"],
            "holdout_generation_digest": evaluator["holdout_generation_digest"],
            "skill_treatment_spec_ref": "artifact://skill-treatment/1",
            "skill_treatment_spec_digest": SHA["1"],
        },
        "evaluation_policy": {
            "pairing_key": SHA["2"],
            "required_gates": ["correctness"],
            "required_score_dimensions": ["correctness"],
            "score_weights_digest": evaluator["weights_digest"],
            "numeric_policy": "finite_bounded",
            "min_trials": 2,
            "min_delta": 2,
            "evaluation_purpose": "adoption_lift",
            "skill_adoption": {
                "min_distinct_cases": 3,
                "min_replicates_per_case": 2,
                "required_arms": ["raw", "current", "candidate"],
                "routing_stress_ref": "artifact://routing/1",
                "routing_stress_digest": SHA["3"],
                "routing_stress_status": "passed",
            },
        },
        "execution_policy": {
            "attempt_idempotency_key": SHA["4"],
            "lease_seconds": 60,
            "max_trial_attempts": 2,
            "retry_policy": "infrastructure_only",
        },
        "budget": {"max_cost_usd": 10, "max_wall_seconds": 600, "max_tokens": 10000},
        "policy": {"apply_mode": "proposal_only", "owner_approval_required": True, "canary_required": True},
    }


def test_skill_attempt_contract_enforces_persistent_evidence_floor() -> None:
    evaluator = _evaluator(min_trials=2)
    attempt = _full_attempt(evaluator)

    normalized = validate_evolution_attempt(attempt)
    assert normalized["evaluation_policy"]["skill_adoption"][
        "required_arms"
    ] == ["candidate", "current", "raw"]

    too_small = deepcopy(attempt)
    too_small["evaluation_policy"]["min_trials"] = 1
    with pytest.raises(EvolutionContractError, match="min_trials >= 2"):
        validate_evolution_attempt(too_small)


def _archive_trial(state_dir: Path, trial_id: str) -> tuple[str, str]:
    live = state_dir / "evolution" / "test-live" / trial_id
    live.mkdir(parents=True)
    (live / "events.jsonl").write_text("", encoding="utf-8")
    result = archive_run(
        project_root=state_dir.parent,
        state_dir=state_dir,
        live_state_dir=live,
        run_id=f"skill-{stable_digest(trial_id)[:16]}",
        status="passed",
        command="mock-skill-evaluation",
        provider={"provider": "mock", "model": "deterministic"},
        summary={"trial_id": trial_id},
    )
    return str(result.manifest_path), result.manifest_digest


def _skill_asset(attempt_id: str) -> dict:
    return {
        "schema_version": "learning-asset.v1",
        "asset_id": "demo-method-candidate",
        "asset_kind": "skill_prompt",
        "skill_name": "demo-method",
        "version": 1,
        "digest": SHA["a"],
        "source_attempt_ids": [attempt_id],
        "content": _skill_source("demo-method", "candidate"),
        "applicability": {
            "task_families": ["issue"],
            "providers": ["codex"],
        },
        "quality": {
            "confidence": "medium",
            "expires_at": "2099-01-01T00:00:00+00:00",
        },
        "activation": {
            "mode": "proposal_only",
            "owner_approval_required": True,
            "canary_scope_ref": "canary://issue/demo-method",
            "expected_active_key": "",
            "retain_policy": {
                "min_matched_outcomes": 2,
                "max_negative_transfer": 0,
            },
        },
        "rollback": {"previous_version_ref": "", "conditions": ["regression"]},
        "dependencies": [],
        "provenance": {"project": "mock", "target_validation": "passed"},
        "taint": {
            "blocked": False,
            "secret": False,
            "pii": False,
            "license_unknown": False,
        },
    }


def test_skill_coordinator_mock_e2e_settles_three_arms_before_proposal(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    evaluator = _evaluator(min_trials=2)
    attempt = _full_attempt(evaluator)
    attempt_id = attempt["attempt_id"]
    identities = _trial_spec()["treatment_identities"]
    coordinator = EvolutionCoordinator(state_dir)
    materialized = coordinator.materialize_attempt(attempt)
    assert materialized["created"] is True

    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    arms = {
        "control": ("raw", 60),
        "baseline": ("current", 70),
        "candidate": ("candidate", 90),
    }
    for replicate in (1, 2):
        for generic_arm, (treatment_arm, score) in arms.items():
            trial = coordinator.ensure_trial(
                attempt_id=attempt_id,
                arm=generic_arm,
                replicate=replicate,
            )["trial"]
            running = coordinator.start_trial(
                trial["trial_id"],
                lease_owner="skill-mock-evaluator",
                lease_expires_at=future,
            )["trial"]
            archive_ref, archive_digest = _archive_trial(
                state_dir, trial["trial_id"]
            )
            settlement = coordinator.settle_trial(
                trial["trial_id"],
                lease_owner="skill-mock-evaluator",
                attempt_number=running["attempt_number"],
                outcome="passed",
                evaluator_generation=evaluator,
                measurement=_measurement(
                    evaluator,
                    generic_arm=generic_arm,
                    identity=identities[treatment_arm],
                    replicate=replicate,
                    score=score,
                    trial_id=trial["trial_id"],
                    treatment_spec_digest=SHA["1"],
                ),
                archive_ref=archive_ref,
                archive_digest=archive_digest,
                cost_receipt_refs=[f"cost://{trial['trial_id']}"],
            )
            assert settlement["settlement_status"] == "accepted"

    restarted = EvolutionCoordinator(state_dir)
    comparison = restarted.compare_attempt(
        attempt_id,
        evaluator_generation=evaluator,
    )["comparison"]

    assert comparison["status"] == "candidate_better"
    assert comparison["adoption_eligible"] is True
    assert comparison["blocking_reasons"] == []
    assert comparison["arms"]["control"]["trial_count"] == 2
    assert comparison["arms"]["baseline"]["trial_count"] == 2
    assert comparison["arms"]["candidate"]["trial_count"] == 2
    stored_comparison = restarted.trials.load()["comparisons"][
        comparison["comparison_id"]
    ]
    assert stored_comparison["object_kind"] == "skill_prompt"
    assert stored_comparison["claim_scope"] == "product_adoption_lift"
    asset = restarted.propose_asset(
        _skill_asset(attempt_id),
        comparison_id=comparison["comparison_id"],
    )["asset"]
    assert asset["asset_kind"] == "skill_prompt"
    assert asset["state"] == "candidate"

    wrong_kind = _skill_asset(attempt_id)
    wrong_kind.update({"asset_id": "wrong-kind", "asset_kind": "memory_entry"})
    with pytest.raises(
        EvolutionContractError,
        match="only propose a skill_prompt asset",
    ):
        restarted.propose_asset(
            wrong_kind,
            comparison_id=comparison["comparison_id"],
        )

    policy_harness = EvolutionActionsMixin()
    policy_harness.state_dir = state_dir
    policy_harness.actor = "run-manager"
    policy_harness.source = "self-evolution"
    policy_harness.config = SimpleNamespace(runtime=SimpleNamespace(
        evolution=SimpleNamespace(
            enabled=True,
            mode="auto_low_risk",
            auto_asset_kinds=["skill_prompt"],
        )
    ))
    policy_error = policy_harness._evolution_policy_error(
        {
            "asset_id": asset["asset_id"],
            "version": asset["version"],
            "target_state": "validated",
            "policy_digest": "even-an-explicit-whitelist-cannot-bypass-source-approval",
        },
        transition=True,
    )
    assert "owner-approved source patch" in policy_error


def test_skill_trial_cli_is_a_real_entrypoint(tmp_path: Path, capsys) -> None:
    spec = _trial_spec()
    candidate = _candidate(spec["eval_suite"])
    current = {
        "skill_name": "demo-method",
        "content": _skill_source("demo-method", "current"),
        "version": "current-v1",
    }
    paths = {
        "candidate": candidate,
        "current": current,
        "common": {
            key: spec["treatment_identities"]["raw"][key]
            for key in (
                "support_skill_inventory_digest",
                "role_profile_digest",
                "briefing_digest",
                "prompt_digest",
                "workspace_fixture_digest",
                "tool_policy_digest",
                "eval_suite_generation_digest",
            )
        },
        "suite": spec["eval_suite"],
        "support": spec["support_skills"],
    }
    files: dict[str, Path] = {}
    for name, body in paths.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        files[name] = path

    assert main([
        "evolution",
        "skill-trial-spec",
        "--candidate-file", str(files["candidate"]),
        "--current-file", str(files["current"]),
        "--common-identity-file", str(files["common"]),
        "--eval-suite-file", str(files["suite"]),
        "--support-skills-file", str(files["support"]),
    ]) == 0
    rendered = json.loads(capsys.readouterr().out)
    assert rendered["trial_arms"] == ["control", "baseline", "candidate"]

    identities_path = tmp_path / "identities.json"
    identities_path.write_text(
        json.dumps(list(rendered["treatment_identities"].values())),
        encoding="utf-8",
    )
    assert main([
        "evolution",
        "skill-treatment-compare",
        "--identities-file", str(identities_path),
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["comparable"] is True

    drifted = list(rendered["treatment_identities"].values())
    drifted[-1]["prompt_digest"] = SHA["f"]
    identities_path.write_text(json.dumps(drifted), encoding="utf-8")
    assert main([
        "evolution",
        "skill-treatment-compare",
        "--identities-file", str(identities_path),
    ]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["comparable"] is False
