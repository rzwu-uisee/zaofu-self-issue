"""Resolve scoped Skill canaries without mutating canonical Skill source."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.evolution_contracts import EvolutionContractError
from zf.runtime.evolution_skill import validate_skill_candidate
from zf.runtime.evolution_store import CapabilityRegistry
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


@dataclass(frozen=True)
class SkillOverlayResolution:
    paths: dict[str, Path]
    selected: tuple[dict[str, Any], ...]
    excluded: tuple[dict[str, Any], ...]


def resolve_skill_overlays(
    state_dir: Path,
    *,
    role_instance: str,
    task_family: str,
    cohort: str = "",
    now: str | None = None,
    project_root: Path | None = None,
) -> SkillOverlayResolution:
    """Select one current canary overlay per Skill for a future dispatch."""

    state_dir = Path(state_dir).resolve(strict=False)
    current = now or datetime.now(timezone.utc).isoformat()
    registry = CapabilityRegistry(
        state_dir / "evolution" / "capabilities.json"
    ).load()
    selected_by_name: dict[str, tuple[dict[str, Any], Path]] = {}
    excluded: list[dict[str, Any]] = []
    for row in registry["assets"].values():
        if row.get("asset_kind") != "skill_prompt":
            continue
        reason = skill_overlay_health_reason(
            row,
            state_dir=state_dir,
            now=current,
            project_root=Path(project_root) if project_root is not None else None,
        ) or _overlay_scope_exclusion(
            row,
            role_instance=role_instance,
            task_family=task_family,
            cohort=cohort,
        )
        if reason:
            excluded.append(_decision(row, reason=reason))
            continue
        try:
            path, identity = _materialize_overlay(state_dir, row)
        except (EvolutionContractError, OSError, ValueError) as exc:
            excluded.append(_decision(row, reason=f"invalid_overlay:{exc}"))
            continue
        name = str(identity["skill_name"])
        prior = selected_by_name.get(name)
        if prior is not None and int(prior[0]["version"]) >= int(row["version"]):
            excluded.append(_decision(row, reason="older_active_canary"))
            continue
        if prior is not None:
            excluded.append(_decision(prior[0], reason="superseded_active_canary"))
        selected_by_name[name] = (dict(row), path)
    selected = tuple(
        {
            **_decision(row, reason="selected"),
            "skill_name": name,
            "path": str(path),
        }
        for name, (row, path) in sorted(selected_by_name.items())
    )
    return SkillOverlayResolution(
        paths={name: path for name, (_row, path) in selected_by_name.items()},
        selected=selected,
        excluded=tuple(excluded),
    )


def skill_overlay_health_reason(
    row: Mapping[str, Any],
    *,
    state_dir: Path | None = None,
    now: str | None = None,
    project_root: Path | None = None,
) -> str:
    """Return a mechanical reason that requires a canary overlay revoke."""

    if row.get("state") != "canary_active":
        return "inactive"
    activation = row.get("activation")
    if not isinstance(activation, Mapping) or activation.get("overlay_mode") != "scoped_overlay":
        return "not_scoped_overlay"
    if not isinstance(activation.get("scope"), Mapping):
        return "scope_missing"
    current = now or datetime.now(timezone.utc).isoformat()
    expiry = str(activation.get("expires_at") or "")
    if not expiry or _expired(expiry, current):
        return "expired"
    budget = activation.get("budget")
    if isinstance(budget, Mapping) and _budget_exceeded(row, budget):
        return "budget_exceeded"
    previous_digest = str(activation.get("previous_digest") or "")
    rollback_digest = str((row.get("rollback") or {}).get("previous_digest") or "")
    if previous_digest != rollback_digest:
        return "rollback_identity_drift"
    name = str(activation.get("skill_name") or row.get("skill_name") or "")
    if not name and state_dir is not None:
        descriptor = row.get("artifact_ref")
        if isinstance(descriptor, Mapping):
            try:
                hydrated = hydrate_sidecar_ref(
                    Path(state_dir),
                    dict(descriptor),
                    purpose="skill-overlay-health",
                    actor="run-manager",
                )
            except (EvolutionContractError, OSError, ValueError):
                return "skill_identity_unreadable"
            if isinstance(hydrated.payload, Mapping):
                name = str(hydrated.payload.get("skill_name") or "")
    if not name:
        return "skill_identity_missing"
    return _source_currentness_reason(
        row,
        skill_name=name,
        project_root=project_root,
    )


def _overlay_scope_exclusion(
    row: Mapping[str, Any],
    *,
    role_instance: str,
    task_family: str,
    cohort: str,
) -> str:
    activation = row.get("activation")
    if not isinstance(activation, Mapping):
        return "activation_missing"
    scope = activation.get("scope")
    if not isinstance(scope, Mapping):
        return "scope_missing"
    if not _matches(scope.get("roles"), role_instance):
        return "outside_role_scope"
    if not _matches(scope.get("task_families"), task_family):
        return "outside_task_family_scope"
    if not _matches(scope.get("cohorts"), cohort):
        return "outside_cohort_scope"
    return ""


def _materialize_overlay(
    state_dir: Path,
    row: Mapping[str, Any],
) -> tuple[Path, dict[str, Any]]:
    descriptor = row.get("artifact_ref")
    if not isinstance(descriptor, Mapping):
        raise EvolutionContractError("Skill overlay artifact ref is missing")
    hydrated = hydrate_sidecar_ref(
        state_dir,
        dict(descriptor),
        purpose="skill-canary-overlay",
        actor="skill-overlay-resolver",
    )
    body = hydrated.payload
    if not isinstance(body, Mapping):
        raise EvolutionContractError("Skill overlay artifact is not an object")
    name = str(body.get("skill_name") or row.get("skill_name") or "")
    candidate = validate_skill_candidate({
        "schema_version": "skill-candidate.v1",
        "skill_name": name,
        "candidate_version": str(row.get("digest") or ""),
        "task_families": list((row.get("applicability") or {}).get("task_families") or ["unknown"]),
        "applicability_ref": "learning-asset://" + str(row.get("asset_id") or ""),
        "applicability_digest": str(row.get("digest") or ""),
        "source_trajectories": [{
            "ref": "evolution-attempt://" + str((row.get("source_attempt_ids") or [""])[0]),
            "digest": str(row.get("digest") or ""),
            "outcome": "passed",
        }],
        "content": str(body.get("content") or ""),
        "public_eval_suite_ref": "learning-asset://evaluation/" + str(row.get("asset_id") or ""),
        "public_eval_suite_digest": str(row.get("digest") or ""),
        "sealed_eval_generation_ref": "sealed-evaluator://generation/retained-evidence",
        "evaluation_purpose": "adoption_lift",
        "routing_mode": "natural",
    })
    if str(candidate["content_digest"]) != str(row.get("digest") or ""):
        raise EvolutionContractError("Skill overlay content digest drift")
    root = (
        state_dir
        / "evolution"
        / "overlays"
        / f"{row['asset_id']}@{row['version']}-{str(row['digest'])[:12]}"
        / name
    )
    target = root / "SKILL.md"
    atomic_write_text(target, str(candidate["content"]))
    return target, candidate


def _matches(values: object, current: str) -> bool:
    if not isinstance(values, list) or not values:
        return True
    return current in {str(item) for item in values}


def _expired(expires_at: str, now: str) -> bool:
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        current = datetime.fromisoformat(now.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return expiry <= current


def _budget_exceeded(row: Mapping[str, Any], budget: Mapping[str, Any]) -> bool:
    totals = {"tokens": 0.0, "cost_usd": 0.0}
    for outcome in row.get("outcomes") or []:
        if not isinstance(outcome, Mapping):
            continue
        cost = outcome.get("cost") if isinstance(outcome.get("cost"), Mapping) else {}
        totals["tokens"] += float(cost.get("tokens") or 0.0)
        totals["cost_usd"] += float(cost.get("cost_usd") or 0.0)
    limits = {
        "tokens": float(budget.get("max_tokens") or 0.0),
        "cost_usd": float(budget.get("max_cost_usd") or 0.0),
    }
    return any(limits[key] > 0 and totals[key] > limits[key] for key in totals)


def _source_currentness_reason(
    row: Mapping[str, Any],
    *,
    skill_name: str,
    project_root: Path | None,
) -> str:
    if project_root is None:
        return ""
    expected = str((row.get("activation") or {}).get("previous_digest") or "")
    source = project_root / "skills" / skill_name / "SKILL.md"
    if not source.is_file():
        return "" if not expected else "previous_source_missing"
    actual = hashlib.sha256(source.read_bytes()).hexdigest()
    return "" if actual == expected else "previous_source_digest_drift"


def _decision(row: Mapping[str, Any], *, reason: str) -> dict[str, Any]:
    return {
        "asset_id": str(row.get("asset_id") or ""),
        "version": int(row.get("version") or 0),
        "digest": str(row.get("digest") or ""),
        "state": str(row.get("state") or ""),
        "reason": reason,
    }


__all__ = [
    "SkillOverlayResolution",
    "resolve_skill_overlays",
    "skill_overlay_health_reason",
]
