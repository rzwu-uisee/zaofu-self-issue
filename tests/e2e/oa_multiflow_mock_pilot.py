#!/usr/bin/env python3
"""Run isolated deterministic evidence for the four workflow families."""

from __future__ import annotations

import argparse
import hashlib
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
REPORT_SCHEMA = "oa-multiflow-mock-pilot-report.v1"


@dataclass(frozen=True)
class FlowCase:
    flow_kind: str
    coverage: str
    node_id: str


FLOW_CASES = (
    FlowCase(
        flow_kind="prd",
        coverage="semantic_control_full_checkpoint_chain",
        node_id=(
            "tests/e2e/test_orchestrator_agent_semantic_control_mock_e2e.py::"
            "test_full_prd_semantic_control_run_is_replay_safe_and_auditable"
        ),
    ),
    FlowCase(
        flow_kind="issue",
        coverage="semantic_control_plan_checkpoint_and_handoff",
        node_id=(
            "tests/e2e/test_orchestrator_agent_semantic_control_mock_e2e.py::"
            "test_scoped_product_flow_task_map_runs_plan_checkpoint[issue]"
        ),
    ),
    FlowCase(
        flow_kind="refactor",
        coverage="semantic_control_plan_checkpoint_and_handoff",
        node_id=(
            "tests/e2e/test_orchestrator_agent_semantic_control_mock_e2e.py::"
            "test_scoped_product_flow_task_map_runs_plan_checkpoint[refactor]"
        ),
    ),
    FlowCase(
        flow_kind="general",
        coverage="generic_replan_closeout_restart",
        node_id=(
            "tests/e2e/test_generic_workflow_complex_mock_e2e.py::"
            "test_generic_workflow_replans_once_and_closes_after_restart"
        ),
    ),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tail(value: str, *, limit: int = 4000) -> str:
    return value[-limit:]


def _git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout


def _git_bytes(repo_root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        timeout=30,
    )
    return result.stdout


def _untracked_digest(repo_root: Path) -> tuple[str, int]:
    names = [
        name
        for name in _git_bytes(
            repo_root,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        ).split(b"\0")
        if name
    ]
    digest = hashlib.sha256()
    count = 0
    for encoded_name in sorted(names):
        path = repo_root / encoded_name.decode(errors="surrogateescape")
        digest.update(encoded_name)
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(path.readlink().as_posix().encode())
        elif path.is_file():
            digest.update(path.read_bytes())
        else:
            digest.update(b"<missing-or-special>")
        digest.update(b"\0")
        count += 1
    return digest.hexdigest(), count


def source_identity(repo_root: Path) -> dict[str, Any]:
    status = _git(repo_root, "status", "--porcelain=v1")
    diff = _git(repo_root, "diff", "--binary", "HEAD")
    untracked_sha256, untracked_file_count = _untracked_digest(repo_root)
    worktree = hashlib.sha256()
    worktree.update(status.encode())
    worktree.update(diff.encode())
    worktree.update(untracked_sha256.encode())
    return {
        "head_commit": _git(repo_root, "rev-parse", "HEAD").strip(),
        "dirty": bool(status.strip()),
        "status_sha256": hashlib.sha256(status.encode()).hexdigest(),
        "tracked_diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
        "untracked_sha256": untracked_sha256,
        "untracked_file_count": untracked_file_count,
        "worktree_sha256": worktree.hexdigest(),
    }


def execute_flow_case(
    repo_root: Path,
    case: FlowCase,
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
        status = "passed" if result.returncode == 0 else "failed"
        return {
            **asdict(case),
            "status": status,
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


def build_multiflow_report(
    *,
    repo_root: Path,
    timeout_seconds: int,
    cases: Sequence[FlowCase] = FLOW_CASES,
    runner: Callable[[Path, FlowCase, int], dict[str, Any]] = execute_flow_case,
    identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started_at = _utc_now()
    results = [runner(repo_root, case, timeout_seconds) for case in cases]
    passed = sum(row["status"] == "passed" for row in results)
    report_status = "passed" if passed == len(results) else "failed"
    return {
        "schema_version": REPORT_SCHEMA,
        "status": report_status,
        "execution_mode": "deterministic_mock",
        "started_at": started_at,
        "finished_at": _utc_now(),
        "source_identity": identity or source_identity(repo_root),
        "summary": {
            "flow_count": len(results),
            "passed": passed,
            "failed": len(results) - passed,
        },
        "flows": results,
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the isolated OA four-flow deterministic pilot.",
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = build_multiflow_report(
        repo_root=args.repo_root.resolve(),
        timeout_seconds=max(args.timeout_seconds, 1),
    )
    write_report(args.output.resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
