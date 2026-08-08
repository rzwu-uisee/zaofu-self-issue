"""Workspace Feishu bridge lease lifecycle tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

from zf.integrations.feishu.workspace_bridge_lease import (
    join_live_workspace_bridge,
    join_live_provider_bridge,
    provider_bridge_lease_path,
    register_provider_bridge,
    register_workspace_bridge,
    release_provider_bridge,
    release_workspace_bridge,
    workspace_bridge_lease_path,
)


def test_workspace_bridge_lease_keeps_bridge_until_last_project_releases(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    owner = register_workspace_bridge(
        workspace_id="team",
        app_id="cli_shared",
        project_id="project-a",
        pid=os.getpid(),
        log_path=tmp_path / "bridge.log",
    )
    joined = join_live_workspace_bridge(
        workspace_id="team",
        app_id="cli_shared",
        project_id="project-b",
    )

    assert owner.shared is False
    assert joined is not None and joined.shared is True
    first_release = release_workspace_bridge(owner)
    assert first_release.terminate is False
    assert first_release.remaining_projects == ("project-b",)

    last_release = release_workspace_bridge(joined)
    assert last_release.terminate is True
    state = json.loads(workspace_bridge_lease_path("team").read_text(encoding="utf-8"))
    assert state["bridges"] == {}


def test_provider_bridge_lease_keeps_one_app_bridge_across_workspaces(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    owner = register_provider_bridge(
        workspace_id="team",
        app_id="cli_shared",
        project_id="project-a",
        pid=os.getpid(),
        log_path=tmp_path / "bridge.log",
    )
    joined = join_live_provider_bridge(
        workspace_id="product",
        app_id="cli_shared",
        project_id="project-a",
    )

    assert owner.shared is False
    assert joined is not None and joined.shared is True
    first_release = release_provider_bridge(owner)
    assert first_release.terminate is False
    assert first_release.remaining_projects == ("product:project-a",)

    last_release = release_provider_bridge(joined)
    assert last_release.terminate is True
    state = json.loads(provider_bridge_lease_path().read_text(encoding="utf-8"))
    assert state["bridges"] == {}
