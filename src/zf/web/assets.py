from __future__ import annotations

from pathlib import Path


def web_repo_root(
    package_repo_root: Path,
    installed_source_root: Path | None,
) -> Path:
    """Prefer Web assets beside the Python source imported by this process."""
    if (package_repo_root / "web").is_dir():
        return package_repo_root
    if (
        installed_source_root is not None
        and (installed_source_root / "web").is_dir()
    ):
        return installed_source_root
    return package_repo_root


__all__ = ["web_repo_root"]
