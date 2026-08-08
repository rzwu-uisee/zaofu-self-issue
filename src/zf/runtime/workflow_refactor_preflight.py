"""Mechanical safety checks for Refactor workflow admission."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from zf.core.safety.path_guard import PathGuard, PathGuardError
from zf.runtime.workflow_intake import _now_iso


def _is_flow_template_placeholder(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized.startswith(("todo:", "todo ", "<todo", "${todo"))

def _git_is_work_tree(root: Path) -> bool:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=10,
        )
    except OSError:
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"

def _git_source_fingerprint(root: Path) -> dict[str, str]:
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=10,
    )
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True, text=True, timeout=30,
    )
    return {
        "head": head.stdout.strip() if head.returncode == 0 else "",
        "status_sha256": hashlib.sha256(
            (status.stdout if status.returncode == 0 else "").encode("utf-8")
        ).hexdigest(),
    }

def _resolve_declared_root(raw: str, project_root: Path) -> Path | None:
    if not raw or raw.startswith("TODO"):
        return None
    root = Path(raw).expanduser()
    return root if root.is_absolute() else (project_root / root)

def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True

def _target_git_root(target_root: Path, project_root: Path) -> Path | None:
    if _git_is_work_tree(target_root):
        return target_root
    if _is_relative_to(target_root, project_root) and _git_is_work_tree(project_root):
        return project_root
    return None

def _refactor_safety_report(
    *,
    project_root: Path,
    metadata: dict[str, Any],
    configured_metadata: dict[str, Any],
    flow_kind: str,
    intake_report: dict[str, Any],
) -> dict[str, Any]:
    """Mechanical refactor prechecks (doc 125 §6): disjoint source/target,
    target must be git (r10 target_ref lesson), source baseline must not move
    within one request (r6 write_violation class). Handwritten configs without
    flow_metadata get WARN, profile-driven refactor flows fail closed."""
    if flow_kind != "refactor":
        return {"status": "not_applicable", "diagnostics": []}
    diagnostics: list[dict[str, Any]] = []
    source_raw = str(metadata.get("source_root") or "")
    target_raw = str(metadata.get("target_root") or "")
    source_root = _resolve_declared_root(source_raw, project_root)
    target_root = _resolve_declared_root(target_raw, project_root) or project_root
    for key, label in (
        ("source_root", "source_root"),
        ("target_root", "target_root"),
    ):
        configured_raw = str(configured_metadata.get(key) or "").strip()
        intake_raw = str(intake_report.get(key) or "").strip()
        if (
            not configured_raw
            or _is_flow_template_placeholder(configured_raw)
            or not intake_raw
        ):
            continue
        configured_root = _resolve_declared_root(configured_raw, project_root)
        intake_root = _resolve_declared_root(intake_raw, project_root)
        if (
            configured_root is not None
            and intake_root is not None
            and configured_root.resolve(strict=False)
            != intake_root.resolve(strict=False)
        ):
            diagnostics.append({
                "severity": "STOP",
                "kind": "workflow_root_binding_conflict",
                "title": f"refactor {label} 配置与请求不一致",
                "message": (
                    f"config={configured_root.resolve(strict=False)}; "
                    f"intake={intake_root.resolve(strict=False)}"
                ),
                "why_it_matters": (
                    "同一 request 的执行根不能由 profile 和 intake 各自解释，"
                    "否则恢复时会切换 parity 分母或写入目标。"
                ),
                "fix_it": (
                    "让 profile 使用 TODO 占位并由 intake 绑定，或让两处声明"
                    "解析到同一个绝对路径后重新提交 request。"
                ),
                "safe_auto_fix": False,
                "root_field": key,
            })
    if source_root is None:
        severity = "STOP" if metadata else "WARN"
        diagnostics.append({
            "severity": severity,
            "kind": "workflow_source_root_undeclared",
            "title": "refactor source_root 未声明",
            "message": source_raw or "flow_metadata 无 source_root",
            "why_it_matters": "没有 source_root 就无法做 source/target 隔离与基线保护。",
            "fix_it": "在 FlowSpec/intake 中声明真实 sourceRoot(手写配置至少在 prompt 中锚定)。",
            "safe_auto_fix": False,
        })
    elif not source_root.exists():
        diagnostics.append({
            "severity": "STOP",
            "kind": "workflow_source_root_not_found",
            "title": "refactor source_root 不存在",
            "message": str(source_root),
            "why_it_matters": "source 路径无效时 scan/parity 全部建立在空分母上。",
            "fix_it": "修正 sourceRoot 路径。",
            "safe_auto_fix": False,
        })
    else:
        try:
            PathGuard.assert_disjoint(source_root, target_root)
        except PathGuardError as exc:
            diagnostics.append({
                "severity": "STOP",
                "kind": "workflow_source_target_overlap",
                "title": "source_root 与 target 重叠",
                "message": str(exc),
                "why_it_matters": "重叠时 candidate 写入会直接篡改 source(r6 write_violation 类事故)。",
                "fix_it": "让 sourceRoot 与 targetRoot 完全互斥。",
                "safe_auto_fix": False,
            })
    target_git_root = _target_git_root(target_root, project_root)
    if target_git_root is None:
        diagnostics.append({
            "severity": "STOP",
            "kind": "workflow_target_not_git",
            "title": "refactor target 不是 git 仓库",
            "message": str(target_root),
            "why_it_matters": "candidate/worktree 机制需要一个 git 承载根; target 子目录可不存在,但必须在 git project root 内。",
            "fix_it": "在项目根运行 git init,或使用 `zf project init --kind refactor --git-init`。",
            "safe_auto_fix": True,
        })
    resolved_source = (
        source_root.resolve(strict=False) if source_root is not None else None
    )
    resolved_target = target_root.resolve(strict=False)
    root_binding = {
        "schema_version": "workflow-root-binding.v1",
        "source_root": str(resolved_source or ""),
        "target_root": str(resolved_target),
    }
    root_binding["digest"] = hashlib.sha256(
        json.dumps(
            root_binding,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    report: dict[str, Any] = {
        "source_root": str(resolved_source or ""),
        "target_root": str(resolved_target),
        "target_git_root": str(target_git_root or ""),
        "root_binding": root_binding,
    }
    if source_root is not None and source_root.exists():
        if not _git_is_work_tree(source_root):
            diagnostics.append({
                "severity": "WARN",
                "kind": "workflow_source_not_git",
                "title": "source_root 不是 git 仓库",
                "message": str(source_root),
                "why_it_matters": "无法建立 source 基线快照,运行中 source 被改动将不可检测。",
                "fix_it": "优先使用 git 管理的 source;否则自行保证 source 只读。",
                "safe_auto_fix": False,
            })
        else:
            fingerprint = _git_source_fingerprint(source_root)
            manifest_ref = str(intake_report.get("workflow_input_manifest_ref") or "")
            if manifest_ref:
                baseline_path = Path(manifest_ref).parent / "source-baseline.json"
                baseline = _load_json(baseline_path)
                if baseline:
                    if (
                        baseline.get("head") != fingerprint["head"]
                        or baseline.get("status_sha256") != fingerprint["status_sha256"]
                    ):
                        diagnostics.append({
                            "severity": "STOP",
                            "kind": "workflow_source_root_modified",
                            "title": "source_root 相对基线被改动",
                            "message": (
                                f"baseline head {baseline.get('head', '')[:12]} -> "
                                f"{fingerprint['head'][:12]}"
                            ),
                            "why_it_matters": "同一 request 内 source 变动会让 parity 分母漂移,结论不可信。",
                            "fix_it": "恢复 source 到基线,或显式开启新 request 重建基线。",
                            "safe_auto_fix": False,
                        })
                else:
                    baseline_path.write_text(
                        json.dumps({
                            "schema_version": "workflow.source_baseline.v1",
                            "source_root": str(source_root),
                            **fingerprint,
                            "created_at": _now_iso(),
                        }, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                report["source_baseline_ref"] = str(baseline_path)
            report["source_fingerprint"] = fingerprint
    stop = any(d["severity"] == "STOP" for d in diagnostics)
    warn = any(d["severity"] == "WARN" for d in diagnostics)
    report["status"] = "STOP" if stop else "WARN" if warn else "PASS"
    report["diagnostics"] = diagnostics
    return report


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


is_flow_template_placeholder = _is_flow_template_placeholder
git_is_work_tree = _git_is_work_tree
git_source_fingerprint = _git_source_fingerprint
is_relative_to = _is_relative_to
refactor_safety_report = _refactor_safety_report
resolve_declared_root = _resolve_declared_root
target_git_root = _target_git_root


__all__ = [
    "is_flow_template_placeholder",
    "git_is_work_tree",
    "git_source_fingerprint",
    "is_relative_to",
    "refactor_safety_report",
    "resolve_declared_root",
    "target_git_root",
]
