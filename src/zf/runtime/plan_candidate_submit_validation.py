"""Pinned Plan candidate validation shared by validate and submit."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


def validate_plan_candidate_semantic(
    *,
    state_dir: Path,
    request: Mapping[str, Any],
    semantic: Mapping[str, Any],
    profile_id: str,
    project_root: Path,
) -> dict[str, Any] | None:
    validation = request.get("plan_candidate_validation")
    if not isinstance(validation, Mapping):
        return None
    if profile_id != "workflow-read":
        _raise_submit_error(
            "plan_candidate_profile_invalid",
            "Plan candidate validation requires the workflow-read profile",
        )
    manifest = validation.get("manifest")
    if not isinstance(manifest, Mapping):
        _raise_submit_error(
            "plan_candidate_context_invalid",
            "operation has no pinned Plan candidate manifest",
        )
    metadata = validation.get("metadata")
    if metadata is not None and not isinstance(metadata, Mapping):
        _raise_submit_error(
            "plan_candidate_context_invalid",
            "operation Plan candidate metadata must be an object",
        )
    writer_policy = validation.get("writer_policy")
    if writer_policy is not None and not isinstance(writer_policy, Mapping):
        _raise_submit_error(
            "plan_candidate_context_invalid",
            "operation Plan candidate writer policy must be an object",
        )
    from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight

    return evaluate_plan_candidate_preflight(
        state_dir=Path(state_dir),
        project_root=Path(project_root),
        reports=[{"report": semantic}],
        manifest=dict(manifest),
        metadata=dict(metadata) if isinstance(metadata, Mapping) else None,
        writer_policy=(
            dict(writer_policy) if isinstance(writer_policy, Mapping) else None
        ),
    )


def _raise_submit_error(code: str, message: str) -> None:
    # Imported lazily so result_submit can delegate here without import cycles.
    from zf.runtime.result_submit import ResultSubmitError

    raise ResultSubmitError(code, message)


__all__ = [
    "validate_plan_candidate_semantic",
]
