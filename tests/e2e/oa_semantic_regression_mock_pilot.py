#!/usr/bin/env python3
"""Run the deterministic OA semantic regression matrix."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.e2e.oa_multiflow_mock_pilot import source_identity, write_report  # noqa: E402


REPORT_SCHEMA = "oa-semantic-regression-mock-pilot-report.v1"


@dataclass(frozen=True)
class SemanticCase:
    category: str
    scenario: str
    flow_kind: str
    coverage: str
    node_id: str


SEMANTIC_CASES = (
    SemanticCase(
        category="generation",
        scenario="plan_revision_currentness",
        flow_kind="prd",
        coverage="revised_package_and_required_read_ledger",
        node_id=(
            "tests/test_orchestrator_agent_run_plan.py::"
            "test_revised_generation_is_the_only_downstream_read_authority"
        ),
    ),
    SemanticCase(
        category="rework",
        scenario="verify_exact_target",
        flow_kind="prd",
        coverage="single_owner_feedback_and_unaffected_lane",
        node_id=(
            "tests/test_orchestrator_agent_semantic_failure.py::"
            "test_admitted_directive_dispatches_only_exact_target_with_bound_feedback"
        ),
    ),
    SemanticCase(
        category="judge",
        scenario="bounded_nonconvergence",
        flow_kind="product",
        coverage="three_rejections_then_owner_escalation",
        node_id=(
            "tests/test_judge_convergence_gate.py::"
            "test_three_consecutive_failures_escalate_owner"
        ),
    ),
    SemanticCase(
        category="judge",
        scenario="blocked_terminal_owner_delivery",
        flow_kind="product",
        coverage="blocked_dossier_without_false_completion_receipt",
        node_id=(
            "tests/test_goal_dossier_owner_delivery.py::"
            "test_blocked_terminal_delivers_blocker_without_completion_receipt"
        ),
    ),
    SemanticCase(
        category="language_and_lanes",
        scenario="english_parallel_collectors",
        flow_kind="general",
        coverage="objective_identity_parallel_lanes_and_goal_closure",
        node_id=(
            "tests/e2e/test_generic_workflow_complex_mock_e2e.py::"
            "test_generic_workflow_preserves_bilingual_goal_across_parallel_lanes[english]"
        ),
    ),
    SemanticCase(
        category="language_and_lanes",
        scenario="chinese_parallel_collectors",
        flow_kind="general",
        coverage="objective_identity_parallel_lanes_and_goal_closure",
        node_id=(
            "tests/e2e/test_generic_workflow_complex_mock_e2e.py::"
            "test_generic_workflow_preserves_bilingual_goal_across_parallel_lanes[chinese]"
        ),
    ),
    SemanticCase(
        category="flow_contract",
        scenario="prd_full_semantic_control",
        flow_kind="prd",
        coverage="full_checkpoint_chain_and_product_completion",
        node_id=(
            "tests/e2e/test_orchestrator_agent_semantic_control_mock_e2e.py::"
            "test_full_prd_semantic_control_run_is_replay_safe_and_auditable"
        ),
    ),
    SemanticCase(
        category="flow_contract",
        scenario="issue_scoped_handoff",
        flow_kind="issue",
        coverage="issue_plan_checkpoint_and_product_completion",
        node_id=(
            "tests/e2e/test_orchestrator_agent_semantic_control_mock_e2e.py::"
            "test_scoped_product_flow_task_map_runs_plan_checkpoint[issue]"
        ),
    ),
    SemanticCase(
        category="flow_contract",
        scenario="refactor_scoped_handoff",
        flow_kind="refactor",
        coverage="refactor_plan_checkpoint_and_product_completion",
        node_id=(
            "tests/e2e/test_orchestrator_agent_semantic_control_mock_e2e.py::"
            "test_scoped_product_flow_task_map_runs_plan_checkpoint[refactor]"
        ),
    ),
    SemanticCase(
        category="flow_contract",
        scenario="general_artifact_delivery",
        flow_kind="general",
        coverage="five_stage_replan_restart_and_artifact_delivery",
        node_id=(
            "tests/e2e/test_generic_workflow_complex_mock_e2e.py::"
            "test_generic_workflow_replans_once_and_closes_after_restart"
        ),
    ),
    SemanticCase(
        category="flow_contract",
        scenario="prd_controller_stage_graph",
        flow_kind="prd",
        coverage="prd_pipeline_and_product_completion_profile",
        node_id=(
            "tests/test_controller_flow_smoke_matrix.py::"
            "test_prd_flow_controller_smoke_matrix"
        ),
    ),
    SemanticCase(
        category="flow_contract",
        scenario="issue_controller_stage_graph",
        flow_kind="issue",
        coverage="issue_pipeline_and_regression_completion_profile",
        node_id=(
            "tests/test_controller_flow_smoke_matrix.py::"
            "test_issue_flow_controller_smoke_matrix"
        ),
    ),
    SemanticCase(
        category="flow_contract",
        scenario="refactor_controller_stage_graph",
        flow_kind="refactor",
        coverage="refactor_pipeline_and_parity_completion_profile",
        node_id=(
            "tests/test_controller_flow_smoke_matrix.py::"
            "test_refactor_flow_controller_smoke_matrix"
        ),
    ),
    SemanticCase(
        category="flow_contract",
        scenario="general_controller_completion_profile",
        flow_kind="general",
        coverage="artifact_delivery_profile_and_verified_report_contract",
        node_id=(
            "tests/test_controller_blocking_rollout.py::"
            "test_general_controllers_pin_verified_artifact_delivery"
            "[general-workflow-v3.yaml]"
        ),
    ),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tail(value: str, *, limit: int = 4000) -> str:
    return value[-limit:]


def execute_semantic_case(
    repo_root: Path,
    case: SemanticCase,
    timeout_seconds: int,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        case.node_id,
        "-q",
        "--no-cov",
    ]
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return {
            **asdict(case),
            "status": "passed" if result.returncode == 0 else "failed",
            "exit_code": result.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": _tail(result.stdout),
            "stderr_tail": _tail(result.stderr),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            **asdict(case),
            "status": "timed_out",
            "exit_code": None,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": _tail(str(exc.stdout or "")),
            "stderr_tail": _tail(str(exc.stderr or "")),
        }


def build_semantic_regression_report(
    *,
    repo_root: Path,
    timeout_seconds: int,
    cases: Sequence[SemanticCase] = SEMANTIC_CASES,
    runner: Callable[[Path, SemanticCase, int], dict[str, Any]] = (
        execute_semantic_case
    ),
    identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started_at = _utc_now()
    results = [runner(repo_root, case, timeout_seconds) for case in cases]
    passed = sum(row["status"] == "passed" for row in results)
    categories = sorted({case.category for case in cases})
    category_results = {
        category: {
            "scenario_count": sum(row["category"] == category for row in results),
            "passed": sum(
                row["category"] == category and row["status"] == "passed"
                for row in results
            ),
        }
        for category in categories
    }
    return {
        "schema_version": REPORT_SCHEMA,
        "status": "passed" if passed == len(results) else "failed",
        "execution_mode": "deterministic_mock",
        "started_at": started_at,
        "finished_at": _utc_now(),
        "source_identity": identity or source_identity(repo_root),
        "summary": {
            "scenario_count": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "categories": category_results,
        },
        "scenarios": results,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the deterministic OA semantic regression matrix.",
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = build_semantic_regression_report(
        repo_root=args.repo_root.resolve(),
        timeout_seconds=max(args.timeout_seconds, 1),
    )
    write_report(args.output.resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
