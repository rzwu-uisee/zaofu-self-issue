#!/usr/bin/env python3
"""Run serial, preregistered v3/v4 Task Pipeline provider canaries."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from zf.core.metrics.task_pipeline_rollout import (  # noqa: E402
    build_task_pipeline_ab_report,
    build_task_pipeline_canary_manifest,
)


def run_canary(
    *,
    repo_root: Path,
    registration: Mapping[str, Any],
    output_root: Path,
    command_template: list[str],
    dry_run: bool = False,
) -> dict[str, Any]:
    """Execute one process per frozen manifest entry without a shell."""

    repo_root = Path(repo_root).resolve()
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    manifest = build_task_pipeline_canary_manifest(
        registration,
        repo_root=repo_root,
        output_root=output_root,
    )
    _write_json(output_root / "manifest.json", manifest)
    if dry_run:
        return {
            "manifest": manifest,
            "report": None,
            "cleanup_residuals": [],
        }
    _verify_source(repo_root, str(registration["source_commit"]))

    arm_samples: dict[str, list[dict[str, Any]]] = {"v3": [], "v4": []}
    cleanup_residuals = []
    timeout = int((registration.get("budget") or {}).get("timeout_seconds") or 3600)
    for run in manifest["runs"]:
        worktree = Path(run["worktree"])
        result_path = Path(run["result_path"])
        state_dir = Path(run["state_dir"])
        worktree.parent.mkdir(parents=True, exist_ok=True)
        state_dir.mkdir(parents=True, exist_ok=True)
        _git(
            repo_root,
            "worktree",
            "add",
            "--detach",
            str(worktree),
            str(run["source_commit"]),
        )
        result = _run_one(
            run,
            command_template=command_template,
            result_path=result_path,
            timeout=timeout,
        )
        clean = _git_output(worktree, "status", "--porcelain") == ""
        if clean:
            _git(repo_root, "worktree", "remove", str(worktree))
        else:
            result["status"] = "failed"
            result["terminal_closed"] = False
            result["dirty_worktree"] = True
            cleanup_residuals.append(str(worktree))
        arm_samples[str(run["arm"])].append(result)

    arm_reports = {
        arm: {
            "execution_profile": (
                "stage_barrier" if arm == "v3" else "task_pipeline_pool"
            ),
            "samples": arm_samples[arm],
        }
        for arm in ("v3", "v4")
    }
    report = build_task_pipeline_ab_report(registration, arm_reports)
    report["cleanup_residuals"] = cleanup_residuals
    if cleanup_residuals:
        report["status"] = "hold"
        report["rollout_decision"] = "HOLD"
        report["winner"] = None
        report["hold_reasons"] = sorted({
            *report["hold_reasons"],
            "dirty_worktree_residual",
        })
    _write_json(output_root / "report.json", report)
    return {
        "manifest": manifest,
        "report": report,
        "cleanup_residuals": cleanup_residuals,
    }


def _run_one(
    run: Mapping[str, Any],
    *,
    command_template: list[str],
    result_path: Path,
    timeout: int,
) -> dict[str, Any]:
    values = {key: str(value) for key, value in run.items()}
    command = [part.format_map(values) for part in command_template]
    env = dict(os.environ)
    env.update({
        "ZF_TASK_PIPELINE_CANARY_ARM": str(run["arm"]),
        "ZF_TASK_PIPELINE_EXECUTION_PROFILE": str(run["execution_profile"]),
        "ZF_TASK_PIPELINE_SAMPLE_ID": str(run["sample_id"]),
        "ZF_STATE_DIR": str(run["state_dir"]),
        "ZF_CANARY_RESULT_PATH": str(result_path),
    })
    completed = subprocess.run(
        command,
        cwd=run["worktree"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    log_path = result_path.with_suffix(".log")
    log_path.write_text(
        f"exit_code={completed.returncode}\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}\n",
        encoding="utf-8",
    )
    if completed.returncode != 0:
        return _failed_sample(run, f"provider command exited {completed.returncode}")
    try:
        value = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _failed_sample(run, f"result unavailable: {exc}")
    if not isinstance(value, dict):
        return _failed_sample(run, "result root is not an object")
    value.setdefault("sample_id", str(run["sample_id"]))
    return value


def _failed_sample(run: Mapping[str, Any], reason: str) -> dict[str, Any]:
    return {
        "sample_id": str(run["sample_id"]),
        "status": "failed",
        "terminal_closed": False,
        "reason": reason,
        "metrics": {},
    }


def _verify_source(repo_root: Path, source_commit: str) -> None:
    resolved = _git_output(
        repo_root,
        "rev-parse",
        "--verify",
        f"{source_commit}^{{commit}}",
    )
    if resolved != source_commit:
        raise RuntimeError(
            f"registered source {source_commit} resolved to {resolved}"
        )


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _git_output(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--command-json",
        help=(
            "JSON argv template; supports {worktree}, {state_dir}, {arm}, "
            "{execution_profile}, {sample_id}, and {result_path}"
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    registration = json.loads(args.registration.read_text(encoding="utf-8"))
    command_template = json.loads(args.command_json or "[]")
    if not args.dry_run and not (
        isinstance(command_template, list)
        and command_template
        and all(isinstance(item, str) for item in command_template)
    ):
        raise SystemExit("--command-json must be a non-empty JSON argv list")
    result = run_canary(
        repo_root=args.repo,
        registration=registration,
        output_root=args.output_dir,
        command_template=command_template,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
