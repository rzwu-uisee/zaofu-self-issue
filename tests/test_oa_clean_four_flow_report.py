from __future__ import annotations

import json
from pathlib import Path

from tests.e2e.scripts.oa_clean_four_flow_report import (
    build_four_flow_report,
)


COMMIT = "a" * 40


def _product_run(flow: str, *, status: str = "passed") -> dict:
    passed = status == "passed"
    return {
        "name": flow,
        "status": status,
        "terminal": "run.goal.completed" if passed else "run.goal.blocked",
        "state_dir": f"/tmp/{flow}/.zf",
        "source_identity": {
            "zaofu_commit": COMMIT,
            "zaofu_clean": True,
            "product_source_commit": "b" * 40,
            "product_head_commit": "c" * 40,
        },
        "prompt_sha256": "d" * 64,
        "config": {"rendered_sha256": "e" * 64},
        "provider": {"backend": "codex"},
        "budget": {"timeout_seconds": 600},
        "duration_seconds": 12,
        "usage": {"total_tokens": 1000, "total_usd": 1.0},
        "attempts": {"workflow_operation_requested": 3},
        "terminal_delivery": {"status": "passed" if passed else "failed"},
        "context_handoff": {
            "checks": {
                "plan_artifact_package": passed,
                "impl_verify_exact_handoff": passed,
            },
        },
        "failure_classification": "none" if passed else "terminal_delivery",
    }


def _write_reports(
    tmp_path: Path,
    *,
    failed_flow: str = "",
) -> tuple[Path, Path]:
    product = tmp_path / "product.json"
    product.write_text(json.dumps({
        "runs": [
            _product_run(
                flow,
                status="failed" if flow == failed_flow else "passed",
            )
            for flow in ("prd", "issue", "refactor")
        ],
    }), encoding="utf-8")
    general = tmp_path / "general.json"
    general.write_text(json.dumps({
        "status": "passed",
        "execution_mode": "hybrid_real_provider",
        "source_identity": {"head_commit": COMMIT, "dirty": False},
        "workflow_run_id": "run-general",
        "terminal_event_id": "evt-general-terminal",
        "dossier_status": "ready",
        "required_artifact_refs": ["artifacts/report.json"],
        "effective_config_digest": "f" * 64,
        "run_contract_digest": "1" * 64,
        "completion_profile": "artifact_delivery",
        "stage_graph": [
            "scope",
            "collect-a",
            "collect-b",
            "synthesize",
            "verify",
        ],
        "provider_session_id": "thread-general",
        "prompt_sha256": "2" * 64,
        "backend": "codex",
        "model": "gpt-test",
        "reasoning_effort": "low",
        "budget": {"provider_turns": 1},
        "duration_seconds": 3.0,
        "usage": {"total_tokens": 250},
        "semantic_replan_count": 1,
        "protocol_repair_count": 1,
        "cleaned": True,
    }), encoding="utf-8")
    return product, general


def test_clean_four_flow_report_preserves_per_flow_evidence(
    tmp_path: Path,
) -> None:
    product, general = _write_reports(tmp_path)

    report = build_four_flow_report(product, general)

    assert report["status"] == "passed"
    assert report["source_commit"] == COMMIT
    assert report["source_identity_closed"] is True
    assert report["summary"] == {"flow_count": 4, "passed": 4, "failed": 0}
    assert [run["flow_kind"] for run in report["runs"]] == [
        "prd",
        "issue",
        "refactor",
        "general",
    ]
    assert report["runs"][-1]["execution_mode"] == "hybrid_real_provider"


def test_clean_four_flow_report_does_not_overwrite_failed_attempt(
    tmp_path: Path,
) -> None:
    product, general = _write_reports(tmp_path, failed_flow="issue")

    report = build_four_flow_report(product, general)

    assert report["status"] == "failed"
    assert report["summary"] == {"flow_count": 4, "passed": 3, "failed": 1}
    issue = next(run for run in report["runs"] if run["flow_kind"] == "issue")
    assert issue["failure_classification"] == "terminal_delivery"
