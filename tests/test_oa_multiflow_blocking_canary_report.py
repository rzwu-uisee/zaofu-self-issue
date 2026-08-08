from __future__ import annotations

import json
from pathlib import Path

from tests.e2e.scripts.oa_multiflow_blocking_canary_report import (
    build_canary_report,
)


COMMIT = "a" * 40


def _ab(flow: str) -> dict:
    arms = []
    for policy in ("shadow", "blocking"):
        arms.append({
            "policy": policy,
            "status": "passed",
            "terminal": "run.goal.completed",
            "duration_seconds": 10,
            "usage": {"total_tokens": 1000, "total_usd": 1.0},
            "oa_metrics": {"total_tokens": 100, "cost_usd": 0.1},
            "context_complete": True,
            "first_verify_gate_passed": True,
        })
    return {
        "schema_version": "oa-full-workflow-ab-report.v2",
        "status": "passed",
        "flow_kind": flow,
        "source_identity": {"zaofu_commit": COMMIT, "zaofu_clean": True},
        "fairness": {"source": True, "policy_only": True},
        "arms": arms,
        "comparison": {
            "policy_behavior_observed": True,
            "plan_gap_escape": {
                "count": None,
                "evidence_status": "not_classifiable_from_current_events",
            },
        },
        "rollout_decision": "insufficient_evidence",
    }


def _general() -> dict:
    return {
        "status": "passed",
        "source_identity": {"head_commit": COMMIT, "dirty": False},
        "terminal_event_id": "evt-terminal",
        "dossier_status": "ready",
        "required_artifact_refs": ["artifacts/report.json"],
        "effective_config_digest": "b" * 64,
        "run_contract_digest": "c" * 64,
        "stage_graph": [
            "scope",
            "collect-a",
            "collect-b",
            "synthesize",
            "verify",
        ],
        "provider_session_id": "provider-session",
        "prompt_sha256": "d" * 64,
        "backend": "codex",
        "model": "gpt-test",
        "reasoning_effort": "low",
        "usage": {
            "input_tokens": 400,
            "cached_input_tokens": 100,
            "output_tokens": 100,
            "cost_usd": 0.5,
        },
        "duration_seconds": 5,
        "oa": {
            "checkpoint_requested": 0,
            "decision_observed": 0,
            "decision_applied": 0,
            "provider_turns": 0,
        },
        "cleaned": True,
    }


def _write(tmp_path: Path, name: str, value: dict) -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_canary_report_closes_three_pairs_and_general_control(
    tmp_path: Path,
) -> None:
    products = {
        flow: _write(tmp_path, flow, _ab(flow))
        for flow in ("prd", "issue", "refactor")
    }
    general = _write(tmp_path, "general", _general())

    report = build_canary_report(products, general, wall_seconds=25)

    assert report["status"] == "passed"
    assert report["summary"] == {
        "flow_families": 4,
        "product_ab_arms": 6,
        "general_negative_controls": 1,
        "passed_flow_families": 4,
        "failed_flow_families": 0,
    }
    assert report["usage"] == {
        "total_tokens": 6500,
        "total_usd": 6.5,
        "oa_total_tokens": 600,
        "oa_total_usd": 0.6,
    }
    assert report["rollout_decision"] == "canary_pass"
    assert report["production_rollout_decision"] == "insufficient_evidence"
    assert report["timing"]["observed_wall_seconds"] == 25
    assert report["timing"]["execution_mode"] == "serial"


def test_canary_report_records_explicit_parallel_stress_mode(tmp_path: Path) -> None:
    products = {
        flow: _write(tmp_path, flow, _ab(flow))
        for flow in ("prd", "issue", "refactor")
    }
    general = _write(tmp_path, "general", _general())

    report = build_canary_report(
        products,
        general,
        execution_mode="parallel",
    )

    assert report["status"] == "passed"
    assert report["timing"]["execution_mode"] == "parallel"


def test_canary_report_holds_when_general_invokes_oa(tmp_path: Path) -> None:
    products = {
        flow: _write(tmp_path, flow, _ab(flow))
        for flow in ("prd", "issue", "refactor")
    }
    general_value = _general()
    general_value["oa"]["checkpoint_requested"] = 1
    general = _write(tmp_path, "general", general_value)

    report = build_canary_report(products, general)

    assert report["status"] == "failed"
    assert report["rollout_decision"] == "hold"
    assert report["general_negative_control"]["checks"][
        "oa_checkpoint_zero"
    ] is False
