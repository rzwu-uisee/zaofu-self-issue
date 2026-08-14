"""Normalize action-bound Channel setup Plan options."""

from __future__ import annotations

from typing import Any

from zf.runtime.channel_contracts import discussion_engine_mode
from zf.runtime.channel_templates import materialize_channel_template


def normalize_channel_setup_submit_payload(
    raw_payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    allowed_keys = {
        "channel_id",
        "mode",
        "name",
        "overrides",
        "task_id",
        "template_id",
        "thread_id",
    }
    unknown = sorted(set(raw_payload) - allowed_keys)
    if unknown:
        return (
            {},
            {},
            "unsupported submit_payload field(s): " + ", ".join(unknown),
        )
    template_id = str(raw_payload.get("template_id") or "").strip()
    if not template_id:
        return {}, {}, "submit_payload.template_id is required"
    overrides = raw_payload.get("overrides")
    if overrides is not None and not isinstance(overrides, dict):
        return {}, {}, "submit_payload.overrides must be a mapping"
    materialized, error = materialize_channel_template(
        template_id,
        overrides=overrides,
    )
    if error or materialized is None:
        return {}, {}, error or "channel template preflight failed"

    mode = str(raw_payload.get("mode") or "").strip()
    if not mode:
        return (
            {},
            {},
            "submit_payload.mode is required for Channel setup Plans",
        )
    if mode not in {"conversation", "clarification", "multi_lens"}:
        return (
            {},
            {},
            "submit_payload.mode must be conversation, clarification, or multi_lens",
        )

    payload: dict[str, Any] = {
        "template_id": template_id,
        "mode": mode,
    }
    name = str(raw_payload.get("name") or "").strip()
    if name:
        payload["name"] = name
    for key in ("channel_id", "task_id", "thread_id"):
        value = str(raw_payload.get(key) or "").strip()
        if value:
            payload[key] = value
    if isinstance(overrides, dict) and overrides:
        payload["overrides"] = overrides

    members = [
        {
            "member_id": str(member.get("member_id") or ""),
            "role": str(member.get("channel_role") or ""),
            "permission_profile": str(
                member.get("permission_profile") or "read_only"
            ),
        }
        for member in materialized["members"]
        if isinstance(member, dict)
    ]
    discussion = (
        materialized.get("discussion")
        if isinstance(materialized.get("discussion"), dict)
        else {}
    )
    engine_mode = discussion_engine_mode(mode)
    details = {
        "template_id": template_id,
        "template_name": str(materialized.get("name") or template_id),
        "template_version": str(materialized.get("template_version") or ""),
        "template_digest": str(materialized.get("template_digest") or ""),
        "materialization_digest": str(
            materialized.get("materialization_digest") or ""
        ),
        "member_count": len(members),
        "members": members,
        "product_mode": mode,
        "mode": mode,
        "engine_mode": engine_mode,
        "routing_strategy": {
            "conversation": "single_responder",
            "clarification": "facilitated_relay",
            "multi_lens": "blind_fanout_then_synthesis",
        }[mode],
        "first_pass_reply_count": (
            len(members)
            if engine_mode == "fanout_then_synthesis"
            else min(1, len(members))
        ),
        "max_rounds": int(discussion.get("max_rounds") or 0),
    }
    return payload, details, ""


__all__ = ["normalize_channel_setup_submit_payload"]
