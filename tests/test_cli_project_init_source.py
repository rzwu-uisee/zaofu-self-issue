from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

from zf.cli.project import init_flow_project
from zf.core.config.loader import load_config


def test_prd_project_init_snapshots_external_source(tmp_path: Path) -> None:
    external = tmp_path / "source" / "requirements.md"
    external.parent.mkdir()
    external.write_text("# Product requirements\n", encoding="utf-8")
    project_root = tmp_path / "project"

    result = init_flow_project(
        kind="prd",
        name="demo",
        project_root=project_root,
        source_ref=str(external),
        request_kind="prd",
        backend="claude-code",
        lanes=1,
        state_dir=".zf",
        request_id="demo-request",
        create_root=True,
        workspace_register=False,
    )

    local_ref = "docs/prd/requirements.md"
    assert (project_root / local_ref).read_text(encoding="utf-8") == (
        "# Product requirements\n"
    )
    docs = list(yaml.safe_load_all((project_root / "zf.yaml").read_text()))
    assert docs[0]["spec"]["prdRef"] == local_ref
    manifest = json.loads(
        (
            project_root
            / "artifacts/workflow/demo-request/workflow-input-manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["source_ref"] == local_ref
    assert result["request"]["request_id"] == "demo-request"


def test_project_init_canonicalizes_claude_product_alias(tmp_path: Path) -> None:
    project_root = tmp_path / "project"

    result = init_flow_project(
        kind="prd",
        name="demo",
        project_root=project_root,
        objective="Build a deterministic text counter.",
        backend="claude",
        lanes=1,
        state_dir=".zf",
        create_root=True,
        workspace_register=False,
    )

    docs = list(yaml.safe_load_all((project_root / "zf.yaml").read_text()))
    assert docs[0]["spec"]["backend"] == "claude-code"
    assert docs[0]["spec"]["prdRef"] == "docs/intake/project-init-request.md"
    assert docs[0]["spec"]["targetRoot"] == "."
    profile = next(doc for doc in docs if doc["kind"] == "ConfigProfile")
    assert profile["spec"]["runtime"]["run_manager"]["backend"] == "claude-code"
    request = json.loads(
        Path(result["request"]["workflow_input_manifest_ref"]).read_text(
            encoding="utf-8"
        )
    )
    assert request["requested_backend"] == "claude-code"
    assert request["source_ref"] == "docs/intake/project-init-request.md"
    assert result["readiness"] == {
        "launch_ready": True,
        "missing_required_fields": [],
        "source_ref": "docs/intake/project-init-request.md",
    }
    assert result["next_actions"]


def test_project_init_persists_context_provider_policy_and_declared_stack(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"

    result = init_flow_project(
        kind="multi",
        name="release-console",
        description="Coordinate release evidence for platform operators.",
        project_root=project_root,
        backend="codex",
        verify_backend="claude-code",
        stack="rust",
        create_root=True,
        workspace_register=False,
    )

    config = load_config(project_root / "zf.yaml")
    assert config.project.description == (
        "Coordinate release evidence for platform operators."
    )
    assert {
        role.backend
        for role in config.roles
        if "verify-lane" in role.name
    } == {"claude-code"}
    agents = (project_root / "AGENTS.md").read_text(encoding="utf-8")
    assert "Coordinate release evidence" in agents
    assert "confidence: declared" in agents
    assert "cargo test" in agents
    assert result["provider_policy"] == {
        "primary_backend": "codex",
        "mixed_enabled": True,
        "verify_backend": "claude-code",
    }
    assert result["instruction_docs"]["profile"]["languages"] == ["rust"]


def test_project_init_greenfield_git_readiness(tmp_path: Path) -> None:
    project_root = tmp_path / "greenfield"

    result = init_flow_project(
        kind="multi",
        name="greenfield",
        project_root=project_root,
        backend="codex",
        create_root=True,
        git_init=True,
        workspace_register=False,
    )

    head = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", "--verify", "HEAD"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout.strip()
    assert result["git_readiness"] == {
        "created": True,
        "head": head,
        "ready": True,
    }
    assert (project_root / "README.md").is_file()
    assert (project_root / "src" / ".gitkeep").is_file()
    assert (project_root / "tests" / ".gitkeep").is_file()
    assert subprocess.run(
        ["git", "-C", str(project_root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout == ""
