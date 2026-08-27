from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path

from zf.runtime.self_issue_assessment_workspace import build_assessment_workspace
from zf.runtime.self_issue_reproduction_ledger import (
    initialize_reproduction_ledger,
    read_reproduction_ledger,
    seed_workspace_reproduction_state,
    sync_workspace_reproduction_state,
)


def _git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "add", "src", "tests", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=path, check=True)


def test_workspace_contains_only_committed_redacted_source(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "src" / "app.py").write_text("TOKEN = 'committed-secret-value'\n", encoding="utf-8")
    (project / "tests" / "test_app.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    (project / "README.md").write_text("safe\n", encoding="utf-8")
    _git_repo(project)
    (project / "src" / "app.py").write_text("uncommitted = True\n", encoding="utf-8")
    (project / ".env").write_text("TOKEN=untracked-secret\n", encoding="utf-8")
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("read-only\n", encoding="utf-8")
    evidence = tmp_path / "input.json"
    evidence.write_text('{"schema_version":"self-issue-evidence-input.v1"}\n', encoding="utf-8")

    workspace = build_assessment_workspace(
        capsule=tmp_path / "capsule", project_root=project,
        harness_root=project, input_path=evidence, skill_root=skill,
    )

    source = workspace.root / "repository" / "src" / "app.py"
    assert source.is_file()
    assert "uncommitted" not in source.read_text(encoding="utf-8")
    assert "committed-secret-value" not in source.read_text(encoding="utf-8")
    assert not (workspace.root / "repository" / ".env").exists()
    assert (workspace.root / "evidence-input.json").is_file()
    assert (workspace.root / "ASSESSMENT_WORKSPACE.md").is_file()
    assert not os.access(source, os.W_OK)
    manifest = json.loads((workspace.root / "source-manifest.json").read_text())
    assert manifest["snapshot_policy"] == "committed_source_only"
    assert manifest["sources"][0]["working_tree_diverged"] is True


def test_workspace_separates_subject_and_harness_snapshots(tmp_path: Path) -> None:
    subject = tmp_path / "subject"
    harness = tmp_path / "harness"
    for root, text in ((subject, "subject"), (harness, "harness")):
        (root / "src").mkdir(parents=True)
        (root / "tests").mkdir()
        (root / "src" / "app.py").write_text(f"VALUE = '{text}'\n", encoding="utf-8")
        (root / "tests" / "test_app.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
        (root / "README.md").write_text(text, encoding="utf-8")
        _git_repo(root)
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("safe", encoding="utf-8")
    evidence = tmp_path / "input.json"
    evidence.write_text("{}", encoding="utf-8")

    workspace = build_assessment_workspace(
        capsule=tmp_path / "capsule", project_root=subject,
        harness_root=harness, input_path=evidence, skill_root=skill,
    )
    assert (workspace.root / "subject" / "src" / "app.py").is_file()
    assert (workspace.root / "harness" / "src" / "app.py").is_file()
    assert [item["label"] for item in workspace.manifest["sources"]] == ["subject", "harness"]


def test_workspace_copies_digest_bound_playwright_capture_with_provenance(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "tests" / "test_app.py").write_text(
        "def test_ok(): assert True\n", encoding="utf-8",
    )
    (project / "README.md").write_text("fixture", encoding="utf-8")
    _git_repo(project)
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("safe", encoding="utf-8")
    state = tmp_path / "state"
    screenshot = state / "artifacts" / "self-issues" / "sid-1" / "browser" / "shot.png"
    screenshot.parent.mkdir(parents=True)
    screenshot.write_bytes(b"safe-local-image")
    evidence = tmp_path / "input.json"
    evidence.write_text(json.dumps({
        "mechanical_evidence": {
            "screenshot_refs": [{
                "ref": screenshot.relative_to(state).as_posix(),
                "sha256": hashlib.sha256(screenshot.read_bytes()).hexdigest(),
                "byte_count": screenshot.stat().st_size,
                "content_type": "image/png",
                "capture_source": "playwright",
                "capture_kind": "playwright_clean_reproduction",
            }],
        },
    }), encoding="utf-8")

    workspace = build_assessment_workspace(
        capsule=tmp_path / "capsule",
        project_root=project,
        harness_root=project,
        input_path=evidence,
        skill_root=skill,
        state_dir=state,
    )

    captured = workspace.manifest["evidence_files"][0]
    assert captured["capture_source"] == "playwright"
    assert captured["capture_kind"] == "playwright_clean_reproduction"
    assert (workspace.root / captured["workspace_path"]).read_bytes() == b"safe-local-image"


def test_reproduction_runner_blocks_invalid_targets_and_network(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "tests" / "test_network.py").write_text(
        "import socket\n\ndef test_network():\n    socket.create_connection(('example.com', 80))\n",
        encoding="utf-8",
    )
    (project / "README.md").write_text("fixture", encoding="utf-8")
    _git_repo(project)
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("safe", encoding="utf-8")
    evidence = tmp_path / "input.json"
    evidence.write_text("{}", encoding="utf-8")
    workspace = build_assessment_workspace(
        capsule=tmp_path / "capsule", project_root=project,
        harness_root=project, input_path=evidence, skill_root=skill,
    )
    runner = workspace.root / "run-reproduction"

    invalid = subprocess.run(
        [str(runner), "repository", "../secret"], cwd=workspace.root,
        capture_output=True, text=True, check=False,
    )
    blocked = subprocess.run(
        [str(runner), "repository", "tests/test_network.py::test_network"],
        cwd=workspace.root,
        env={**os.environ, "PATH": f"{Path(sys.executable).parent}:{os.environ.get('PATH', '')}"},
        capture_output=True, text=True, check=False,
    )
    assert invalid.returncode != 0
    assert "reproduction target" in invalid.stderr
    assert blocked.returncode != 0
    assert "network disabled by Self-Issue assessment runner" in blocked.stdout + blocked.stderr


def test_reproduction_runner_mechanically_rejects_a_fourth_attempt(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "tests" / "test_app.py").write_text(
        "def test_ok(): assert True\n", encoding="utf-8",
    )
    (project / "README.md").write_text("fixture", encoding="utf-8")
    _git_repo(project)
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("safe", encoding="utf-8")
    evidence = tmp_path / "input.json"
    evidence.write_text("{}", encoding="utf-8")
    workspace = build_assessment_workspace(
        capsule=tmp_path / "capsule", project_root=project,
        harness_root=project, input_path=evidence, skill_root=skill,
    )
    runner = workspace.root / "run-reproduction"
    env = {
        **os.environ,
        "PATH": f"{Path(sys.executable).parent}:{os.environ.get('PATH', '')}",
    }
    results = [
        subprocess.run(
            [str(runner), "repository", "tests/test_app.py::test_ok"],
            cwd=workspace.root, env=env, capture_output=True, text=True, check=False,
        )
        for _ in range(4)
    ]

    assert [result.returncode for result in results[:3]] == [0, 0, 0]
    assert results[3].returncode == 75
    assert '"attempt": 4' in results[3].stdout
    assert '"status": "budget_exhausted"' in results[3].stdout
    state = json.loads((
        workspace.root / ".assessment-runtime" / "reproductions.json"
    ).read_text(encoding="utf-8"))
    assert len(state["attempts"]) == 3


def test_reproduction_runner_budget_survives_a_new_resume_workspace(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "tests" / "test_app.py").write_text(
        "def test_ok(): assert True\n", encoding="utf-8",
    )
    (project / "README.md").write_text("fixture", encoding="utf-8")
    _git_repo(project)
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("safe", encoding="utf-8")
    evidence = tmp_path / "input.json"
    evidence.write_text("{}", encoding="utf-8")
    ledger = initialize_reproduction_ledger(
        tmp_path / "state", draft_id="sid-1", run_id="sie-1",
    )
    env = {
        **os.environ,
        "PATH": f"{Path(sys.executable).parent}:{os.environ.get('PATH', '')}",
    }

    first = build_assessment_workspace(
        capsule=tmp_path / "capsule-1", project_root=project,
        harness_root=project, input_path=evidence, skill_root=skill,
    )
    seed_workspace_reproduction_state(ledger, workspace_root=first.root)
    first_result = subprocess.run(
        [str(first.root / "run-reproduction"), "repository", "tests/test_app.py::test_ok"],
        cwd=first.root, env=env, capture_output=True, text=True, check=False,
    )
    sync_workspace_reproduction_state(ledger, workspace_root=first.root)

    resumed = build_assessment_workspace(
        capsule=tmp_path / "capsule-2", project_root=project,
        harness_root=project, input_path=evidence, skill_root=skill,
    )
    seed_workspace_reproduction_state(ledger, workspace_root=resumed.root)
    resumed_results = [
        subprocess.run(
            [
                str(resumed.root / "run-reproduction"), "repository",
                "tests/test_app.py::test_ok",
            ],
            cwd=resumed.root, env=env, capture_output=True, text=True, check=False,
        )
        for _ in range(3)
    ]
    sync_workspace_reproduction_state(ledger, workspace_root=resumed.root)

    assert first_result.returncode == 0
    assert [result.returncode for result in resumed_results] == [0, 0, 75]
    assert '"attempt": 2' in resumed_results[0].stdout
    assert '"attempt": 3' in resumed_results[1].stdout
    assert '"attempt": 4' in resumed_results[2].stdout
    assert len(read_reproduction_ledger(ledger)["attempts"]) == 3
