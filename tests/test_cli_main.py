"""Tests for CLI entry point."""

from __future__ import annotations

import json
from pathlib import Path
import tomllib

import zf

from zf.cli.main import main


ROOT = Path(__file__).resolve().parents[1]


def test_version_flag(capsys):
    """zf --version prints version string."""
    try:
        main(["--version"])
    except SystemExit as e:
        assert e.code == 0
    captured = capsys.readouterr()
    assert zf.__version__ in captured.out


def test_no_args_prints_help(capsys):
    """zf with no args prints help and exits 0."""
    result = main([])
    assert result == 0
    captured = capsys.readouterr()
    assert "zf" in captured.out


def test_version_importable():
    """Package version is importable."""
    assert zf.__version__ == "0.0.7"


def test_public_version_metadata_is_aligned():
    """Python and Web public package metadata expose the same version."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    web_package = json.loads(
        (ROOT / "web" / "package.json").read_text(encoding="utf-8")
    )
    web_lock = json.loads(
        (ROOT / "web" / "package-lock.json").read_text(encoding="utf-8")
    )

    assert pyproject["project"]["version"] == zf.__version__
    assert web_package["version"] == zf.__version__
    assert web_lock["version"] == zf.__version__
    assert web_lock["packages"][""]["version"] == zf.__version__
