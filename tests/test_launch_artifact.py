from __future__ import annotations

import subprocess
from pathlib import Path

from zf.runtime.launch_artifact import _harness_source_summary


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_harness_source_summary_binds_checkout_revision(tmp_path: Path) -> None:
    repo = tmp_path / "zaofu-source"
    module = repo / "src" / "zf" / "runtime" / "launch_artifact.py"
    module.parent.mkdir(parents=True)
    module.write_text("# source\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", "src/zf/runtime/launch_artifact.py")
    _git(repo, "commit", "-qm", "fixture")

    summary = _harness_source_summary(
        source_file=module,
        cli_command=str(repo / ".venv" / "bin" / "zf"),
    )

    assert summary["kind"] == "git_checkout"
    assert summary["git_root"] == str(repo)
    assert summary["commit"] == _git(repo, "rev-parse", "HEAD")
    assert summary["branch"]
    assert summary["dirty"] is False
    assert summary["cli_command"].endswith("/.venv/bin/zf")


def test_harness_source_summary_marks_non_git_install(tmp_path: Path) -> None:
    module = tmp_path / "site-packages" / "zf" / "runtime" / "launch_artifact.py"
    module.parent.mkdir(parents=True)
    module.write_text("# installed\n", encoding="utf-8")

    summary = _harness_source_summary(source_file=module)

    assert summary["kind"] == "installed"
    assert summary["git_root"] == ""
    assert summary["commit"] == ""
    assert summary["dirty"] is None
