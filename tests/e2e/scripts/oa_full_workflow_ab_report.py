#!/usr/bin/env python3
"""Compare two full Product Flow runs that differ only in OA Plan policy."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import yaml


SCHEMA_VERSION = "oa-full-workflow-ab-report.v2"
_PRODUCT_FLOWS = frozenset({"prd", "issue", "refactor"})


def build_ab_report(
    shadow_report_path: Path,
    blocking_report_path: Path,
    *,
    flow_kind: str = "prd",
) -> dict[str, Any]:
    if flow_kind not in _PRODUCT_FLOWS:
        raise ValueError(f"unsupported Product Flow kind: {flow_kind}")
    allowed_policy_diffs = _allowed_policy_diffs(flow_kind)
    shadow = _load_run(shadow_report_path)
    blocking = _load_run(blocking_report_path)
    shadow_config = _load_config(shadow)
    blocking_config = _load_config(blocking)
    config_diff_paths = sorted(_diff_paths(shadow_config, blocking_config))
    normalized_shadow = _normalize_policy(shadow_config, flow_kind)
    normalized_blocking = _normalize_policy(blocking_config, flow_kind)
    normalized_shadow_digest = _sha(normalized_shadow)
    normalized_blocking_digest = _sha(normalized_blocking)

    shadow_source = _nested(shadow, "source_identity", "zaofu_commit")
    blocking_source = _nested(blocking, "source_identity", "zaofu_commit")
    shadow_baseline = _nested(shadow, "source_identity", "product_source_commit")
    blocking_baseline = _nested(blocking, "source_identity", "product_source_commit")
    shadow_tree = _nested(shadow, "source_identity", "product_source_tree")
    blocking_tree = _nested(blocking, "source_identity", "product_source_tree")
    shadow_manifest = _nested(shadow, "source_identity", "baseline_manifest")
    blocking_manifest = _nested(blocking, "source_identity", "baseline_manifest")
    shadow_manifest_sha = _nested(
        shadow,
        "source_identity",
        "baseline_manifest_sha256",
    )
    blocking_manifest_sha = _nested(
        blocking,
        "source_identity",
        "baseline_manifest_sha256",
    )
    shadow_prompt = shadow.get("prompt_sha256")
    blocking_prompt = blocking.get("prompt_sha256")
    shadow_template = _nested(shadow, "config", "template_sha256")
    blocking_template = _nested(blocking, "config", "template_sha256")
    fairness = {
        "flow_kind": bool(shadow)
        and bool(blocking)
        and shadow.get("name") == blocking.get("name") == flow_kind,
        "zaofu_source_commit": bool(shadow_source) and shadow_source == blocking_source,
        "clean_source": bool(_nested(shadow, "source_identity", "zaofu_clean"))
        and bool(_nested(blocking, "source_identity", "zaofu_clean")),
        "product_baseline": bool(shadow_baseline)
        and shadow_baseline == blocking_baseline,
        "product_baseline_tree": bool(shadow_tree) and shadow_tree == blocking_tree,
        "golden_baseline_manifest": _golden_manifest_fair(
            flow_kind,
            shadow_manifest,
            blocking_manifest,
            shadow_manifest_sha,
            blocking_manifest_sha,
        ),
        "prompt_digest": bool(shadow_prompt) and shadow_prompt == blocking_prompt,
        "template_digest": bool(shadow_template)
        and shadow_template == blocking_template,
        "provider_model": bool(_provider_request(shadow))
        and _provider_request(shadow) == _provider_request(blocking),
        "provider_actual_identity": _provider_actual_ready(shadow)
        and _provider_actual_ready(blocking)
        and _provider_actual(shadow) == _provider_actual(blocking),
        "budget": bool(shadow.get("budget"))
        and shadow.get("budget") == blocking.get("budget"),
        "normalized_config": bool(normalized_shadow)
        and normalized_shadow_digest == normalized_blocking_digest,
        "config_diff_policy_only": bool(shadow_config)
        and bool(blocking_config)
        and set(config_diff_paths) <= allowed_policy_diffs,
    }
    arms = [
        _arm("A", "shadow", shadow),
        _arm("B", "blocking", blocking),
    ]
    terminal_closed = all(
        arm["status"] == "passed"
        and arm["terminal"] == "run.goal.completed"
        and arm["context_complete"]
        for arm in arms
    )
    policy_observed = (
        arms[0]["oa_decisions"]["observed"] > 0
        and arms[0]["oa_decisions"]["applied"] == 0
        and arms[1]["oa_decisions"]["applied"] > 0
    )
    passed = all(fairness.values()) and terminal_closed and policy_observed
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "passed" if passed else "failed",
        "flow_kind": flow_kind,
        "scope": (f"full_{flow_kind}_plan_to_owner_delivery_shadow_vs_blocking"),
        "evidence_completeness": {
            "shadow_report": bool(shadow),
            "blocking_report": bool(blocking),
        },
        "source_identity": {
            "zaofu_commit": str(
                _nested(shadow or blocking, "source_identity", "zaofu_commit") or ""
            ),
            "zaofu_clean": fairness["clean_source"],
        },
        "fairness": fairness,
        "config_comparison": {
            "actual_diff_paths": config_diff_paths,
            "allowed_diff_paths": sorted(allowed_policy_diffs),
            "shadow_normalized_sha256": normalized_shadow_digest,
            "blocking_normalized_sha256": normalized_blocking_digest,
        },
        "arms": arms,
        "comparison": {
            "terminal_closed_both": terminal_closed,
            "policy_behavior_observed": policy_observed,
            "blocking_minus_shadow": {
                "duration_seconds": _number(blocking, "duration_seconds")
                - _number(shadow, "duration_seconds"),
                "total_tokens": _number(blocking, "usage", "total_tokens")
                - _number(shadow, "usage", "total_tokens"),
                "cost_usd": round(
                    _number(blocking, "usage", "total_usd")
                    - _number(shadow, "usage", "total_usd"),
                    6,
                ),
                "plan_revisions": arms[1]["plan_revisions"] - arms[0]["plan_revisions"],
                "targeted_reworks": arms[1]["targeted_reworks"]
                - arms[0]["targeted_reworks"],
            },
            "plan_gap_escape": {
                "count": None,
                "evidence_status": "not_classifiable_from_current_events",
            },
        },
        "winner": None,
        "rollout_decision": "insufficient_evidence" if passed else "hold",
        "limitations": [
            "one bounded full-workflow sample per arm",
            "a successful blocking sample does not justify wider rollout",
        ],
    }


def _arm(name: str, policy: str, run: Mapping[str, Any]) -> dict[str, Any]:
    counts = run.get("counts")
    counts = counts if isinstance(counts, Mapping) else {}
    context = run.get("context_handoff")
    context = context if isinstance(context, Mapping) else {}
    checks = context.get("checks")
    checks = checks if isinstance(checks, Mapping) else {}
    attempts = run.get("attempts")
    attempts = attempts if isinstance(attempts, Mapping) else {}
    return {
        "arm": name,
        "policy": policy,
        "status": str(run.get("status") or ""),
        "terminal": str(run.get("terminal") or ""),
        "duration_seconds": _number(run, "duration_seconds"),
        "usage": dict(run.get("usage") or {}),
        "oa_metrics": dict(run.get("oa_metrics") or {}),
        "context_complete": bool(checks) and all(bool(v) for v in checks.values()),
        "oa_decisions": {
            "observed": int(counts.get("orchestrator.semantic.decision.observed") or 0),
            "applied": int(counts.get("orchestrator.semantic.decision.applied") or 0),
        },
        "plan_revisions": int(counts.get("plan.rejected") or 0),
        "targeted_reworks": int(
            counts.get("orchestrator.semantic.rework.requested") or 0
        ),
        "semantic_escape_count": int(counts.get("run.goal.blocked") or 0),
        "first_verify_gate_passed": (
            int(counts.get("test.passed") or 0) > 0
            and int(counts.get("test.failed") or 0) == 0
        ),
        "task_attempts": dict(attempts),
        "context_checks": dict(checks),
    }


def _load_run(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    runs = value.get("runs") if isinstance(value, Mapping) else None
    if not isinstance(runs, list) or len(runs) != 1:
        return {}
    run = runs[0]
    return run if isinstance(run, dict) else {}


def _load_config(run: Mapping[str, Any]) -> dict[str, Any]:
    state_dir_value = str(run.get("state_dir") or "")
    if not state_dir_value:
        return {}
    state_dir = Path(state_dir_value)
    path = state_dir.parent / "zf.yaml"
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return value if isinstance(value, dict) else {}


def _normalize_policy(
    config: Mapping[str, Any],
    flow_kind: str,
) -> dict[str, Any]:
    if not config:
        return {}
    value = copy.deepcopy(dict(config))
    try:
        orchestration = value["workflow"]["orchestration"]
        policy = orchestration["flow_policies"][flow_kind]
    except (KeyError, TypeError):
        return {}
    policy["checkpoint_policies"]["plan_candidate"] = "<ab-policy>"
    policy.pop("pilot_id", None)
    return value


def _allowed_policy_diffs(flow_kind: str) -> frozenset[str]:
    base = f"workflow.orchestration.flow_policies.{flow_kind}"
    return frozenset(
        {
            f"{base}.checkpoint_policies.plan_candidate",
            f"{base}.pilot_id",
        }
    )


def _provider_request(run: Mapping[str, Any]) -> dict[str, str]:
    provider = run.get("provider")
    if not isinstance(provider, Mapping):
        return {}
    return {
        "backend": str(provider.get("backend") or ""),
        "model": str(provider.get("model") or ""),
        "reasoning_effort": str(provider.get("reasoning_effort") or ""),
    }


def _provider_actual(run: Mapping[str, Any]) -> Mapping[str, Any]:
    actual = _nested(run, "provider", "actual_identity")
    return actual if isinstance(actual, Mapping) else {}


def _provider_actual_ready(run: Mapping[str, Any]) -> bool:
    actual = _provider_actual(run)
    roles = actual.get("roles")
    if actual.get("status") != "ready" or not isinstance(roles, list) or not roles:
        return False
    required = (
        "role_instance",
        "model",
        "comp_hash",
        "multi_agent_version",
        "reasoning_effort",
    )
    return all(
        isinstance(role, Mapping)
        and all(bool(str(role.get(key) or "")) for key in required)
        for role in roles
    )


def _golden_manifest_fair(
    flow_kind: str,
    shadow: Any,
    blocking: Any,
    shadow_sha: Any,
    blocking_sha: Any,
) -> bool:
    if flow_kind == "prd":
        return not shadow and not blocking and not shadow_sha and not blocking_sha
    if not isinstance(shadow, Mapping) or not isinstance(blocking, Mapping):
        return False
    return bool(shadow_sha) and shadow_sha == blocking_sha and shadow == blocking and (
        shadow.get("schema_version") == "product-pulse-golden-baseline.v1"
        and shadow.get("flow_kind") == flow_kind
        and isinstance(shadow.get("git_blobs"), Mapping)
        and bool(shadow.get("git_blobs"))
    )


def _diff_paths(
    left: Any,
    right: Any,
    prefix: str = "",
) -> set[str]:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        paths: set[str] = set()
        for key in set(left) | set(right):
            child = f"{prefix}.{key}" if prefix else str(key)
            if key not in left or key not in right:
                paths.add(child)
            else:
                paths.update(_diff_paths(left[key], right[key], child))
        return paths
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return {prefix}
        paths: set[str] = set()
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            paths.update(_diff_paths(left_item, right_item, f"{prefix}[{index}]"))
        return paths
    return set() if left == right else {prefix}


def _sha(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _nested(value: Mapping[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _number(value: Mapping[str, Any], *keys: str) -> float:
    raw = _nested(value, *keys)
    return float(raw) if isinstance(raw, (int, float)) else 0.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shadow-report", required=True, type=Path)
    parser.add_argument("--blocking-report", required=True, type=Path)
    parser.add_argument(
        "--flow-kind",
        choices=sorted(_PRODUCT_FLOWS),
        default="prd",
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = build_ab_report(
        args.shadow_report,
        args.blocking_report,
        flow_kind=args.flow_kind,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
