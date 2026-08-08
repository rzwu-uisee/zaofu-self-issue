#!/usr/bin/env python3
"""Test-only coordinator for parallel isolated Kanban Workflow cases.

This module coordinates external E2E drivers and the existing terminal runner.
It never creates Tasks, emits workflow facts, or acts as a runtime scheduler.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from zf.core.security.redaction import redact_obj
from zf.core.state.atomic_io import atomic_write_text
from zf.core.config.loader import load_config
from zf.runtime.task_pipeline_reconciler import task_pipeline_policy


SUITE_SCHEMA = "kanban-parallel-workflow-suite.v1"
REPORT_SCHEMA = "kanban-parallel-workflow-suite-report.v1"
REQUIRED_FAMILIES = frozenset({"general", "issue", "prd", "refactor"})
REQUIRED_PROFILES = {
    "general": "general-workflow-v3",
    "issue": "issue-flow-v4-task-pipeline",
    "prd": "prd-flow-v4-task-pipeline",
    "refactor": "refactor-flow-v4-task-pipeline",
}


class ParallelSuiteError(ValueError):
    pass


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    status: str
    stdout: str = ""
    stderr: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return redact_obj(asdict(self))


CommandRunner = Callable[
    [Sequence[str], Path, float, Mapping[str, str]],
    CommandResult,
]


def validate_suite_manifest(manifest: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema_version") != SUITE_SCHEMA:
        errors.append("unsupported suite schema")
    suite_id = str(manifest.get("suite_id") or "").strip()
    if not suite_id:
        errors.append("suite_id is required")
    implementation_root = Path(
        str(manifest.get("implementation_root") or "")
    )
    implementation_commit = str(
        manifest.get("implementation_commit") or ""
    ).strip()
    if not implementation_root.is_dir():
        errors.append("implementation_root is missing")
    if not implementation_commit:
        errors.append("implementation_commit is required")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        return [*errors, "cases are required"]

    case_ids: set[str] = set()
    families: set[str] = set()
    project_roots: set[str] = set()
    state_dirs: set[str] = set()
    for index, value in enumerate(cases):
        if not isinstance(value, Mapping):
            errors.append(f"cases[{index}] must be an object")
            continue
        case_id = str(value.get("case_id") or "").strip()
        family = str(value.get("family") or "").strip().lower()
        project_root = str(value.get("project_root") or "").strip()
        state_dir = str(value.get("state_dir") or "").strip()
        config_path = Path(str(value.get("config_path") or ""))
        if not case_id or case_id in case_ids:
            errors.append(f"cases[{index}] has missing or duplicate case_id")
        case_ids.add(case_id)
        if family not in REQUIRED_FAMILIES or family in families:
            errors.append(f"cases[{index}] has unsupported or duplicate family")
        families.add(family)
        if not project_root or project_root in project_roots:
            errors.append(f"cases[{index}] must have an isolated project_root")
        project_roots.add(project_root)
        if not state_dir or state_dir in state_dirs:
            errors.append(f"cases[{index}] must have an isolated state_dir")
        state_dirs.add(state_dir)
        if not Path(project_root).is_dir():
            errors.append(f"cases[{index}] project_root is missing")
        if not config_path.is_file():
            errors.append(f"cases[{index}] config_path is missing")
        else:
            errors.extend(_config_errors(
                value,
                index=index,
                family=family,
                config_path=config_path,
            ))
        if str(value.get("implementation_commit") or "") != implementation_commit:
            errors.append(f"cases[{index}] implementation commit drift")
        for field in (
            "driver_argv",
            "observer_argv",
            "simulation_done_argv",
            "cleanup_argv",
        ):
            if not _argv(value.get(field)):
                errors.append(f"cases[{index}] {field} is required")
        simulation = " ".join(_argv(value.get("simulation_done_argv")))
        if simulation and "simulation.done" not in simulation:
            errors.append(
                f"cases[{index}] simulation_done_argv must emit simulation.done"
            )
    if families != REQUIRED_FAMILIES:
        errors.append(
            "suite must contain exactly general, issue, prd, and refactor"
        )
    return errors


def run_suite(
    manifest: Mapping[str, Any],
    *,
    command_runner: CommandRunner | None = None,
) -> dict[str, Any]:
    errors = validate_suite_manifest(manifest)
    implementation_root = Path(
        str(manifest.get("implementation_root") or "")
    ).resolve()
    expected_commit = str(manifest.get("implementation_commit") or "")
    before = _git_snapshot(implementation_root)
    if before["head"] != expected_commit:
        errors.append("implementation checkout is not at immutable suite commit")
    if before["dirty"]:
        errors.append("implementation checkout is dirty before suite start")
    if errors:
        return _report(manifest, [], errors, before, before)

    runner = command_runner or _run_command
    cases = [dict(value) for value in manifest.get("cases") or []]
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(cases)) as executor:
        futures = {
            executor.submit(
                _run_case,
                case,
                suite_id=str(manifest.get("suite_id") or ""),
                runner=runner,
            ): str(case.get("case_id") or "")
            for case in cases
        }
        for future in as_completed(futures):
            case_id = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                results.append({
                    "case_id": case_id,
                    "status": "failed",
                    "reason": f"coordinator_error:{exc}",
                    "steps": [],
                    "terminal": {},
                })
    results.sort(key=lambda row: str(row.get("case_id") or ""))
    errors.extend(_cross_case_errors(results))
    after = _git_snapshot(implementation_root)
    if after != before:
        errors.append("immutable implementation checkout changed during suite")
    return _report(manifest, results, errors, before, after)


def _run_case(
    case: dict[str, Any],
    *,
    suite_id: str,
    runner: CommandRunner,
) -> dict[str, Any]:
    case_id = str(case.get("case_id") or "")
    project_root = Path(str(case.get("project_root") or "")).resolve()
    config_path = Path(str(case.get("config_path") or "")).resolve()
    config_digest_before = _file_digest(config_path)
    timeout = max(1.0, float(case.get("timeout_seconds") or 1800))
    env = {
        "ZF_E2E_SUITE_ID": suite_id,
        "ZF_E2E_CASE_ID": case_id,
        "ZF_E2E_FAMILY": str(case.get("family") or ""),
    }
    configured_env = case.get("env")
    if isinstance(configured_env, Mapping):
        env.update({
            str(key): str(value)
            for key, value in configured_env.items()
            if str(key).strip()
        })
    steps: list[dict[str, Any]] = []
    terminal: dict[str, Any] = {}
    status = "failed"
    reason = "driver_not_started"
    try:
        prepare = _argv(case.get("prepare_argv"))
        ready = True
        if prepare:
            result = runner(prepare, project_root, timeout, env)
            steps.append({"phase": "prepare", **result.to_dict()})
            if result.returncode != 0:
                ready = False
                reason = "prepare_failed"
        if ready:
            driver = runner(
                _argv(case.get("driver_argv")),
                project_root,
                timeout,
                env,
            )
            steps.append({"phase": "driver", **driver.to_dict()})
            if driver.returncode != 0:
                ready = False
                reason = "driver_failed"
        if ready:
            observer = runner(
                _argv(case.get("observer_argv")),
                project_root,
                timeout,
                env,
            )
            steps.append({"phase": "observer", **observer.to_dict()})
            terminal = _observer_terminal_payload(
                case,
                observer,
                project_root=project_root,
            )
            if observer.returncode != 0 or terminal.get("status") != "passed":
                recover = _argv(case.get("recover_argv"))
                if recover:
                    recovery = runner(recover, project_root, timeout, env)
                    steps.append({"phase": "recover", **recovery.to_dict()})
                    if recovery.returncode == 0:
                        observer = runner(
                            _argv(case.get("observer_argv")),
                            project_root,
                            timeout,
                            env,
                        )
                        steps.append({
                            "phase": "observer_after_recover",
                            **observer.to_dict(),
                        })
                        terminal = _observer_terminal_payload(
                            case,
                            observer,
                            project_root=project_root,
                        )
            if observer.returncode == 0 and terminal.get("status") == "passed":
                status = "passed"
                reason = "terminal_passed"
            else:
                reason = str(
                    terminal.get("reason")
                    or observer.reason
                    or "terminal_failed"
                )
    finally:
        simulation = runner(
            _argv(case.get("simulation_done_argv")),
            project_root,
            timeout,
            env,
        )
        steps.append({"phase": "simulation_done", **simulation.to_dict()})
        cleanup = runner(
            _argv(case.get("cleanup_argv")),
            project_root,
            timeout,
            env,
        )
        steps.append({"phase": "cleanup", **cleanup.to_dict()})
        config_digest_after = _file_digest(config_path)
        config_unchanged = config_digest_after == config_digest_before
        steps.append({
            "phase": "config_integrity",
            "status": "passed" if config_unchanged else "failed",
            "digest_before": config_digest_before,
            "digest_after": config_digest_after,
        })
        if simulation.returncode != 0 or cleanup.returncode != 0:
            status = "failed"
            reason = "simulation_closeout_or_cleanup_failed"
        if not config_unchanged:
            status = "failed"
            reason = "effective_config_changed_during_suite"
    return _case_report(case, steps, terminal, status, reason)


def _run_command(
    argv: Sequence[str],
    cwd: Path,
    timeout: float,
    env_updates: Mapping[str, str],
) -> CommandResult:
    env = os.environ.copy()
    env.update({str(key): str(value) for key, value in env_updates.items()})
    try:
        completed = subprocess.run(
            list(argv),
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return CommandResult(
            returncode=int(completed.returncode),
            status="passed" if completed.returncode == 0 else "failed",
            stdout=_tail(completed.stdout),
            stderr=_tail(completed.stderr),
            reason="" if completed.returncode == 0 else "command_failed",
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            returncode=124,
            status="failed",
            stdout=_tail(exc.stdout),
            stderr=_tail(exc.stderr),
            reason="timeout",
        )
    except OSError as exc:
        return CommandResult(
            returncode=127,
            status="failed",
            stderr=str(exc),
            reason="spawn_failed",
        )


def _terminal_payload(stdout: str) -> dict[str, Any]:
    for line in reversed(str(stdout or "").splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and str(value.get("status") or ""):
            return value
    return {}


def _observer_terminal_payload(
    case: Mapping[str, Any],
    result: CommandResult,
    *,
    project_root: Path,
) -> dict[str, Any]:
    payload = _terminal_payload(result.stdout)
    if payload:
        return payload
    argv = _argv(case.get("observer_argv"))
    try:
        index = argv.index("--evidence-dir")
        evidence_dir = Path(argv[index + 1])
    except (ValueError, IndexError):
        return {}
    if not evidence_dir.is_absolute():
        evidence_dir = project_root / evidence_dir
    names = (
        ("terminal-result.json", "terminal.json")
        if result.returncode == 0
        else ("terminal.json", "terminal-result.json")
    )
    for name in names:
        try:
            value = json.loads((evidence_dir / name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and str(value.get("status") or ""):
            return value
    return {}


def _cross_case_errors(results: Sequence[Mapping[str, Any]]) -> list[str]:
    errors: list[str] = []
    run_ids = [
        str((row.get("terminal") or {}).get("workflow_run_id") or "")
        for row in results
        if isinstance(row.get("terminal"), Mapping)
    ]
    nonempty = [value for value in run_ids if value]
    if len(nonempty) != len(set(nonempty)):
        errors.append("workflow_run_id leaked across isolated cases")
    return errors


def _config_errors(
    case: Mapping[str, Any],
    *,
    index: int,
    family: str,
    config_path: Path,
) -> list[str]:
    errors: list[str] = []
    configured_env = case.get("env")
    configured_env = configured_env if isinstance(configured_env, Mapping) else {}
    previous = {key: os.environ.get(str(key)) for key in configured_env}
    try:
        os.environ.update({
            str(key): str(value) for key, value in configured_env.items()
        })
        config = load_config(config_path)
    except Exception as exc:
        return [f"cases[{index}] config is invalid: {exc}"]
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(str(key), None)
            else:
                os.environ[str(key)] = value
    roles = list(getattr(config, "roles", []) or [])
    orchestrators = [
        role for role in roles
        if str(getattr(role, "name", "") or "") == "orchestrator"
        or str(getattr(role, "instance_id", "") or "") == "orchestrator"
    ]
    if len(orchestrators) != 1 or str(
        getattr(getattr(orchestrators[0], "lifecycle", None), "mode", "")
        if orchestrators else ""
    ) != "resident":
        errors.append(f"cases[{index}] config must enable one resident orchestrator")
    non_orchestrators = [role for role in roles if role not in orchestrators]
    if not non_orchestrators or any(
        str(getattr(getattr(role, "lifecycle", None), "mode", ""))
        != "on_demand"
        for role in non_orchestrators
    ):
        errors.append(f"cases[{index}] config coding agents must be on_demand")
    expected_profile = REQUIRED_PROFILES.get(family, "")
    actual_profile = str(getattr(getattr(config, "project", None), "name", "") or "")
    if family in {"issue", "prd", "refactor"}:
        policy = task_pipeline_policy(config, flow_kind=family)
        actual_profile = str(policy.get("profile_id") or "")
        if str(policy.get("mode") or "") != "blocking":
            errors.append(f"cases[{index}] Task Pipeline must compile in blocking mode")
    if actual_profile != expected_profile:
        errors.append(
            f"cases[{index}] config profile {actual_profile!r} is not {expected_profile!r}"
        )
    if str(case.get("profile") or "") != actual_profile:
        errors.append(f"cases[{index}] profile does not match compiled config")
    expected_state = (
        Path(str(case.get("project_root") or "")).resolve()
        / str(getattr(getattr(config, "project", None), "state_dir", ".zf") or ".zf")
    ).resolve()
    if expected_state != Path(str(case.get("state_dir") or "")).resolve():
        errors.append(f"cases[{index}] state_dir does not match compiled config")
    return errors


def _case_report(
    case: Mapping[str, Any],
    steps: list[dict[str, Any]],
    terminal: dict[str, Any],
    status: str,
    reason: str,
) -> dict[str, Any]:
    return redact_obj({
        "case_id": str(case.get("case_id") or ""),
        "family": str(case.get("family") or ""),
        "project_root": str(case.get("project_root") or ""),
        "state_dir": str(case.get("state_dir") or ""),
        "status": status,
        "reason": reason,
        "terminal": terminal,
        "steps": steps,
    })


def _report(
    manifest: Mapping[str, Any],
    cases: list[dict[str, Any]],
    errors: list[str],
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    passed = bool(cases) and all(row.get("status") == "passed" for row in cases)
    return redact_obj({
        "schema_version": REPORT_SCHEMA,
        "suite_id": str(manifest.get("suite_id") or ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if passed and not errors else "failed",
        "errors": errors,
        "implementation_before": before,
        "implementation_after": after,
        "summary": {
            "total": len(cases),
            "passed": sum(row.get("status") == "passed" for row in cases),
            "failed": sum(row.get("status") != "passed" for row in cases),
        },
        "cases": cases,
    })


def _git_snapshot(root: Path) -> dict[str, Any]:
    if not root.is_dir():
        return {"head": "", "dirty": True}
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "head": head.stdout.strip() if head.returncode == 0 else "",
        "dirty": status.returncode != 0 or bool(status.stdout.strip()),
    }


def _argv(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


def _tail(value: Any, *, limit: int = 4000) -> str:
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value or "")
    return text[-limit:]


def _file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ParallelSuiteError("suite manifest must be an object")
        report = run_suite(manifest)
    except (OSError, ValueError) as exc:
        report = {
            "schema_version": REPORT_SCHEMA,
            "status": "failed",
            "errors": [str(exc)],
            "cases": [],
        }
    atomic_write_text(
        args.report,
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report.get("status") == "passed" else 20


if __name__ == "__main__":
    raise SystemExit(main())
