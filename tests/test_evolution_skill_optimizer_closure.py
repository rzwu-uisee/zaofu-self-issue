from __future__ import annotations

import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.evolution_automation import reconcile_evolution_automation
from zf.runtime.evolution_contracts import stable_digest
from zf.runtime.evolution_skill_optimizer import (
    SkillOptimizationService,
    SkillOptimizerError,
)
from zf.runtime.evolution_skill_optimizer_agent import (
    OPTIMIZER_AGENT_REQUESTED,
    execute_skill_optimizer_proposal,
)
from zf.runtime.evolution_skill_optimizer_automation import (
    reconcile_skill_optimizer_automation,
    submit_skill_optimizer_selection,
)
from zf.runtime.evolution_skill_optimizer_contracts import normalize_campaign
from zf.runtime.run_archive import archive_run


SHA = {key: key * 64 for key in "123456789abcdef"}


def _skill_source(body: str = "Base rule.") -> str:
    return (
        "---\n"
        "name: demo-method\n"
        "description: Demonstrate isolated Skill optimization.\n"
        "---\n\n"
        "# Demo Method\n\n"
        f"{body}\n"
    )


def _split(name: str, digest: str, suffix: str) -> dict:
    generation = (
        f"sealed-evaluator://generation/{name}-1"
        if name in {"selection", "test"}
        else f"artifact://generation/{name}-1"
    )
    return {
        "schema_version": "skill-eval-split.v1",
        "split": name,
        "ref": f"artifact://skillopt/{name}",
        "digest": digest,
        "generation_ref": generation,
        "case_ids": [f"{name}-case-{suffix}"],
        "fixture_digests": [SHA[suffix]],
        "lineage_refs": [f"trajectory://{name}-{suffix}"],
    }


def _campaign(*, max_epochs: int = 1) -> dict:
    splits = {
        "train": _split("train", SHA["1"], "1"),
        "selection": _split("selection", SHA["2"], "2"),
        "test": _split("test", SHA["3"], "3"),
    }
    return {
        "schema_version": "skill-optimization-campaign.v2",
        "campaign_id": "skillopt-closure-1",
        "skill_name": "demo-method",
        "base_content": _skill_source(),
        "candidate_metadata": {
            "task_families": ["issue"],
            "source_trajectories": [
                {"ref": "run://passed", "digest": SHA["4"], "outcome": "passed"},
                {"ref": "run://failed", "digest": SHA["5"], "outcome": "failed"},
            ],
            "applicability_ref": "artifact://skillopt/applicability",
            "applicability_digest": SHA["6"],
            "public_eval_suite_ref": splits["test"]["ref"],
            "public_eval_suite_digest": splits["test"]["digest"],
            "sealed_eval_generation_ref": splits["test"]["generation_ref"],
            "evaluation_purpose": "content_lift",
        },
        "eval_suite_digest": splits["selection"]["digest"],
        "frozen_identity": {
            "eval_suite_digest": splits["selection"]["digest"],
            "grader_digest": SHA["7"],
            "model_digest": SHA["8"],
            "prompt_digest": SHA["9"],
            "provider_digest": SHA["a"],
            "support_skill_inventory_digest": SHA["b"],
            "workspace_fixture_digest": SHA["c"],
            "runtime_commit_digest": SHA["d"],
            "role_profile_digest": SHA["e"],
            "tool_policy_digest": SHA["f"],
            "sandbox_policy_digest": "0" * 64,
            "network_policy_digest": "1" * 64,
            "evaluator_generation_digest": "2" * 64,
            "budget_digest": "3" * 64,
        },
        "dataset_splits": splits,
        "split_seed": 42,
        "sealed_test_generation_ref": splits["test"]["generation_ref"],
        "score_dimensions": [
            {"id": "correctness", "weight": 2, "blocking": True},
            {"id": "efficiency", "weight": 1, "blocking": False},
        ],
        "max_epochs": max_epochs,
        "max_edits_per_step": 4,
        "rejection_buffer_size": 20,
        "max_consecutive_no_improvement": max_epochs,
        "slow_meta_cadence": 2,
    }


def _evaluation(campaign: dict, candidate_digest: str, score: float) -> dict:
    normalized, _ = normalize_campaign(campaign)
    selection = normalized["dataset_splits"]["selection"]
    return {
        "schema_version": "skill-optimization-evaluation.v1",
        "campaign_id": normalized["campaign_id"],
        "candidate_digest": candidate_digest,
        "eval_suite_digest": normalized["eval_suite_digest"],
        "frozen_identity_digest": normalized["frozen_identity_digest"],
        "split": "selection",
        "split_ref": selection["ref"],
        "split_digest": selection["digest"],
        "split_descriptor_digest": selection["descriptor_digest"],
        "evaluator_generation_ref": selection["generation_ref"],
        "scores": {"correctness": score, "efficiency": score},
        "case_result_refs": [
            {
                "case_id": selection["case_ids"][0],
                "ref": "sealed-evaluator://result/selection-1",
                "digest": SHA["d"],
            }
        ],
    }


def _service(tmp_path: Path) -> SkillOptimizationService:
    log = EventLog(tmp_path / "events.jsonl")
    return SkillOptimizationService(
        tmp_path,
        event_log=log,
        event_writer=EventWriter(log),
    )


def test_split_overlap_and_selection_generation_drift_fail_closed(
    tmp_path: Path,
) -> None:
    campaign = _campaign()
    overlap = deepcopy(campaign)
    overlap["dataset_splits"]["test"]["case_ids"] = ["selection-case-2"]
    with pytest.raises(SkillOptimizerError, match="case_ids overlap"):
        normalize_campaign(overlap)

    service = _service(tmp_path)
    base_digest = hashlib.sha256(_skill_source().encode()).hexdigest()
    initialized = service.initialize(
        campaign,
        baseline_evaluation=_evaluation(campaign, base_digest, 0.5),
    )
    prepared = service.prepare_step(
        initialized["state_ref"],
        proposal={
            "schema_version": "skill-edit-proposal.v1",
            "campaign_id": campaign["campaign_id"],
            "base_digest": base_digest,
            "edits": [
                {
                    "edit_id": "replace-base",
                    "operation": "replace",
                    "old_text": "Base rule.",
                    "new_text": "Improved rule.",
                }
            ],
        },
    )
    stale = _evaluation(campaign, prepared["candidate"]["content_digest"], 0.8)
    stale["evaluator_generation_ref"] = "sealed-evaluator://generation/old"
    with pytest.raises(SkillOptimizerError, match="evaluator_generation_ref drift"):
        service.settle_step(
            initialized["state_ref"],
            prepared["step_ref"],
            evaluation=stale,
        )


def test_autoresearch_optimizer_agent_mock_closes_to_179_candidate(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    train_ref = write_immutable_json_sidecar(
        state_dir,
        {
            "schema_version": "skill-optimizer-train-evidence.v1",
            "sentinel": "TRAIN_VISIBLE_SENTINEL",
            "successful_steps": ["read contract", "run focused check"],
        },
        root="evolution/test-fixtures",
        kind="skill_optimizer_train_evidence",
        schema_version="skill-optimizer-train-evidence.v1",
        created_by="test",
    )
    failure_ref = write_immutable_json_sidecar(
        state_dir,
        {
            "schema_version": "skill-failure-cluster.v1",
            "failure_class": "missing verification step",
        },
        root="evolution/test-fixtures",
        kind="skill_failure_cluster",
        schema_version="skill-failure-cluster.v1",
        created_by="test",
    )
    selection_ref = write_immutable_json_sidecar(
        state_dir,
        {"schema_version": "skill-selection-suite.v1", "cases": ["selection"]},
        root="evolution/test-fixtures",
        kind="skill_selection_suite",
        schema_version="skill-selection-suite.v1",
        created_by="test",
    )
    test_ref = write_immutable_json_sidecar(
        state_dir,
        {"schema_version": "skill-test-suite.v1", "secret": "TEST_SECRET_SENTINEL"},
        root="evolution/test-fixtures",
        kind="skill_test_suite",
        schema_version="skill-test-suite.v1",
        created_by="test",
    )
    campaign = _campaign()
    campaign["dataset_splits"]["train"].update(
        {
            "ref": train_ref["ref"],
            "digest": train_ref["sha256"],
        }
    )
    campaign["dataset_splits"]["selection"].update(
        {
            "ref": selection_ref["ref"],
            "digest": selection_ref["sha256"],
        }
    )
    campaign["dataset_splits"]["test"].update(
        {
            "ref": test_ref["ref"],
            "digest": test_ref["sha256"],
        }
    )
    campaign["eval_suite_digest"] = selection_ref["sha256"]
    campaign["frozen_identity"]["eval_suite_digest"] = selection_ref["sha256"]
    campaign["candidate_metadata"].update(
        {
            "public_eval_suite_ref": test_ref["ref"],
            "public_eval_suite_digest": test_ref["sha256"],
        }
    )
    base_digest = hashlib.sha256(_skill_source().encode()).hexdigest()
    baseline = _evaluation(campaign, base_digest, 0.5)
    policy = SimpleNamespace(
        enabled=True,
        mode="evaluate_only",
        backend="codex",
        model="mock-model",
        model_reasoning_effort="low",
        trial_timeout_seconds=30,
        max_actions_per_tick=8,
    )
    deposition = {
        "schema_version": "capability-deposition.v1",
        "artifact_id": "optimizer-deposition-1",
        "run_id": "optimizer-learn-1",
        "capability": "Improve a failing delivery method.",
        "verification": "Train failure clusters are evidence-bound.",
        "skill_optimizer": {
            "schema_version": "skill-optimizer-intake.v1",
            "campaign": campaign,
            "baseline_evaluation": baseline,
            "train_evidence_ref": train_ref,
            "failure_cluster_refs": [failure_ref],
        },
    }
    live = tmp_path / "learn-live"
    live.mkdir()
    (live / "events.jsonl").write_text("", encoding="utf-8")
    deposition_path = live / "skill-optimizer-deposition.json"
    deposition_path.write_text(json.dumps(deposition), encoding="utf-8")
    archive = archive_run(
        project_root=tmp_path,
        state_dir=state_dir,
        live_state_dir=live,
        run_id="optimizer-learn-archive",
        status="passed",
        command="mock autoresearch learn",
        provider={"provider": "mock"},
        supplemental_files={
            "artifacts/skill-optimizer-deposition.json": deposition_path,
        },
    )
    writer.append(
        ZfEvent(
            type="autoresearch.loop.completed",
            actor="zf-autoresearch-resident",
            payload={
                "mode": "learn",
                "loop_request_id": "optimizer-learn-1",
                "archive_refs": {
                    "manifest": str(archive.manifest_path),
                    "manifest_digest": archive.manifest_digest,
                },
            },
        )
    )
    config = SimpleNamespace(runtime=SimpleNamespace(evolution=policy))
    intake = reconcile_evolution_automation(
        state_dir=state_dir,
        writer=writer,
        config=config,
        project_root=tmp_path,
    )
    assert intake.intake_materialized == 1
    assert intake.optimizer_requests == 1
    events = writer.event_log.read_all()
    request_event = next(
        event for event in events if event.type == OPTIMIZER_AGENT_REQUESTED
    )

    def fake_codex(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        prompt = command[-1]
        assert "TRAIN_VISIBLE_SENTINEL" in prompt
        assert "TEST_SECRET_SENTINEL" not in prompt
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(
            json.dumps(
                {
                    "schema_version": "skill-edit-proposal.v1",
                    "campaign_id": campaign["campaign_id"],
                    "base_digest": base_digest,
                    "edits": [
                        {
                            "edit_id": "replace-base",
                            "operation": "replace",
                            "old_text": "Base rule.",
                            "new_text": "Improved rule.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    executed = execute_skill_optimizer_proposal(
        state_dir=state_dir,
        request_event_id=request_event.id,
        writer=writer,
        runner=fake_codex,
    )
    selection_request = next(
        event
        for event in writer.event_log.read_all()
        if event.id == executed["selection_request_event_id"]
    )
    candidate_digest = selection_request.payload["candidate_content_digest"]
    submitted = submit_skill_optimizer_selection(
        state_dir=state_dir,
        writer=writer,
        selection_request_event_id=selection_request.id,
        evaluation=_evaluation(campaign, candidate_digest, 0.9),
    )
    assert submitted["created"] is True

    closed = reconcile_skill_optimizer_automation(
        state_dir=state_dir,
        writer=writer,
        policy=policy,
        max_actions=4,
    )

    assert closed.steps == 1
    assert closed.exports == 1
    exported = next(
        event
        for event in writer.event_log.read_all()
        if event.type == "evolution.skill_optimizer.candidate.exported"
    )
    assert exported.payload["next_lifecycle"] == "design-179-evaluation-and-adoption"
    assert not (tmp_path / "skills" / "demo-method" / "SKILL.md").exists()
