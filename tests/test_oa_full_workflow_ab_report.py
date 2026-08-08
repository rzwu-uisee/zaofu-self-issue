from __future__ import annotations

import json
from pathlib import Path

import yaml

from tests.e2e.scripts.oa_full_workflow_ab_report import build_ab_report


def _write_arm(
    root: Path,
    *,
    policy: str,
    flow_kind: str = "prd",
    extra_config: dict | None = None,
) -> Path:
    state_dir = root / "product" / ".zf"
    state_dir.mkdir(parents=True)
    flow_policy = {
        "mode": "semantic_control",
        "checkpoints": ["plan_candidate"],
        "checkpoint_policies": {"plan_candidate": policy},
        "shadow_sample_percent": 100,
    }
    if policy == "blocking":
        flow_policy["pilot_id"] = "full-workflow-ab"
    config = {
        "project": {"name": "ab-product", "state_dir": ".zf"},
        "workflow": {
            "orchestration": {
                "mode": "exception_advisor",
                "flow_policies": {flow_kind: flow_policy},
            },
        },
    }
    if extra_config:
        config.update(extra_config)
    (state_dir.parent / "zf.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )
    observed = 1 if policy == "shadow" else 0
    applied = 1 if policy == "blocking" else 0
    run = {
        "name": flow_kind,
        "state_dir": str(state_dir),
        "source_identity": {
            "zaofu_commit": "a" * 40,
            "zaofu_clean": True,
            "product_source_commit": "b" * 40,
            "product_source_tree": "f" * 40,
            "product_head_commit": "c" * 40,
            "baseline_manifest": (
                {
                    "schema_version": "product-pulse-golden-baseline.v1",
                    "flow_kind": flow_kind,
                    "git_blobs": {"app/server.mjs": "1" * 40},
                }
                if flow_kind in {"issue", "refactor"}
                else {}
            ),
            "baseline_manifest_sha256": (
                "2" * 64 if flow_kind in {"issue", "refactor"} else ""
            ),
        },
        "prompt_sha256": "d" * 64,
        "config": {"template_sha256": "e" * 64},
        "provider": {
            "backend": "codex",
            "model": "gpt-test",
            "reasoning_effort": "low",
            "actual_identity": {
                "status": "ready",
                "roles": [
                    {
                        "role_instance": "planner-0",
                        "model": "gpt-test",
                        "comp_hash": "build-1",
                        "multi_agent_version": "v2",
                        "reasoning_effort": "low",
                    }
                ],
                "models": ["gpt-test"],
                "comp_hashes": ["build-1"],
                "multi_agent_versions": ["v2"],
                "reasoning_efforts": ["low"],
            },
        },
        "budget": {"timeout_seconds": 600, "global_cost_budget_usd": 20},
        "status": "passed",
        "terminal": "run.goal.completed",
        "duration_seconds": 10 if policy == "shadow" else 12,
        "usage": {
            "total_tokens": 1000 if policy == "shadow" else 1100,
            "total_usd": 1.0 if policy == "shadow" else 1.1,
        },
        "counts": {
            "orchestrator.semantic.decision.observed": observed,
            "orchestrator.semantic.decision.applied": applied,
            "plan.rejected": 0,
            "orchestrator.semantic.rework.requested": 0,
            "run.goal.blocked": 0,
            "test.passed": 1,
            "test.failed": 0,
        },
        "context_handoff": {
            "checks": {
                "plan_artifact_package": True,
                "impl_verify_exact_handoff": True,
            },
        },
    }
    report_path = root / "report.json"
    report_path.write_text(
        json.dumps({"runs": [run]}) + "\n",
        encoding="utf-8",
    )
    return report_path


def test_full_workflow_ab_requires_fair_terminal_runs(tmp_path: Path) -> None:
    shadow = _write_arm(tmp_path / "shadow", policy="shadow")
    blocking = _write_arm(tmp_path / "blocking", policy="blocking")

    report = build_ab_report(shadow, blocking)

    assert report["status"] == "passed"
    assert report["schema_version"] == "oa-full-workflow-ab-report.v2"
    assert report["flow_kind"] == "prd"
    assert all(report["fairness"].values())
    assert report["comparison"]["terminal_closed_both"] is True
    assert report["comparison"]["policy_behavior_observed"] is True
    assert report["comparison"]["blocking_minus_shadow"] == {
        "duration_seconds": 2.0,
        "total_tokens": 100.0,
        "cost_usd": 0.1,
        "plan_revisions": 0,
        "targeted_reworks": 0,
    }
    assert report["winner"] is None
    assert report["rollout_decision"] == "insufficient_evidence"


def test_full_workflow_ab_supports_issue_and_refactor_policy_paths(
    tmp_path: Path,
) -> None:
    for flow_kind in ("issue", "refactor"):
        shadow = _write_arm(
            tmp_path / flow_kind / "shadow",
            policy="shadow",
            flow_kind=flow_kind,
        )
        blocking = _write_arm(
            tmp_path / flow_kind / "blocking",
            policy="blocking",
            flow_kind=flow_kind,
        )

        report = build_ab_report(
            shadow,
            blocking,
            flow_kind=flow_kind,
        )

        assert report["status"] == "passed"
        assert report["flow_kind"] == flow_kind
        assert report["arms"][0]["first_verify_gate_passed"] is True
        assert report["comparison"]["plan_gap_escape"] == {
            "count": None,
            "evidence_status": "not_classifiable_from_current_events",
        }
        assert all(
            f"flow_policies.{flow_kind}" in path
            for path in report["config_comparison"]["allowed_diff_paths"]
        )


def test_full_workflow_ab_rejects_non_policy_config_drift(
    tmp_path: Path,
) -> None:
    shadow = _write_arm(tmp_path / "shadow", policy="shadow")
    blocking = _write_arm(
        tmp_path / "blocking",
        policy="blocking",
        extra_config={"unexpected": {"drift": True}},
    )

    report = build_ab_report(shadow, blocking)

    assert report["status"] == "failed"
    assert report["fairness"]["normalized_config"] is False
    assert report["fairness"]["config_diff_policy_only"] is False
    assert "unexpected" in report["config_comparison"]["actual_diff_paths"]


def test_full_workflow_ab_rejects_actual_provider_or_source_tree_drift(
    tmp_path: Path,
) -> None:
    shadow = _write_arm(tmp_path / "shadow", policy="shadow")
    blocking = _write_arm(tmp_path / "blocking", policy="blocking")
    value = json.loads(blocking.read_text(encoding="utf-8"))
    value["runs"][0]["provider"]["actual_identity"]["roles"][0][
        "comp_hash"
    ] = "build-2"
    value["runs"][0]["source_identity"]["product_source_tree"] = "9" * 40
    blocking.write_text(json.dumps(value) + "\n", encoding="utf-8")

    report = build_ab_report(shadow, blocking)

    assert report["status"] == "failed"
    assert report["fairness"]["provider_actual_identity"] is False
    assert report["fairness"]["product_baseline_tree"] is False


def test_full_workflow_ab_requires_golden_manifest_for_independent_flow(
    tmp_path: Path,
) -> None:
    shadow = _write_arm(
        tmp_path / "shadow",
        policy="shadow",
        flow_kind="issue",
    )
    blocking = _write_arm(
        tmp_path / "blocking",
        policy="blocking",
        flow_kind="issue",
    )
    value = json.loads(blocking.read_text(encoding="utf-8"))
    value["runs"][0]["source_identity"]["baseline_manifest"] = {}
    value["runs"][0]["source_identity"]["baseline_manifest_sha256"] = ""
    blocking.write_text(json.dumps(value) + "\n", encoding="utf-8")

    report = build_ab_report(shadow, blocking, flow_kind="issue")

    assert report["status"] == "failed"
    assert report["fairness"]["golden_baseline_manifest"] is False


def test_full_workflow_ab_emits_hold_for_missing_blocking_arm(
    tmp_path: Path,
) -> None:
    shadow = _write_arm(
        tmp_path / "shadow",
        policy="shadow",
        flow_kind="issue",
    )

    report = build_ab_report(
        shadow,
        tmp_path / "blocking" / "report.json",
        flow_kind="issue",
    )

    assert report["status"] == "failed"
    assert report["rollout_decision"] == "hold"
    assert report["evidence_completeness"] == {
        "shadow_report": True,
        "blocking_report": False,
    }
    assert report["arms"][0]["policy"] == "shadow"
    assert report["arms"][1]["status"] == ""
    assert not all(report["fairness"].values())
