#!/usr/bin/env python3
"""Run a bounded real-provider A/B pilot for OA Plan adoption."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.e2e.oa_multiflow_mock_pilot import source_identity, write_report
from zf.runtime.orchestrator_agent_contracts import (
    OrchestratorAgentContractError,
    normalize_orchestration_decision,
)


REPORT_SCHEMA = "oa-real-provider-ab-pilot-report.v2"
TASK_PROMPT = (
    "为 ZaoFu 增加项目级 Automation：支持定时与手动触发、权限控制、"
    "审计记录、失败重试，并提供可验证的 Web/API 交付。"
)
KNOWN_GAPS = {
    "GAP-CLAIM-SECURITY-UNMAPPED": (
        "安全与权限 mandatory claim 没有映射到任何 work unit"
    ),
    "GAP-EVIDENCE-AUDIT-MISSING": (
        "审计交付没有独立 evidence contract 或验证命令"
    ),
}
PLAN_FIXTURE = {
    "schema_version": "plan-artifact-package.v1",
    "flow_kind": "prd",
    "goal_claim_set": {
        "mandatory_claims": [
            {"claim_id": "CLAIM-AUTOMATION", "statement": "定时与手动触发可用"},
            {"claim_id": "CLAIM-SECURITY", "statement": "权限与审计可证明"},
        ],
    },
    "task_map": {
        "tasks": [
            {
                "task_id": "TASK-AUTO-API",
                "claim_ids": ["CLAIM-AUTOMATION"],
                "owner": "backend",
                "scope": ["scheduler", "manual trigger", "retry"],
                "verification": ["pytest tests/test_automation.py"],
            },
            {
                "task_id": "TASK-AUTO-WEB",
                "claim_ids": ["CLAIM-AUTOMATION"],
                "owner": "frontend",
                "scope": ["automation controls"],
                "verification": ["npm run test -- automation"],
            },
        ],
    },
    "planning_result": {
        "summary": "Implement scheduler/API first, then add Web controls.",
        "evidence_contracts": [],
    },
}


class ProviderPilotError(RuntimeError):
    """The real-provider pilot could not produce admissible evidence."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _identity(plan_digest: str) -> dict[str, str]:
    input_digest = _sha({"task_prompt": TASK_PROMPT, "plan": PLAN_FIXTURE})
    config_digest = _sha({"mode": "semantic_control", "checkpoint": "plan_candidate"})
    return {
        "operation_id": f"op-provider-pilot-{input_digest[:12]}",
        "workflow_run_id": "run-oa-provider-ab-pilot",
        "checkpoint": "plan_candidate",
        "input_digest": input_digest,
        "effective_config_digest": config_digest,
        "plan_artifact_package_ref": "benchmark/plan-artifact-package.v1.json",
        "plan_artifact_package_digest": plan_digest,
        "task_map_generation": "g1",
    }


def _descriptor_schema(*, ref: str, digest: str) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["ref", "sha256"],
        "properties": {
            "ref": {"type": "string", "enum": [ref]},
            "sha256": {"type": "string", "enum": [digest]},
        },
    }


def provider_output_schema(identity: Mapping[str, str]) -> dict[str, Any]:
    descriptor = _descriptor_schema(
        ref=identity["plan_artifact_package_ref"],
        digest=identity["plan_artifact_package_digest"],
    )
    identity_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": list(identity),
        "properties": {
            key: {"type": "string", "enum": [value]}
            for key, value in identity.items()
        },
    }
    delta_identity = {
        key: identity[key]
        for key in (
            "operation_id",
            "workflow_run_id",
            "checkpoint",
            "input_digest",
        )
    }
    string_array = {
        "type": "array",
        "items": {"type": "string"},
        "maxItems": 8,
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "execution_status",
            "identity",
            "decision",
            "reason_codes",
            "detected_gap_ids",
            "affected_work_units",
            "required_followup",
            "expected_outcome",
            "confidence",
            "delta",
        ],
        "properties": {
            "schema_version": {
                "type": "string",
                "enum": ["orchestration-decision.v1"],
            },
            "execution_status": {"type": "string", "enum": ["completed"]},
            "identity": identity_schema,
            "decision": {
                "type": "string",
                "enum": ["adopt", "revise", "clarify", "block"],
            },
            "reason_codes": {**string_array, "minItems": 1},
            "detected_gap_ids": {
                "type": "array",
                "items": {"type": "string", "enum": sorted(KNOWN_GAPS)},
                "maxItems": len(KNOWN_GAPS),
            },
            "affected_work_units": string_array,
            "required_followup": {"type": "string"},
            "expected_outcome": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "delta": {
                "type": "object",
                "additionalProperties": False,
                "required": ["schema_version", "identity", "directives"],
                "properties": {
                    "schema_version": {
                        "type": "string",
                        "enum": ["orchestration-delta.v1"],
                    },
                    "identity": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": list(delta_identity),
                        "properties": {
                            key: {"type": "string", "enum": [value]}
                            for key, value in delta_identity.items()
                        },
                    },
                    "directives": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 4,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "directive_id",
                                "action",
                                "basis_refs",
                                "required_actions",
                                "reuse_refs",
                                "invalidate_refs",
                            ],
                            "properties": {
                                "directive_id": {"type": "string"},
                                "action": {
                                    "type": "string",
                                    "enum": ["adopt", "revise", "clarify", "block"],
                                },
                                "basis_refs": {
                                    "type": "array",
                                    "minItems": 1,
                                    "maxItems": 1,
                                    "items": descriptor,
                                },
                                "required_actions": {
                                    **string_array,
                                    "minItems": 1,
                                },
                                "reuse_refs": {
                                    "type": "array",
                                    "maxItems": 1,
                                    "items": descriptor,
                                },
                                "invalidate_refs": {
                                    "type": "array",
                                    "maxItems": 1,
                                    "items": descriptor,
                                },
                            },
                        },
                    },
                },
            },
        },
    }


def provider_prompt(identity: Mapping[str, str]) -> str:
    rubric = "\n".join(
        f"- {gap_id}: {description}"
        for gap_id, description in KNOWN_GAPS.items()
    )
    return "\n".join([
        "You are the ZaoFu Orchestrator Agent at a Plan adoption checkpoint.",
        "This is a read-only semantic review. Do not run tools or modify files.",
        "Return only JSON matching the supplied schema.",
        "Preserve every prefilled identity value exactly.",
        "Use revise plus a revise directive when a mandatory semantic gap exists.",
        "The directive basis_refs must cite the supplied Plan Package descriptor.",
        "Select detected_gap_ids only when the package actually demonstrates them.",
        "Task prompt:",
        TASK_PROMPT,
        "Known-gap evaluation rubric:",
        rubric,
        "Exact decision identity:",
        json.dumps(dict(identity), ensure_ascii=False, sort_keys=True),
        "Canonical Plan Artifact Package:",
        json.dumps(PLAN_FIXTURE, ensure_ascii=False, sort_keys=True),
    ])


def _walk_objects(value: Any):  # noqa: ANN202
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_objects(child)


def provider_usage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    usages = [
        value
        for row in rows
        for value in _walk_objects(row)
        if any(
            key in value
            for key in ("input_tokens", "output_tokens", "total_cost_usd")
        )
    ]
    usage = usages[-1] if usages else {}
    cost = usage.get("total_cost_usd")
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "cached_input_tokens": int(
            usage.get("cached_input_tokens")
            or usage.get("cache_read_input_tokens")
            or 0
        ),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cost_usd": float(cost) if isinstance(cost, (int, float)) else None,
        "cost_status": (
            "provider_reported" if isinstance(cost, (int, float))
            else "provider_not_reported"
        ),
    }


def _validate_provider_result(
    result: Mapping[str, Any],
    *,
    identity: Mapping[str, str],
) -> dict[str, Any]:
    normalized = normalize_orchestration_decision(result)
    if normalized["identity"] != dict(identity):
        raise ProviderPilotError("provider decision identity mismatch")
    detected_rows = [
        str(gap_id)
        for gap_id in result.get("detected_gap_ids") or []
    ]
    detected = set(detected_rows)
    if len(detected) != len(detected_rows):
        raise ProviderPilotError("provider returned duplicate gap ids")
    unknown = detected - set(KNOWN_GAPS)
    if unknown:
        raise ProviderPilotError(f"provider returned unknown gap ids: {unknown}")
    directives = normalized["delta"]["directives"]
    action_match = all(
        str(item.get("action") or "") == normalized["decision"]
        for item in directives
    )
    return {
        "normalized": normalized,
        "detected_gap_ids": sorted(detected),
        "known_gap_recall": len(detected) / len(KNOWN_GAPS),
        "intervention_valid": (
            normalized["decision"] == "revise"
            and action_match
            and detected == set(KNOWN_GAPS)
        ),
    }


def _provider_command(
    *,
    model: str,
    reasoning_effort: str,
    sandbox: str,
    schema_path: Path,
    output_path: Path,
    root: Path,
) -> list[str]:
    return [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--model",
        model,
        "--config",
        f'model_reasoning_effort="{reasoning_effort}"',
        "--sandbox",
        sandbox,
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
        "-C",
        str(root),
        "-",
    ]


def invoke_codex_candidate(
    *,
    identity: Mapping[str, str],
    model: str,
    reasoning_effort: str,
    sandbox: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="zf-oa-provider-ab-", dir="/tmp"))
    try:
        schema_path = root / "output-schema.json"
        output_path = root / "last-message.json"
        schema_path.write_text(
            json.dumps(provider_output_schema(identity), indent=2),
            encoding="utf-8",
        )
        command = _provider_command(
            model=model,
            reasoning_effort=reasoning_effort,
            sandbox=sandbox,
            schema_path=schema_path,
            output_path=output_path,
            root=root,
        )
        started = time.monotonic()
        completed = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            input=provider_prompt(identity),
            timeout=timeout_seconds,
            check=False,
        )
        duration = round(time.monotonic() - started, 3)
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout)[-1200:]
            raise ProviderPilotError(
                f"codex exited {completed.returncode}: {message}"
            )
        if not output_path.is_file():
            raise ProviderPilotError("codex did not write the structured result")
        rows = [
            json.loads(line)
            for line in completed.stdout.splitlines()
            if line.strip()
        ]
        result = json.loads(output_path.read_text(encoding="utf-8"))
        commands: list[str] = []
        file_changes: list[dict[str, Any]] = []
        thread_id = ""
        for row in rows:
            if row.get("type") == "thread.started":
                thread_id = str(row.get("thread_id") or "")
            item = row.get("item") if isinstance(row.get("item"), dict) else {}
            if item.get("type") == "command_execution":
                commands.append(str(item.get("command") or ""))
            elif item.get("type") in {"file_change", "file_write"}:
                file_changes.append(dict(item))
        if commands or file_changes:
            raise ProviderPilotError(
                "read-only inline pilot performed side effects: "
                f"commands={commands}, file_changes={file_changes}"
            )
        evaluated = _validate_provider_result(result, identity=identity)
        return {
            "duration_seconds": duration,
            "provider_session_id": thread_id,
            "usage": provider_usage(rows),
            "result": result,
            "evaluation": {
                key: value
                for key, value in evaluated.items()
                if key != "normalized"
            },
        }
    except (json.JSONDecodeError, OrchestratorAgentContractError) as exc:
        raise ProviderPilotError(str(exc)) from exc
    finally:
        shutil.rmtree(root)


def blocked_report(
    *,
    reason: str,
    repo_identity: Mapping[str, Any],
    provider: str,
    model: str,
    budget: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA,
        "status": "blocked",
        "scope": "plan_candidate_shadow_vs_blocking",
        "reason": reason,
        "winner": None,
        "task_prompt_sha256": _sha(TASK_PROMPT),
        "source_identity": dict(repo_identity),
        "provider": provider,
        "model": model,
        "budget": dict(budget),
        "arms": [],
        "rollout_decision": "insufficient_evidence",
        "recorded_at": _utc_now(),
    }


def completed_report(
    *,
    repo_identity: Mapping[str, Any],
    model: str,
    budget: Mapping[str, Any],
    shadow: Mapping[str, Any],
    blocking: Mapping[str, Any],
) -> dict[str, Any]:
    common = {
        "task_prompt_sha256": _sha(TASK_PROMPT),
        "source_identity": dict(repo_identity),
        "provider": "codex",
        "model": model,
        "budget": dict(budget),
    }
    def arm(
        *,
        name: str,
        mode: str,
        candidate: Mapping[str, Any],
        applies_decision: bool,
    ) -> dict[str, Any]:
        evaluation = dict(candidate["evaluation"])
        intervention_applied = bool(
            applies_decision and evaluation["intervention_valid"]
        )
        return {
            "arm": name,
            "mode": mode,
            **common,
            "provider_calls": 1,
            "duration_seconds": candidate["duration_seconds"],
            "provider_session_id": candidate["provider_session_id"],
            "usage": dict(candidate["usage"]),
            "quality": {
                **evaluation,
                "intervention_applied": intervention_applied,
                "escaped_known_gap_ids": (
                    [] if intervention_applied else sorted(KNOWN_GAPS)
                ),
            },
            "decision": dict(candidate["result"]),
        }

    shadow_arm = arm(
        name="A",
        mode="plan_candidate_shadow",
        candidate=shadow,
        applies_decision=False,
    )
    blocking_arm = arm(
        name="B",
        mode="plan_candidate_blocking",
        candidate=blocking,
        applies_decision=True,
    )
    fairness_fields = (
        "task_prompt_sha256",
        "source_identity",
        "provider",
        "model",
        "budget",
    )
    fairness = {
        field: shadow_arm[field] == blocking_arm[field]
        for field in fairness_fields
    }
    fair = all(fairness.values())
    candidate_better = bool(
        fair
        and blocking_arm["quality"]["intervention_applied"]
        and not shadow_arm["quality"]["intervention_applied"]
    )
    return {
        "schema_version": REPORT_SCHEMA,
        "status": "completed" if fair else "blocked",
        "scope": "plan_candidate_shadow_vs_blocking",
        "task_prompt_sha256": common["task_prompt_sha256"],
        "source_identity": dict(repo_identity),
        "known_gap_catalog": dict(KNOWN_GAPS),
        "fairness": fairness,
        "arms": [shadow_arm, blocking_arm],
        "winner": "B" if candidate_better else "A",
        "enforcement_delta": int(
            blocking_arm["quality"]["intervention_applied"]
        ) - int(shadow_arm["quality"]["intervention_applied"]),
        "rollout_decision": "insufficient_evidence",
        "limitations": [
            "single synthetic PRD Plan checkpoint",
            "not a full PRD/Issue/Refactor provider workflow comparison",
            "provider cost may be unavailable even when token usage is reported",
        ],
        "recorded_at": _utc_now(),
    }


def _safe_reason(exc: Exception) -> str:
    value = re.sub(r"\s+", " ", str(exc)).strip()
    return value[:1200] or type(exc).__name__


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one bounded Codex OA Plan-checkpoint A/B pilot.",
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--reasoning-effort",
        choices=("minimal", "low", "medium", "high", "xhigh"),
        default="low",
    )
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument(
        "--sandbox",
        choices=("read-only", "danger-full-access"),
        default="read-only",
    )
    parser.add_argument("--confirm-real", action="store_true")
    parser.add_argument("--confirm-danger-full-access", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    repo_root = args.repo_root.resolve()
    output = args.output.resolve()
    identity = source_identity(repo_root)
    budget = {
        "max_oa_provider_turns_per_arm": 1,
        "reasoning_effort": args.reasoning_effort,
        "timeout_seconds": max(args.timeout_seconds, 1),
        "hard_token_cap": None,
    }
    blocked_reason = ""
    if not args.confirm_real:
        blocked_reason = "real_provider_confirmation_missing"
    elif shutil.which("codex") is None:
        blocked_reason = "codex_cli_unavailable"
    elif (
        args.sandbox == "danger-full-access"
        and not args.confirm_danger_full_access
    ):
        blocked_reason = "danger_full_access_confirmation_missing"
    if blocked_reason:
        report = blocked_report(
            reason=blocked_reason,
            repo_identity=identity,
            provider="codex",
            model=args.model,
            budget=budget,
        )
        write_report(output, report)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 2

    plan_digest = _sha(PLAN_FIXTURE)
    decision_identity = _identity(plan_digest)
    try:
        shadow = invoke_codex_candidate(
            identity=decision_identity,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            sandbox=args.sandbox,
            timeout_seconds=max(args.timeout_seconds, 1),
        )
        blocking = invoke_codex_candidate(
            identity=decision_identity,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            sandbox=args.sandbox,
            timeout_seconds=max(args.timeout_seconds, 1),
        )
        report = completed_report(
            repo_identity=identity,
            model=args.model,
            budget=budget,
            shadow=shadow,
            blocking=blocking,
        )
    except (ProviderPilotError, subprocess.TimeoutExpired) as exc:
        report = blocked_report(
            reason=_safe_reason(exc),
            repo_identity=identity,
            provider="codex",
            model=args.model,
            budget=budget,
        )
    write_report(output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
