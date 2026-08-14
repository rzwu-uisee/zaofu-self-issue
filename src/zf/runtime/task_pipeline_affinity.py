"""Task-stage provider-session affinity for the v4 Task Pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.core.state.role_sessions import RoleSessionRegistry


def task_pipeline_preferred_roles(
    runtime: Any,
    *,
    generation_contexts: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, str]]:
    """Prefer the slot that owns a Task-stage provider transcript."""

    if not hasattr(runtime, "state_dir") or not hasattr(runtime, "project_root"):
        return {}
    registry = RoleSessionRegistry(
        Path(runtime.state_dir) / "role_sessions.yaml",
        project_root=str(runtime.project_root),
    )
    configured_instances = {
        str(role.instance_id)
        for role in getattr(runtime.config, "roles", []) or []
        if str(role.instance_id).strip()
    }
    result: dict[str, dict[str, str]] = {}
    for binding in registry.task_stage_bindings().values():
        task_id = str(binding.get("task_id") or "")
        context = generation_contexts.get(task_id)
        if context is None or str(binding.get("status") or "") == "archived":
            continue
        task_map_generation = str(context.get("task_map_generation") or "")
        if not str(binding.get("rework_affinity_id") or "").startswith(
            f"{task_map_generation}:"
        ):
            continue
        stage = str(binding.get("stage") or "")
        preferred = str(
            binding.get("provider_session_role_instance") or ""
        )
        if not preferred:
            preferred = role_instance_from_codex_session_path(
                Path(runtime.state_dir),
                str(binding.get("session_path") or ""),
            )
        preferred = preferred or str(
            binding.get("current_role_instance") or ""
        )
        if stage and preferred in configured_instances:
            result.setdefault(task_id, {})[stage] = preferred
    return result


def role_instance_from_codex_session_path(
    state_dir: Path,
    session_path: str,
) -> str:
    if not session_path:
        return ""
    try:
        relative = Path(session_path).resolve().relative_to(
            (Path(state_dir) / "workdirs").resolve()
        )
    except (OSError, ValueError):
        return ""
    parts = relative.parts
    if len(parts) < 3 or parts[1:3] != ("codex-home", "sessions"):
        return ""
    return str(parts[0])


__all__ = [
    "role_instance_from_codex_session_path",
    "task_pipeline_preferred_roles",
]
