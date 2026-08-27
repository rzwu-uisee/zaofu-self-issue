from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    RuntimeConfig,
    RuntimeEvolutionConfig,
    ZfConfig,
)
from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.core.skills.materialize import materialize_role_skills
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.evolution_coordinator import EvolutionCoordinator
from zf.runtime.evolution_contracts import EvolutionContractError, stable_digest
from zf.runtime.evolution_automation import reconcile_evolution_automation
from zf.runtime.evolution_skill_overlay import resolve_skill_overlays
from zf.runtime.evolution_skill_source import (
    apply_skill_retain_proposal,
    build_skill_maintenance_proposal,
    build_skill_retain_proposal,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _skill(name: str, marker: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {marker} method\n"
        "---\n\n"
        f"# {marker}\n\nUse {marker}.\n"
    )


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _canary_asset(
    state_dir: Path,
    *,
    current: str,
    candidate: str,
    asset_id: str = "demo-method-candidate",
    cohorts: tuple[str, ...] = (),
) -> tuple[EvolutionCoordinator, dict]:
    writer = EventWriter(event_log_from_project(state_dir))
    coordinator = EvolutionCoordinator(state_dir, writer=writer)
    content_digest = _digest(candidate)
    body = {
        "schema_version": "learning-asset.v1",
        "asset_id": asset_id,
        "asset_kind": "skill_prompt",
        "skill_name": "demo-method",
        "version": 1,
        "digest": content_digest,
        "source_attempt_ids": ["attempt-1"],
        "content": candidate,
        "applicability": {"task_families": ["issue"]},
        "quality": {"expires_at": "2099-01-01T00:00:00+00:00"},
        "activation": {
            "mode": "proposal_only",
            "overlay_mode": "scoped_overlay",
            "owner_approval_required": True,
            "canary_scope_ref": "canary://demo-method/1",
            "expected_active_key": "",
            "scope": {
                "roles": ["dev-1"],
                "task_families": ["issue"],
                "cohorts": list(cohorts),
            },
            "previous_digest": _digest(current),
            "expires_at": "2099-01-01T00:00:00+00:00",
            "budget": {"max_tokens": 1000, "max_cost_usd": 1},
            "retain_policy": {
                "min_matched_outcomes": 1,
                "max_negative_transfer": 0,
            },
            "automation_policy_digest": stable_digest({
                "mode": "auto_low_risk",
                "auto_asset_kinds": ["skill_prompt"],
            }),
        },
        "rollback": {
            "previous_version_ref": "",
            "previous_digest": _digest(current),
            "conditions": ["negative_transfer"],
        },
        "dependencies": [],
        "provenance": {"project": "test", "target_validation": "passed"},
        "taint": {
            "blocked": False,
            "secret": False,
            "pii": False,
            "license_unknown": False,
        },
    }
    artifact_ref = write_immutable_json_sidecar(
        state_dir,
        body,
        root="evolution/assets",
        kind="learning_asset",
        schema_version="learning-asset.v1",
        created_by="test",
    )
    row, _created = coordinator.capabilities.propose(
        body,
        artifact_ref=artifact_ref,
        created_at="2026-08-25T00:00:00+00:00",
    )
    for target in ("validated", "approved", "canary_active"):
        receipt = write_immutable_json_sidecar(
            state_dir,
            {"target": target, "asset_id": asset_id, "revision": row["revision"]},
            root="evolution/receipts",
            kind="test_receipt",
            schema_version="test-receipt.v1",
            created_by="test",
        )
        row = coordinator.transition_asset(
            asset_id=asset_id,
            version=1,
            target_state=target,
            expected_revision=int(row["revision"]),
            action_id=f"{asset_id}-{target}",
            receipt_ref=receipt,
            actor="test",
        )["asset"]
    return coordinator, row


def test_scoped_overlay_requires_exact_task_cohort(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state_dir = project / ".zf"
    state_dir.mkdir(parents=True)
    current = _skill("demo-method", "current")
    source = project / "skills" / "demo-method" / "SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text(current, encoding="utf-8")
    _coordinator, _row = _canary_asset(
        state_dir,
        current=current,
        candidate=_skill("demo-method", "candidate"),
        cohorts=("TASK-CANARY",),
    )

    excluded = resolve_skill_overlays(
        state_dir,
        role_instance="dev-1",
        task_family="issue",
        cohort="TASK-OTHER",
        project_root=project,
    )
    selected = resolve_skill_overlays(
        state_dir,
        role_instance="dev-1",
        task_family="issue",
        cohort="TASK-CANARY",
        project_root=project,
    )

    assert excluded.paths == {}
    assert set(selected.paths) == {"demo-method"}


def test_scoped_overlay_and_owner_retain_preserve_source_boundary(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state_dir = project / ".zf"
    state_dir.mkdir(parents=True)
    current = _skill("demo-method", "current")
    candidate = _skill("demo-method", "candidate")
    source = project / "skills" / "demo-method" / "SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text(current, encoding="utf-8")
    coordinator, row = _canary_asset(
        state_dir,
        current=current,
        candidate=candidate,
    )

    outside = resolve_skill_overlays(
        state_dir,
        role_instance="verify-1",
        task_family="issue",
    )
    inside = resolve_skill_overlays(
        state_dir,
        role_instance="dev-1",
        task_family="issue",
    )
    assert outside.paths == {}
    assert set(inside.paths) == {"demo-method"}
    assert source.read_text(encoding="utf-8") == current

    role = RoleConfig(
        name="dev",
        instance_id="dev-1",
        backend="codex",
        skills=[],
    )
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=".zf"),
        roles=[role],
    )
    materialized = materialize_role_skills(
        config=config,
        project_root=project,
        state_dir=state_dir,
        role=role,
        task_id="TASK-1",
        skill_overrides=inside.paths,
    )
    assert materialized is not None
    assert materialized.skills[0].sha256 == _digest(candidate)
    assert materialized.skills[0].source_name == "evolution-overlay"

    with pytest.raises(EvolutionContractError, match="passed canary"):
        build_skill_retain_proposal(
            state_dir,
            project_root=project,
            asset_id=row["asset_id"],
            version=row["version"],
        )

    coordinator.record_asset_outcome(
        asset_id=row["asset_id"],
        version=row["version"],
        usage_ref="test://matched-pass",
        matched=True,
        outcome="passed",
        cost={"tokens": 10, "cost_usd": 0.01},
    )
    proposal = build_skill_retain_proposal(
        state_dir,
        project_root=project,
        asset_id=row["asset_id"],
        version=row["version"],
    )
    with pytest.raises(PermissionError, match="owner token"):
        apply_skill_retain_proposal(
            state_dir,
            project_root=project,
            proposal_ref=proposal["proposal_ref"],
            supplied_token="wrong",
            expected_token="owner-secret",
        )
    assert source.read_text(encoding="utf-8") == current

    escaped = dict(proposal["proposal"])
    escaped["target_ref"] = "../outside/SKILL.md"
    escaped_ref = write_immutable_json_sidecar(
        state_dir,
        escaped,
        root="evolution/skill-source-proposals",
        kind="skill_source_change_proposal",
        schema_version="skill-source-change-proposal.v1",
        created_by="test",
    )
    with pytest.raises(EvolutionContractError, match="target path drift"):
        apply_skill_retain_proposal(
            state_dir,
            project_root=project,
            proposal_ref=escaped_ref,
            supplied_token="owner-secret",
            expected_token="owner-secret",
        )

    applied = apply_skill_retain_proposal(
        state_dir,
        project_root=project,
        proposal_ref=proposal["proposal_ref"],
        supplied_token="owner-secret",
        expected_token="owner-secret",
    )
    assert applied["asset"]["state"] == "active_retained"
    parity_paths = [
        source,
        project / ".codex" / "skills" / "demo-method" / "SKILL.md",
        project / ".claude" / "skills" / "demo-method" / "SKILL.md",
    ]
    assert {path.read_text(encoding="utf-8") for path in parity_paths} == {candidate}

    maintenance = build_skill_maintenance_proposal(
        state_dir,
        skill_name="demo-method",
        action="deactivate",
        rationale="matched tasks show negative transfer",
        evidence_refs=[applied["receipt_ref"]],
    )
    assert maintenance["proposal"]["apply_mode"] == "proposal_only"
    assert maintenance["proposal"]["source_delete"] is False
    assert source.is_file()


def test_negative_skill_outcome_revokes_future_overlay(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state_dir = project / ".zf"
    state_dir.mkdir(parents=True)
    current = _skill("demo-method", "current")
    candidate = _skill("demo-method", "candidate")
    source = project / "skills" / "demo-method" / "SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text(current, encoding="utf-8")
    coordinator, row = _canary_asset(
        state_dir,
        current=current,
        candidate=candidate,
        asset_id="demo-method-negative",
    )
    overlay = resolve_skill_overlays(
        state_dir,
        role_instance="dev-1",
        task_family="issue",
    )
    role = RoleConfig(
        name="dev",
        instance_id="dev-1",
        backend="codex",
        skills=[],
    )
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=".zf"),
        roles=[role],
        runtime=RuntimeConfig(
            evolution=RuntimeEvolutionConfig(
                enabled=True,
                mode="auto_low_risk",
                auto_asset_kinds=["skill_prompt"],
            )
        ),
    )
    materialized = materialize_role_skills(
        config=config,
        project_root=project,
        state_dir=state_dir,
        role=role,
        task_id="TASK-NEG",
        skill_overrides=overlay.paths,
    )
    assert materialized is not None
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-NEG",
        title="negative transfer",
        status="in_progress",
        assigned_to="dev-1",
        contract=TaskContract(campaign="issue", owner_role="dev"),
    ))
    writer = coordinator.writer
    dispatch = writer.emit(
        "task.dispatched",
        actor="orchestrator",
        task_id="TASK-NEG",
        payload={
            "role_instance": "dev-1",
            "dispatch_id": "dispatch-neg",
            "attempt_id": "attempt-neg",
        },
    )
    target = next(
        item.materialized_to
        for item in materialized.skills
        if item.name == "demo-method"
    )
    writer.emit(
        "agent.tool.use",
        actor="dev-1",
        task_id="TASK-NEG",
        causation_id=dispatch.id,
        payload={
            "tool": "read_file",
            "input": {"path": str(Path(str(target)) / "SKILL.md")},
        },
    )
    result = coordinator.record_skill_outcome(
        asset_id=row["asset_id"],
        version=row["version"],
        skill_name="demo-method",
        task_id="TASK-NEG",
        role_instance="dev-1",
        outcome="regressed",
        cost={"tokens": 1100, "cost_usd": 0.1},
        feedback={
            "rework_count": 2,
            "replan_count": 1,
            "blocking_regression": True,
        },
        config=config,
        project_root=project,
    )
    assert result["asset"]["state"] == "revoked"
    assert result["auto_revoke"]["applied"] is True
    assert result["auto_revoke"]["reasons"] == [
        "outcome_regressed",
        "blocking_regression",
        "rework_increased",
        "replan_increased",
        "budget_exceeded",
    ]
    assert result["invocation"]["feedback_ref"]["ref"].startswith(
        "artifacts/evolution/skill-feedback/"
    )
    after = resolve_skill_overlays(
        state_dir,
        role_instance="dev-1",
        task_family="issue",
    )
    assert after.paths == {}
    assert source.read_text(encoding="utf-8") == current
    assert stable_digest(result["auto_revoke"]["receipt_ref"])


def test_run_manager_revokes_source_drifted_overlay(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state_dir = project / ".zf"
    state_dir.mkdir(parents=True)
    current = _skill("demo-method", "current")
    source = project / "skills" / "demo-method" / "SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text(current, encoding="utf-8")
    coordinator, row = _canary_asset(
        state_dir,
        current=current,
        candidate=_skill("demo-method", "candidate"),
        asset_id="demo-method-drifted",
    )
    source.write_text(_skill("demo-method", "externally-changed"), encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=".zf"),
        runtime=RuntimeConfig(
            evolution=RuntimeEvolutionConfig(
                enabled=True,
                mode="evaluate_only",
            )
        ),
    )

    result = reconcile_evolution_automation(
        state_dir=state_dir,
        project_root=project,
        writer=coordinator.writer,
        config=config,
    )

    assert result.controlled_actions == 1
    revoked = coordinator.capabilities.load()["assets"][
        f"{row['asset_id']}@{row['version']}"
    ]
    assert revoked["state"] == "revoked"
    receipt = next(
        event.payload["receipt_ref"]
        for event in coordinator.writer.event_log.read_all()
        if event.type == "evolution.asset.revoked"
    )
    hydrated = hydrate_sidecar_ref(
        state_dir,
        receipt,
        purpose="test-source-drift-revoke",
    ).payload
    assert hydrated["reason"] == "previous_source_digest_drift"
    assert hydrated["source_mutated"] is False
