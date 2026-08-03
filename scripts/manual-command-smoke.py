#!/usr/bin/env python3
"""Execute documented CLI commands against completed project state with guards."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = ROOT / "docs" / "manual" / "reference" / "command-validation-matrix.yaml"
MATRIX_SCHEMA = "zf-manual-command-validation.v1"
MANIFEST_SCHEMA = "zf-manual-command-smoke-run.v1"
ORIGINAL_CLASS_PREFIX = "completed-project-"
SNAPSHOT_CLASS_PREFIX = "snapshot-"
GUARDED_STATE_PATHS = (
    "actions/pending.json",
    "events.jsonl",
    "feature_list.json",
    "kanban.json",
    "progress.md",
    "refs/task-index.json",
    "role_sessions.yaml",
    "session.yaml",
)


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    execution_class: str
    command: list[str]
    exit_code: int | None
    expected_exit_codes: list[int]
    duration_seconds: float
    status: str
    stdout_sha256: str
    stderr_sha256: str
    stdout_excerpt: str
    stderr_excerpt: str
    changed_state_paths: list[str]
    unexpected_state_paths: list[str]
    error: str


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML object")
    return data


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def guarded_state_hashes(state_dir: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in GUARDED_STATE_PATHS:
        path = state_dir / relative
        if path.is_file():
            hashes[relative] = _sha256_file(path)
    return hashes


def state_inventory(state_dir: Path) -> dict[str, tuple[int, int]]:
    inventory: dict[str, tuple[int, int]] = {}
    for path in sorted(state_dir.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        stat = path.stat()
        inventory[path.relative_to(state_dir).as_posix()] = (
            stat.st_size,
            stat.st_mtime_ns,
        )
    return inventory


def changed_inventory_paths(
    before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]
) -> list[str]:
    return sorted(
        path
        for path in set(before) | set(after)
        if before.get(path) != after.get(path)
    )


def _event_task_ids(event: dict[str, Any]) -> set[str]:
    payload = event.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    return {
        str(value)
        for value in (
            event.get("task_id"),
            event.get("task"),
            payload.get("task_id"),
            payload.get("feature_id"),
        )
        if value
    }


def _event_run_ids(event: dict[str, Any]) -> set[str]:
    payload = event.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    return {
        str(value)
        for value in (event.get("run_id"), payload.get("run_id"))
        if value
    }


def _payload_matches(event: dict[str, Any], expected: Any) -> bool:
    if not expected:
        return True
    if not isinstance(expected, dict):
        return False
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return False
    return all(payload.get(key) == value for key, value in expected.items())


def terminal_evidence(
    events_path: Path, expected: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[int]]:
    matches: list[dict[str, Any] | None] = [None] * len(expected)
    malformed_lines: list[int] = []
    with events_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                malformed_lines.append(line_number)
                continue
            if not isinstance(event, dict):
                continue
            for index, requirement in enumerate(expected):
                event_type = str(requirement.get("event_type") or "")
                task_id = str(requirement.get("task_id") or "")
                run_id = str(requirement.get("run_id") or "")
                if (
                    event.get("type") == event_type
                    and (not task_id or task_id in _event_task_ids(event))
                    and (not run_id or run_id in _event_run_ids(event))
                    and _payload_matches(event, requirement.get("payload"))
                ):
                    matches[index] = {
                        "event_id": str(event.get("id") or ""),
                        "ts": str(event.get("ts") or ""),
                        "line": line_number,
                    }
    evidence: list[dict[str, Any]] = []
    for requirement, found in zip(expected, matches):
        evidence.append(
            {
                "event_type": str(requirement.get("event_type") or ""),
                "task_id": str(requirement.get("task_id") or ""),
                "run_id": str(requirement.get("run_id") or ""),
                "payload": dict(requirement.get("payload") or {}),
                "found": found is not None,
                "event_id": str((found or {}).get("event_id") or ""),
                "ts": str((found or {}).get("ts") or ""),
                "line": int((found or {}).get("line") or 0),
            }
        )
    return evidence, malformed_lines


def _excerpt(value: bytes, limit: int = 3000) -> str:
    text = value.decode("utf-8", errors="replace").strip()
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n... <truncated> ...\n{text[-half:]}"


def _format_command(tokens: list[str], context: dict[str, str]) -> list[str]:
    try:
        return [str(token).format_map(context) for token in tokens]
    except KeyError as exc:
        raise ValueError(f"missing command context value: {exc.args[0]}") from exc


def _source_state() -> dict[str, Any]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--short"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    return {"root": str(ROOT), "revision": revision, "dirty": dirty}


def _selected_cases(
    matrix: dict[str, Any], manifest: dict[str, Any], override: list[str]
) -> list[dict[str, Any]]:
    cases = matrix.get("cases")
    if not isinstance(cases, list):
        raise ValueError("matrix cases must be a list")
    by_id = {
        str(case.get("id")): case
        for case in cases
        if isinstance(case, dict) and case.get("id")
    }
    selected_ids = override or list(manifest.get("cases") or [])
    if not selected_ids:
        raise ValueError("manifest cases or at least one --case is required")
    unknown = [case_id for case_id in selected_ids if case_id not in by_id]
    if unknown:
        raise ValueError(f"unknown matrix case(s): {', '.join(unknown)}")
    return [by_id[case_id] for case_id in selected_ids]


def _validate_execution_scope(
    state_kind: str, cases: list[dict[str, Any]]
) -> None:
    for case in cases:
        execution_class = str(case.get("execution_class") or "")
        if state_kind == "completed-original" and not execution_class.startswith(
            ORIGINAL_CLASS_PREFIX
        ):
            raise ValueError(
                f"{case.get('id')}: {execution_class} is forbidden on completed-original state"
            )
        if state_kind == "snapshot" and not (
            execution_class.startswith(ORIGINAL_CLASS_PREFIX)
            or execution_class.startswith(SNAPSHOT_CLASS_PREFIX)
        ):
            raise ValueError(
                f"{case.get('id')}: {execution_class} is forbidden in snapshot smoke"
            )


def _unexpected_state_changes(
    execution_class: str, changed: list[str], state_kind: str
) -> list[str]:
    if state_kind == "snapshot":
        return []
    if execution_class in {
        "completed-project-projection-refresh",
        "completed-project-projection-output",
    }:
        return [path for path in changed if not path.startswith("projections/")]
    if execution_class == "completed-project-runtime-cache-refresh":
        allowed = {
            "config/last-known-good.hash",
            "config/last-known-good.yaml",
            "config/last-known-good.yaml.lock",
            "config/validation-report.json",
        }
        return [path for path in changed if path not in allowed]
    return changed


def _run_case(
    case: dict[str, Any],
    *,
    context: dict[str, str],
    project_root: Path,
    state_dir: Path,
    environment: dict[str, str],
    state_kind: str,
) -> CaseResult:
    case_id = str(case["id"])
    execution_class = str(case["execution_class"])
    expected_exit_codes = [int(code) for code in case.get("expected_exit_codes", [0])]
    before = state_inventory(state_dir)
    start = time.monotonic()
    try:
        command = _format_command(list(case["command"]), context)
    except ValueError as exc:
        return CaseResult(
            case_id=case_id,
            execution_class=execution_class,
            command=[],
            exit_code=None,
            expected_exit_codes=expected_exit_codes,
            duration_seconds=0.0,
            status="failed",
            stdout_sha256=_sha256_bytes(b""),
            stderr_sha256=_sha256_bytes(b""),
            stdout_excerpt="",
            stderr_excerpt="",
            changed_state_paths=[],
            unexpected_state_paths=[],
            error=str(exc),
        )
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "zf.cli.main", *command],
            cwd=project_root,
            env=environment,
            capture_output=True,
            timeout=float(case.get("timeout_seconds", 60)),
        )
        exit_code: int | None = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        error = ""
    except subprocess.TimeoutExpired as exc:
        exit_code = None
        stdout = exc.stdout or b""
        stderr = exc.stderr or b""
        error = f"timed out after {case.get('timeout_seconds', 60)} seconds"
    duration = time.monotonic() - start
    after = state_inventory(state_dir)
    changed = changed_inventory_paths(before, after)
    unexpected = _unexpected_state_changes(execution_class, changed, state_kind)
    passed = exit_code in expected_exit_codes and not error and not unexpected
    if unexpected:
        error = "command changed state outside its declared execution class"
    elif exit_code not in expected_exit_codes and not error:
        error = f"exit code {exit_code} not in {expected_exit_codes}"
    return CaseResult(
        case_id=case_id,
        execution_class=execution_class,
        command=["zf", *command],
        exit_code=exit_code,
        expected_exit_codes=expected_exit_codes,
        duration_seconds=round(duration, 3),
        status="passed" if passed else "failed",
        stdout_sha256=_sha256_bytes(stdout),
        stderr_sha256=_sha256_bytes(stderr),
        stdout_excerpt=_excerpt(stdout),
        stderr_excerpt=_excerpt(stderr),
        changed_state_paths=changed,
        unexpected_state_paths=unexpected,
        error=error,
    )


def _render_markdown(receipt: dict[str, Any]) -> str:
    project = receipt["project"]
    summary = receipt["summary"]
    lines = [
        "# Manual 文档命令实跑回执",
        "",
        f"- 执行时间：`{receipt['generated_at']}`",
        f"- Project：`{project['name']}` (`{project['state_kind']}`)",
        f"- Project root：`{project['root']}`",
        f"- State dir：`{project['state_dir']}`",
        f"- ZaoFu revision：`{receipt['source']['revision']}`"
        + ("（dirty worktree）" if receipt["source"]["dirty"] else ""),
        f"- 结果：**{summary['passed']} passed / {summary['failed']} failed**",
        f"- State guard：`{summary['state_guard']}`",
        "",
        "## 终态证据",
        "",
        "| Event | Task / Run | Evidence |",
        "|---|---|---|",
    ]
    for evidence in receipt["terminal_evidence"]:
        subject = evidence["task_id"] or evidence["run_id"] or "-"
        proof = (
            f"`{evidence['event_id']}` @ `{evidence['ts']}`"
            if evidence["found"]
            else "**missing**"
        )
        lines.append(f"| `{evidence['event_type']}` | `{subject}` | {proof} |")
    lines.extend(
        [
            "",
            "容错扫描发现 malformed event 行："
            f"`{receipt['malformed_event_lines']}`"
            + (
                f"（line {', '.join(map(str, receipt['malformed_event_line_numbers']))}）"
                if receipt["malformed_event_line_numbers"]
                else ""
            )
            + "。",
            "",
            "## 命令结果",
            "",
            "| Case | Class | Exit | Duration | State changes | Result |",
            "|---|---|---:|---:|---:|---|",
        ]
    )
    for result in receipt["results"]:
        exit_value = "timeout" if result["exit_code"] is None else result["exit_code"]
        lines.append(
            f"| `{result['case_id']}` | `{result['execution_class']}` | "
            f"{exit_value} | {result['duration_seconds']:.3f}s | "
            f"{len(result['changed_state_paths'])} | **{result['status']}** |"
        )
    failures = [result for result in receipt["results"] if result["status"] != "passed"]
    if failures:
        lines.extend(["", "## 失败详情", ""])
        for result in failures:
            lines.extend(
                [
                    f"### `{result['case_id']}`",
                    "",
                    f"- Command：`{' '.join(result['command'])}`",
                    f"- Error：{result['error'] or '-'}",
                    f"- Changed state：`{', '.join(result['changed_state_paths']) or '-'}`",
                    f"- Unexpected state：`{', '.join(result['unexpected_state_paths']) or '-'}`",
                    f"- stderr：`{result['stderr_excerpt'] or '-'}`",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def execute(
    matrix_path: Path,
    manifest_path: Path,
    receipt_dir: Path,
    case_override: list[str],
) -> tuple[dict[str, Any], int]:
    matrix = _load_yaml(matrix_path)
    manifest = _load_yaml(manifest_path)
    if matrix.get("schema_version") != MATRIX_SCHEMA:
        raise ValueError(f"matrix schema_version must be {MATRIX_SCHEMA}")
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise ValueError(f"manifest schema_version must be {MANIFEST_SCHEMA}")
    project = manifest.get("project")
    if not isinstance(project, dict):
        raise ValueError("manifest project must be an object")
    project_root = Path(str(project.get("root") or "")).resolve()
    state_dir = Path(str(project.get("state_dir") or "")).resolve()
    config_path = Path(str(project.get("config_path") or project_root / "zf.yaml")).resolve()
    state_kind = str(project.get("state_kind") or "")
    if state_kind not in {"completed-original", "snapshot"}:
        raise ValueError("project.state_kind must be completed-original or snapshot")
    if not project_root.is_dir() or not state_dir.is_dir() or not config_path.is_file():
        raise ValueError("project root, state dir, and config path must exist")
    cases = _selected_cases(matrix, manifest, case_override)
    _validate_execution_scope(state_kind, cases)

    receipt_dir.mkdir(parents=True, exist_ok=True)
    if state_kind == "completed-original" and receipt_dir.is_relative_to(state_dir):
        raise ValueError("receipt_dir must be outside completed project state")
    context = {
        key: str(value)
        for key, value in dict(manifest.get("context") or {}).items()
    }
    context.update(
        {
            "config_path": str(config_path),
            "output_dir": str(receipt_dir),
            "project_root": str(project_root),
            "state_dir": str(state_dir),
        }
    )
    environment = os.environ.copy()
    environment.update(
        {
            key: str(value)
            for key, value in dict(project.get("env") or {}).items()
        }
    )
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "src"), environment.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)

    expected_evidence = list(project.get("terminal_evidence") or [])
    evidence, malformed_line_numbers = terminal_evidence(
        state_dir / "events.jsonl", expected_evidence
    )
    missing_evidence = [item for item in evidence if not item["found"]]
    initial_hashes = guarded_state_hashes(state_dir)
    results = [
        _run_case(
            case,
            context=context,
            project_root=project_root,
            state_dir=state_dir,
            environment=environment,
            state_kind=state_kind,
        )
        for case in cases
    ]
    final_hashes = guarded_state_hashes(state_dir)
    guard_changed = sorted(
        path
        for path in set(initial_hashes) | set(final_hashes)
        if initial_hashes.get(path) != final_hashes.get(path)
    )
    failed = sum(result.status != "passed" for result in results)
    preflight_failed = bool(
        missing_evidence or (state_kind == "completed-original" and guard_changed)
    )
    receipt = {
        "schema_version": "zf-manual-command-smoke-receipt.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": _source_state(),
        "matrix": str(matrix_path),
        "manifest": str(manifest_path),
        "project": {
            "name": str(project.get("name") or project_root.name),
            "root": str(project_root),
            "config_path": str(config_path),
            "state_dir": str(state_dir),
            "state_kind": state_kind,
        },
        "terminal_evidence": evidence,
        "malformed_event_lines": len(malformed_line_numbers),
        "malformed_event_line_numbers": malformed_line_numbers,
        "guarded_state_hashes_before": initial_hashes,
        "guarded_state_hashes_after": final_hashes,
        "guarded_state_changed": guard_changed,
        "results": [asdict(result) for result in results],
        "summary": {
            "passed": sum(result.status == "passed" for result in results),
            "failed": failed,
            "preflight_failed": preflight_failed,
            "state_guard": (
                (
                    "snapshot changes observed: "
                    + (", ".join(guard_changed) if guard_changed else "none")
                )
                if state_kind == "snapshot"
                else (
                    "canonical unchanged"
                    if not guard_changed
                    else f"canonical changed: {', '.join(guard_changed)}"
                )
            ),
            "terminal_evidence": "complete" if not missing_evidence else "missing",
        },
    }
    (receipt_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (receipt_dir / "receipt.md").write_text(
        _render_markdown(receipt), encoding="utf-8"
    )
    return receipt, 0 if failed == 0 and not preflight_failed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--receipt-dir", type=Path, required=True)
    parser.add_argument("--case", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        receipt, exit_code = execute(
            args.matrix.resolve(),
            args.manifest.resolve(),
            args.receipt_dir.resolve(),
            args.case,
        )
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    summary = receipt["summary"]
    print(
        f"manual command smoke: {summary['passed']} passed / "
        f"{summary['failed']} failed; receipt={args.receipt_dir.resolve() / 'receipt.md'}"
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
