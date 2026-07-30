"""zf web — missing react dist must warn once, not silently serve the shell.

2026-07-10: a fresh worktree has no web/dist (gitignored build artifact); the
server silently fell back to the bare static skeleton — API alive, UI blank,
zero diagnostics.
"""

from __future__ import annotations

from pathlib import Path

import zf.web.server as server
from zf.web.assets import web_repo_root


def test_ui_index_warns_once_when_dist_missing(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(server, "_REACT_DIST_DIR", tmp_path / "no-dist")
    monkeypatch.setattr(server, "_react_dist_missing_warned", False)

    first = server._ui_index()
    second = server._ui_index()

    assert first == server._STATIC_DIR / "index.html"
    assert second == first
    err = capsys.readouterr().err
    assert err.count("react dist not found") == 1


def test_ui_index_prefers_built_dist_without_warning(tmp_path: Path, monkeypatch, capsys):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(server, "_REACT_DIST_DIR", dist)
    monkeypatch.setattr(server, "_react_dist_missing_warned", False)

    assert server._ui_index() == dist / "index.html"
    assert "react dist not found" not in capsys.readouterr().err


def test_web_repo_root_prefers_current_imported_source(tmp_path: Path):
    package_root = tmp_path / "worktree"
    installed_root = tmp_path / "editable-main"
    (package_root / "web").mkdir(parents=True)
    (installed_root / "web").mkdir(parents=True)

    assert web_repo_root(package_root, installed_root) == package_root


def test_web_repo_root_falls_back_to_editable_source(tmp_path: Path):
    package_root = tmp_path / "installed-package"
    installed_root = tmp_path / "editable-main"
    (installed_root / "web").mkdir(parents=True)

    assert web_repo_root(package_root, installed_root) == installed_root
