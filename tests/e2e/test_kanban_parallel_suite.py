from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

from tests.e2e.kanban_parallel_suite import (
    CommandResult,
    SUITE_SCHEMA,
    run_suite,
    validate_suite_manifest,
)


ROOT = Path(__file__).resolve().parents[2]


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _manifest(tmp_path: Path) -> dict:
    implementation = tmp_path / "implementation"
    implementation.mkdir()
    _git(implementation, "init", "-q")
    _git(implementation, "config", "user.email", "test@example.com")
    _git(implementation, "config", "user.name", "Test User")
    (implementation / "README.md").write_text("suite\n", encoding="utf-8")
    _git(implementation, "add", "README.md")
    _git(implementation, "commit", "-q", "-m", "init")
    commit = _git(implementation, "rev-parse", "HEAD")
    cases = []
    configs = {
        "general": ROOT / "examples/prod/controller/general-workflow-v3.yaml",
        "issue": ROOT / "examples/prod/controller/issue-task-pipeline-v4-canary.yaml",
        "prd": ROOT / "examples/prod/controller/prd-task-pipeline-v4-canary.yaml",
        "refactor": ROOT / "examples/prod/controller/refactor-task-pipeline-v4-canary.yaml",
    }
    profiles = {
        "general": "general-workflow-v3",
        "issue": "issue-flow-v4-task-pipeline",
        "prd": "prd-flow-v4-task-pipeline",
        "refactor": "refactor-flow-v4-task-pipeline",
    }
    for family in ("general", "issue", "prd", "refactor"):
        project = tmp_path / family
        project.mkdir()
        cases.append({
            "case_id": f"case-{family}",
            "family": family,
            "project_root": str(project),
            "state_dir": str(project / ".zf"),
            "config_path": str(configs[family]),
            "implementation_commit": commit,
            "profile": profiles[family],
            "env": {"ZF_TASK_PIPELINE_MODE": "blocking"}
            if family in {"issue", "prd", "refactor"} else {},
            "driver_argv": ["driver", family],
            "observer_argv": ["observer", family],
            "recover_argv": ["recover", family],
            "simulation_done_argv": [
                "zf", "emit", "simulation.done", family,
            ],
            "cleanup_argv": ["cleanup", family],
        })
    return {
        "schema_version": SUITE_SCHEMA,
        "suite_id": "suite-parallel-1",
        "implementation_root": str(implementation),
        "implementation_commit": commit,
        "cases": cases,
    }


def test_four_isolated_kanban_cases_reach_terminal_and_cleanup(tmp_path):
    manifest = _manifest(tmp_path)
    calls: list[tuple[str, str]] = []
    lock = threading.Lock()

    def runner(argv, cwd, timeout, env):
        with lock:
            calls.append((argv[0], env["ZF_E2E_FAMILY"]))
        if argv[0] == "observer":
            return CommandResult(
                0,
                "passed",
                stdout=json.dumps({
                    "status": "passed",
                    "workflow_run_id": f"run-{env['ZF_E2E_FAMILY']}",
                }),
            )
        return CommandResult(0, "passed")

    report = run_suite(manifest, command_runner=runner)

    assert report["status"] == "passed"
    assert report["summary"] == {"total": 4, "passed": 4, "failed": 0}
    assert sum(phase == "driver" for phase, _family in calls) == 4
    assert sum(phase == "cleanup" for phase, _family in calls) == 4
    assert sum(phase == "zf" for phase, _family in calls) == 4


def test_failed_observer_gets_one_recover_then_reobserves(tmp_path):
    manifest = _manifest(tmp_path)
    attempts: dict[str, int] = {}
    lock = threading.Lock()

    def runner(argv, cwd, timeout, env):
        family = env["ZF_E2E_FAMILY"]
        if argv[0] != "observer":
            return CommandResult(0, "passed")
        with lock:
            attempts[family] = attempts.get(family, 0) + 1
            attempt = attempts[family]
        if family == "issue" and attempt == 1:
            return CommandResult(
                20,
                "failed",
                stdout=json.dumps({"status": "failed", "reason": "blocked"}),
            )
        return CommandResult(
            0,
            "passed",
            stdout=json.dumps({
                "status": "passed",
                "workflow_run_id": f"run-{family}",
            }),
        )

    report = run_suite(manifest, command_runner=runner)

    assert report["status"] == "passed"
    issue = next(row for row in report["cases"] if row["family"] == "issue")
    assert [step["phase"] for step in issue["steps"]] == [
        "driver",
        "observer",
        "recover",
        "observer_after_recover",
        "simulation_done",
        "cleanup",
        "config_integrity",
    ]


def test_truncated_observer_stdout_uses_terminal_evidence_without_recover(
    tmp_path,
):
    manifest = _manifest(tmp_path)
    calls: list[tuple[str, str]] = []
    lock = threading.Lock()
    for case in manifest["cases"]:
        evidence_dir = tmp_path / case["family"] / "terminal"
        case["observer_argv"] = [
            "observer",
            case["family"],
            "--evidence-dir",
            str(evidence_dir),
        ]

    def runner(argv, cwd, timeout, env):
        family = env["ZF_E2E_FAMILY"]
        with lock:
            calls.append((argv[0], family))
        if argv[0] == "observer":
            evidence_dir = Path(argv[argv.index("--evidence-dir") + 1])
            evidence_dir.mkdir(parents=True, exist_ok=True)
            (evidence_dir / "terminal-result.json").write_text(
                json.dumps({
                    "status": "passed",
                    "workflow_run_id": f"run-{family}",
                }),
                encoding="utf-8",
            )
            return CommandResult(
                0,
                "passed",
                stdout='truncated-middle-of-large-json"}',
            )
        return CommandResult(0, "passed")

    report = run_suite(manifest, command_runner=runner)

    assert report["status"] == "passed"
    assert report["summary"] == {"total": 4, "passed": 4, "failed": 0}
    assert not any(phase == "recover" for phase, _family in calls)


def test_manifest_rejects_shared_state_and_non_v4_profile(tmp_path):
    manifest = _manifest(tmp_path)
    manifest["cases"][1]["state_dir"] = manifest["cases"][0]["state_dir"]
    manifest["cases"][2]["profile"] = "legacy"

    errors = validate_suite_manifest(manifest)

    assert any("isolated state_dir" in error for error in errors)
    assert any("profile does not match compiled config" in error for error in errors)


def test_driver_failure_still_records_closeout_and_cleanup(tmp_path):
    manifest = _manifest(tmp_path)

    def runner(argv, cwd, timeout, env):
        if argv[0] == "driver" and env["ZF_E2E_FAMILY"] == "issue":
            return CommandResult(2, "failed", reason="driver rejected")
        return CommandResult(0, "passed", stdout=json.dumps({
            "status": "passed",
            "workflow_run_id": f"run-{env['ZF_E2E_FAMILY']}",
        }))

    report = run_suite(manifest, command_runner=runner)

    assert report["status"] == "failed"
    issue = next(row for row in report["cases"] if row["family"] == "issue")
    assert issue["reason"] == "driver_failed"
    assert [step["phase"] for step in issue["steps"]] == [
        "driver",
        "simulation_done",
        "cleanup",
        "config_integrity",
    ]
