"""Conservative audit and migration for legacy resident worktrees."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from zf.autoresearch.worktree_preparation import PREPARATION_MANIFEST


DEFAULT_RESIDENT_WORKTREE_ROOT = Path("/tmp/zaofu-autoresearch-resident/worktrees")


def register_worktree_migration_cli(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "migrate-worktrees",
        help="Audit legacy resident worktrees; defaults to dry-run",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_RESIDENT_WORKTREE_ROOT,
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Unlink only legacy framework symlinks with exact ownership proof",
    )
    parser.set_defaults(func=_run_migration_cli)


def _run_migration_cli(args: Any) -> int:
    result = migrate_legacy_resident_worktrees(
        root=args.root,
        apply=bool(args.apply),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def migrate_legacy_resident_worktrees(
    *,
    root: Path = DEFAULT_RESIDENT_WORKTREE_ROOT,
    apply: bool = False,
) -> dict[str, Any]:
    root = Path(root).expanduser().resolve(strict=False)
    rows: list[dict[str, Any]] = []
    if root.is_dir():
        for path in sorted(root.iterdir()):
            if not path.is_dir():
                continue
            rows.append(_audit_worktree(path.resolve(), apply=apply))
    return {
        "schema_version": "autoresearch.legacy_worktree_migration.v1",
        "mode": "apply" if apply else "dry-run",
        "root": str(root),
        "worktrees": rows,
        "summary": {
            "inspected": len(rows),
            "would_unlink": sum(row.get("action") == "would_unlink" for row in rows),
            "unlinked": sum(row.get("action") == "unlinked" for row in rows),
            "retained": sum(row.get("disposition") == "retained" for row in rows),
        },
    }


def _audit_worktree(path: Path, *, apply: bool) -> dict[str, Any]:
    branch = _git(path, "branch", "--show-current")
    head = _git(path, "rev-parse", "HEAD")
    dirty = _git(path, "status", "--porcelain=v1", "--untracked-files=all")
    merged = _is_merged_into_dev(path, head)
    base: dict[str, Any] = {
        "worktree": str(path),
        "branch": branch,
        "head": head,
        "merged_into_dev": merged,
        "candidate_paths": [line for line in dirty.splitlines() if line.strip()][:100],
        "branch_disposition": "preserved",
        "worktree_disposition": "preserved",
        "archive_present": any(
            candidate.is_file()
            for candidate in (path / ".zf" / "autoresearch" / "runs").glob(
                "*/report.md"
            )
        ),
    }
    if not branch.startswith("experiment/autoresearch-"):
        return {
            **base,
            "action": "none",
            "disposition": "out_of_scope",
            "reason": "branch is not an autoresearch experiment branch",
        }

    dependencies = path / "web" / "node_modules"
    if not dependencies.is_symlink():
        return {
            **base,
            "action": "none",
            "disposition": "retained",
            "reason": "legacy dependency symlink not present",
        }
    evidence = _legacy_link_evidence(path, dependencies)
    if evidence is None:
        return {
            **base,
            "action": "none",
            "disposition": "retained",
            "reason": "exact legacy link ownership evidence not found",
            "symlink": str(dependencies),
            "current_target": _readlink(dependencies),
        }
    logged_target, log_path = evidence
    current_target = _readlink(dependencies)
    if current_target != logged_target:
        return {
            **base,
            "action": "none",
            "disposition": "retained",
            "reason": "current symlink target does not match legacy evidence",
            "symlink": str(dependencies),
            "current_target": current_target,
            "logged_target": logged_target,
            "evidence_log": str(log_path),
        }

    if not apply:
        return {
            **base,
            "action": "would_unlink",
            "disposition": "retained",
            "reason": "exact legacy framework symlink ownership proved",
            "symlink": str(dependencies),
            "current_target": current_target,
            "logged_target": logged_target,
            "evidence_log": str(log_path),
        }
    dependencies.unlink()
    return {
        **base,
        "action": "unlinked",
        "disposition": "migrated",
        "reason": "exact legacy framework symlink ownership proved",
        "symlink": str(dependencies),
        "current_target": current_target,
        "logged_target": logged_target,
        "evidence_log": str(log_path),
    }


def _legacy_link_evidence(
    worktree: Path,
    dependencies: Path,
) -> tuple[str, Path] | None:
    run_root = worktree / ".zf" / "autoresearch" / "runs"
    logs = sorted(
        run_root.glob("*/prepare-web-deps.log"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    expected_link = str(dependencies)
    for log_path in logs:
        manifest_path = log_path.parent / "worktree-preparation" / PREPARATION_MANIFEST
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
        if isinstance(manifest, dict) and isinstance(
            manifest.get("web_dependencies"),
            dict,
        ):
            return None
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.startswith("linked ") or " -> " not in line:
                continue
            link_path, target = line[len("linked ") :].split(" -> ", 1)
            if link_path == expected_link and target:
                return target, log_path
    return None


def _readlink(path: Path) -> str:
    try:
        return os.readlink(path)
    except OSError:
        return ""


def _git(path: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return (proc.stdout or "").strip() if proc.returncode == 0 else ""


def _is_merged_into_dev(path: Path, head: str) -> bool | None:
    if not head:
        return None
    proc = subprocess.run(
        ["git", "-C", str(path), "merge-base", "--is-ancestor", head, "dev"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    return None


__all__ = [
    "DEFAULT_RESIDENT_WORKTREE_ROOT",
    "migrate_legacy_resident_worktrees",
    "register_worktree_migration_cli",
]
