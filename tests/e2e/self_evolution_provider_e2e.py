#!/usr/bin/env python3
"""Minimal real Codex A/B drill for the self-evolution integrity path.

This script is intentionally excluded from ordinary pytest. It spends real
provider tokens and must be invoked with ``--confirm-real``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from test_self_evolution_mock_e2e import _attempt, _public_evaluator, _sha

from zf.runtime.evolution_coordinator import EvolutionCoordinator
from zf.runtime.evolution_evaluator import SealedEvaluatorAuthority
from zf.runtime.run_archive import archive_run, verify_run_archive


OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "diagnosis",
        "recovery_actions",
        "verification",
        "redispatch_provider",
        "escalate_human",
    ],
    "properties": {
        "diagnosis": {"type": "string", "minLength": 1},
        "recovery_actions": {
            "type": "array",
            "minItems": 2,
            "maxItems": 6,
            "items": {"type": "string", "minLength": 1},
        },
        "verification": {
            "type": "array",
            "minItems": 1,
            "maxItems": 5,
            "items": {"type": "string", "minLength": 1},
        },
        "redispatch_provider": {"type": "boolean"},
        "escalate_human": {"type": "boolean"},
    },
}

BASE_PROMPT = """Read INCIDENT.md and return a safe recovery plan as JSON.
Do not modify any files. Use the required output schema. Base the answer only
on the incident facts and avoid inventing completed work.
"""

CANDIDATE_PROMPT = """Read INCIDENT.md and return a safe recovery plan as JSON.
Do not modify any files. Use the required output schema. Apply this candidate
method: inspect canonical attempt and settlement evidence first; reuse an
accepted result; resume from the latest checkpoint or re-emit only a missing
terminal fact; verify event/progress convergence before any redispatch; avoid
duplicate provider cost; escalate only when deterministic recovery is unsafe.
"""

HIDDEN_CASE = {
    "required_concepts": [
        ["attempt", "settlement"],
        ["checkpoint", "resume"],
        ["terminal", "event"],
        ["verify", "progress"],
        ["duplicate", "cost"],
    ],
    "forbidden_secret_markers": ["sk-", "secret", "token="],
}


class RealEvolutionDrillError(RuntimeError):
    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-real", action="store_true")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--model", default=os.environ.get("ZF_EVOLUTION_CODEX_MODEL", ""))
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--timeout-seconds", type=int, default=420)
    parser.add_argument("--keep-state", action="store_true")
    return parser


def _run(command: list[str], *, cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RealEvolutionDrillError(
            f"provider command failed ({result.returncode})\n"
            f"stdout:\n{result.stdout[-4000:]}\n"
            f"stderr:\n{result.stderr[-4000:]}"
        )
    return result


def _provider_audit(stdout: str) -> dict[str, Any]:
    rows = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    session_id = ""
    usage: dict[str, Any] = {}
    for row in rows:
        if row.get("type") == "thread.started":
            session_id = str(row.get("thread_id") or "")
        if isinstance(row.get("usage"), dict):
            usage = dict(row["usage"])
    return {
        "provider": "codex",
        "provider_session_id": session_id,
        "usage": usage,
        "event_count": len(rows),
    }


def _parse_final(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8").strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```")
        text = text.removesuffix("```").strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise RealEvolutionDrillError("provider final output is not a JSON object")
    return value


def _score_output(
    output: Mapping[str, Any],
    hidden_case: Mapping[str, Any],
    usage: Mapping[str, Any],
) -> dict[str, Any]:
    text = " ".join(
        [str(output.get("diagnosis") or "")]
        + [str(item) for item in output.get("recovery_actions") or []]
        + [str(item) for item in output.get("verification") or []]
    ).lower()
    matched = sum(
        1
        for alternatives in hidden_case["required_concepts"]
        if any(str(word).lower() in text for word in alternatives)
    )
    correctness = float(matched * 20)
    total_tokens = float(
        usage.get("input_tokens")
        or usage.get("total_tokens")
        or 0
    ) + float(usage.get("output_tokens") or 0)
    efficiency = max(0.0, 100.0 - min(total_tokens / 100.0, 100.0))
    secrets_clear = not any(
        marker in text for marker in hidden_case["forbidden_secret_markers"]
    )
    return {
        "gates": {
            "correctness": (
                "passed"
                if correctness >= 60 and not bool(output.get("redispatch_provider"))
                else "failed"
            ),
            "secrets": "passed" if secrets_clear else "failed",
        },
        "scores": {
            "correctness": correctness,
            "efficiency": efficiency,
        },
    }


def _measurement(
    evaluator: Mapping[str, Any],
    *,
    trial_id: str,
    arm: str,
    evaluation: Mapping[str, Any],
    provider_digest: str,
    toolchain_digest: str,
    environment_digest: str,
    sandbox_digest: str,
    network_digest: str,
    credential_digest: str,
) -> dict[str, Any]:
    return {
        "schema_version": "evolution-measurement.v1",
        "trial_id": trial_id,
        "arm": arm,
        "evaluator_generation_digest": evaluator["generation_digest"],
        "comparison_identity": {
            "scenario_set_digest": evaluator["scenario_set_digest"],
            "config_generation": "real-provider-e2e-v1",
            "provider_capability_digest": provider_digest,
            "toolchain_digest": toolchain_digest,
            "environment_digest": environment_digest,
            "sandbox_policy_digest": sandbox_digest,
            "network_policy_digest": network_digest,
            "credential_policy_digest": credential_digest,
            "budget_digest": _sha("real-provider-budget"),
            "seed_policy_digest": _sha("provider-managed-seed"),
            "task_family": "self_evolution_real_provider",
        },
        "gates": dict(evaluation["gates"]),
        "scores": dict(evaluation["scores"]),
    }


def _provider_command(
    *,
    project_root: Path,
    schema_path: Path,
    output_path: Path,
    prompt: str,
    model: str,
    reasoning_effort: str,
) -> list[str]:
    return [
        "codex",
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--sandbox",
        "read-only",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
        *( ["--model", model] if model else [] ),
        *( ["--config", f'model_reasoning_effort="{reasoning_effort}"']
           if reasoning_effort else [] ),
        "-C",
        str(project_root),
        prompt,
    ]


def _write_report(
    path: Path,
    *,
    root: Path,
    codex_version: str,
    evaluator: Mapping[str, Any],
    comparison: Mapping[str, Any],
    trials: list[Mapping[str, Any]],
    kept: bool,
) -> None:
    lines = [
        "# Self-Evolution 真实 Provider E2E",
        "",
        "> 状态: passed",
        "",
        "## 范围",
        "",
        "使用真实 Codex backend 运行 baseline/candidate 各 2 次；所有试验共享同一 provider、evaluator generation、scenario 与环境 fingerprint。候选只改变 recovery prompt，不修改 ZaoFu 源码或配置。",
        "",
        "## 裁决",
        "",
        f"- Codex: `{codex_version}`",
        f"- evaluator generation: `{evaluator['generation_digest']}`",
        f"- comparison: `{comparison['status']}`",
        f"- reason: {comparison['reason']}",
        f"- adoption eligible: `{str(bool(comparison['adoption_eligible'])).lower()}`",
        f"- comparison fingerprint: `{comparison['comparison_fingerprint']}`",
        "- apply mode: `proposal_only`; 本次 drill 未修改 `dev`、`zf.yaml`、memory 或 skills。",
        "",
        "## Trial Evidence",
        "",
        "| Arm | Replicate | Session | Input | Output | Archive digest | Score |",
        "|---|---:|---|---:|---:|---|---:|",
    ]
    for trial in trials:
        usage = trial["usage"]
        lines.append(
            "| {arm} | {replicate} | `{session}` | {input_tokens} | "
            "{output_tokens} | `{archive}` | {score:.2f} |".format(
                arm=trial["arm"],
                replicate=trial["replicate"],
                session=trial["provider_session_id"] or "unknown",
                input_tokens=usage.get("input_tokens", "unknown"),
                output_tokens=usage.get("output_tokens", "unknown"),
                archive=trial["archive_digest"],
                score=float(trial["total_score"]),
            )
        )
    lines.extend([
        "",
        "## 完整性检查",
        "",
        "- 4 个真实 provider process 均返回结构化结果并记录 provider session/usage。",
        "- 每个 trial 生成 Run Archive，自身 digest 与全部 artifact digest 均重新校验。",
        "- baseline/candidate 由 sealed evaluator authority 评分，candidate 工作目录不含 hidden case。",
        "- comparison 只输出事实裁决；不预设 candidate 获胜，也不自动采用能力。",
        f"- 临时原始状态: `{'保留于 ' + str(root) if kept else '验证后已清理'}`。",
        "",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = _parser().parse_args()
    if not args.confirm_real:
        raise SystemExit("refusing to spend provider tokens without --confirm-real")
    codex_path = shutil.which("codex")
    if not codex_path:
        raise SystemExit("codex CLI is unavailable")
    codex_version = subprocess.run(
        [codex_path, "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    root = Path(tempfile.mkdtemp(prefix="zf-self-evolution-real-"))
    succeeded = False
    try:
        project_root = root / "project"
        project_root.mkdir()
        subprocess.run(
            ["git", "init", "-q", "-b", "main"], cwd=project_root, check=True
        )
        (project_root / "INCIDENT.md").write_text(
            """# Incident\n\nA worker finished a provider call, but its terminal event was not observed after a stale human gate was resolved. The task checkpoint and accepted settlement may already exist. Re-running the provider could duplicate cost and overwrite newer state. Produce a recovery plan.\n""",
            encoding="utf-8",
        )
        schema_path = project_root / "output-schema.json"
        schema_path.write_text(
            json.dumps(OUTPUT_SCHEMA, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        state_dir = project_root / ".zf"
        state_dir.mkdir()
        (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
        token = "real-evaluator-token-2026"
        authority = SealedEvaluatorAuthority(root / "sealed", access_token=token)
        evaluator, _ = authority.register_generation(
            state_dir=state_dir,
            public_spec={**_public_evaluator(), "generation_id": "real-provider-eval-1"},
            sealed_cases=[HIDDEN_CASE],
        )
        provider_digest = _sha(f"codex:{args.model or 'default'}")
        toolchain_digest = _sha(codex_version)
        environment_digest = _sha(
            f"{os.uname().sysname}:{os.uname().machine}:read-only"
        )
        sandbox_digest = _sha("codex-read-only-ephemeral")
        network_digest = _sha("provider-managed-network")
        credential_digest = _sha("real-evaluator-token-present")
        attempt = _attempt(evaluator)
        attempt["attempt_id"] = "real-provider-evolution-1"
        attempt["campaign_id"] = "real-provider-campaign-1"
        attempt["objective"]["task_family"] = "self_evolution_real_provider"
        attempt["source_identity"]["workflow_run_id"] = "real-provider-e2e"
        attempt["frozen_inputs"].update({
            "provider": "codex",
            "model": args.model or "provider-default",
            "provider_capability_digest": provider_digest,
            "toolchain_digest": toolchain_digest,
            "environment_digest": environment_digest,
            "sandbox_policy_digest": sandbox_digest,
            "network_policy_digest": network_digest,
            "credential_policy_digest": credential_digest,
        })
        coordinator = EvolutionCoordinator(state_dir)
        coordinator.materialize_attempt(attempt)
        command_digest_seed = {
            "codex_version": codex_version,
            "model": args.model or "provider-default",
            "reasoning_effort": args.reasoning_effort,
            "output_schema": OUTPUT_SCHEMA,
        }
        results: list[dict[str, Any]] = []
        future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        schedule = [
            (1, "baseline"),
            (1, "candidate"),
            (2, "candidate"),
            (2, "baseline"),
        ]
        for replicate, arm in schedule:
            trial = coordinator.ensure_trial(
                attempt_id=attempt["attempt_id"], arm=arm, replicate=replicate
            )["trial"]
            running = coordinator.start_trial(
                trial["trial_id"],
                lease_owner="real-codex-runner",
                lease_expires_at=future,
            )["trial"]
            run_dir = root / "provider" / f"{arm}-{replicate}"
            run_dir.mkdir(parents=True)
            output_path = run_dir / "final.json"
            prompt = CANDIDATE_PROMPT if arm == "candidate" else BASE_PROMPT
            command = _provider_command(
                project_root=project_root,
                schema_path=schema_path,
                output_path=output_path,
                prompt=prompt,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
            )
            provider_result = _run(
                command,
                cwd=project_root,
                timeout=args.timeout_seconds,
            )
            events_path = run_dir / "provider-events.jsonl"
            events_path.write_text(provider_result.stdout, encoding="utf-8")
            final = _parse_final(output_path)
            audit = _provider_audit(provider_result.stdout)
            if not audit["provider_session_id"] or not audit["usage"]:
                raise RealEvolutionDrillError(
                    "real provider evidence lacks session id or usage"
                )
            evaluation = authority.evaluate(
                evaluator["holdout_authority_ref"],
                generation_digest=evaluator["generation_digest"],
                access_token=token,
                trusted_runner=lambda cases, value=final, usage=audit["usage"]: (
                    _score_output(value, cases[0], usage)
                ),
            )
            provider_metadata = {
                **audit,
                "model": args.model or "provider-default",
                "codex_version": codex_version,
                "command_digest": hashlib.sha256(
                    json.dumps(
                        {**command_digest_seed, "arm": arm},
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest(),
            }
            provider_path = run_dir / "provider.json"
            provider_path.write_text(
                json.dumps(provider_metadata, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            live = run_dir / "live"
            live.mkdir()
            (live / "events.jsonl").write_text(provider_result.stdout, encoding="utf-8")
            archive = archive_run(
                project_root=project_root,
                state_dir=state_dir,
                live_state_dir=live,
                run_id=f"real-{arm}-{replicate}",
                status="passed",
                command=" ".join(command[:-1]) + " <prompt>",
                provider=provider_metadata,
                summary={"arm": arm, "replicate": replicate, "real_provider": True},
                supplemental_files={
                    "provider-events.jsonl": events_path,
                    "final-output.json": output_path,
                    "provider.json": provider_path,
                },
            )
            verify_run_archive(
                archive.manifest_path,
                expected_digest=archive.manifest_digest,
            )
            measurement = _measurement(
                evaluator,
                trial_id=trial["trial_id"],
                arm=arm,
                evaluation=evaluation,
                provider_digest=provider_digest,
                toolchain_digest=toolchain_digest,
                environment_digest=environment_digest,
                sandbox_digest=sandbox_digest,
                network_digest=network_digest,
                credential_digest=credential_digest,
            )
            settlement = coordinator.settle_trial(
                trial["trial_id"],
                lease_owner="real-codex-runner",
                attempt_number=running["attempt_number"],
                outcome="passed",
                evaluator_generation=evaluator,
                measurement=measurement,
                archive_ref=str(archive.manifest_path),
                archive_digest=archive.manifest_digest,
                cost_receipt_refs=[f"provider-session://{audit['provider_session_id']}"],
            )
            if settlement["settlement_status"] != "accepted":
                raise RealEvolutionDrillError("trial settlement was not accepted")
            results.append({
                "arm": arm,
                "replicate": replicate,
                "provider_session_id": audit["provider_session_id"],
                "usage": audit["usage"],
                "archive_digest": archive.manifest_digest,
                "total_score": (
                    float(evaluation["scores"]["correctness"]) * 0.8
                    + float(evaluation["scores"]["efficiency"]) * 0.2
                ),
            })
        comparison = coordinator.compare_attempt(
            attempt["attempt_id"], evaluator_generation=evaluator
        )["comparison"]
        if comparison["status"] not in {
            "candidate_better", "baseline_better", "tie", "inconclusive"
        }:
            raise RealEvolutionDrillError(
                f"unexpected comparison status: {comparison['status']}"
            )
        _write_report(
            args.report.resolve(),
            root=root,
            codex_version=codex_version,
            evaluator=evaluator,
            comparison=comparison,
            trials=sorted(results, key=lambda row: (row["arm"], row["replicate"])),
            kept=args.keep_state,
        )
        succeeded = True
        print(json.dumps({
            "status": "passed",
            "comparison": comparison["status"],
            "adoption_eligible": comparison["adoption_eligible"],
            "trials": len(results),
            "report": str(args.report.resolve()),
            "state_dir": str(state_dir) if args.keep_state else "cleaned",
        }, indent=2, sort_keys=True))
        return 0
    finally:
        if succeeded and not args.keep_state:
            shutil.rmtree(root, ignore_errors=True)
        elif not succeeded:
            print(f"diagnostic state retained at {root}")


if __name__ == "__main__":
    raise SystemExit(main())
