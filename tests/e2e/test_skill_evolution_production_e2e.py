from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    RuntimeConfig,
    RuntimeEvolutionConfig,
    ZfConfig,
)
from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.evolution_automation import reconcile_evolution_automation
from zf.runtime.evolution_contracts import stable_digest
from zf.runtime.evolution_coordinator import EvolutionCoordinator
from zf.runtime.evolution_environment import capture_evolution_environment
from zf.runtime.evolution_evaluator import SealedEvaluatorAuthority
from zf.runtime.evolution_skill_eval import (
    build_skill_treatment_identity,
    classify_skill_treatment,
    validate_skill_eval_suite,
)
from zf.runtime.evolution_trial_runner import execute_evolution_request
from zf.runtime.run_archive import archive_run
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


IDENTITY_FIELDS = [
    "scenario_set_digest",
    "config_generation",
    "provider_capability_digest",
    "toolchain_digest",
    "environment_digest",
    "sandbox_policy_digest",
    "network_policy_digest",
    "credential_policy_digest",
    "budget_digest",
    "seed_policy_digest",
    "task_family",
    "skill_treatment_spec_digest",
]


def _skill(name: str, marker: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {marker} delivery method\n"
        "---\n\n"
        f"# {marker}\n\nReturn METHOD_{marker.upper()} and CHECK_{marker.upper()}.\n"
    )


def _public_evaluator() -> dict:
    return {
        "schema_version": "evaluator-generation.v1",
        "generation_id": "skill-production-evaluator",
        "parser_digest": stable_digest({"parser": "required-concepts-v1"}),
        "tcb_digest": stable_digest({"runner": "trusted-skill-runner-v1"}),
        "scenario_set_digest": stable_digest({"scenario": "skill-production"}),
        "required_gates": [{"id": "correctness", "blocking": True}],
        "required_score_dimensions": [{
            "id": "correctness",
            "weight": 1,
            "min": 0,
            "max": 100,
            "blocking_regression": True,
        }],
        "comparison_identity_fields": IDENTITY_FIELDS,
        "min_trials": 2,
        "min_delta": 10,
        "max_spread": 100,
    }


def _config(sealed_root: Path, *, timeout_seconds: int = 30) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="skill-production", state_dir=".zf"),
        runtime=RuntimeConfig(evolution=RuntimeEvolutionConfig(
            enabled=True,
            mode="evaluate_only",
            backend="codex",
            model_reasoning_effort="low",
            trial_repetitions=2,
            trial_timeout_seconds=timeout_seconds,
            lease_seconds=60,
            max_trial_attempts=2,
            max_actions_per_tick=30,
            max_cost_usd=2,
            max_tokens=20_000,
            sealed_root=str(sealed_root),
        )),
        roles=[RoleConfig(
            name="dev",
            instance_id="dev-1",
            backend="codex",
            skills=["shared-method", "demo-method"],
        )],
    )


def _fake_environment_command(
    command: list[str],
    **_kwargs,
) -> subprocess.CompletedProcess[str]:
    if command and command[0] == "git":
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="not a repo")
    return subprocess.CompletedProcess(
        command,
        0,
        stdout=f"{Path(command[0]).name} 1.0.0\n",
        stderr="",
    )


def _environment(**kwargs) -> dict:
    return capture_evolution_environment(
        **kwargs,
        command_runner=_fake_environment_command,
        which=lambda command: f"/mock/{command}",
        auth_probe=lambda _backend: (True, "authenticated"),
    )


def _fake_codex(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    prompt = command[-1]
    assert "METHOD_CANDIDATE" not in prompt
    assert "CHECK_CANDIDATE" not in prompt
    output = Path(command[command.index("--output-last-message") + 1])
    target = Path(kwargs["cwd"]) / ".codex" / "skills" / "demo-method" / "SKILL.md"
    assert "skills.bundled.enabled=false" in command
    assert kwargs["env"].get("CODEX_HOME", "") != str(
        Path(kwargs["cwd"]) / ".zf-evolution-skill-trial"
    )
    task = prompt.rsplit("Task:\n", 1)[-1]
    trace = ""
    if "negative" in task:
        final = "DECLINE"
    elif target.is_file():
        content = target.read_text(encoding="utf-8")
        if "candidate" in content:
            final = "METHOD_CANDIDATE CHECK_CANDIDATE"
        else:
            final = "METHOD_CURRENT"
        trace = json.dumps({
            "type": "item.completed",
            "item": {"type": "command_execution", "command": f"cat {target}"},
        })
    else:
        final = "NO_METHOD"
    output.write_text(final, encoding="utf-8")
    stdout = "\n".join(filter(None, [
        json.dumps({"type": "thread.started", "thread_id": "skill-e2e"}),
        trace,
        json.dumps({
            "type": "turn.completed",
            "usage": {"input_tokens": 20, "output_tokens": 5},
        }),
    ]))
    return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")


def _emit_execution_terminal(writer: EventWriter, request_id: str) -> None:
    request = next(event for event in writer.event_log.read_all() if event.id == request_id)
    writer.emit(
        "evolution.trial.execution.completed",
        actor="zf-autoresearch-resident",
        causation_id=request_id,
        payload={
            "request_event_id": request_id,
            "trial_id": str(request.payload.get("trial_id") or ""),
            "returncode": 0,
        },
    )


def _run_skill_campaign(
    tmp_path: Path,
    monkeypatch,
    *,
    runner,
    environment_snapshotter,
    timeout_seconds: int = 30,
) -> dict:
    project = tmp_path / "project"
    state_dir = project / ".zf"
    state_dir.mkdir(parents=True)
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    for name, marker in (("demo-method", "current"), ("shared-method", "shared")):
        path = project / "skills" / name / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(_skill(name, marker), encoding="utf-8")
    token = "skill-production-token"
    monkeypatch.setenv("ZF_EVOLUTION_EVALUATOR_TOKEN", token)
    sealed_root = tmp_path / "sealed"
    authority = SealedEvaluatorAuthority(sealed_root, access_token=token)
    sealed_cases = [
        {
            "case_id": "explicit-1",
            "case_kind": "explicit",
            "treatment": "optional",
            "prompt": (
                "Use the $demo-method Skill and return exactly the method and "
                "check identifiers it requires."
            ),
            "required_concepts": [["method_candidate"], ["check_candidate"]],
            "minimum_score": 80,
            "behavior_expectations": [
                {"metric": "activation", "value": True},
                {"metric": "security_clear", "value": True},
            ],
        },
        {
            "case_id": "implicit-1",
            "case_kind": "implicit",
            "treatment": "optional",
            "prompt": (
                "Apply the demo-method delivery procedure and return its method "
                "and check identifiers."
            ),
            "required_concepts": [["method_candidate"], ["check_candidate"]],
            "minimum_score": 80,
            "behavior_expectations": [
                {"metric": "activation", "value": True},
                {"metric": "security_clear", "value": True},
            ],
        },
        {
            "case_id": "negative-1",
            "case_kind": "negative",
            "treatment": "forbidden",
            "prompt": "negative task",
            "required_concepts": [["decline"]],
            "minimum_score": 80,
            "behavior_expectations": [
                {"metric": "activation", "value": False},
                {"metric": "security_clear", "value": True},
            ],
        },
    ]
    evaluator, evaluator_ref = authority.register_generation(
        state_dir=state_dir,
        public_spec=_public_evaluator(),
        sealed_cases=sealed_cases,
    )
    suite = validate_skill_eval_suite({
        "schema_version": "skill-eval-suite.v1",
        "suite_id": "skill-production-suite",
        "evaluation_purpose": "adoption_lift",
        "cases": [
            {key: row[key] for key in ("case_id", "case_kind", "treatment")}
            for row in sealed_cases
        ],
    })
    suite_ref = write_immutable_json_sidecar(
        state_dir,
        suite,
        root="evolution/skill-eval-suites",
        kind="skill_eval_suite",
        schema_version="skill-eval-suite.v1",
        created_by="test",
    )
    candidate = _skill("demo-method", "candidate")
    candidate_digest = _sha(candidate)
    routing_identity = build_skill_treatment_identity(
        arm="candidate",
        target_skill={
            "name": "demo-method",
            "available": True,
            "version": candidate_digest,
            "digest": candidate_digest,
            "materialized_path_digest": stable_digest("demo-method-path"),
        },
        common_identity={
            key: stable_digest({"routing": key})
            for key in (
                "runtime_commit_digest",
                "provider_digest",
                "model_digest",
                "support_skill_inventory_digest",
                "role_profile_digest",
                "briefing_digest",
                "prompt_digest",
                "workspace_fixture_digest",
                "tool_policy_digest",
                "sandbox_policy_digest",
                "network_policy_digest",
                "budget_digest",
                "eval_suite_generation_digest",
            )
        },
        evaluation_purpose="adoption_lift",
    )
    observation_ref = write_immutable_json_sidecar(
        state_dir,
        {
            "schema_version": "skill-routing-observation.v1",
            "skill_name": "demo-method",
            "candidate_digest": candidate_digest,
            "eval_suite_digest": suite["suite_digest"],
            "pool_size": 1,
            "treatment": classify_skill_treatment(
                identity=routing_identity,
                cases=suite["cases"],
                loaded_case_ids=["explicit-1", "implicit-1"],
            ),
        },
        root="evolution/routing-observations",
        kind="skill_routing_observation",
        schema_version="skill-routing-observation.v1",
        created_by="test",
    )
    routing = {
        "schema_version": "skill-routing-stress-report.v1",
        "skill_name": "demo-method",
        "candidate_digest": candidate_digest,
        "eval_suite_digest": suite["suite_digest"],
        "required_pool_sizes": [1],
        "executed_pool_sizes": [1],
        "negative_case_ids": ["negative-1"],
        "confusable_case_ids": [],
        "observation_refs": [observation_ref],
        "overtrigger_count": 0,
        "status": "passed",
    }
    routing_ref = write_immutable_json_sidecar(
        state_dir,
        routing,
        root="evolution/routing-stress",
        kind="skill_routing_stress_report",
        schema_version="skill-routing-stress-report.v1",
        created_by="test",
    )
    deposition = {
        "schema_version": "capability-deposition.v1",
        "artifact_id": "skill-production-deposition",
        "run_id": "autoresearch-skill-production",
        "iteration": 1,
        "capability": "Improve demo-method delivery",
        "target_asset": "skill_prompt",
        "trigger": "matched issue tasks",
        "verification": "three-arm adoption lift",
        "evidence_refs": ["event://skill-production-source"],
        "evolution_candidate": {
            "schema_version": "evolution-candidate.v1",
            "asset_id": "demo-method-candidate",
            "asset_kind": "skill_prompt",
            "skill_name": "demo-method",
            "role_instance": "dev-1",
            "task_family": "issue",
            "content": candidate,
            "evaluator_ref": evaluator_ref,
            "skill_eval_suite_ref": suite_ref,
            "routing_stress_ref": routing_ref,
            "support_skill_names": ["shared-method"],
            "min_distinct_cases": 3,
            "min_replicates_per_case": 2,
        },
    }
    live = tmp_path / "learn-live"
    live.mkdir()
    (live / "events.jsonl").write_text("", encoding="utf-8")
    deposition_path = live / "skill-deposition.json"
    deposition_path.write_text(json.dumps(deposition), encoding="utf-8")
    archive = archive_run(
        project_root=project,
        state_dir=state_dir,
        live_state_dir=live,
        run_id="skill-production-learn",
        status="passed",
        command="mock autoresearch learn",
        provider={"provider": "mock"},
        supplemental_files={"artifacts/skill-deposition.json": deposition_path},
    )
    config = _config(sealed_root, timeout_seconds=timeout_seconds)
    writer = EventWriter(event_log_from_project(state_dir, config=config))
    writer.emit(
        "autoresearch.loop.completed",
        actor="zf-autoresearch-resident",
        payload={
            "loop_request_id": "skill-production-loop",
            "mode": "learn",
            "archive_refs": {
                "manifest": str(archive.manifest_path),
                "manifest_digest": archive.manifest_digest,
            },
        },
    )
    first = reconcile_evolution_automation(
        state_dir=state_dir,
        writer=writer,
        config=config,
        project_root=project,
        environment_snapshotter=environment_snapshotter,
    )
    assert first.intake_materialized == 1
    requests = [
        event for event in writer.event_log.read_all()
        if event.type == "evolution.trial.requested"
    ]
    assert [(row.payload["replicate"], row.payload["arm"]) for row in requests] == [
        (1, "control"),
        (1, "baseline"),
        (1, "candidate"),
        (2, "baseline"),
        (2, "candidate"),
        (2, "control"),
    ]
    campaign = hydrate_sidecar_ref(
        state_dir,
        requests[0].payload["campaign_ref"],
        purpose="test-skill-production-campaign",
    ).payload
    assert campaign["attempt"]["mutation"]["object_kind"] == "skill_prompt"
    assert campaign["trial_arms"] == ["control", "baseline", "candidate"]

    settled_results = []
    for request in requests:
        result = execute_evolution_request(
            state_dir=state_dir,
            project_root=project,
            config=config,
            request_event_id=request.id,
            writer=writer,
            runner=runner,
            environment_snapshotter=environment_snapshotter,
        )
        assert result["status"] == "settled"
        assert result["provider"]["materializations"]
        settled_results.append(result)
        _emit_execution_terminal(writer, request.id)

    final = reconcile_evolution_automation(
        state_dir=state_dir,
        writer=writer,
        config=config,
        project_root=project,
        environment_snapshotter=environment_snapshotter,
    )
    assert final.comparisons_completed == 1
    completion = next(
        event for event in writer.event_log.read_all()
        if event.type == "evolution.campaign.completed"
    )
    registry = EvolutionCoordinator(state_dir).capabilities.load()
    return {
        "candidate": candidate,
        "campaign": campaign,
        "completion": completion,
        "final": final,
        "registry": registry,
        "requests": requests,
        "settled_results": settled_results,
        "state_dir": state_dir,
    }


def test_skill_campaign_uses_real_materialization_and_archive_admission(
    tmp_path: Path,
    monkeypatch,
) -> None:
    proof = _run_skill_campaign(
        tmp_path,
        monkeypatch,
        runner=_fake_codex,
        environment_snapshotter=_environment,
    )
    assert proof["final"].assets_proposed == 1
    assert proof["completion"].payload["adoption"] == "proposal_only"
    control_outputs = [
        output
        for result in proof["settled_results"]
        if result["trial"]["arm"] == "control"
        for output in result["provider"]["outputs"]
    ]
    assert control_outputs
    assert all(not output["target_skill_loaded"] for output in control_outputs)
    all_outputs = [
        output
        for result in proof["settled_results"]
        for output in result["provider"]["outputs"]
    ]
    assert all(output["trajectory_ref"]["ref"] for output in all_outputs)
    candidate_results = [
        case
        for result in proof["settled_results"]
        if result["trial"]["arm"] == "candidate"
        for case in result["provider"]["evaluation"]["case_results"]
    ]
    assert candidate_results
    assert all(case["behavior_followed"] is True for case in candidate_results)
    assert all(case["behavior_verdict_ref"]["ref"] for case in candidate_results)
    row = proof["registry"]["assets"]["demo-method-candidate@1"]
    assert row["state"] == "candidate"
    assert row["digest"] == _sha(proof["candidate"])


@pytest.mark.real_provider
def test_skill_campaign_real_codex_provider_matrix(
    tmp_path: Path,
    monkeypatch,
) -> None:
    if os.environ.get("ZF_RUN_REAL_PROVIDER_E2E") != "1":
        pytest.skip("set ZF_RUN_REAL_PROVIDER_E2E=1 for the 18-call provider tier")
    proof = _run_skill_campaign(
        tmp_path,
        monkeypatch,
        runner=subprocess.run,
        environment_snapshotter=capture_evolution_environment,
        timeout_seconds=120,
    )
    results = proof["settled_results"]
    assert len(results) == 6
    assert sum(len(row["provider"]["outputs"]) for row in results) == 18
    measurements = [
        hydrate_sidecar_ref(
            proof["state_dir"],
            row["trial"]["settlement_ref"],
            purpose="real-provider-skill-matrix-assertion",
        ).payload
        for row in results
    ]
    identity_digests = {
        stable_digest(measurement["comparison_identity"])
        for measurement in measurements
    }
    assert len(identity_digests) == 1
    assert all(row["trial"]["archive_ref"] for row in results)


def _sha(content: str) -> str:
    import hashlib

    return hashlib.sha256(content.encode("utf-8")).hexdigest()
