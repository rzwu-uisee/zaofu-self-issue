"""Shared authorization rules for immutable artifact occurrences."""

from __future__ import annotations

from typing import Any, Mapping


class ArtifactAccessError(ValueError):
    """An artifact occurrence is outside the caller's declared scope."""


def artifact_access_allowed(
    scope_value: object,
    *,
    actor: str = "",
    role: str = "",
    purpose: str = "",
) -> bool:
    scope = scope_value if isinstance(scope_value, Mapping) else {}
    visibility = str(scope.get("visibility") or "project").strip()
    if visibility not in {"project", "public"}:
        return False

    actors = _values(scope.get("actors"))
    legacy_actor = str(scope.get("actor") or "").strip()
    if legacy_actor:
        actors.add(legacy_actor)
    if actors and not ({str(actor or "").strip(), str(role or "").strip()} & actors):
        return False

    roles = _values(scope.get("roles"))
    legacy_role = str(scope.get("role") or "").strip()
    if legacy_role:
        roles.add(legacy_role)
    if roles and str(role or "").strip() not in roles:
        return False

    purposes = _values(scope.get("purposes"))
    legacy_purpose = str(scope.get("purpose") or "").strip()
    if legacy_purpose:
        purposes.add(legacy_purpose)
    if purposes and str(purpose or "").strip() not in purposes:
        return False
    return True


def require_artifact_access(
    scope_value: object,
    *,
    actor: str = "",
    role: str = "",
    purpose: str = "",
) -> None:
    if not artifact_access_allowed(
        scope_value,
        actor=actor,
        role=role,
        purpose=purpose,
    ):
        raise ArtifactAccessError("artifact occurrence is not authorized")


def _values(value: Any) -> set[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}


__all__ = [
    "ArtifactAccessError",
    "artifact_access_allowed",
    "require_artifact_access",
]
