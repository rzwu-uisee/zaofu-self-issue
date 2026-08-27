"""Mechanical health reconciliation for active Skill canary overlays."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.core.events.writer import EventWriter
from zf.runtime.evolution_automation_support import controlled_transition
from zf.runtime.evolution_contracts import EvolutionContractError
from zf.runtime.evolution_coordinator import EvolutionCoordinator
from zf.runtime.evolution_skill_overlay import skill_overlay_health_reason


def reconcile_skill_overlay_health(
    *,
    state_dir: Path,
    project_root: Path,
    writer: EventWriter,
    config: Any,
    max_actions: int,
) -> int:
    """Revoke mechanically stale Skill overlays through Controlled Action."""

    coordinator = EvolutionCoordinator(state_dir, writer=writer)
    assets = coordinator.capabilities.load()["assets"].values()
    applied = 0
    for asset in sorted(
        (dict(row) for row in assets if isinstance(row, Mapping)),
        key=lambda row: (str(row.get("asset_id") or ""), int(row.get("version") or 0)),
    ):
        if applied >= max_actions:
            break
        if asset.get("asset_kind") != "skill_prompt" or asset.get("state") != "canary_active":
            continue
        reason = skill_overlay_health_reason(
            asset,
            state_dir=state_dir,
            project_root=project_root,
        )
        if not reason:
            continue
        activation = asset.get("activation")
        policy_digest = str(
            activation.get("automation_policy_digest")
            if isinstance(activation, Mapping)
            else ""
        )
        if not policy_digest:
            raise EvolutionContractError(
                "Skill overlay health revoke lacks automation policy identity"
            )
        campaign_id = str(
            (asset.get("source_attempt_ids") or [asset["asset_id"]])[0]
            or asset["asset_id"]
        )
        result = controlled_transition(
            state_dir=state_dir,
            project_root=project_root,
            writer=writer,
            config=config,
            campaign={
                "campaign_id": campaign_id,
                "policy_digest": policy_digest,
            },
            asset=asset,
            target_state="revoked",
            reason=reason,
        )
        applied += int(bool(result.get("applied")))
    return applied


__all__ = ["reconcile_skill_overlay_health"]
