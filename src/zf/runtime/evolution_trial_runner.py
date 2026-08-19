"""Isolated provider executor for evolution trial and canary requests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.evolution_contracts import EvolutionContractError, stable_digest
from zf.runtime.evolution_coordinator import EvolutionCoordinator
from zf.runtime.evolution_evaluator import SealedEvaluatorAuthority
from zf.runtime.run_archive import archive_run
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


ProviderRunner = Callable[..., subprocess.CompletedProcess[str]]


def execute_evolution_request(
    *,
    state_dir: Path,
    project_root: Path,
    config: Any,
    request_event_id: str,
    writer: EventWriter,
    runner: ProviderRunner = subprocess.run,
) -> dict[str, Any]:
    """Execute one EventLog request and settle it with immutable evidence."""

    state_dir = Path(state_dir).resolve(strict=False)
    project_root = Path(project_root).resolve(strict=False)
    request = next((
        event for event in writer.event_log.read_all()
        if event.id == request_event_id
    ), None)
    if request is None:
        raise EvolutionContractError(f"evolution request event not found: {request_event_id}")
    if request.type not in {"evolution.trial.requested", "evolution.canary.requested"}:
        raise EvolutionContractError(f"unsupported evolution request type: {request.type}")
    payload = _payload(request)
    campaign = _campaign(state_dir, payload)
    policy = getattr(getattr(config, "runtime", None), "evolution", None)
    if policy is None or not bool(getattr(policy, "enabled", False)):
        raise EvolutionContractError("runtime.evolution is disabled")
    backend = str(payload.get("backend") or getattr(policy, "backend", "")).strip()
    if backend not in {"codex", "claude-code"}:
        raise EvolutionContractError(f"unsupported evolution backend: {backend}")
    sealed_root = str(getattr(policy, "sealed_root", "") or "").strip()
    token_env = str(
        getattr(policy, "access_token_env", "ZF_EVOLUTION_EVALUATOR_TOKEN")
        or "ZF_EVOLUTION_EVALUATOR_TOKEN"
    )
    token = os.environ.get(token_env, "")
    if not sealed_root or len(token) < 16:
        raise EvolutionContractError(
            "evolution trial requires configured sealed_root and evaluator token"
        )
    authority = SealedEvaluatorAuthority(Path(sealed_root), access_token=token)
    if request.type == "evolution.trial.requested":
        return _execute_trial(
            state_dir=state_dir,
            project_root=project_root,
            request=request,
            campaign=campaign,
            authority=authority,
            token=token,
            backend=backend,
            writer=writer,
            runner=runner,
        )
    return _execute_canary(
        state_dir=state_dir,
        project_root=project_root,
        request=request,
        campaign=campaign,
        authority=authority,
        token=token,
        backend=backend,
        writer=writer,
        runner=runner,
    )


def _execute_trial(
    *,
    state_dir: Path,
    project_root: Path,
    request: ZfEvent,
    campaign: Mapping[str, Any],
    authority: SealedEvaluatorAuthority,
    token: str,
    backend: str,
    writer: EventWriter,
    runner: ProviderRunner,
) -> dict[str, Any]:
    payload = _payload(request)
    coordinator = EvolutionCoordinator(state_dir, writer=writer)
    trial_id = str(payload.get("trial_id") or "")
    lease_owner = f"autoresearch-resident:{request.id}"
    future = (
        datetime.now(timezone.utc)
        + timedelta(seconds=int(campaign["attempt"]["execution_policy"]["lease_seconds"]))
    ).isoformat()
    claimed = coordinator.start_trial(
        trial_id,
        lease_owner=lease_owner,
        lease_expires_at=future,
        actor="autoresearch-evolution-runner",
    )
    row = claimed["trial"]
    if not claimed["claimed"]:
        return {
            "ok": row.get("status") == "settled",
            "status": "already_settled" if row.get("status") == "settled" else "not_claimed",
            "trial": row,
        }
    arm = str(row["arm"])
    try:
        evidence = _run_evaluator_cases(
            state_dir=state_dir,
            request=request,
            campaign=campaign,
            evaluator=campaign["evaluator"],
            authority=authority,
            token=token,
            backend=backend,
            arm=arm,
            runner=runner,
        )
        archive = _archive_provider_evidence(
            state_dir=state_dir,
            project_root=project_root,
            request=request,
            evidence=evidence,
            run_id=f"evo-{trial_id}-a{row['attempt_number']}",
            backend=backend,
            arm=arm,
        )
        measurement = _measurement(
            campaign,
            evaluator=campaign["evaluator"],
            trial_id=trial_id,
            arm=arm,
            evaluation=evidence["evaluation"],
        )
        cost_ref = write_immutable_json_sidecar(
            state_dir,
            {
                "schema_version": "evolution-cost-receipt.v1",
                "trial_id": trial_id,
                "provider": backend,
                "usage": evidence["usage"],
            },
            root="evolution/cost-receipts",
            kind="evolution_cost_receipt",
            schema_version="evolution-cost-receipt.v1",
            created_by="autoresearch-evolution-runner",
            source_event_id=request.id,
        )
        settled = coordinator.settle_trial(
            trial_id,
            lease_owner=lease_owner,
            attempt_number=int(row["attempt_number"]),
            outcome="passed",
            evaluator_generation=campaign["evaluator"],
            measurement=measurement,
            archive_ref=str(archive["manifest"]),
            archive_digest=str(archive["digest"]),
            cost_receipt_refs=[str(cost_ref["ref"])],
            actor="autoresearch-evolution-runner",
        )
        return {"ok": True, "status": "settled", **settled, "provider": evidence}
    except Exception as exc:
        archive = _archive_infrastructure_failure(
            state_dir=state_dir,
            project_root=project_root,
            request=request,
            run_id=f"evo-{trial_id}-a{row['attempt_number']}-failed",
            backend=backend,
            reason=f"{type(exc).__name__}: {exc}",
        )
        settled = coordinator.settle_trial(
            trial_id,
            lease_owner=lease_owner,
            attempt_number=int(row["attempt_number"]),
            outcome="infrastructure_failed",
            archive_ref=str(archive["manifest"]),
            archive_digest=str(archive["digest"]),
            failure_class=type(exc).__name__,
            actor="autoresearch-evolution-runner",
        )
        return {
            "ok": False,
            "status": "retryable",
            "reason": str(exc),
            **settled,
        }


def _execute_canary(
    *,
    state_dir: Path,
    project_root: Path,
    request: ZfEvent,
    campaign: Mapping[str, Any],
    authority: SealedEvaluatorAuthority,
    token: str,
    backend: str,
    writer: EventWriter,
    runner: ProviderRunner,
) -> dict[str, Any]:
    payload = _payload(request)
    evaluator = campaign.get("canary_evaluator")
    if not isinstance(evaluator, Mapping) or not evaluator:
        raise EvolutionContractError("campaign has no independent canary evaluator")
    asset_id = str(payload.get("asset_id") or "")
    version = int(payload.get("version") or 0)
    try:
        evidence = _run_evaluator_cases(
            state_dir=state_dir,
            request=request,
            campaign=campaign,
            evaluator=evaluator,
            authority=authority,
            token=token,
            backend=backend,
            arm="candidate",
            runner=runner,
        )
        archive = _archive_provider_evidence(
            state_dir=state_dir,
            project_root=project_root,
            request=request,
            evidence=evidence,
            run_id="evocanary-" + stable_digest({
                "asset_id": asset_id,
                "version": version,
                "request": request.id,
            })[:20],
            backend=backend,
            arm="candidate",
        )
        passed = bool(evidence["evaluation"]["gate_passed"])
        outcome = "passed" if passed else "regressed"
        event = writer.emit(
            "evolution.canary.completed",
            actor="autoresearch-evolution-runner",
            causation_id=request.id,
            correlation_id=str(campaign["campaign_id"]),
            payload={
                "schema_version": "evolution-canary-result.v1",
                "campaign_id": campaign["campaign_id"],
                "asset_id": asset_id,
                "version": version,
                "outcome": outcome,
                "usage_ref": f"run-archive://{archive['digest']}",
                "archive_ref": archive["manifest"],
                "archive_digest": archive["digest"],
                "cost": evidence["usage"],
                "cohort": {
                    "task_family": campaign["attempt"]["objective"]["task_family"],
                    "provider": backend,
                    "model": str(payload.get("model") or "provider-default"),
                },
                "evaluation": {
                    "baseline_ref": "",
                    "candidate_ref": "",
                    "holdout_matched": True,
                },
                "score": evidence["evaluation"]["total_score"],
            },
        )
        return {"ok": True, "status": outcome, "event_id": event.id}
    except Exception as exc:
        event = writer.emit(
            "evolution.canary.failed",
            actor="autoresearch-evolution-runner",
            causation_id=request.id,
            correlation_id=str(campaign["campaign_id"]),
            payload={
                "schema_version": "evolution-canary-failure.v1",
                "campaign_id": campaign["campaign_id"],
                "asset_id": asset_id,
                "version": version,
                "failure_class": type(exc).__name__,
                "reason": str(exc),
            },
        )
        return {"ok": False, "status": "failed", "event_id": event.id, "reason": str(exc)}


def _run_evaluator_cases(
    *,
    state_dir: Path,
    request: ZfEvent,
    campaign: Mapping[str, Any],
    evaluator: Mapping[str, Any],
    authority: SealedEvaluatorAuthority,
    token: str,
    backend: str,
    arm: str,
    runner: ProviderRunner,
) -> dict[str, Any]:
    outputs: list[dict[str, Any]] = []
    usage: dict[str, float] = {"input_tokens": 0.0, "output_tokens": 0.0}

    def trusted(cases: list[dict[str, Any]]) -> Mapping[str, Any]:
        for index, case in enumerate(cases, start=1):
            result = _invoke_provider(
                request=request,
                campaign=campaign,
                case=case,
                backend=backend,
                arm=arm,
                index=index,
                runner=runner,
            )
            outputs.append(result)
            for key in ("input_tokens", "output_tokens"):
                usage[key] += float((result.get("usage") or {}).get(key) or 0)
        return _score_outputs(outputs, cases, evaluator)

    evaluation = authority.evaluate(
        str(evaluator["holdout_authority_ref"]),
        generation_digest=str(evaluator["generation_digest"]),
        access_token=token,
        trusted_runner=trusted,
    )
    return {
        "outputs": outputs,
        "usage": {key: int(value) for key, value in usage.items()},
        "evaluation": evaluation,
    }


def _invoke_provider(
    *,
    request: ZfEvent,
    campaign: Mapping[str, Any],
    case: Mapping[str, Any],
    backend: str,
    arm: str,
    index: int,
    runner: ProviderRunner,
) -> dict[str, Any]:
    work = Path(tempfile.mkdtemp(prefix="zf-evolution-provider-"))
    try:
        output = work / "final.txt"
        candidate_method = (
            str(campaign["asset"]["content"])
            if arm == "candidate" else "No candidate method is available."
        )
        prompt = (
            "Solve the evaluation task without reading files or using tools. "
            "Return only the requested answer; do not mention this evaluation.\n\n"
            f"Method available:\n{candidate_method}\n\n"
            f"Task:\n{str(case.get('prompt') or '')}"
        )
        model = str(_payload(request).get("model") or "")
        effort = str(_payload(request).get("model_reasoning_effort") or "")
        timeout = int(_payload(request).get("timeout_seconds") or 300)
        if backend == "codex":
            command = [
                "codex", "exec", "--json", "--ephemeral", "--ignore-user-config",
                "--sandbox", "read-only", "--skip-git-repo-check",
                "--output-last-message", str(output),
                *( ["--model", model] if model else [] ),
                *( ["--config", f'model_reasoning_effort="{effort}"'] if effort else [] ),
                "-C", str(work), prompt,
            ]
        else:
            command = [
                "claude", "-p", "--output-format", "json",
                *( ["--model", model] if model else [] ),
                prompt,
            ]
        proc = runner(
            command,
            cwd=work,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"{backend} exited {proc.returncode}: {(proc.stderr or '')[-1000:]}"
            )
        if backend == "codex":
            final = output.read_text(encoding="utf-8") if output.exists() else ""
            audit = _codex_audit(proc.stdout or "")
        else:
            final, audit = _claude_result(proc.stdout or "")
        if not final.strip():
            raise RuntimeError("provider returned no final answer")
        return {
            "case_index": index,
            "final": final.strip(),
            "provider_session_id": audit["provider_session_id"],
            "usage": audit["usage"],
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _score_outputs(
    outputs: list[Mapping[str, Any]],
    cases: list[Mapping[str, Any]],
    evaluator: Mapping[str, Any],
) -> dict[str, Any]:
    scores: list[float] = []
    secrets_clear = True
    for output, case in zip(outputs, cases, strict=True):
        text = str(output.get("final") or "").lower()
        required = case.get("required_concepts") or []
        matched = sum(
            1 for alternatives in required
            if any(str(term).lower() in text for term in alternatives)
        )
        if required:
            score = matched / len(required) * 100.0
        else:
            expected = str(case.get("expected_text") or "").strip().lower()
            score = 100.0 if expected and expected in text else 0.0
        scores.append(score)
        secrets_clear = secrets_clear and not any(
            str(term).lower() in text
            for term in case.get("forbidden_terms") or []
        )
    correctness = sum(scores) / len(scores) if scores else 0.0
    minimum = min(
        [float(case.get("minimum_score") or 60.0) for case in cases],
        default=60.0,
    )
    gate_passed = correctness >= minimum and secrets_clear
    gates: dict[str, str] = {}
    for gate in evaluator["required_gates"]:
        gate_id = str(gate["id"])
        if "secret" in gate_id.lower():
            gates[gate_id] = "passed" if secrets_clear else "failed"
        else:
            gates[gate_id] = "passed" if gate_passed else "failed"
    dimensions = {
        str(item["id"]): (
            max(0.0, min(100.0, correctness))
            if "correct" in str(item["id"]).lower()
            else 100.0 if gate_passed else 0.0
        )
        for item in evaluator["required_score_dimensions"]
    }
    total_score = sum(dimensions.values()) / len(dimensions) if dimensions else 0.0
    return {
        "gates": gates,
        "scores": dimensions,
        "gate_passed": gate_passed,
        "total_score": total_score,
    }


def _measurement(
    campaign: Mapping[str, Any],
    *,
    evaluator: Mapping[str, Any],
    trial_id: str,
    arm: str,
    evaluation: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "evolution-measurement.v1",
        "trial_id": trial_id,
        "arm": arm,
        "evaluator_generation_digest": evaluator["generation_digest"],
        "comparison_identity": dict(campaign["comparison_identity"]),
        "gates": dict(evaluation["gates"]),
        "scores": dict(evaluation["scores"]),
    }


def _archive_provider_evidence(
    *,
    state_dir: Path,
    project_root: Path,
    request: ZfEvent,
    evidence: Mapping[str, Any],
    run_id: str,
    backend: str,
    arm: str,
) -> dict[str, str]:
    live = state_dir / "evolution" / "live" / run_id
    live.mkdir(parents=True, exist_ok=True)
    outputs = live / "provider-outputs.json"
    outputs.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    events_path = live / "events.jsonl"
    events_path.write_text(
        "".join(str(item.get("stdout") or "") for item in evidence["outputs"]),
        encoding="utf-8",
    )
    (live / "cost.jsonl").write_text(
        json.dumps({"backend": backend, **dict(evidence["usage"])}) + "\n",
        encoding="utf-8",
    )
    archive = archive_run(
        project_root=project_root,
        state_dir=state_dir,
        live_state_dir=live,
        run_id=run_id,
        status="passed",
        command=f"{backend} isolated evolution {arm}",
        provider={
            "provider": backend,
            "model": str(_payload(request).get("model") or "provider-default"),
            "usage": dict(evidence["usage"]),
            "session_ids": [
                str(item.get("provider_session_id") or "")
                for item in evidence["outputs"]
            ],
        },
        summary={"arm": arm, "evolution": True, "request_event_id": request.id},
        supplemental_files={"provider-outputs.json": outputs},
    )
    shutil.rmtree(live, ignore_errors=True)
    return {"manifest": str(archive.manifest_path), "digest": archive.manifest_digest}


def _archive_infrastructure_failure(
    *,
    state_dir: Path,
    project_root: Path,
    request: ZfEvent,
    run_id: str,
    backend: str,
    reason: str,
) -> dict[str, str]:
    live = state_dir / "evolution" / "live" / run_id
    live.mkdir(parents=True, exist_ok=True)
    (live / "events.jsonl").write_text("", encoding="utf-8")
    failure = live / "failure.json"
    failure.write_text(json.dumps({"reason": reason}) + "\n", encoding="utf-8")
    archive = archive_run(
        project_root=project_root,
        state_dir=state_dir,
        live_state_dir=live,
        run_id=run_id,
        status="failed",
        command=f"{backend} isolated evolution",
        exit_code=1,
        provider={"provider": backend},
        summary={"reason": reason, "request_event_id": request.id},
        supplemental_files={"failure.json": failure},
    )
    shutil.rmtree(live, ignore_errors=True)
    return {"manifest": str(archive.manifest_path), "digest": archive.manifest_digest}


def _campaign(state_dir: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    descriptor = payload.get("campaign_ref")
    if not isinstance(descriptor, Mapping):
        raise EvolutionContractError("evolution request lacks campaign_ref")
    hydrated = hydrate_sidecar_ref(
        state_dir,
        dict(descriptor),
        purpose="evolution-provider-execution",
        actor="autoresearch-evolution-runner",
    )
    if not isinstance(hydrated.payload, Mapping):
        raise EvolutionContractError("evolution campaign is invalid")
    return dict(hydrated.payload)


def _codex_audit(stdout: str) -> dict[str, Any]:
    session_id = ""
    usage: dict[str, Any] = {}
    for line in stdout.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("type") == "thread.started":
            session_id = str(row.get("thread_id") or "")
        if isinstance(row.get("usage"), Mapping):
            usage = dict(row["usage"])
    return {"provider_session_id": session_id, "usage": usage}


def _claude_result(stdout: str) -> tuple[str, dict[str, Any]]:
    try:
        row = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout, {"provider_session_id": "", "usage": {}}
    if not isinstance(row, Mapping):
        return stdout, {"provider_session_id": "", "usage": {}}
    return str(row.get("result") or ""), {
        "provider_session_id": str(row.get("session_id") or ""),
        "usage": dict(row.get("usage") or {}),
    }


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


__all__ = ["execute_evolution_request"]
