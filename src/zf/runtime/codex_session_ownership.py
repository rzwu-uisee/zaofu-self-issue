"""Role-local ownership checks for resumable Codex sessions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.state import role_sessions


def codex_session_exists_for_role(
    registry: Any,
    *,
    role_sessions_root: Path,
    instance_id: str,
    session_id: str,
) -> bool:
    """Return true only when the rollout is visible to this role's home."""

    allowed_roots = (
        Path(role_sessions_root).resolve(),
        Path(role_sessions.CODEX_SESSIONS_ROOT).resolve(),
    )

    def matches(path: Path) -> bool:
        try:
            resolved = path.resolve()
            owned = any(resolved.is_relative_to(root) for root in allowed_roots)
            return (
                path.is_file()
                and owned
                and session_id in path.stem
                and registry._rollout_matches_project(path)
            )
        except OSError:
            return False

    cached = registry.get_path(instance_id)
    if cached is not None and matches(cached):
        return True
    for root in allowed_roots:
        try:
            candidates = root.glob(f"*/*/*/rollout-*-{session_id}.jsonl")
        except OSError:
            continue
        if any(matches(path) for path in candidates):
            return True
    return False


__all__ = ["codex_session_exists_for_role"]
