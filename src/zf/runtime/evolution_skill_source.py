"""Owner-controlled Skill source retention and maintenance proposals."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import Any, Mapping, Sequence

from zf.core.events.writer import EventWriter
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.evolution_contracts import EvolutionContractError, stable_digest
from zf.runtime.evolution_coordinator import EvolutionCoordinator
from zf.runtime.evolution_skill import validate_skill_candidate
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


SOURCE_PROPOSAL_SCHEMA = "skill-source-change-proposal.v1"
MAINTENANCE_ACTIONS = frozenset({"optimize", "replace", "merge", "deactivate"})


def build_skill_retain_proposal(
    state_dir: Path,
    *,
    project_root: Path,
    asset_id: str,
    version: int,
    writer: EventWriter | None = None,
) -> dict[str, Any]:
    """Create an immutable exact source proposal; never mutate the repo."""

    state_dir = Path(state_dir).resolve(strict=False)
    project_root = Path(project_root).resolve(strict=False)
    coordinator = EvolutionCoordinator(state_dir, writer=writer)
    row = coordinator.capabilities.load()["assets"].get(f"{asset_id}@{version}")
    if not isinstance(row, Mapping) or row.get("asset_kind") != "skill_prompt":
        raise EvolutionContractError("Skill retain proposal requires a skill_prompt asset")
    if row.get("state") != "canary_active":
        raise EvolutionContractError("Skill retain proposal requires canary_active state")
    _assert_skill_retain_eligible(row)
    candidate = _asset_candidate(state_dir, row)
    name = str(candidate["skill_name"])
    target = project_root / "skills" / name / "SKILL.md"
    current_digest = _source_digest(target, skill_name=name)
    expected = str((row.get("activation") or {}).get("previous_digest") or "")
    if expected != current_digest and not (not expected and not target.exists()):
        raise EvolutionContractError("canonical Skill changed since canary activation")
    proposal = {
        "schema_version": SOURCE_PROPOSAL_SCHEMA,
        "proposal_id": "skillsrc-" + stable_digest({
            "asset_id": asset_id,
            "version": version,
            "candidate_digest": candidate["content_digest"],
            "current_digest": current_digest,
        })[:20],
        "operation": "retain",
        "asset_id": asset_id,
        "asset_version": version,
        "expected_asset_revision": int(row["revision"]),
        "skill_name": name,
        "target_ref": f"skills/{name}/SKILL.md",
        "expected_current_digest": current_digest,
        "candidate_digest": str(candidate["content_digest"]),
        "candidate_content": str(candidate["content"]),
        "owner_approval_required": True,
        "provider_parity_targets": [
            f".codex/skills/{name}/SKILL.md",
            f".claude/skills/{name}/SKILL.md",
        ],
        "source_delete": False,
    }
    descriptor = write_immutable_json_sidecar(
        state_dir,
        proposal,
        root="evolution/skill-source-proposals",
        kind="skill_source_change_proposal",
        schema_version=SOURCE_PROPOSAL_SCHEMA,
        created_by="run-manager",
    )
    if writer is not None:
        writer.emit(
            "evolution.skill.source_change.proposed",
            actor="run-manager",
            correlation_id=asset_id,
            payload={
                "proposal_id": proposal["proposal_id"],
                "operation": "retain",
                "asset_id": asset_id,
                "version": version,
                "skill_name": name,
                "proposal_ref": descriptor,
                "owner_approval_required": True,
            },
        )
    return {"proposal": proposal, "proposal_ref": descriptor}


def build_skill_maintenance_proposal(
    state_dir: Path,
    *,
    skill_name: str,
    action: str,
    evidence_refs: Sequence[Mapping[str, Any]],
    rationale: str,
    writer: EventWriter | None = None,
) -> dict[str, Any]:
    """Record an Autoresearch maintenance recommendation as proposal-only."""

    normalized_action = str(action or "").strip()
    if normalized_action not in MAINTENANCE_ACTIONS:
        raise EvolutionContractError("unsupported Skill maintenance action")
    refs = [dict(item) for item in evidence_refs if isinstance(item, Mapping)]
    if not skill_name or not refs or not str(rationale or "").strip():
        raise EvolutionContractError(
            "Skill maintenance proposal requires name, rationale, and evidence"
        )
    for descriptor in refs:
        hydrate_sidecar_ref(
            Path(state_dir),
            descriptor,
            purpose="skill-maintenance-evidence",
            actor="autoresearch",
        )
    proposal = {
        "schema_version": "skill-maintenance-proposal.v1",
        "proposal_id": "skillmaint-" + stable_digest({
            "skill_name": skill_name,
            "action": normalized_action,
            "evidence_refs": refs,
            "rationale": rationale,
        })[:20],
        "skill_name": skill_name,
        "action": normalized_action,
        "rationale": str(rationale).strip(),
        "evidence_refs": refs,
        "apply_mode": "proposal_only",
        "owner_approval_required": True,
        "automatic_effect": (
            "revoke_scoped_overlay_only"
            if normalized_action == "deactivate"
            else "none"
        ),
        "source_delete": False,
    }
    descriptor = write_immutable_json_sidecar(
        Path(state_dir),
        proposal,
        root="evolution/skill-maintenance-proposals",
        kind="skill_maintenance_proposal",
        schema_version="skill-maintenance-proposal.v1",
        created_by="autoresearch",
    )
    if writer is not None:
        writer.emit(
            "evolution.skill.maintenance.proposed",
            actor="autoresearch",
            correlation_id=skill_name,
            payload={
                "proposal_id": proposal["proposal_id"],
                "skill_name": skill_name,
                "action": normalized_action,
                "proposal_ref": descriptor,
                "owner_approval_required": True,
            },
        )
    return {"proposal": proposal, "proposal_ref": descriptor}


def apply_skill_retain_proposal(
    state_dir: Path,
    *,
    project_root: Path,
    proposal_ref: Mapping[str, Any],
    supplied_token: str,
    expected_token: str,
    writer: EventWriter | None = None,
) -> dict[str, Any]:
    """Apply one exact retain patch after owner-token and currentness checks."""

    if not expected_token or not hmac.compare_digest(expected_token, supplied_token):
        raise PermissionError("invalid Skill evolution owner token")
    state_dir = Path(state_dir).resolve(strict=False)
    project_root = Path(project_root).resolve(strict=False)
    hydrated = hydrate_sidecar_ref(
        state_dir,
        dict(proposal_ref),
        purpose="skill-source-retain",
        actor="owner",
    )
    proposal = hydrated.payload
    if not isinstance(proposal, Mapping) or proposal.get("schema_version") != SOURCE_PROPOSAL_SCHEMA:
        raise EvolutionContractError("Skill source proposal is invalid")
    if proposal.get("operation") != "retain" or proposal.get("source_delete") is not False:
        raise EvolutionContractError("Skill source proposal operation is not retain")
    name = str(proposal.get("skill_name") or "")
    content = str(proposal.get("candidate_content") or "")
    candidate = _proposal_candidate(proposal, content=content)
    if str(proposal.get("candidate_digest") or "") != str(
        candidate["content_digest"]
    ):
        raise EvolutionContractError("Skill retain proposal candidate digest drift")
    coordinator = EvolutionCoordinator(state_dir, writer=writer)
    key = f"{proposal['asset_id']}@{int(proposal['asset_version'])}"
    row = coordinator.capabilities.load()["assets"].get(key)
    if not isinstance(row, Mapping) or row.get("state") != "canary_active":
        raise EvolutionContractError("Skill retain asset is no longer current")
    if int(row.get("revision") or 0) != int(proposal["expected_asset_revision"]):
        raise EvolutionContractError("Skill retain asset revision is stale")
    if str(row.get("digest") or "") != str(candidate["content_digest"]):
        raise EvolutionContractError("Skill retain candidate digest drift")
    expected_target = f"skills/{name}/SKILL.md"
    expected_parity = [
        f".codex/skills/{name}/SKILL.md",
        f".claude/skills/{name}/SKILL.md",
    ]
    if str(proposal.get("target_ref") or "") != expected_target or list(
        proposal.get("provider_parity_targets") or []
    ) != expected_parity:
        raise EvolutionContractError("Skill retain proposal target path drift")
    targets = [
        project_root / expected_target,
        *(project_root / item for item in expected_parity),
    ]
    lock_path = state_dir / "evolution" / "skill-source-apply.lock"
    with locked_path(lock_path):
        current = _source_digest(targets[0], skill_name=name)
        if current != str(proposal["expected_current_digest"]):
            raise EvolutionContractError("canonical Skill source currentness drift")
        previous = [(path, path.read_text(encoding="utf-8") if path.is_file() else None) for path in targets]
        try:
            for path in targets:
                atomic_write_text(path, content)
            parity = {_relative(path, project_root): _sha256(path) for path in targets}
            if len(set(parity.values())) != 1:
                raise EvolutionContractError("provider Skill parity sync drift")
            receipt = write_immutable_json_sidecar(
                state_dir,
                {
                    "schema_version": "skill-source-apply-receipt.v1",
                    "proposal_id": str(proposal["proposal_id"]),
                    "asset_id": str(proposal["asset_id"]),
                    "version": int(proposal["asset_version"]),
                    "skill_name": name,
                    "previous_digest": current,
                    "applied_digest": str(candidate["content_digest"]),
                    "parity": parity,
                    "owner_authorized": True,
                    "source_delete": False,
                },
                root="evolution/skill-source-receipts",
                kind="skill_source_apply_receipt",
                schema_version="skill-source-apply-receipt.v1",
                created_by="owner",
            )
            transitioned = coordinator.transition_asset(
                asset_id=str(proposal["asset_id"]),
                version=int(proposal["asset_version"]),
                target_state="active_retained",
                expected_revision=int(proposal["expected_asset_revision"]),
                action_id="skillretain-" + str(proposal["proposal_id"]),
                receipt_ref=receipt,
                actor="owner",
            )
        except Exception:
            for path, prior in previous:
                if prior is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write_text(path, prior)
            raise
    if writer is not None:
        writer.emit(
            "evolution.skill.source_change.applied",
            actor="owner",
            correlation_id=str(proposal["asset_id"]),
            payload={
                "proposal_id": str(proposal["proposal_id"]),
                "asset_id": str(proposal["asset_id"]),
                "version": int(proposal["asset_version"]),
                "skill_name": name,
                "receipt_ref": receipt,
                "source_delete": False,
            },
        )
    return {"asset": transitioned["asset"], "receipt_ref": receipt, "parity": parity}


def _asset_candidate(state_dir: Path, row: Mapping[str, Any]) -> dict[str, Any]:
    hydrated = hydrate_sidecar_ref(
        state_dir,
        dict(row["artifact_ref"]),
        purpose="skill-source-proposal",
        actor="run-manager",
    )
    body = hydrated.payload
    if not isinstance(body, Mapping):
        raise EvolutionContractError("Skill asset body is invalid")
    return _proposal_candidate(body, content=str(body.get("content") or ""))


def _assert_skill_retain_eligible(row: Mapping[str, Any]) -> None:
    activation = row.get("activation")
    policy = activation.get("retain_policy") if isinstance(activation, Mapping) else {}
    if not isinstance(policy, Mapping):
        raise EvolutionContractError("Skill retain policy is missing")
    minimum = int(policy.get("min_matched_outcomes") or 1)
    maximum_negative = int(policy.get("max_negative_transfer") or 0)
    matched = [
        item
        for item in row.get("outcomes") or []
        if isinstance(item, Mapping) and bool(item.get("matched"))
    ]
    passed = [item for item in matched if item.get("outcome") == "passed"]
    negative = [item for item in matched if bool(item.get("negative_transfer"))]
    if len(passed) < minimum:
        raise EvolutionContractError(
            "Skill retain proposal lacks matched passed canary outcomes"
        )
    if len(negative) > maximum_negative:
        raise EvolutionContractError(
            "Skill retain proposal exceeds negative-transfer allowance"
        )


def _proposal_candidate(raw: Mapping[str, Any], *, content: str) -> dict[str, Any]:
    name = str(raw.get("skill_name") or "")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return validate_skill_candidate({
        "schema_version": "skill-candidate.v1",
        "skill_name": name,
        "candidate_version": digest,
        "task_families": ["source-retain"],
        "applicability_ref": "source-proposal://" + name,
        "applicability_digest": digest,
        "source_trajectories": [{
            "ref": "source-proposal://evidence/" + name,
            "digest": digest,
            "outcome": "passed",
        }],
        "content": content,
        "public_eval_suite_ref": "source-proposal://evaluation/" + name,
        "public_eval_suite_digest": digest,
        "sealed_eval_generation_ref": "sealed-evaluator://generation/source-retain",
        "evaluation_purpose": "adoption_lift",
        "routing_mode": "natural",
    })


def _source_digest(path: Path, *, skill_name: str) -> str:
    if not path.is_file():
        return stable_digest({"skill_name": skill_name, "source": "absent"})
    return _sha256(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


__all__ = [
    "MAINTENANCE_ACTIONS",
    "SOURCE_PROPOSAL_SCHEMA",
    "apply_skill_retain_proposal",
    "build_skill_maintenance_proposal",
    "build_skill_retain_proposal",
]
