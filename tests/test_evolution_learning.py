from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.config.loader import load_config
from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.evolution_contracts import EvolutionContractError
from zf.runtime.evolution_coordinator import EvolutionCoordinator
from zf.runtime.evolution_learning import compile_workflow_learning_proposal
from zf.runtime.evolution_store import CapabilityRegistry, EvolutionConflictError
from zf.runtime.injection import generate_task_briefing
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_requests import workflow_request_path


SHA = {letter: letter * 64 for letter in "abcdef0123456789"}


def _state(tmp_path: Path, name: str = "project") -> Path:
    state_dir = tmp_path / name / ".zf"
    state_dir.mkdir(parents=True)
    return state_dir


def _receipt(state_dir: Path, action: str) -> dict:
    return write_immutable_json_sidecar(
        state_dir,
        {"schema_version": "controlled-action-receipt.v1", "action": action},
        root="evolution/receipts",
        kind="controlled_action_receipt",
        schema_version="controlled-action-receipt.v1",
        created_by="test",
    )


def _asset(
    *,
    asset_id: str,
    kind: str = "memory_entry",
    content: str = "Read settlement evidence before redispatch.",
) -> dict:
    return {
        "schema_version": "learning-asset.v1",
        "asset_id": asset_id,
        "asset_kind": kind,
        "version": 1,
        "digest": SHA["a"],
        "source_attempt_ids": ["attempt-1"],
        "content": content,
        "skill_name": "review-method" if kind == "skill_prompt" else "",
        "applicability": {
            "task_families": ["issue"],
            "providers": ["codex"],
        },
        "quality": {
            "confidence": "medium",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "contradiction_refs": [],
        },
        "activation": {
            "mode": "proposal_only",
            "owner_approval_required": True,
            "canary_scope_ref": "canary://issue",
            "expected_active_key": "",
            "retain_policy": {
                "min_matched_outcomes": 1,
                "max_negative_transfer": 0,
            },
        },
        "rollback": {"previous_version_ref": "", "conditions": ["regression"]},
        "dependencies": [],
        "provenance": {"project": "source", "target_validation": "passed"},
        "taint": {
            "blocked": False,
            "secret": False,
            "pii": False,
            "license_unknown": False,
        },
    }


def _activate(
    state_dir: Path,
    registry: CapabilityRegistry,
    body: dict,
    *,
    retain: bool = True,
) -> dict:
    descriptor = write_immutable_json_sidecar(
        state_dir,
        body,
        root="evolution/learning-assets",
        kind="learning_asset",
        schema_version="learning-asset.v1",
        created_by="test",
    )
    row, _ = registry.propose(
        body,
        artifact_ref=descriptor,
        created_at="2026-01-01T00:00:00+00:00",
    )
    for target in ("validated", "approved", "canary_active"):
        row, _ = registry.transition(
            asset_id=row["asset_id"],
            version=row["version"],
            target_state=target,
            expected_revision=row["revision"],
            action_id=f"{row['asset_id']}-{target}",
            receipt_ref=_receipt(state_dir, target),
            updated_at="2026-01-01T00:00:01+00:00",
        )
    if retain:
        row, _ = registry.record_outcome(
            asset_id=row["asset_id"],
            version=row["version"],
            usage_ref=f"task://{row['asset_id']}-canary",
            matched=True,
            outcome="passed",
            cost={"cost_usd": 0.01},
            recorded_at="2026-01-01T00:00:02+00:00",
        )
        row, _ = registry.transition(
            asset_id=row["asset_id"],
            version=row["version"],
            target_state="active_retained",
            expected_revision=row["revision"],
            action_id=f"{row['asset_id']}-retain",
            receipt_ref=_receipt(state_dir, "retain"),
            updated_at="2026-01-01T00:00:03+00:00",
        )
    return row


def _write_issue_config(path: Path, *, lanes: int) -> None:
    path.write_text(
        f"""\
apiVersion: zaofu.dev/v1
kind: IssueFlow
metadata: {{name: issue-demo}}
spec:
  lanes: {lanes}
  backend: mock
  issueRef: docs/issue.md
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {{name: demo}}
spec:
  version: "1.0"
  project: {{name: demo, state_dir: .zf}}
""",
        encoding="utf-8",
    )
    load_config(path)


def _workflow_request(state_dir: Path) -> dict:
    requirement = state_dir / "workflow-requests/req-learning/requirement.json"
    requirement.parent.mkdir(parents=True)
    requirement.write_text(
        json.dumps({
            "schema_version": "requirement-spec.v1",
            "request_id": "req-learning",
            "revision": 1,
        }),
        encoding="utf-8",
    )
    import hashlib

    request = {
        "schema_version": "workflow.request.v1",
        "request_id": "req-learning",
        "kind": "issue",
        "status": "ready",
        "revision": 1,
        "requirement_spec_ref": str(requirement),
        "requirement_spec_digest": hashlib.sha256(requirement.read_bytes()).hexdigest(),
        "open_questions": [],
    }
    path = workflow_request_path(state_dir, request["request_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(request), encoding="utf-8")
    return request


def test_loop_learning_compiles_standard_proposal_without_writing_config(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    base = state_dir.parent / "zf.yaml"
    candidate = state_dir.parent / "candidate.yaml"
    _write_issue_config(base, lanes=1)
    _write_issue_config(candidate, lanes=2)
    original = base.read_bytes()
    request = _workflow_request(state_dir)
    promotion = write_immutable_json_sidecar(
        state_dir,
        {
            "schema_version": "loop-learning-promotion.v1",
            "promotion_id": "promotion-1",
            "target": "workflow_patch_proposal",
            "project_id": "demo",
            "source": {"learning_id": "learning-1"},
            "proposal": {"summary": "Use two lanes", "evidence_refs": ["run://1"]},
            "promotion_policy": {
                "proposal_only": True,
                "requires_operator_review": True,
                "writes_canonical_truth": False,
            },
        },
        root="loop/promotions/promotion-1",
        kind="loop_learning_promotion",
        schema_version="loop-learning-promotion.v1",
        created_by="test",
    )
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))

    proposal, descriptor = compile_workflow_learning_proposal(
        state_dir,
        promotion_descriptor=promotion,
        request=request,
        base_config_path=base,
        candidate_config_path=candidate,
        preflight={"status": "passed", "blockers": []},
        writer=writer,
    )

    assert proposal["schema_version"] == "workflow-proposal.v1"
    assert proposal["flow_family"] == "IssueFlow"
    assert proposal["approval_status"] == "approvable"
    assert base.read_bytes() == original
    assert hydrate_sidecar_ref(state_dir, descriptor).payload["proposal_id"]
    compiled = [
        event for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.type == "evolution.workflow.proposal.compiled"
    ]
    assert len(compiled) == 1
    link = hydrate_sidecar_ref(state_dir, compiled[0].payload["link_ref"]).payload
    assert link["promotion_digest"] == promotion["sha256"]
    assert link["workflow_proposal_digest"] == proposal["proposal_digest"]


def test_retained_memory_is_injected_only_inside_applicability(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    registry = CapabilityRegistry(state_dir / "evolution/capabilities.json")
    _activate(state_dir, registry, _asset(asset_id="memory-1"))
    config = ZfConfig(project=ProjectConfig(name="demo"))
    role = RoleConfig(name="dev", instance_id="dev-1", backend="codex")
    matched = Task(
        id="TASK-1",
        title="repair",
        contract=TaskContract(campaign="issue"),
    )
    outside = Task(
        id="TASK-2",
        title="research",
        contract=TaskContract(campaign="research"),
    )

    matched_text = generate_task_briefing(
        config, role, matched, state_dir_ref=state_dir, project_root=state_dir.parent
    )
    outside_text = generate_task_briefing(
        config, role, outside, state_dir_ref=state_dir, project_root=state_dir.parent
    )

    assert "Read settlement evidence before redispatch" in matched_text
    assert "Read settlement evidence before redispatch" not in outside_text
    assert "outside_task_families" in outside_text


def test_skill_credit_requires_observed_current_dispatch_invocation(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    coordinator = EvolutionCoordinator(state_dir)
    row = _activate(
        state_dir,
        coordinator.capabilities,
        _asset(asset_id="skill-1", kind="skill_prompt"),
        retain=False,
    )
    skill_dir = state_dir / "workdirs/dev-1/codex-home/skills/review-method"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("# review\n", encoding="utf-8")
    manifest = state_dir / "workdirs/dev-1/runtime/skills-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "instance_id": "dev-1",
        "task_id": "TASK-SKILL",
        "skills": [{
            "name": "review-method",
            "status": "resolved",
            "materialized_to": str(skill_dir),
            "sha256": SHA["b"],
        }],
    }), encoding="utf-8")
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-SKILL",
        title="review",
        status="in_progress",
        assigned_to="dev-1",
        skills_required=["review-method"],
    ))
    log = EventLog(state_dir / "events.jsonl")
    dispatch = ZfEvent(
        id="dispatch-skill",
        type="task.dispatched",
        task_id="TASK-SKILL",
        payload={"assignee": "dev-1", "attempt_id": "attempt-1"},
    )
    log.append(dispatch)
    config = ZfConfig(
        project=ProjectConfig(name="demo"),
        roles=[RoleConfig(
            name="dev",
            instance_id="dev-1",
            backend="codex",
            skills=["review-method"],
        )],
    )
    with pytest.raises(EvolutionContractError, match="no observed invocation"):
        coordinator.record_skill_outcome(
            asset_id="skill-1",
            version=1,
            skill_name="review-method",
            task_id="TASK-SKILL",
            role_instance="dev-1",
            outcome="passed",
            cost={},
            config=config,
            project_root=state_dir.parent,
        )
    invocation = ZfEvent(
        id="skill-read",
        type="codex.hook.pre_tool_use",
        actor="dev-1",
        causation_id=dispatch.id,
        payload={"tool_name": "Read", "tool_input": {"file_path": str(skill_path)}},
    )
    log.append(invocation)

    result = coordinator.record_skill_outcome(
        asset_id="skill-1",
        version=1,
        skill_name="review-method",
        task_id="TASK-SKILL",
        role_instance="dev-1",
        outcome="passed",
        cost={"tokens": 10},
        config=config,
        project_root=state_dir.parent,
    )

    assert result["recorded"] is True
    assert result["invocation"]["evidence_event_ids"] == ["skill-read"]
    assert result["asset"]["outcomes"][0]["matched"] is True


def test_challenge_requires_stability_and_verified_evaluator_receipt(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    coordinator = EvolutionCoordinator(state_dir)
    challenge = {
        "schema_version": "challenge-case.v1",
        "challenge_id": "challenge-1",
        "source_event_ref": "event://failure-1",
        "run_ref": "run://1",
        "trace_ref": "trace://1",
        "reproduction_ref": "artifact://repro/1",
        "expected_invariant": "settlement remains effectively once",
        "visibility_policy": "shadow_visible",
        "secret_status": "redacted",
        "stability_observations": [
            {"run_ref": "run://r1", "reproduced": True},
            {"run_ref": "run://r2", "reproduced": True},
        ],
    }
    row = coordinator.materialize_challenge(challenge)
    receipt = _receipt(state_dir, "promote-challenge")
    promoted = coordinator.decide_challenge(
        challenge_id=row["challenge_id"],
        expected_revision=row["revision"],
        verdict="promoted",
        evaluator_receipt_ref=receipt,
    )
    assert promoted["status"] == "promoted"
    assert coordinator.decide_challenge(
        challenge_id=row["challenge_id"],
        expected_revision=row["revision"],
        verdict="promoted",
        evaluator_receipt_ref=receipt,
    )["revision"] == promoted["revision"]


def test_portable_asset_preserves_body_and_requires_target_validation(
    tmp_path: Path,
) -> None:
    source_state = _state(tmp_path, "source")
    source = EvolutionCoordinator(source_state)
    _activate(source_state, source.capabilities, _asset(asset_id="portable-memory"))
    exported = source.export_asset(asset_id="portable-memory", version=1)
    assert exported["package"]["asset"]["content"].startswith("Read settlement")

    target_state = _state(tmp_path, "target")
    target = EvolutionCoordinator(target_state)
    imported = target.import_asset(
        package_descriptor=exported["artifact_ref"],
        target_project="target",
        source_state_dir=source_state,
    )["asset"]
    assert imported["state"] == "candidate"
    assert imported["provenance"]["target_validation"] == "pending"
    body = hydrate_sidecar_ref(target_state, imported["artifact_ref"]).payload
    assert body["content"].startswith("Read settlement")
    for lifecycle in ("validated", "approved"):
        imported = target.transition_asset(
            asset_id=imported["asset_id"],
            version=imported["version"],
            target_state=lifecycle,
            expected_revision=imported["revision"],
            action_id=f"target-{lifecycle}",
            receipt_ref=_receipt(target_state, lifecycle),
        )["asset"]
    with pytest.raises(EvolutionConflictError, match="target validation"):
        target.transition_asset(
            asset_id=imported["asset_id"],
            version=imported["version"],
            target_state="canary_active",
            expected_revision=imported["revision"],
            action_id="target-canary-early",
            receipt_ref=_receipt(target_state, "early"),
        )
    imported = target.record_target_validation(
        asset_id=imported["asset_id"],
        version=imported["version"],
        expected_revision=imported["revision"],
        action_id="target-validation",
        passed=True,
        receipt_ref=_receipt(target_state, "target-validation"),
    )["asset"]
    canary = target.transition_asset(
        asset_id=imported["asset_id"],
        version=imported["version"],
        target_state="canary_active",
        expected_revision=imported["revision"],
        action_id="target-canary",
        receipt_ref=_receipt(target_state, "target-canary"),
    )["asset"]
    assert canary["state"] == "canary_active"
