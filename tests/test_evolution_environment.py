from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.evolution_automation import (
    _canary_terminal_outcome,
    _terminal_trial_outcome,
)
from zf.runtime.evolution_contracts import stable_digest
from zf.runtime.evolution_coordinator import EvolutionCoordinator
from zf.runtime.evolution_environment import (
    capture_evolution_environment,
    environment_identity_digests,
    evaluate_evolution_environment,
)
from zf.runtime.evolution_trial_runner import execute_evolution_request
from zf.runtime.run_archive import verify_run_archive
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


_TOKEN_ENV = "ZF_EVOLUTION_EVALUATOR_TOKEN"
_TOKEN = "environment-contract-test-token"


def _command(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
    if command[:3] == ["git", "rev-parse", "HEAD"]:
        return subprocess.CompletedProcess(command, 0, stdout="a" * 40 + "\n", stderr="")
    if command[:3] == ["git", "status", "--porcelain"]:
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
    return subprocess.CompletedProcess(
        command,
        0,
        stdout=f"{Path(command[0]).name} 1.0.0\n",
        stderr="",
    )


def _available_snapshot(**kwargs) -> dict:
    return capture_evolution_environment(
        **kwargs,
        command_runner=_command,
        which=lambda command: f"/mock/{command}",
        auth_probe=lambda _backend: (True, "authenticated"),
        environ={_TOKEN_ENV: _TOKEN},
    )


def _missing_provider_snapshot(**kwargs) -> dict:
    return capture_evolution_environment(
        **kwargs,
        command_runner=_command,
        which=lambda command: None if command == "codex" else f"/mock/{command}",
        auth_probe=lambda _backend: (True, "authenticated"),
        environ={_TOKEN_ENV: _TOKEN},
    )


def test_environment_snapshot_is_redacted_and_lockfile_drift_blocks_comparison(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state_dir = project / ".zf"
    state_dir.mkdir()
    sealed_root = tmp_path / "sealed"
    sealed_root.mkdir()
    (project / "uv.lock").write_text("version = 1\n", encoding="utf-8")

    snapshot = _available_snapshot(
        project_root=project,
        state_dir=state_dir,
        backend="codex",
        model="gpt-5",
        reasoning_effort="high",
        token_env=_TOKEN_ENV,
        sealed_root=str(sealed_root),
    )
    frozen = environment_identity_digests(snapshot)

    assert snapshot["provider"]["model_reasoning_effort"] == "high"
    assert _TOKEN not in json.dumps(snapshot, sort_keys=True)
    assert evaluate_evolution_environment(snapshot, frozen_inputs=frozen)["ok"] is True

    (project / "uv.lock").write_text("version = 2\n", encoding="utf-8")
    changed = _available_snapshot(
        project_root=project,
        state_dir=state_dir,
        backend="codex",
        model="gpt-5",
        reasoning_effort="high",
        token_env=_TOKEN_ENV,
        sealed_root=str(sealed_root),
    )
    assert environment_identity_digests(changed)["toolchain_digest"] != frozen[
        "toolchain_digest"
    ]
    verdict = evaluate_evolution_environment(changed, frozen_inputs=frozen)
    assert verdict["ok"] is False
    assert verdict["failure_class"] == "evolution_environment_comparison_drift"


def test_environment_preflight_dead_letters_before_provider_turn(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state_dir = project / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    sealed_root = tmp_path / "sealed"
    sealed_root.mkdir()
    monkeypatch.setenv(_TOKEN_ENV, _TOKEN)

    snapshot = _missing_provider_snapshot(
        project_root=project,
        state_dir=state_dir,
        backend="codex",
        model="",
        reasoning_effort="",
        token_env=_TOKEN_ENV,
        sealed_root=str(sealed_root),
    )
    campaign = {
        "schema_version": "evolution-campaign.v1",
        "campaign_id": "campaign-environment-missing-provider",
        "attempt": {
            "attempt_id": "attempt-environment-missing-provider",
            "execution_policy": {"lease_seconds": 60},
            "frozen_inputs": environment_identity_digests(snapshot),
        },
    }
    writer = EventWriter(event_log_from_project(state_dir))
    coordinator = EvolutionCoordinator(state_dir, writer=writer)
    coordinator.trials.register_attempt(
        attempt_id=campaign["attempt"]["attempt_id"],
        artifact_ref={"ref": "artifact://environment-test", "sha256": stable_digest(campaign)},
        idempotency_key=stable_digest({"attempt": campaign["attempt"]["attempt_id"]}),
        max_trial_attempts=2,
        created_at="2026-08-19T00:00:00+00:00",
    )
    trial = coordinator.ensure_trial(
        attempt_id=campaign["attempt"]["attempt_id"],
        arm="baseline",
        replicate=1,
    )["trial"]
    campaign_ref = write_immutable_json_sidecar(
        state_dir,
        campaign,
        root="evolution/campaigns",
        kind="evolution_campaign",
        schema_version="evolution-campaign.v1",
        created_by="test-evolution-environment",
    )
    request = writer.emit(
        "evolution.trial.requested",
        actor="test-evolution-environment",
        payload={
            "campaign_id": campaign["campaign_id"],
            "trial_id": trial["trial_id"],
            "arm": "baseline",
            "replicate": 1,
            "campaign_ref": campaign_ref,
            "backend": "codex",
            "model": "",
            "model_reasoning_effort": "",
            "timeout_seconds": 5,
        },
    )
    config = SimpleNamespace(
        runtime=SimpleNamespace(
            evolution=SimpleNamespace(
                enabled=True,
                backend="codex",
                sealed_root=str(sealed_root),
                access_token_env=_TOKEN_ENV,
            )
        )
    )
    calls: list[list[str]] = []

    def provider_must_not_run(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        raise AssertionError("provider runner must not execute after environment failure")

    result = execute_evolution_request(
        state_dir=state_dir,
        project_root=project,
        config=config,
        request_event_id=request.id,
        writer=EventWriter(event_log_from_project(state_dir)),
        runner=provider_must_not_run,
        environment_snapshotter=_missing_provider_snapshot,
    )

    assert result["status"] == "environment_failed"
    assert result["failure_class"] == "evolution_environment_provider_cli_missing"
    assert result["settlement_status"] == "dead_letter"
    assert result["trial"]["status"] == "dead_letter"
    assert result["trial"]["retryable"] is False
    assert calls == []
    preflight = hydrate_sidecar_ref(
        state_dir,
        result["environment_preflight_ref"],
        purpose="test-environment-preflight",
    ).payload
    assert preflight["failure_class"] == "evolution_environment_provider_cli_missing"
    verify_run_archive(
        Path(result["trial"]["archive_ref"]),
        expected_digest=result["trial"]["archive_digest"],
    )
    events = writer.event_log.read_all()
    assert any(event.type == "evolution.environment.preflight.failed" for event in events)
    assert not any(event.type == "evolution.trial.retry.requested" for event in events)


def test_environment_terminal_outcomes_remain_distinct_from_provider_exhaustion() -> None:
    assert _terminal_trial_outcome([
        {"failure_class": "evolution_environment_comparison_drift"}
    ]) == "environment_comparison_drift"
    assert _terminal_trial_outcome([
        {"failure_class": "evolution_environment_provider_auth_unavailable"}
    ]) == "environment_preflight_failed"
    assert _canary_terminal_outcome({
        "failure_class": "evolution_environment_sealed_root_missing"
    }) == "environment_preflight_failed"
