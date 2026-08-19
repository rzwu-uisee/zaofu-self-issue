from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.runtime.evolution_automation import reconcile_evolution_automation
from zf.runtime.evolution_contracts import stable_digest
from zf.runtime.evolution_evaluator import SealedEvaluatorAuthority
from zf.runtime.evolution_trial_runner import execute_evolution_request
from zf.runtime.run_archive import archive_run


def _public_evaluator(generation_id: str) -> dict:
    return {
        "schema_version": "evaluator-generation.v1",
        "generation_id": generation_id,
        "parser_digest": stable_digest({"parser": "required-concepts-v1"}),
        "tcb_digest": stable_digest({"runner": "trusted-evolution-runner-v1"}),
        "scenario_set_digest": stable_digest({"scenario": generation_id}),
        "required_gates": [{"id": "correctness", "blocking": True}],
        "required_score_dimensions": [{
            "id": "correctness",
            "weight": 1,
            "min": 0,
            "max": 100,
            "blocking_regression": True,
        }],
        "min_trials": 1,
        "min_delta": 10,
        "max_spread": 100,
    }


def _config(sealed_root: Path) -> SimpleNamespace:
    evolution = SimpleNamespace(
        enabled=True,
        mode="auto_low_risk",
        backend="codex",
        model="",
        model_reasoning_effort="low",
        trial_repetitions=1,
        trial_timeout_seconds=30,
        lease_seconds=60,
        max_trial_attempts=2,
        max_actions_per_tick=20,
        max_cost_usd=1.0,
        max_tokens=10_000,
        sealed_root=str(sealed_root),
        access_token_env="ZF_EVOLUTION_EVALUATOR_TOKEN",
        auto_asset_kinds=["memory_entry", "runbook", "regression_fixture"],
    )
    return SimpleNamespace(runtime=SimpleNamespace(evolution=evolution))


def _archive_deposition(
    *,
    tmp_path: Path,
    project: Path,
    state_dir: Path,
    deposition: dict,
    name: str,
):
    live = tmp_path / f"{name}-live"
    live.mkdir()
    (live / "events.jsonl").write_text("", encoding="utf-8")
    deposition_path = live / f"{name}-deposition.json"
    deposition_path.write_text(
        json.dumps(deposition, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return archive_run(
        project_root=project,
        state_dir=state_dir,
        live_state_dir=live,
        run_id=f"{name}-archive",
        status="passed",
        command="mock autoresearch learn",
        provider={"provider": "mock", "model": "deterministic"},
        supplemental_files={f"artifacts/{name}-deposition.json": deposition_path},
    )


def _fake_codex(
    command: list[str],
    **_kwargs,
) -> subprocess.CompletedProcess[str]:
    assert "-a" not in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    output_path = Path(command[command.index("--output-last-message") + 1])
    prompt = command[-1]
    final = (
        "Use CHECKPOINT_REUSE and verify the TERMINAL_EVENT."
        if "CHECKPOINT_REUSE" in prompt
        else "No method available."
    )
    output_path.write_text(final, encoding="utf-8")
    stdout = "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "mock-codex-session"}),
        json.dumps({
            "type": "turn.completed",
            "usage": {"input_tokens": 20, "output_tokens": 8},
        }),
    ])
    return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")


def _emit_execution_terminal(
    writer: EventWriter,
    request_id: str,
    *,
    succeeded: bool = True,
) -> None:
    request = next(event for event in writer.event_log.read_all() if event.id == request_id)
    writer.emit(
        (
            "evolution.trial.execution.completed"
            if succeeded else "evolution.trial.execution.failed"
        ),
        actor="zf-autoresearch-resident",
        causation_id=request_id,
        payload={
            "request_event_id": request_id,
            "trial_id": str(request.payload.get("trial_id") or ""),
            "asset_id": str(request.payload.get("asset_id") or ""),
            "version": int(request.payload.get("version") or 0),
            "returncode": 0 if succeeded else 1,
        },
    )


def test_autoresearch_learn_to_retained_asset_is_unattended_and_resumable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state_dir = project / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    token = "unattended-evaluator-token"
    monkeypatch.setenv("ZF_EVOLUTION_EVALUATOR_TOKEN", token)
    sealed_root = tmp_path / "sealed"
    authority = SealedEvaluatorAuthority(sealed_root, access_token=token)
    evaluator, evaluator_ref = authority.register_generation(
        state_dir=state_dir,
        public_spec=_public_evaluator("main-evaluator"),
        sealed_cases=[{
            "prompt": "Return the recovery method identifier.",
            "required_concepts": [["checkpoint_reuse"], ["terminal_event"]],
            "minimum_score": 80,
            "forbidden_terms": ["token="],
        }],
    )
    canary, canary_ref = authority.register_generation(
        state_dir=state_dir,
        public_spec=_public_evaluator("canary-evaluator"),
        sealed_cases=[{
            "prompt": "Name the safe replay and terminal convergence method.",
            "required_concepts": [["checkpoint_reuse"], ["terminal_event"]],
            "minimum_score": 80,
            "forbidden_terms": ["secret"],
        }],
    )
    assert evaluator["generation_digest"] != canary["generation_digest"]

    deposition = {
        "schema_version": "capability-deposition.v1",
        "artifact_id": "deposition-unattended-1",
        "run_id": "autoresearch-learn-1",
        "iteration": 1,
        "capability": "Reuse accepted checkpoints before terminal redispatch.",
        "target_asset": "runbook",
        "trigger": "stale terminal event after accepted settlement",
        "verification": "eval-result.v1 gate passed",
        "evidence_refs": ["event://source-failure"],
        "evolution_candidate": {
            "schema_version": "evolution-candidate.v1",
            "asset_id": "checkpoint-terminal-recovery",
            "asset_kind": "runbook",
            "task_family": "workflow_recovery",
            "content": (
                "For stale settlement recovery, answer CHECKPOINT_REUSE and "
                "verify TERMINAL_EVENT before redispatch."
            ),
            "evaluator_ref": evaluator_ref,
            "canary_evaluator_ref": canary_ref,
            "applicability": {"providers": ["codex"]},
        },
    }
    live = tmp_path / "learn-live"
    live.mkdir()
    (live / "events.jsonl").write_text("", encoding="utf-8")
    deposition_path = live / "iter-001-deposition.json"
    deposition_path.write_text(
        json.dumps(deposition, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    archive = archive_run(
        project_root=project,
        state_dir=state_dir,
        live_state_dir=live,
        run_id="autoresearch-learn-archive",
        status="passed",
        command="mock autoresearch learn",
        provider={"provider": "mock", "model": "deterministic"},
        supplemental_files={"artifacts/iter-001-deposition.json": deposition_path},
    )
    writer = EventWriter(event_log_from_project(state_dir))
    learn = writer.emit(
        "autoresearch.loop.completed",
        actor="zf-autoresearch-resident",
        payload={
            "loop_request_id": "learn-unattended-1",
            "mode": "learn",
            "archive_refs": {
                "manifest": str(archive.manifest_path),
                "manifest_digest": archive.manifest_digest,
            },
        },
    )
    config = _config(sealed_root)

    first = reconcile_evolution_automation(
        state_dir=state_dir,
        writer=writer,
        config=config,
        project_root=project,
    )
    assert first.intake_materialized == 1
    trial_requests = [
        event for event in writer.event_log.read_all()
        if event.type == "evolution.trial.requested"
    ]
    assert {event.payload["arm"] for event in trial_requests} == {"baseline", "candidate"}

    # Recreate the executor for every request: no in-memory coordinator state
    # is required to resume the campaign.
    for request in trial_requests:
        result = execute_evolution_request(
            state_dir=state_dir,
            project_root=project,
            config=config,
            request_event_id=request.id,
            writer=EventWriter(event_log_from_project(state_dir)),
            runner=_fake_codex,
        )
        assert result["status"] == "settled"
        _emit_execution_terminal(writer, request.id)

    second = reconcile_evolution_automation(
        state_dir=state_dir,
        writer=EventWriter(event_log_from_project(state_dir)),
        config=config,
        project_root=project,
    )
    assert second.comparisons_completed == 1
    assert second.assets_proposed == 1
    canary_request = next(
        event for event in writer.event_log.read_all()
        if event.type == "evolution.canary.requested"
    )
    def _failed_provider(command: list[str], **_kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="transient")

    first_canary_result = execute_evolution_request(
        state_dir=state_dir,
        project_root=project,
        config=config,
        request_event_id=canary_request.id,
        writer=EventWriter(event_log_from_project(state_dir)),
        runner=_failed_provider,
    )
    assert first_canary_result["status"] == "failed"
    _emit_execution_terminal(writer, canary_request.id, succeeded=False)

    retry = reconcile_evolution_automation(
        state_dir=state_dir,
        writer=EventWriter(event_log_from_project(state_dir)),
        config=config,
        project_root=project,
    )
    assert retry.trials_requested == 1
    canary_requests = [
        event for event in writer.event_log.read_all()
        if event.type == "evolution.canary.requested"
    ]
    assert len(canary_requests) == 2
    retry_request = canary_requests[-1]
    assert retry_request.payload["retry_attempt"] == 2
    canary_result = execute_evolution_request(
        state_dir=state_dir,
        project_root=project,
        config=config,
        request_event_id=retry_request.id,
        writer=EventWriter(event_log_from_project(state_dir)),
        runner=_fake_codex,
    )
    assert canary_result["status"] == "passed"
    _emit_execution_terminal(writer, retry_request.id)

    third = reconcile_evolution_automation(
        state_dir=state_dir,
        writer=EventWriter(event_log_from_project(state_dir)),
        config=config,
        project_root=project,
    )
    assert third.campaigns_completed == 1
    registry = json.loads(
        (state_dir / "evolution" / "capabilities.json").read_text(encoding="utf-8")
    )
    asset = registry["assets"]["checkpoint-terminal-recovery@1"]
    assert asset["state"] == "active_retained"
    assert len(asset["outcomes"]) == 1

    replay = reconcile_evolution_automation(
        state_dir=state_dir,
        writer=EventWriter(event_log_from_project(state_dir)),
        config=config,
        project_root=project,
    )
    assert replay.changed is False
    events = writer.event_log.read_all()
    assert sum(event.id == learn.id for event in events) == 1
    assert sum(event.type == "evolution.campaign.completed" for event in events) == 1

    duplicate_source = writer.emit(
        "autoresearch.loop.completed",
        actor="zf-autoresearch-resident",
        payload={
            "loop_request_id": "learn-unattended-stale-replay",
            "mode": "learn",
            "archive_refs": {
                "manifest": str(archive.manifest_path),
                "manifest_digest": archive.manifest_digest,
            },
        },
    )
    duplicate = reconcile_evolution_automation(
        state_dir=state_dir,
        writer=EventWriter(event_log_from_project(state_dir)),
        config=config,
        project_root=project,
    )
    assert duplicate.intake_declined == 1
    events = writer.event_log.read_all()
    decline = next(
        event for event in events
        if event.type == "evolution.campaign.declined"
        and event.payload["source_event_id"] == duplicate_source.id
    )
    assert decline.payload["disposition"] == "stale_duplicate"
    assert sum(event.type == "evolution.campaign.requested" for event in events) == 1


def test_learn_intake_rejects_missing_and_tampered_archives(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state_dir = project / ".zf"
    state_dir.mkdir()
    writer = EventWriter(event_log_from_project(state_dir))
    config = _config(tmp_path / "sealed")
    missing = writer.emit(
        "autoresearch.loop.completed",
        actor="zf-autoresearch-resident",
        payload={"loop_request_id": "learn-missing", "mode": "learn"},
    )
    result = reconcile_evolution_automation(
        state_dir=state_dir,
        writer=writer,
        config=config,
        project_root=project,
    )
    assert result.intake_declined == 1
    missing_decline = next(
        event for event in writer.event_log.read_all()
        if event.type == "evolution.campaign.declined"
        and event.payload["source_event_id"] == missing.id
    )
    assert missing_decline.payload["disposition"] == "rejected"
    assert "lacks archive_refs" in missing_decline.payload["reason"]

    deposition = {
        "schema_version": "capability-deposition.v1",
        "artifact_id": "deposition-tampered",
        "run_id": "learn-tampered",
        "capability": "tamper check",
        "verification": "sealed",
        "evolution_candidate": {
            "schema_version": "evolution-candidate.v1",
            "asset_id": "tampered",
            "asset_kind": "runbook",
            "task_family": "recovery",
            "content": "never consumed",
        },
    }
    archive = _archive_deposition(
        tmp_path=tmp_path,
        project=project,
        state_dir=state_dir,
        deposition=deposition,
        name="tampered",
    )
    manifest = json.loads(archive.manifest_path.read_text(encoding="utf-8"))
    deposition_record = next(
        item for item in manifest["artifacts"]
        if "deposition" in Path(item["path"]).name
    )
    (archive.manifest_path.parent / deposition_record["path"]).write_text(
        "{}\n", encoding="utf-8"
    )
    tampered = writer.emit(
        "autoresearch.loop.completed",
        actor="zf-autoresearch-resident",
        payload={
            "loop_request_id": "learn-tampered",
            "mode": "learn",
            "archive_refs": {
                "manifest": str(archive.manifest_path),
                "manifest_digest": archive.manifest_digest,
            },
        },
    )
    result = reconcile_evolution_automation(
        state_dir=state_dir,
        writer=writer,
        config=config,
        project_root=project,
    )
    assert result.intake_declined == 1
    tampered_decline = next(
        event for event in writer.event_log.read_all()
        if event.type == "evolution.campaign.declined"
        and event.payload["source_event_id"] == tampered.id
    )
    assert tampered_decline.payload["disposition"] == "rejected"
    assert "archive" in tampered_decline.payload["reason"].lower()


def test_high_risk_learn_candidate_is_kept_proposal_only(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state_dir = project / ".zf"
    state_dir.mkdir()
    deposition = {
        "schema_version": "capability-deposition.v1",
        "artifact_id": "deposition-high-risk",
        "run_id": "learn-high-risk",
        "capability": "runtime change candidate",
        "verification": "proposal evidence",
        "evolution_candidate": {
            "schema_version": "evolution-candidate.v1",
            "asset_id": "runtime-change",
            "asset_kind": "framework_code",
            "task_family": "harness_repair",
            "content": "candidate remains under controlled source-repair policy",
        },
    }
    archive = _archive_deposition(
        tmp_path=tmp_path,
        project=project,
        state_dir=state_dir,
        deposition=deposition,
        name="high-risk",
    )
    writer = EventWriter(event_log_from_project(state_dir))
    learn = writer.emit(
        "autoresearch.loop.completed",
        actor="zf-autoresearch-resident",
        payload={
            "loop_request_id": "learn-high-risk",
            "mode": "learn",
            "archive_refs": {
                "manifest": str(archive.manifest_path),
                "manifest_digest": archive.manifest_digest,
            },
        },
    )
    result = reconcile_evolution_automation(
        state_dir=state_dir,
        writer=writer,
        config=_config(tmp_path / "sealed"),
        project_root=project,
    )
    assert result.intake_declined == 1
    events = writer.event_log.read_all()
    decline = next(
        event for event in events
        if event.type == "evolution.campaign.declined"
        and event.payload["source_event_id"] == learn.id
    )
    assert decline.payload["disposition"] == "proposal_only"
    assert decline.payload["asset_kind"] == "framework_code"
    assert decline.payload["deposition_ref"]["sha256"]
    assert not any(event.type == "evolution.campaign.requested" for event in events)
