"""Deterministic failed-task rework scope expansion."""

from __future__ import annotations

import json
from pathlib import Path

from zf.runtime.artifact_refs import resolve_runtime_artifact_ref
from zf.runtime.writer_fanout_admission import writer_task_items
from zf.runtime.task_refs import _path_allowed_by_scope


def expand_rework_task_ids(
    failed_task_ids: list[str],
    *,
    task_map_ref: str,
    state_dir: Path,
    project_root: Path,
    completed_task_ids: set[str] | None = None,
) -> list[str]:
    """Return failed tasks plus every transitive downstream consumer."""
    failed = _dedupe(failed_task_ids)
    if not failed or not str(task_map_ref or "").strip():
        return failed
    path = resolve_runtime_artifact_ref(
        task_map_ref,
        project_root=Path(project_root),
        state_dir=Path(state_dir),
    )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return failed
    items = writer_task_items(data)
    if not items:
        return failed
    closure = set(failed)
    changed = True
    while changed:
        changed = False
        for item in items:
            task_id = str(item.get("task_id") or "").strip()
            dependencies = {
                str(value).strip()
                for value in (
                    list(item.get("blocked_by") or [])
                    + list(item.get("depends_on") or [])
                )
                if str(value or "").strip()
            }
            if task_id and task_id not in closure and dependencies & closure:
                closure.add(task_id)
                changed = True
    completed = set(completed_task_ids or set()) - set(failed)
    ordered = [
        str(item.get("task_id") or "")
        for item in items
        if str(item.get("task_id") or "") in closure
        and str(item.get("task_id") or "") not in completed
    ]
    return _dedupe([*failed, *ordered])


def task_ids_for_rework_paths(
    rework_paths: list[str],
    *,
    task_map_ref: str,
    state_dir: Path,
    project_root: Path,
) -> list[str]:
    """Map verifier finding paths to their deterministic Task Map owners."""

    paths = _dedupe(rework_paths)
    if not paths or not str(task_map_ref or "").strip():
        return []
    task_map_path = resolve_runtime_artifact_ref(
        task_map_ref,
        project_root=Path(project_root),
        state_dir=Path(state_dir),
    )
    try:
        data = json.loads(task_map_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out: list[str] = []
    for item in writer_task_items(data):
        task_id = str(item.get("task_id") or "").strip()
        allowed_paths = [
            str(path).strip()
            for path in item.get("allowed_paths") or []
            if str(path or "").strip()
        ]
        if task_id and any(
            _path_allowed_by_scope(path, allowed_paths) for path in paths
        ):
            out.append(task_id)
    return _dedupe(out)


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(
        str(value).strip() for value in values if str(value or "").strip()
    ))


__all__ = ["expand_rework_task_ids", "task_ids_for_rework_paths"]
