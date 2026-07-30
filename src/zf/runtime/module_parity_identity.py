"""Immutable plan and candidate identity for module-parity handoffs."""

from __future__ import annotations

from typing import Any, Mapping


def module_parity_identity_payload(
    primary: Mapping[str, Any],
    fallback: Mapping[str, Any],
) -> dict[str, str]:
    """Resolve the parent-owned identity that parity and closure must share."""

    candidate_head_commit = _first_text(
        primary,
        fallback,
        "candidate_head_commit",
        "target_commit",
        "commit",
    )
    return {
        "task_map_generation": _first_text(
            primary,
            fallback,
            "task_map_generation",
        ),
        "candidate_head_commit": candidate_head_commit,
        "target_commit": candidate_head_commit,
        "plan_artifact_package_id": _first_text(
            primary,
            fallback,
            "plan_artifact_package_id",
        ),
        "plan_artifact_package_ref": _first_text(
            primary,
            fallback,
            "plan_artifact_package_ref",
        ),
        "plan_artifact_package_digest": _first_text(
            primary,
            fallback,
            "plan_artifact_package_digest",
        ),
    }


def _first_text(
    primary: Mapping[str, Any],
    fallback: Mapping[str, Any],
    *keys: str,
) -> str:
    for key in keys:
        for source in (primary, fallback):
            text = str(source.get(key) or "").strip()
            if text:
                return text
    return ""


__all__ = ["module_parity_identity_payload"]
