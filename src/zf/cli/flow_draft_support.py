"""Shared helpers for portable typed-flow configuration drafts."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


def default_tmux_session(project: str) -> str:
    slug = re.sub(
        r"[^a-zA-Z0-9_-]+",
        "-",
        str(project or "flow").strip(),
    ).strip("-")
    slug = slug.lower()[:48] or "flow"
    return f"zf-{slug}"


def explicit_orchestrator_spec(backend: str) -> dict[str, Any]:
    return {
        "orchestrator": {
            "backend": backend,
            "wake_min_interval_s": 5,
        },
        "roles": [{
            "name": "orchestrator",
            "instance_id": "orchestrator",
            "role_kind": "reader",
            "backend": backend,
            "permission_mode": "bypass",
            "transport": "tmux",
            "stuck_threshold_seconds": 900,
            "spawn_ready_timeout_seconds": 240,
            "triggers": [
                "dispatch.silent_stall",
                "orchestrator.rework.triage.requested",
            ],
            "publishes": ["orchestrator.rework.triage.recorded"],
            "skills": [
                "zf-yoke-orchestrator-role-context",
                "zf-harness-state-sync",
            ],
        }],
    }


def draft_runtime_profile_doc(
    *,
    name: str,
    backend: str,
    kind: str = "",
    role_skill_bundles: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Return the executable runtime profile embedded in portable flow drafts."""

    run_manager_backend = backend if backend in {"codex", "claude-code"} else ""
    resident_enabled = bool(run_manager_backend)
    spec: dict[str, Any] = {
        "runtime": {
            "workdirs": {
                "enabled": True,
                "mode": "worktree",
            },
            "run_manager": {
                "backend": run_manager_backend,
                "resident_agent": {
                    "enabled": resident_enabled,
                    "session_mode": "dedicated",
                },
            },
            "autoresearch_resident": {
                "enabled": resident_enabled,
                "interval_seconds": 10,
                "max_actions_per_tick": 1,
            },
            "feishu_inbound": {
                "enabled": "${ZF_FEISHU_INBOUND_ENABLED:-false}",
                "mode": "bridge",
                "require_routing": True,
            },
        },
        "workflow": {
            "candidate_quality_source": "task_contract_required",
            "rework_routing": (
                {"static_gate.failed": "fix-lane-0"}
                if kind == "issue"
                else {"static_gate.failed": "dev-lane-0"}
                if kind in {"prd", "refactor"}
                else {}
            ),
            "work_units": {
                "enabled": True,
                "split_quality": {
                    "mode": "blocking",
                    "max_acceptance_criteria": 8,
                },
            },
        },
    }
    if kind in {"issue", "prd", "refactor"} and role_skill_bundles:
        spec["flow_defaults"] = {
            kind: {
                "roleSkillBundles": role_skill_bundles,
            },
        }
    return {
        "apiVersion": "zaofu.dev/v1",
        "kind": "ConfigProfile",
        "metadata": {"name": name},
        "spec": spec,
    }


def non_empty_mapping(value: object) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, item in value.items():
        if not isinstance(item, list):
            continue
        values = [str(entry) for entry in item if str(entry).strip()]
        if values:
            out[str(key)] = values
    return out


def skill_sources_from_adapter_plan(
    adapter_plan: dict[str, Any],
    *,
    project_root: Path,
) -> list[dict[str, str]]:
    loaded = adapter_plan.get("loaded_skills")
    if not isinstance(loaded, list):
        return []
    source_paths: dict[str, Path] = {}
    for item in loaded:
        if not isinstance(item, dict):
            continue
        source_name = str(item.get("source_name") or "")
        if source_name in {"", "project", "state", "yoke"}:
            continue
        source_ref = str(item.get("source_ref") or "")
        if not source_ref:
            continue
        skill_root = Path(source_ref).expanduser().parent.parent
        config_name = (
            "zaofu-skills"
            if source_name in {"zaofu", "skill-source:zaofu"}
            else source_name.removeprefix("skill-source:")
        )
        source_paths[config_name] = skill_root
    return [
        {
            "name": name,
            "path": _display_or_relative_path(root, project_root),
            "mode": "readonly",
        }
        for name, root in sorted(source_paths.items())
    ]


def _display_or_relative_path(path: Path, project_root: Path) -> str:
    root = project_root.expanduser().resolve()
    target = path.expanduser().resolve(strict=False)
    try:
        return os.path.relpath(target, root)
    except ValueError:
        return str(target)


__all__ = [
    "default_tmux_session",
    "draft_runtime_profile_doc",
    "explicit_orchestrator_spec",
    "non_empty_mapping",
    "skill_sources_from_adapter_plan",
]
