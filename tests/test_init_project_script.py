from __future__ import annotations

import os
import subprocess
from pathlib import Path

from zf.core.config.loader import load_config


def test_init_project_script_delegates_greenfield_creation_to_project_init(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    project_root = tmp_path / "script-project"
    env = os.environ.copy()
    env["ZF_WORKSPACE_HOME"] = str(tmp_path / "workspace-home")

    completed = subprocess.run(
        [
            str(repo_root / "tools" / "init-project.sh"),
            "--project-dir",
            str(project_root),
            "--name",
            "script-project",
            "--description",
            "Prepare audited delivery workflows.",
            "--stack",
            "go",
            "--backend",
            "codex",
            "--no-workspace-register",
            "--git-policy",
            "skip",
            "--skip-validate",
            "--skip-start-dry-run",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )

    assert "creating multi-kind project through zf project init" in completed.stdout
    config = load_config(project_root / "zf.yaml")
    assert config.project.name == "script-project"
    assert config.project.description == "Prepare audited delivery workflows."
    agents = (project_root / "AGENTS.md").read_text(encoding="utf-8")
    assert "Prepare audited delivery workflows." in agents
    assert "confidence: declared" in agents
    assert "go test ./..." in agents
    assert (project_root / config.project.state_dir / "events.jsonl").is_file()


def test_init_project_script_auto_policy_creates_git_head(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    project_root = tmp_path / "script-greenfield"
    env = os.environ.copy()
    env["ZF_WORKSPACE_HOME"] = str(tmp_path / "workspace-home")

    subprocess.run(
        [
            str(repo_root / "tools" / "init-project.sh"),
            "--project-dir",
            str(project_root),
            "--name",
            "script-greenfield",
            "--no-workspace-register",
            "--yes",
            "--skip-validate",
            "--skip-start-dry-run",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )

    head = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", "--verify", "HEAD"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout.strip()
    assert head
    assert (project_root / "README.md").is_file()
    assert (project_root / "src" / ".gitkeep").is_file()
    assert (project_root / "tests" / ".gitkeep").is_file()
