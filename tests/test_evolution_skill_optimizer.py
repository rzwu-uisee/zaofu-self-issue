from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.evolution_skill import validate_skill_candidate
from zf.runtime.evolution_skill_optimizer import (
    SkillOptimizationService,
    SkillOptimizerError,
)


SHA = {key: key * 64 for key in "123456789abcdef"}


def _skill_source(body: str = "Base rule.") -> str:
    return (
        "---\n"
        "name: demo-method\n"
        "description: Demonstrate bounded Skill optimization.\n"
        "---\n\n"
        "# Demo Method\n\n"
        f"{body}\n"
    )


def _campaign() -> dict:
    return {
        "schema_version": "skill-optimization-campaign.v1",
        "campaign_id": "skillopt-demo-1",
        "skill_name": "demo-method",
        "base_content": _skill_source(),
        "candidate_metadata": {
            "task_families": ["prd-plan"],
            "source_trajectories": [
                {"ref": "run://passed", "digest": SHA["1"], "outcome": "passed"},
                {"ref": "run://failed", "digest": SHA["2"], "outcome": "failed"},
            ],
            "applicability_ref": "artifact://skillopt/applicability",
            "applicability_digest": SHA["3"],
            "public_eval_suite_ref": "artifact://skillopt/heldout",
            "public_eval_suite_digest": SHA["4"],
            "sealed_eval_generation_ref": "sealed-evaluator://generation/skillopt-1",
            "evaluation_purpose": "content_lift",
        },
        "eval_suite_digest": SHA["4"],
        "frozen_identity": {
            "eval_suite_digest": SHA["4"],
            "grader_digest": SHA["5"],
            "model_digest": SHA["6"],
            "prompt_digest": SHA["7"],
            "provider_digest": SHA["8"],
            "support_skill_inventory_digest": SHA["9"],
            "workspace_fixture_digest": SHA["a"],
        },
        "score_dimensions": [
            {"id": "correctness", "weight": 2, "blocking": True},
            {"id": "efficiency", "weight": 1, "blocking": False},
        ],
        "max_epochs": 3,
        "max_edits_per_step": 4,
        "rejection_buffer_size": 20,
        "max_consecutive_no_improvement": 3,
        "slow_meta_cadence": 2,
    }


def _base_digest() -> str:
    return hashlib.sha256(_skill_source().encode("utf-8")).hexdigest()


def _evaluation(candidate_digest: str, correctness: float, efficiency: float) -> dict:
    return {
        "schema_version": "skill-optimization-evaluation.v1",
        "campaign_id": "skillopt-demo-1",
        "candidate_digest": candidate_digest,
        "eval_suite_digest": SHA["4"],
        "frozen_identity_digest": "",
        "scores": {
            "correctness": correctness,
            "efficiency": efficiency,
        },
        "case_result_refs": ["sealed-evaluator://result/demo"],
    }


def _service(tmp_path: Path) -> SkillOptimizationService:
    log = EventLog(tmp_path / "events.jsonl")
    return SkillOptimizationService(
        tmp_path,
        event_log=log,
        event_writer=EventWriter(log),
    )


def _init(service: SkillOptimizationService) -> dict:
    campaign = _campaign()
    from zf.runtime.evolution_contracts import stable_digest

    baseline = _evaluation(_base_digest(), 0.5, 0.5)
    baseline["frozen_identity_digest"] = stable_digest(campaign["frozen_identity"])
    return service.initialize(campaign, baseline_evaluation=baseline)


def _proposal(base_digest: str, replacement: str, *, slow: bool = False) -> dict:
    proposal = {
        "schema_version": "skill-edit-proposal.v1",
        "campaign_id": "skillopt-demo-1",
        "base_digest": base_digest,
        "edits": [{
            "edit_id": "replace-base",
            "operation": "replace",
            "old_text": "Base rule.",
            "new_text": replacement,
        }],
        "rationale": "Agent-proposed bounded edit",
    }
    if slow:
        proposal["slow_meta_update"] = {
            "avoid": ["repeat rejected wording"],
            "strategy": "prefer explicit acceptance fields",
        }
    return proposal


def _settle(
    service: SkillOptimizationService,
    state_ref: dict,
    *,
    replacement: str,
    correctness: float,
    efficiency: float,
    slow: bool = False,
) -> dict:
    prepared = service.prepare_step(
        state_ref,
        proposal=_proposal(
            str(service._current_state(state_ref)["best_content_digest"]),
            replacement,
            slow=slow,
        ),
    )
    evaluation = _evaluation(
        prepared["candidate"]["content_digest"],
        correctness,
        efficiency,
    )
    evaluation["frozen_identity_digest"] = service._campaign(
        service._current_state(state_ref)
    )["frozen_identity_digest"]
    return service.settle_step(
        state_ref,
        prepared["step_ref"],
        evaluation=evaluation,
    )


def test_bounded_optimizer_rejects_tie_accepts_improvement_and_exports(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    initialized = _init(service)

    rejected = _settle(
        service,
        initialized["state_ref"],
        replacement="Rejected rule.",
        correctness=0.4,
        efficiency=0.9,
    )
    assert rejected["selection"] == "rejected"
    assert rejected["selection_reason"] == "blocking_dimension_regression"
    assert rejected["state"]["best_content_digest"] == _base_digest()

    tied = _settle(
        service,
        rejected["state_ref"],
        replacement="Tie rule.",
        correctness=0.5,
        efficiency=0.5,
        slow=True,
    )
    assert tied["selection_reason"] == "strict_selection_tie"
    assert len(tied["state"]["rejection_buffer"]) == 2
    assert tied["state"]["slow_meta_revision"] == 1

    accepted = _settle(
        service,
        tied["state_ref"],
        replacement="Accepted rule.",
        correctness=0.8,
        efficiency=0.7,
    )
    assert accepted["selection"] == "accepted"
    assert accepted["state"]["status"] == "completed"
    assert accepted["state"]["accepted_step_count"] == 1

    exported = service.export_best(accepted["state_ref"])
    validated = validate_skill_candidate(exported["candidate"])
    assert "Accepted rule." in validated["content"]
    assert validated["optimizer_provenance"]["accepted_step_count"] == 1
    assert not (tmp_path / "skills" / "demo-method" / "SKILL.md").exists()

    event_types = [event.type for event in service.event_log.read_all()]
    assert event_types.count("evolution.skill_optimizer.step.completed") == 3
    assert "evolution.skill_optimizer.candidate.exported" in event_types


def test_optimizer_rejects_stale_state_and_out_of_cadence_slow_meta(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    initialized = _init(service)
    with pytest.raises(SkillOptimizerError, match="outside the configured cadence"):
        service.prepare_step(
            initialized["state_ref"],
            proposal=_proposal(_base_digest(), "Wrong cadence.", slow=True),
        )
    rejected = _settle(
        service,
        initialized["state_ref"],
        replacement="Rejected rule.",
        correctness=0.4,
        efficiency=0.4,
    )

    with pytest.raises(SkillOptimizerError, match="state is stale"):
        service.prepare_step(
            initialized["state_ref"],
            proposal=_proposal(_base_digest(), "Stale rule."),
        )


def test_optimizer_rejects_edit_budget_and_ambiguous_targets(tmp_path: Path) -> None:
    service = _service(tmp_path)
    initialized = _init(service)

    over_budget = _proposal(_base_digest(), "Replacement rule.")
    over_budget["edits"] = [
        {
            "edit_id": f"replace-{index}",
            "operation": "replace",
            "old_text": "Base rule.",
            "new_text": f"Rule {index}.",
        }
        for index in range(5)
    ]
    with pytest.raises(SkillOptimizerError, match="exceeds edit budget"):
        service.prepare_step(initialized["state_ref"], proposal=over_budget)

    ambiguous = _proposal(_base_digest(), "Replacement rule.")
    ambiguous["edits"][0]["old_text"] = "missing target"
    with pytest.raises(SkillOptimizerError, match="occur exactly once"):
        service.prepare_step(initialized["state_ref"], proposal=ambiguous)


def test_skill_optimizer_cli_initializes_real_state(tmp_path: Path, capsys) -> None:
    campaign = _campaign()
    from zf.runtime.evolution_contracts import stable_digest

    baseline = _evaluation(_base_digest(), 0.5, 0.5)
    baseline["frozen_identity_digest"] = stable_digest(campaign["frozen_identity"])
    campaign_path = tmp_path / "campaign.json"
    evaluation_path = tmp_path / "baseline.json"
    campaign_path.write_text(json.dumps(campaign), encoding="utf-8")
    evaluation_path.write_text(json.dumps(baseline), encoding="utf-8")

    assert main([
        "evolution",
        "skill-opt-init",
        "--state-dir", str(tmp_path / ".zf"),
        "--campaign-file", str(campaign_path),
        "--baseline-evaluation-file", str(evaluation_path),
    ]) == 0
    rendered = json.loads(capsys.readouterr().out)
    assert rendered["state"]["status"] == "running"
    assert rendered["state_ref"]["ref"]
