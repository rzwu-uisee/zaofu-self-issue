"""Run-pinned Plan Artifact Package rollout policy."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.runtime.plan_artifact_package import (
    PLAN_ARTIFACT_PACKAGE_MODES,
    PlanArtifactPackageError,
    artifact_package_mode,
    hydrate_plan_artifact_package,
)


def effective_artifact_package_mode(
    *,
    state_dir: Path,
    payload: Mapping[str, Any],
    metadata: Mapping[str, Any] | None = None,
) -> str:
    """Resolve one mode without retroactively upgrading an existing run."""

    package_ref = str(payload.get("plan_artifact_package_ref") or "").strip()
    package_digest = str(
        payload.get("plan_artifact_package_digest") or ""
    ).strip()
    legacy_package = False
    if package_ref and package_digest:
        try:
            package = hydrate_plan_artifact_package(
                state_dir,
                {"ref": package_ref, "sha256": package_digest},
                validate_ports=False,
            )
        except Exception:
            return artifact_package_mode(metadata)
        pinned = str(package.get("artifact_package_mode") or "").strip().lower()
        if pinned:
            _require_mode(pinned)
            return pinned
        legacy_package = True
    explicit = str(payload.get("artifact_package_mode") or "").strip().lower()
    if explicit:
        _require_mode(explicit)
        return explicit
    if legacy_package:
        # Pre-R0 packages were shadow unless their dispatch pinned otherwise.
        return "shadow"
    return artifact_package_mode(metadata)


def _require_mode(mode: str) -> None:
    if mode not in PLAN_ARTIFACT_PACKAGE_MODES:
        raise PlanArtifactPackageError(
            f"unsupported artifact package mode: {mode}"
        )


__all__ = ["effective_artifact_package_mode"]
