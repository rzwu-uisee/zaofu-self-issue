"""Mechanical Task Map skill-requirement normalization."""

from __future__ import annotations

from typing import Any


def validated_writer_task_skills(
    task: dict[str, Any],
    *,
    task_id: str,
) -> list[str]:
    if "skills_required" not in task:
        return []
    raw = task.get("skills_required")
    if not isinstance(raw, list):
        raise RuntimeError(
            f"writer fanout task {task_id or '<unknown>'} skills_required must be a list"
        )
    skills: list[str] = []
    for index, value in enumerate(raw):
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError(
                "writer fanout task "
                f"{task_id or '<unknown>'} skills_required[{index}] "
                "must be a non-empty string"
            )
        skill = value.strip()
        if skill in skills:
            raise RuntimeError(
                f"writer fanout task {task_id or '<unknown>'} "
                f"duplicates required skill {skill!r}"
            )
        skills.append(skill)
    return skills


__all__ = ["validated_writer_task_skills"]
