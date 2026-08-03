"""Seal a failed writer's committed continuation without carrying dirty work."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.runtime.git_capture import capture_git_state


def capture_writer_failure_continuation(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    workdir = str(payload.get("workdir") or "").strip()
    if not workdir:
        return {}
    git_state = capture_git_state(Path(workdir))
    partial_head = str(git_state.head or "").strip()
    base_commit = str(payload.get("base_commit") or "").strip()
    if not partial_head or partial_head == base_commit:
        return {}
    dirty_files = list(git_state.dirty_files[:100])
    source_branch = str(
        payload.get("source_branch") or git_state.branch or ""
    )
    return {
        "partial_head_commit": partial_head,
        "partial_source_branch": source_branch,
        "partial_worktree_clean": not dirty_files,
        "partial_dirty_files": dirty_files,
        "continuation_commit": partial_head,
        "continuation_ref": source_branch or partial_head,
    }


__all__ = ["capture_writer_failure_continuation"]
