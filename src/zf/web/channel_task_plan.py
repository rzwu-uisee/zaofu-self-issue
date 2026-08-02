"""Normalize Task proposals sourced from a confirmed Channel PRD."""

from __future__ import annotations

from typing import Any


def normalize_channel_task_submit_payload(
    raw_payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    allowed_keys = {
        "acceptance",
        "acceptance_criteria",
        "channel_authority",
        "explicit_non_goals",
        "objective",
        "priority",
        "scope",
        "skills_required",
        "task_id",
        "title",
    }
    unknown = sorted(set(raw_payload) - allowed_keys)
    if unknown:
        return (
            {},
            {},
            "unsupported submit_payload field(s): " + ", ".join(unknown),
        )
    title = str(
        raw_payload.get("title")
        or raw_payload.get("objective")
        or ""
    ).strip()
    if not title:
        return {}, {}, "submit_payload.title is required"
    authority = (
        dict(raw_payload.get("channel_authority"))
        if isinstance(raw_payload.get("channel_authority"), dict)
        else {}
    )
    required_authority = {
        "channel_id",
        "channel_member_id",
        "leader_revision",
        "prd_revision",
        "source_digest",
        "source_ref",
        "thread_id",
    }
    missing = sorted(
        key for key in required_authority
        if authority.get(key) in (None, "")
    )
    if missing:
        return (
            {},
            {},
            "Channel Task proposal is missing authority field(s): "
            + ", ".join(missing)
            + "; open Channel Details and use Create Task from PRD so the "
            "runtime can bind canonical authority",
        )
    try:
        priority = int(raw_payload.get("priority") or 3)
        leader_revision = int(authority["leader_revision"])
        prd_revision = int(authority["prd_revision"])
    except (TypeError, ValueError):
        return (
            {},
            {},
            "Channel Task proposal revisions/priority must be integers",
        )
    objective = str(raw_payload.get("objective") or title).strip()
    contract = {
        "schema_version": "task-contract.v1",
        "behavior": objective,
        "acceptance": str(
            raw_payload.get("acceptance")
            or "Deliver the exact confirmed Channel PRD."
        ),
        "acceptance_criteria": _string_values(
            raw_payload.get("acceptance_criteria")
        ),
        "scope": _string_values(raw_payload.get("scope")),
        "explicit_non_goals": _string_values(
            raw_payload.get("explicit_non_goals")
        ),
        "source_ref": str(authority["source_ref"]),
        "source_revision": str(prd_revision),
        "source_mode": "channel_prd",
        "source_title": title,
        "evidence_contract": {
            "channel_id": str(authority["channel_id"]),
            "thread_id": str(authority["thread_id"]),
            "channel_member_id": str(authority["channel_member_id"]),
            "leader_revision": leader_revision,
            "prd_revision": prd_revision,
            "source_digest": str(authority["source_digest"]),
        },
    }
    payload: dict[str, Any] = {
        "title": title,
        "priority": priority,
        "execution_mode": "workflow",
        "contract": contract,
        "channel_authority": authority,
        "source_artifact": {
            "kind": "channel_prd",
            "ref": str(authority["source_ref"]),
            "digest": str(authority["source_digest"]),
            "revision": prd_revision,
        },
    }
    task_id = str(raw_payload.get("task_id") or "").strip()
    if task_id:
        payload["task_id"] = task_id
    skills = _string_values(raw_payload.get("skills_required"))
    if skills:
        payload["skills_required"] = skills
    return payload, {
        "title": title,
        "source_ref": str(authority["source_ref"]),
        "source_digest": str(authority["source_digest"]),
        "prd_revision": prd_revision,
    }, ""


def _string_values(value: object) -> list[str]:
    if isinstance(value, str):
        values: list[object] = value.splitlines()
    elif isinstance(value, list):
        values = value
    else:
        return []
    return list(dict.fromkeys(
        str(item).strip()
        for item in values
        if str(item).strip()
    ))
