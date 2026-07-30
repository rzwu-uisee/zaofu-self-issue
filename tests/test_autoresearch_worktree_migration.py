from __future__ import annotations

import os
import subprocess
from pathlib import Path

from zf.autoresearch.worktree_migration import (
    migrate_legacy_resident_worktrees,
)
from zf.cli.main import main


def _git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        check=True,
    )


def _legacy_worktree(
    tmp_path: Path,
    *,
    current_target: Path,
    logged_target: Path,
) -> tuple[Path, Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "dev")
    _git(repo, "config", "user.email", "migration-test@example.invalid")
    _git(repo, "config", "user.name", "Migration Test")
    (repo / ".gitignore").write_text(".zf/\nweb/node_modules\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "tracked.txt")
    _git(repo, "commit", "-m", "test: seed")

    root = tmp_path / "resident-worktrees"
    worktree = root / "legacy"
    root.mkdir()
    branch = "experiment/autoresearch-legacy"
    _git(repo, "worktree", "add", "-b", branch, str(worktree), "HEAD")
    dependencies = worktree / "web" / "node_modules"
    dependencies.parent.mkdir(parents=True)
    dependencies.symlink_to(current_target, target_is_directory=True)
    run_dir = worktree / ".zf" / "autoresearch" / "runs" / "legacy-run"
    preparation = run_dir / "worktree-preparation"
    preparation.mkdir(parents=True)
    (preparation / "worktree-preparation.json").write_text(
        '{"schema_version":"autoresearch-worktree-preparation.v1"}\n',
        encoding="utf-8",
    )
    (run_dir / "prepare-web-deps.log").write_text(
        f"linked {dependencies} -> {logged_target}\n",
        encoding="utf-8",
    )
    return root, dependencies, branch


def test_legacy_worktree_migration_defaults_to_dry_run(tmp_path: Path) -> None:
    target = tmp_path / "shared-node-modules"
    target.mkdir()
    root, dependencies, branch = _legacy_worktree(
        tmp_path,
        current_target=target,
        logged_target=target,
    )

    result = migrate_legacy_resident_worktrees(root=root)

    row = result["worktrees"][0]
    assert result["mode"] == "dry-run"
    assert row["action"] == "would_unlink"
    assert row["branch"] == branch
    assert row["branch_disposition"] == "preserved"
    assert dependencies.is_symlink()


def test_legacy_worktree_apply_unlinks_only_exact_match(tmp_path: Path) -> None:
    target = tmp_path / "shared-node-modules"
    target.mkdir()
    root, dependencies, branch = _legacy_worktree(
        tmp_path,
        current_target=target,
        logged_target=target,
    )

    result = migrate_legacy_resident_worktrees(root=root, apply=True)

    assert result["worktrees"][0]["action"] == "unlinked"
    assert not dependencies.is_symlink()
    branches = _git(
        tmp_path / "repo",
        "branch",
        "--format=%(refname:short)",
    ).stdout.splitlines()
    assert branch in branches


def test_legacy_worktree_target_mismatch_is_retained(tmp_path: Path) -> None:
    current = tmp_path / "current-node-modules"
    logged = tmp_path / "logged-node-modules"
    current.mkdir()
    logged.mkdir()
    root, dependencies, _ = _legacy_worktree(
        tmp_path,
        current_target=current,
        logged_target=logged,
    )

    result = migrate_legacy_resident_worktrees(root=root, apply=True)

    row = result["worktrees"][0]
    assert row["action"] == "none"
    assert row["disposition"] == "retained"
    assert os.readlink(dependencies) == str(current)


def test_migrate_worktrees_cli_is_dry_run_by_default(
    tmp_path: Path,
    capsys,
) -> None:
    rc = main(
        [
            "autoresearch",
            "migrate-worktrees",
            "--root",
            str(tmp_path / "missing"),
        ]
    )

    assert rc == 0
    assert '"mode": "dry-run"' in capsys.readouterr().out
