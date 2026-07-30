"""Ownership-aware cleanup for Autoresearch worktree preparation."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from zf.core.state.atomic_io import atomic_write_text


PREPARATION_MANIFEST = "worktree-preparation.json"


def path_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def write_preparation_journal(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def cleanup_prepared_worktree(
    *,
    worktree: Path,
    run_dir: Path,
) -> dict[str, Any]:
    """Restore only unchanged framework-owned files after one experiment."""

    manifest_path = run_dir / "worktree-preparation" / PREPARATION_MANIFEST
    outcome: dict[str, Any] = {
        "status": "not_prepared",
        "restored": [],
        "removed": [],
        "retained": [],
    }
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return outcome
    if not isinstance(manifest, dict):
        return outcome

    root = Path(worktree).resolve()
    preparation_dir = Path(run_dir).resolve() / "worktree-preparation"
    zf_yaml = root / "zf.yaml"
    generated_zf_sha256 = str(manifest.get("generated_zf_sha256") or "")
    if generated_zf_sha256 and path_sha256(zf_yaml) == generated_zf_sha256:
        original = preparation_dir / "zf.yaml.original"
        if bool(manifest.get("zf_existed")) and original.is_file():
            shutil.copy2(original, zf_yaml)
            outcome["restored"].append(str(zf_yaml))
        else:
            zf_yaml.unlink(missing_ok=True)
            outcome["removed"].append(str(zf_yaml))
    elif zf_yaml.exists():
        outcome["retained"].append(str(zf_yaml))

    seed = root / "autoresearch-seed.txt"
    generated_seed_sha256 = str(manifest.get("generated_seed_sha256") or "")
    if generated_seed_sha256 and path_sha256(seed) == generated_seed_sha256:
        original_seed = preparation_dir / "autoresearch-seed.txt.original"
        if bool(manifest.get("seed_existed")) and original_seed.is_file():
            shutil.copy2(original_seed, seed)
            outcome["restored"].append(str(seed))
        else:
            seed.unlink(missing_ok=True)
            outcome["removed"].append(str(seed))
    elif seed.exists():
        outcome["retained"].append(str(seed))
    _cleanup_web_dependencies(
        root=root,
        manifest=manifest,
        outcome=outcome,
    )
    outcome["status"] = "retained" if outcome["retained"] else "cleaned"
    return outcome


def cleanup_interrupted_prepared_worktree(
    *,
    worktree: Path,
) -> dict[str, Any]:
    """Recover framework-owned preparation after a resident is interrupted."""

    root = Path(worktree).resolve()
    manifest_path = _latest_manifest(root)
    if manifest_path is None:
        return _empty_outcome("not_prepared")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_outcome("not_prepared")
    if not isinstance(manifest, dict) or not _cleanup_pending(root, manifest):
        return _empty_outcome("not_needed")
    return cleanup_prepared_worktree(
        worktree=root,
        run_dir=manifest_path.parents[1],
    )


def _empty_outcome(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "restored": [],
        "removed": [],
        "retained": [],
    }


def _latest_manifest(root: Path) -> Path | None:
    manifests = (root / ".zf" / "autoresearch" / "runs").glob(
        f"*/worktree-preparation/{PREPARATION_MANIFEST}"
    )
    newest: tuple[int, Path] | None = None
    for path in manifests:
        try:
            candidate = (path.stat().st_mtime_ns, path)
        except OSError:
            continue
        if newest is None or candidate[0] > newest[0]:
            newest = candidate
    return newest[1] if newest is not None else None


def _cleanup_pending(root: Path, manifest: dict[str, Any]) -> bool:
    generated_zf_sha256 = str(manifest.get("generated_zf_sha256") or "")
    if (
        generated_zf_sha256
        and path_sha256(root / "zf.yaml") == generated_zf_sha256
    ):
        return True
    generated_seed_sha256 = str(manifest.get("generated_seed_sha256") or "")
    if (
        generated_seed_sha256
        and path_sha256(root / "autoresearch-seed.txt")
        == generated_seed_sha256
    ):
        return True
    web = manifest.get("web_dependencies")
    if not isinstance(web, dict) or bool(web.get("preexisting")):
        return False
    mode = str(web.get("mode") or "")
    dependencies = root / "web" / "node_modules"
    return mode in {"linked", "installed", "preparing"} and (
        dependencies.exists() or dependencies.is_symlink()
    )


def _cleanup_web_dependencies(
    *,
    root: Path,
    manifest: dict[str, Any],
    outcome: dict[str, Any],
) -> None:
    web = manifest.get("web_dependencies")
    if not isinstance(web, dict) or bool(web.get("preexisting")):
        return
    if str(web.get("path") or "") != "web/node_modules":
        return
    mode = str(web.get("mode") or "")
    dependencies = root / "web" / "node_modules"
    if not dependencies.exists() and not dependencies.is_symlink():
        return
    if mode == "linked":
        expected = str(web.get("symlink_target") or "")
        try:
            current = os.readlink(dependencies) if dependencies.is_symlink() else ""
        except OSError:
            current = ""
        if expected and current == expected:
            dependencies.unlink()
            outcome["removed"].append(str(dependencies))
        else:
            outcome["retained"].append(str(dependencies))
        return
    if mode == "preparing" and dependencies.is_symlink():
        expected = str(web.get("symlink_target") or "")
        try:
            current = os.readlink(dependencies)
        except OSError:
            current = ""
        if expected and current == expected:
            dependencies.unlink()
            outcome["removed"].append(str(dependencies))
        else:
            outcome["retained"].append(str(dependencies))
        return
    if mode in {"installed", "preparing"}:
        if dependencies.is_symlink() or not dependencies.is_dir():
            outcome["retained"].append(str(dependencies))
            return
        shutil.rmtree(dependencies)
        outcome["removed"].append(str(dependencies))


__all__ = [
    "PREPARATION_MANIFEST",
    "cleanup_interrupted_prepared_worktree",
    "cleanup_prepared_worktree",
    "path_sha256",
    "write_preparation_journal",
]
