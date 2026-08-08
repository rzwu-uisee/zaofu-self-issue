from __future__ import annotations

from pathlib import Path

from tests.e2e.oa_semantic_regression_mock_pilot import (
    SEMANTIC_CASES,
    build_semantic_regression_report,
)


def _source_identity() -> dict:
    return {
        "head_commit": "a" * 40,
        "dirty": False,
        "status_sha256": "b" * 64,
        "tracked_diff_sha256": "c" * 64,
        "untracked_sha256": "d" * 64,
        "untracked_file_count": 0,
        "worktree_sha256": "e" * 64,
    }


def test_semantic_regression_report_preserves_each_failure(
    tmp_path: Path,
) -> None:
    failed_scenario = "chinese_parallel_collectors"

    def runner(_root: Path, case, _timeout: int):  # noqa: ANN001
        status = "failed" if case.scenario == failed_scenario else "passed"
        return {
            **case.__dict__,
            "status": status,
            "exit_code": 1 if status == "failed" else 0,
            "duration_seconds": 0.01,
            "stdout_tail": "",
            "stderr_tail": "",
        }

    report = build_semantic_regression_report(
        repo_root=tmp_path,
        timeout_seconds=30,
        runner=runner,
        identity=_source_identity(),
    )

    assert report["status"] == "failed"
    assert report["summary"]["scenario_count"] == len(SEMANTIC_CASES)
    assert report["summary"]["failed"] == 1
    assert report["summary"]["categories"]["language_and_lanes"] == {
        "scenario_count": 2,
        "passed": 1,
    }
    failed = [
        row for row in report["scenarios"] if row["status"] == "failed"
    ]
    assert [row["scenario"] for row in failed] == [failed_scenario]
