from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.task_pipeline_v4_canary import run_canary
from zf.core.metrics.task_pipeline_rollout import (
    TASK_PIPELINE_AB_REPORT_SCHEMA,
    TASK_PIPELINE_CANARY_MANIFEST_SCHEMA,
    TaskPipelineRolloutError,
    build_task_pipeline_ab_report,
    build_task_pipeline_canary_manifest,
)


def _registration() -> dict:
    return {
        "schema_version": "task-pipeline-ab-registration.v1",
        "experiment_id": "tpv4-canary-1",
        "source_commit": "a" * 40,
        "plan_package_digest": "b" * 64,
        "task_map_digest": "c" * 64,
        "task_contract_digest": "d" * 64,
        "prompt_digest": "e" * 64,
        "normalized_config_digest": "f" * 64,
        "provider_identity": {
            "backend": "codex",
            "model": "gpt-test",
            "comp_hash": "provider-build-1",
            "multi_agent_version": "v2",
            "reasoning_effort": "high",
        },
        "budget": {
            "timeout_seconds": 600,
            "token_cap": 100_000,
            "cost_usd": 10,
        },
        "arms": {
            "v3": {"execution_profile": "stage_barrier"},
            "v4": {"execution_profile": "task_pipeline_pool"},
        },
        "samples": [
            {
                "sample_id": "healthy",
                "expected_conditional_roles": [],
                "expected_recovery_turns": 0,
            },
            {
                "sample_id": "lease-fault",
                "expected_conditional_roles": ["run-manager"],
                "expected_recovery_turns": 1,
            },
        ],
        "thresholds": {
            "min_latency_gain_seconds": 1,
            "min_utilization_gain": 0.01,
            "max_regression": {},
        },
    }


def _arm_reports(registration: dict) -> dict:
    identity = {
        field: registration[field]
        for field in (
            "source_commit",
            "plan_package_digest",
            "task_map_digest",
            "task_contract_digest",
            "prompt_digest",
            "normalized_config_digest",
            "provider_identity",
            "budget",
        )
    }
    reports = {}
    for arm, profile in (
        ("v3", "stage_barrier"),
        ("v4", "task_pipeline_pool"),
    ):
        samples = []
        for sample in registration["samples"]:
            samples.append({
                "sample_id": sample["sample_id"],
                **identity,
                "actual_provider_identity": registration["provider_identity"],
                "conditional_roles": sample["expected_conditional_roles"],
                "recovery_turns": sample["expected_recovery_turns"],
                "status": "passed",
                "terminal_closed": True,
                "metrics": {
                    "latency_seconds": 100 if arm == "v3" else 80,
                    "utilization": 0.5 if arm == "v3" else 0.7,
                    "intervention_quality": 0.8,
                    "rework_count": 1,
                    "conflict_count": 0,
                    "cost_usd": 2,
                    "false_completion_count": 0,
                    "terminal_residual_count": 0,
                },
            })
        reports[arm] = {
            "execution_profile": profile,
            "samples": samples,
        }
    return reports


def test_canary_manifest_is_serial_and_changes_only_execution_profile(
    tmp_path: Path,
) -> None:
    registration = _registration()
    manifest = build_task_pipeline_canary_manifest(
        registration,
        repo_root=tmp_path,
        output_root=tmp_path / "output",
    )

    assert manifest["schema_version"] == TASK_PIPELINE_CANARY_MANIFEST_SCHEMA
    assert manifest["serial_execution"] is True
    assert manifest["only_preregistered_variable"] == "execution_profile"
    assert [run["arm"] for run in manifest["runs"]] == [
        "v3",
        "v4",
        "v3",
        "v4",
    ]
    assert {run["source_commit"] for run in manifest["runs"]} == {"a" * 40}


def test_fair_ab_report_allows_canary_expansion_but_not_default_enable() -> None:
    registration = _registration()
    report = build_task_pipeline_ab_report(
        registration,
        _arm_reports(registration),
    )

    assert report["schema_version"] == TASK_PIPELINE_AB_REPORT_SCHEMA
    assert report["status"] == "passed"
    assert report["rollout_decision"] == "CANARY_EXPAND"
    assert report["winner"] == "v4"
    assert report["v4_default_enabled"] is False
    assert all(report["fairness"].values())
    assert report["terminal_closed_both"] is True
    assert report["metric_gate"]["latency_gain_seconds"] == 20
    assert report["metric_gate"]["utilization_gain"] == pytest.approx(0.2)
    assert report["metrics"]["v4"]["latency_seconds"]["variance"] == 0


def test_provider_or_conditional_role_drift_forces_hold_without_winner() -> None:
    registration = _registration()
    arms = _arm_reports(registration)
    arms["v4"]["samples"][0]["actual_provider_identity"] = {
        **registration["provider_identity"],
        "comp_hash": "provider-build-2",
    }
    arms["v4"]["samples"][1]["conditional_roles"] = [
        "run-manager",
        "autoresearch",
    ]

    report = build_task_pipeline_ab_report(registration, arms)

    assert report["status"] == "hold"
    assert report["rollout_decision"] == "HOLD"
    assert report["winner"] is None
    assert report["fairness"]["actual_provider_identity"] is False
    assert report["fairness"]["conditional_roles"] is False
    assert "fairness_mismatch" in report["hold_reasons"]


def test_terminal_or_quality_regression_forces_hold() -> None:
    registration = _registration()
    arms = _arm_reports(registration)
    arms["v4"]["samples"][0]["terminal_closed"] = False
    arms["v4"]["samples"][1]["metrics"]["false_completion_count"] = 1

    report = build_task_pipeline_ab_report(registration, arms)

    assert report["rollout_decision"] == "HOLD"
    assert report["terminal_closed_both"] is False
    assert "terminal_not_closed" in report["hold_reasons"]
    assert "false_completion_count_regressed" in report["hold_reasons"]


def test_dry_run_entrypoint_writes_repeatable_manifest(tmp_path: Path) -> None:
    result = run_canary(
        repo_root=tmp_path,
        registration=_registration(),
        output_root=tmp_path / "canary",
        command_template=[],
        dry_run=True,
    )

    assert result["report"] is None
    assert (tmp_path / "canary" / "manifest.json").is_file()
    assert not list((tmp_path / "canary").glob("*/worktree"))


def test_registration_requires_multiple_preregistered_samples() -> None:
    registration = _registration()
    registration["samples"] = registration["samples"][:1]

    with pytest.raises(TaskPipelineRolloutError, match="at least two"):
        build_task_pipeline_ab_report(registration, {})
