"""Mechanical authority gate for Channel-origin Workflow planning."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.runtime.channel_projection import project_channel


_CHANNEL_AUTHORITY_KEYS = (
    "channel_id",
    "thread_id",
    "channel_member_id",
    "leader_revision",
    "prd_revision",
    "source_ref",
    "source_digest",
)


def channel_authority_context_from_task(task: Any) -> dict[str, Any]:
    """Derive immutable Channel authority from one canonical Task contract."""

    contract = getattr(task, "contract", None)
    if contract is None or str(getattr(contract, "source_mode", "")) != "channel_prd":
        return {}
    evidence = getattr(contract, "evidence_contract", {})
    evidence = evidence if isinstance(evidence, dict) else {}
    context = {
        "channel_id": evidence.get("channel_id"),
        "thread_id": evidence.get("thread_id") or "main",
        "channel_member_id": evidence.get("channel_member_id"),
        "leader_revision": evidence.get("leader_revision"),
        "prd_revision": evidence.get("prd_revision") or getattr(
            contract, "source_revision", ""
        ),
        "source_ref": getattr(contract, "source_ref", ""),
        "source_digest": evidence.get("source_digest"),
    }
    return {
        key: value
        for key, value in context.items()
        if value not in (None, "")
    }


def bind_task_channel_authority(
    context: dict[str, Any],
    task: Any,
) -> dict[str, Any]:
    """Overlay canonical Task authority on untrusted Plan context."""

    authority = channel_authority_context_from_task(task)
    return {**dict(context), **authority} if authority else dict(context)


def bind_task_channel_authority_to_submit_payload(
    payload: dict[str, Any],
    task: Any,
) -> dict[str, Any]:
    """Bind a workflow submit payload to its Task-owned Channel authority."""

    authority = channel_authority_context_from_task(task)
    if not authority:
        return dict(payload)
    parameters = (
        dict(payload.get("parameters"))
        if isinstance(payload.get("parameters"), dict)
        else {}
    )
    return {
        **payload,
        "parameters": {**parameters, **authority},
    }


def channel_workflow_authority_error(
    state_dir: Path,
    context: dict[str, Any],
) -> str:
    """Validate the immutable Channel/Leader/PRD planning binding.

    This is intentionally semantic-free. The Kanban Agent still decides which
    workflow option to recommend; the gate only proves who may mint a proposal
    and which confirmed PRD revision the proposal is allowed to reference.
    """

    channel_id = str(context.get("channel_id") or "").strip()
    thread_id = str(context.get("thread_id") or "main").strip() or "main"
    member_id = str(context.get("channel_member_id") or "").strip()
    if not channel_id or not member_id:
        return "Channel workflow planning requires exact channel/member binding"
    channel = project_channel(Path(state_dir), channel_id) or {}
    if not channel:
        return "Channel workflow planning origin does not exist"
    leader_member_id = str(channel.get("leader_member_id") or "")
    if not leader_member_id:
        return "Channel has no active Leader binding"
    if member_id != leader_member_id:
        return "only the exact Channel Leader may request workflow planning"
    try:
        expected_leader_revision = int(context.get("leader_revision") or 0)
    except (TypeError, ValueError):
        expected_leader_revision = 0
    if expected_leader_revision != int(channel.get("leader_revision") or 0):
        return "Channel Leader revision is stale"
    member = next(
        (
            item
            for item in channel.get("members") or []
            if isinstance(item, dict)
            and str(item.get("member_id") or "") == member_id
        ),
        None,
    )
    if member is None or str(member.get("status") or "active").lower() in {
        "failed",
        "rejected",
        "removed",
        "suspended",
    }:
        return "Channel Leader is not an active member"
    if "propose_workflow" not in set(member.get("permissions") or []):
        return "Channel Leader lacks propose_workflow permission"
    consensus = (
        channel.get("consensus", {}).get(thread_id)
        if isinstance(channel.get("consensus"), dict)
        else None
    )
    if (
        not isinstance(consensus, dict)
        or str(consensus.get("status") or "") != "reached"
    ):
        return "Channel workflow planning requires a confirmed PRD"
    current_ref = str(
        consensus.get("prd_ref")
        or consensus.get("artifact_ref")
        or ""
    )
    current_digest = str(
        consensus.get("prd_digest")
        or consensus.get("artifact_digest")
        or ""
    ).removeprefix("sha256:")
    expected_ref = str(
        context.get("source_ref")
        or context.get("channel_prd_ref")
        or ""
    )
    expected_digest = str(
        context.get("source_digest")
        or context.get("channel_prd_digest")
        or ""
    ).removeprefix("sha256:")
    if not expected_ref or expected_ref != current_ref:
        return "Channel workflow planning PRD ref is stale"
    if not expected_digest or expected_digest != current_digest:
        return "Channel workflow planning PRD digest is stale"
    try:
        expected_prd_revision = int(context.get("prd_revision") or 0)
    except (TypeError, ValueError):
        expected_prd_revision = 0
    if expected_prd_revision != int(consensus.get("prd_revision") or 0):
        return "Channel workflow planning PRD revision is stale"
    return ""


def channel_authority_context_from_submit_payload(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Extract the common authority envelope from a Plan-bound action."""

    parameters = (
        payload.get("parameters")
        if isinstance(payload.get("parameters"), dict)
        else {}
    )
    authority = (
        payload.get("channel_authority")
        if isinstance(payload.get("channel_authority"), dict)
        else {}
    )
    context = {
        key: value
        for key in _CHANNEL_AUTHORITY_KEYS
        if (
            value := authority.get(key)
            if key in authority
            else parameters.get(key)
        )
        not in (None, "")
    }
    # Generic Task Plans may carry source_ref/source_digest evidence without
    # originating from a Channel. Channel authority is activated by the
    # canonical Channel identity, not by a coincidental artifact field.
    if not str(context.get("channel_id") or "").strip():
        return {}
    return context


__all__ = [
    "bind_task_channel_authority",
    "bind_task_channel_authority_to_submit_payload",
    "channel_authority_context_from_task",
    "channel_authority_context_from_submit_payload",
    "channel_workflow_authority_error",
]
