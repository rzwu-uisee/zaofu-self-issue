"""Generation-scoped selectors used by the Task Pipeline runtime."""

from __future__ import annotations

from typing import Any, Iterable, Mapping


def operation_matches_generation(
    operation: Mapping[str, Any],
    contexts: Mapping[str, Mapping[str, Any]],
) -> bool:
    """Return whether an operation belongs to one admitted generation."""

    task_id = str(operation.get("task_id") or "")
    context = contexts.get(task_id)
    if context is None:
        return False
    return (
        str(operation.get("workflow_run_id") or "")
        == str(context.get("workflow_run_id") or "")
        and str(operation.get("task_map_generation") or "")
        == str(context.get("task_map_generation") or "")
        and bool(str(operation.get("task_pipeline_stage") or "").strip())
    )


def terminal_dependency_ids(runtime: Any, tasks: Iterable[Any]) -> set[str]:
    """Return dependencies already terminal in canonical Task state."""

    terminal: set[str] = set()
    for task in tasks:
        for dependency_id in getattr(task, "blocked_by", ()) or ():
            dependency = runtime.task_store.get(str(dependency_id))
            if dependency is not None and str(dependency.status) == "done":
                terminal.add(str(dependency_id))
    return terminal


__all__ = ["operation_matches_generation", "terminal_dependency_ids"]
