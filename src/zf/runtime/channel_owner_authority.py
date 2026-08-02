"""Mechanical Channel Owner and scoped delegate authorization."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def channel_owner_authority_error(
    channel: dict[str, Any],
    *,
    actor: str,
    capability: str,
    now: datetime | None = None,
) -> str:
    """Return an authorization error, or an empty string when allowed."""

    actor = str(actor or "").strip()
    owner = str(channel.get("owner_actor_ref") or "").strip()
    if actor and owner and actor == owner:
        return ""
    delegates = (
        channel.get("owner_delegates")
        if isinstance(channel.get("owner_delegates"), list)
        else []
    )
    current = now or datetime.now(timezone.utc)
    for raw in delegates:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("actor_ref") or "").strip() != actor:
            continue
        authorization_ref = str(raw.get("authorization_ref") or "").strip()
        if not authorization_ref:
            return "Channel Owner delegate is missing authorization_ref"
        expires_at = str(raw.get("expires_at") or "").strip()
        expires = _parse_datetime(expires_at)
        if expires is None:
            return "Channel Owner delegate requires a valid expires_at"
        if expires <= current:
            return "Channel Owner delegate authorization has expired"
        scopes = {
            str(item).strip()
            for item in (
                raw.get("scope")
                if isinstance(raw.get("scope"), list)
                else [raw.get("scope")]
            )
            if str(item or "").strip()
        }
        if capability not in scopes and "channel:*" not in scopes:
            return (
                "Channel Owner delegate authorization does not include "
                f"{capability}"
            )
        return ""
    return "only the canonical Channel Owner or an authorized delegate may decide"


def normalize_owner_delegates(value: object) -> list[dict[str, Any]]:
    """Normalize delegate identity without granting missing authority."""

    if not isinstance(value, list):
        return []
    delegates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            continue
        actor_ref = str(raw.get("actor_ref") or "").strip()
        if not actor_ref or actor_ref in seen:
            continue
        seen.add(actor_ref)
        scopes = (
            raw.get("scope")
            if isinstance(raw.get("scope"), list)
            else [raw.get("scope")]
        )
        delegates.append({
            "actor_ref": actor_ref,
            "scope": [
                str(item).strip()
                for item in scopes
                if str(item or "").strip()
            ],
            "expires_at": str(raw.get("expires_at") or "").strip(),
            "authorization_ref": str(
                raw.get("authorization_ref") or ""
            ).strip(),
        })
    return delegates


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


__all__ = [
    "channel_owner_authority_error",
    "normalize_owner_delegates",
]
