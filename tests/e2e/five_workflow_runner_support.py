"""Host probes and evidence I/O for the five-Workflow E2E runner."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Iterable, Mapping, Sequence

import yaml

from zf.core.security.redaction import redact_obj


def git_snapshot(root: Path) -> dict[str, Any]:
    root = Path(root)
    head = command_result(["git", "-C", str(root), "rev-parse", "HEAD"])
    status = command_result([
        "git", "-C", str(root), "status", "--porcelain=v1",
        "--untracked-files=all",
    ])
    errors = [item for item in (head["error"], status["error"]) if item]
    return {
        "root": str(root.resolve()),
        "head": head["stdout"].strip(),
        "dirty": bool(status["stdout"].strip()),
        "status": status["stdout"].splitlines(),
        "errors": errors,
    }


def host_readiness(*, playwright_image: str) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    errors: list[str] = []
    for command in ("codex", "docker", "tmux", "unshare"):
        path = shutil.which(command) or ""
        checks[f"{command}_path"] = path
        if not path:
            errors.append(f"required host command is missing: {command}")
    if checks.get("codex_path"):
        checks["codex_version"] = command_result(["codex", "--version"], timeout=10)
        checks["codex_login"] = command_result(["codex", "login", "status"], timeout=10)
        if checks["codex_login"]["returncode"] != 0:
            errors.append("codex login status failed")
    if checks.get("docker_path"):
        checks["docker_info"] = command_result(["docker", "info"], timeout=15)
        checks["playwright_image"] = command_result(
            ["docker", "image", "inspect", playwright_image],
            timeout=15,
        )
        if checks["docker_info"]["returncode"] != 0:
            errors.append("Docker daemon is unavailable")
        if checks["playwright_image"]["returncode"] != 0:
            errors.append(f"Playwright image is not local: {playwright_image}")
    sandbox_override = os.environ.get(
        "ZF_KANBAN_AGENT_CODEX_HEADLESS_SANDBOX", ""
    ).strip()
    checks["sandbox_override"] = sandbox_override
    if checks.get("unshare_path") and sandbox_override != "danger-full-access":
        checks["user_namespace"] = command_result(
            ["unshare", "--user", "--map-root-user", "true"],
            timeout=10,
        )
        if checks["user_namespace"]["returncode"] != 0:
            errors.append(
                "host user namespace is unavailable and trusted sandbox override is not set"
            )
    return {"checks": redact_obj(checks), "errors": errors}


def command_result(argv: Sequence[str], *, timeout: float = 30) -> dict[str, Any]:
    try:
        result = subprocess.run(
            list(argv),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "argv": list(argv),
            "returncode": -1,
            "stdout": "",
            "stderr": "",
            "error": str(exc),
        }
    return {
        "argv": list(argv),
        "returncode": result.returncode,
        "stdout": result.stdout[-8000:],
        "stderr": result.stderr[-8000:],
        "error": "" if result.returncode == 0 else result.stderr.strip(),
    }


def capture_screenshot(argv: Sequence[str], *, out_dir: Path) -> None:
    if not argv:
        write_json(
            out_dir / "screenshot-command.json",
            {"status": "not_configured", "argv": []},
        )
        return
    result = command_result(list(argv), timeout=180)
    result["status"] = "passed" if result["returncode"] == 0 else "failed"
    write_json(out_dir / "screenshot-command.json", redact_obj(result))


def diagnostic_paths(state_dir: Path, *, task_id: str, run_id: str) -> list[str]:
    roots = [
        state_dir / "diagnostics",
        state_dir / "projections",
        state_dir / "artifacts",
        state_dir / "workdirs",
    ]
    needles = tuple(value for value in (task_id, run_id) if value)
    paths: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(state_dir).as_posix()
            if not needles or any(needle in rel for needle in needles):
                paths.append(rel)
            if len(paths) >= 500:
                return paths
    return paths


def collect_refs(values: Iterable[Any]) -> list[dict[str, str]]:
    refs: dict[tuple[str, str], dict[str, str]] = {}

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, Mapping):
            ref = str(value.get("ref") or value.get("path") or "").strip()
            if ref:
                digest = str(value.get("sha256") or value.get("hash") or "")
                refs[(ref, digest)] = {
                    "ref": ref,
                    "sha256": digest,
                    "kind": str(value.get("kind") or key),
                }
            for child_key, child in value.items():
                visit(child, str(child_key))
        elif isinstance(value, list):
            for child in value:
                visit(child, key)
        elif isinstance(value, str) and key.endswith("_ref") and value.strip():
            refs[(value.strip(), "")] = {
                "ref": value.strip(),
                "sha256": "",
                "kind": key,
            }

    for value in values:
        visit(value)
    return sorted(refs.values(), key=lambda item: (item["ref"], item["sha256"]))


def read_json(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def read_yaml(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def write_json(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(redact_obj(value), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


__all__ = [
    "capture_screenshot",
    "collect_refs",
    "command_result",
    "diagnostic_paths",
    "git_snapshot",
    "host_readiness",
    "read_json",
    "read_yaml",
    "write_json",
]
