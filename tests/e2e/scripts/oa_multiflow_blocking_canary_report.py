#!/usr/bin/env python3
"""Aggregate Product Flow A/B pairs and the General negative control."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "oa-multiflow-blocking-canary-report.v1"
_PRODUCT_FLOWS = ("prd", "issue", "refactor")
_GENERAL_STAGE_GRAPH = [
    "scope",
    "collect-a",
    "collect-b",
    "synthesize",
    "verify",
]


def build_canary_report(
    product_report_paths: Mapping[str, Path],
    general_report_path: Path,
    *,
    wall_seconds: float = 0.0,
    execution_mode: str = "serial",
) -> dict[str, Any]:
    if execution_mode not in {"serial", "parallel"}:
        raise ValueError("execution_mode must be serial or parallel")
    product = [
        _product_result(flow, _read(product_report_paths.get(flow)))
        for flow in _PRODUCT_FLOWS
    ]
    general = _general_result(_read(general_report_path))
    results = [*product, general]
    source_commits = {
        str(result.get("source_commit") or "") for result in results
    }
    same_source = len(source_commits) == 1 and "" not in source_commits
    clean_source = all(bool(result.get("source_clean")) for result in results)
    source_closed = same_source and clean_source
    passed = source_closed and all(
        result.get("status") == "passed" for result in results
    )
    product_arm_seconds = [
        sum(float(arm.get("duration_seconds") or 0.0) for arm in row["arms"])
        for row in product
    ]
    general_seconds = float(general.get("duration_seconds") or 0.0)
    usage_rows = [
        arm.get("usage") or {}
        for row in product
        for arm in row["arms"]
    ] + [general.get("usage") or {}]
    oa_rows = [
        arm.get("oa_metrics") or {}
        for row in product
        for arm in row["arms"]
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "passed" if passed else "failed",
        "source_identity": {
            "commit": next(iter(source_commits)) if same_source else "",
            "same_exact_commit": same_source,
            "all_clean": clean_source,
        },
        "summary": {
            "flow_families": 4,
            "product_ab_arms": 6,
            "general_negative_controls": 1,
            "passed_flow_families": sum(
                result.get("status") == "passed" for result in results
            ),
            "failed_flow_families": sum(
                result.get("status") != "passed" for result in results
            ),
        },
        "timing": {
            "execution_mode": execution_mode,
            "observed_wall_seconds": round(max(0.0, wall_seconds), 3),
            "serial_equivalent_seconds": round(
                sum(product_arm_seconds) + general_seconds,
                3,
            ),
            "parallel_group_critical_path_seconds": round(
                max([*product_arm_seconds, general_seconds], default=0.0),
                3,
            ),
        },
        "usage": {
            "total_tokens": int(sum(_usage_tokens(row) for row in usage_rows)),
            "total_usd": round(sum(_usage_cost(row) for row in usage_rows), 6),
            "oa_total_tokens": int(sum(_number(row, "total_tokens") for row in oa_rows)),
            "oa_total_usd": round(sum(_usage_cost(row) for row in oa_rows), 6),
        },
        "product_flows": product,
        "general_negative_control": general,
        "rollout_decision": "canary_pass" if passed else "hold",
        "production_rollout_decision": "insufficient_evidence",
        "limitations": [
            "one full-workflow sample per Product Flow arm",
            "General validates the exception-advisor boundary, not blocking quality",
            "plan-gap escape is reported as unknown until events carry a typed classification",
            "canary_pass does not authorize a default production rollout",
        ],
    }


def _product_result(flow: str, value: Mapping[str, Any]) -> dict[str, Any]:
    fairness = _mapping(value.get("fairness"))
    arms = value.get("arms")
    arms = arms if isinstance(arms, list) else []
    safe_arms = [dict(arm) for arm in arms if isinstance(arm, Mapping)]
    comparison = _mapping(value.get("comparison"))
    plan_gap = _mapping(comparison.get("plan_gap_escape"))
    source = _mapping(value.get("source_identity"))
    checks = {
        "report_schema": value.get("schema_version")
        == "oa-full-workflow-ab-report.v2",
        "flow_identity": value.get("flow_kind") == flow,
        "fairness": bool(fairness) and all(bool(item) for item in fairness.values()),
        "two_terminal_arms": len(safe_arms) == 2 and all(
            arm.get("status") == "passed"
            and arm.get("terminal") == "run.goal.completed"
            and bool(arm.get("context_complete"))
            for arm in safe_arms
        ),
        "shadow_then_blocking": [arm.get("policy") for arm in safe_arms]
        == ["shadow", "blocking"],
        "policy_behavior": bool(comparison.get("policy_behavior_observed")),
        "first_verify_gate": len(safe_arms) == 2 and all(
            bool(arm.get("first_verify_gate_passed")) for arm in safe_arms
        ),
        "plan_gap_escape_disclosed": "evidence_status" in plan_gap,
        "sample_remains_bounded": value.get("rollout_decision")
        == "insufficient_evidence",
    }
    passed = value.get("status") == "passed" and all(checks.values())
    return {
        "flow_kind": flow,
        "status": "passed" if passed else "failed",
        "source_commit": str(source.get("zaofu_commit") or ""),
        "source_clean": bool(source.get("zaofu_clean")),
        "checks": checks,
        "fairness": dict(fairness),
        "arms": safe_arms,
        "comparison": dict(comparison),
    }


def _general_result(value: Mapping[str, Any]) -> dict[str, Any]:
    source = _mapping(value.get("source_identity"))
    oa = _mapping(value.get("oa"))
    checks = {
        "artifact_delivery_terminal": bool(value.get("terminal_event_id")),
        "dossier_ready": value.get("dossier_status") == "ready",
        "required_artifacts": bool(value.get("required_artifact_refs")),
        "config_pinned": bool(value.get("effective_config_digest")),
        "run_contract_pinned": bool(value.get("run_contract_digest")),
        "stage_graph": value.get("stage_graph") == _GENERAL_STAGE_GRAPH,
        "real_verify_provider_turn": bool(value.get("provider_session_id")),
        "prompt_pinned": bool(value.get("prompt_sha256")),
        "oa_checkpoint_zero": int(oa.get("checkpoint_requested") or 0) == 0,
        "oa_decision_zero": (
            int(oa.get("decision_observed") or 0) == 0
            and int(oa.get("decision_applied") or 0) == 0
        ),
        "oa_provider_turn_zero": int(oa.get("provider_turns") or 0) == 0,
        "temporary_state_cleaned": bool(value.get("cleaned")),
    }
    passed = value.get("status") == "passed" and all(checks.values())
    return {
        "flow_kind": "general",
        "status": "passed" if passed else "failed",
        "source_commit": str(source.get("head_commit") or ""),
        "source_clean": not bool(source.get("dirty", True)),
        "checks": checks,
        "oa": dict(oa),
        "provider": {
            "backend": str(value.get("backend") or ""),
            "model": str(value.get("model") or ""),
            "reasoning_effort": str(value.get("reasoning_effort") or ""),
        },
        "usage": dict(value.get("usage") or {}),
        "duration_seconds": float(value.get("duration_seconds") or 0.0),
    }


def _read(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Mapping[str, Any], key: str) -> float:
    raw = value.get(key)
    return float(raw) if isinstance(raw, (int, float)) else 0.0


def _usage_cost(value: Mapping[str, Any]) -> float:
    return _number(value, "total_usd") or _number(value, "cost_usd")


def _usage_tokens(value: Mapping[str, Any]) -> float:
    total = _number(value, "total_tokens")
    if total:
        return total
    return _number(value, "input_tokens") + _number(value, "output_tokens")


def main() -> int:
    parser = argparse.ArgumentParser()
    for flow in _PRODUCT_FLOWS:
        parser.add_argument(f"--{flow}-report", required=True, type=Path)
    parser.add_argument("--general-report", required=True, type=Path)
    parser.add_argument("--wall-seconds", type=float, default=0.0)
    parser.add_argument(
        "--execution-mode",
        choices=("serial", "parallel"),
        default="serial",
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = build_canary_report(
        {
            "prd": args.prd_report,
            "issue": args.issue_report,
            "refactor": args.refactor_report,
        },
        args.general_report,
        wall_seconds=args.wall_seconds,
        execution_mode=args.execution_mode,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
