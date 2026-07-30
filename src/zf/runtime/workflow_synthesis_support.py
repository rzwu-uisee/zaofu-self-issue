"""Static catalog and prompt helpers for Workflow Synthesis."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from zf.core.workflow.generic_workflow import (
    COMPLETION_PROFILES,
    REGISTERED_OPERATIONS,
    REGISTERED_TEMPLATES,
)
from zf.runtime.execution_profiles import execution_profile_catalog


WORKFLOW_SYNTHESIS_RESULT_SCHEMA = "workflow-synthesis-result.v1"
ALLOWED_FLOW_FAMILIES = frozenset({
    "IssueFlow",
    "PrdFlow",
    "RefactorFlow",
    "Workflow",
})


def admission_catalog(config: Any, project_root: Path) -> dict[str, set[str]]:
    roles: set[str] = set()
    skills: set[str] = set()
    for role in getattr(config, "roles", []) or []:
        roles.update(filter(None, {
            str(getattr(role, "name", "") or ""),
            str(getattr(role, "instance_id", "") or ""),
        }))
        raw_skills = getattr(role, "skills", [])
        if isinstance(raw_skills, (list, tuple, set)):
            skills.update(
                str(item).strip()
                for item in raw_skills
                if str(item).strip()
            )
    for skill_path in Path(project_root).glob("skills/*/SKILL.md"):
        skills.add(skill_path.parent.name)
    return {
        "roles": roles,
        "skills": skills,
        "profiles": set(execution_profile_catalog(config)),
        "templates": set(REGISTERED_TEMPLATES),
        "operations": set(REGISTERED_OPERATIONS),
        "completion_profiles": set(COMPLETION_PROFILES),
    }


def synthesis_prompt(
    *,
    request: Mapping[str, Any],
    requirement: Mapping[str, Any],
    catalog: Mapping[str, set[str]],
) -> str:
    contract = {
        "schema_version": WORKFLOW_SYNTHESIS_RESULT_SCHEMA,
        "request_id": str(request.get("request_id") or ""),
        "request_revision": int(request.get("revision") or 0),
        "requirement_ref": str(request.get("requirement_spec_ref") or ""),
        "requirement_digest": str(
            request.get("requirement_spec_digest") or ""
        ),
        "allowed_flow_families": sorted(ALLOWED_FLOW_FAMILIES),
        "allowed_roles": sorted(catalog["roles"]),
        "allowed_skills": sorted(catalog["skills"]),
        "allowed_profiles": sorted(catalog["profiles"]),
        "allowed_generic_templates": sorted(catalog["templates"]),
        "allowed_generic_operations": sorted(catalog["operations"]),
        "allowed_completion_profiles": sorted(
            catalog["completion_profiles"]
        ),
        "requirement": dict(requirement),
    }
    return (
        "Use the zf-workflow-synthesis method. Return exactly one JSON object "
        "with no prose or code fence. Do not emit expanded config, handlers, "
        "approval, submit, or task objects. Contract:\n"
        + json.dumps(contract, ensure_ascii=False, sort_keys=True)
    )


def normalized_strings(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def safe_component(value: str) -> str:
    return "".join(
        char if char.isalnum() or char in "._-" else "_"
        for char in str(value)
    )[:120] or "request"


__all__ = [
    "ALLOWED_FLOW_FAMILIES",
    "WORKFLOW_SYNTHESIS_RESULT_SCHEMA",
    "admission_catalog",
    "normalized_strings",
    "safe_component",
    "synthesis_prompt",
]
