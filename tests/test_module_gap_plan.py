from __future__ import annotations

from zf.runtime.module_gap_plan import (
    build_gap_task_map_amend,
    gap_tasks_from_gap_plan_payload,
    validate_module_gap_plan_payload,
)
from zf.runtime.task_map import validate_task_map_payload


def test_module_gap_plan_validation_rejects_incomplete_gap_task() -> None:
    result = validate_module_gap_plan_payload({
        "schema_version": "module-gap-plan.v1",
        "gap_tasks": [{
            "task_id": "CANGJIE-WEB-GAP-001",
            "source_refs": ["hermes-agent/web"],
        }],
    })

    assert result.passed is False
    assert "CANGJIE-WEB-GAP-001.claim_paths is required" in result.errors
    assert "CANGJIE-WEB-GAP-001.acceptance is required" in result.errors
    assert "CANGJIE-WEB-GAP-001.verify_commands is required" in result.errors


def test_module_gap_plan_validation_rejects_overlapping_claim_paths() -> None:
    shared = {
        "claim_paths": ["app/src/render/scene.ts"],
        "acceptance": ["render behavior is correct"],
        "verify_commands": ["npm test"],
        "source_refs": ["app/src/render/scene.ts:10"],
    }

    result = validate_module_gap_plan_payload({
        "schema_version": "module-gap-plan.v1",
        "gap_tasks": [
            {"task_id": "GAP-RENDER", **shared},
            {"task_id": "GAP-CAMERA", **shared},
        ],
    })

    assert result.passed is False
    assert result.errors == [
        "gap tasks 'GAP-RENDER' and 'GAP-CAMERA' have overlapping "
        "claim_path 'app/src/render/scene.ts'",
    ]


def test_gap_tasks_append_to_full_task_map_as_canonical_tasks() -> None:
    base = {
        "schema_version": "task-map.v1",
        "feature_id": "CANGJIE",
        "source_refs": {"source_index_ref": ".zf/artifacts/CANGJIE/source_index.json"},
        "tasks": [{
            "task_id": "CANGJIE-WEB-001",
            "title": "Web baseline",
            "owner_role": "dev",
            "wave": 0,
            "allowed_paths": ["web/**"],
            "allowed_paths_reason": "original web slice",
            "acceptance": ["baseline web slice exists"],
        }],
    }
    gap_task = {
        "task_id": "CANGJIE-WEB-GAP-001",
        "module_id": "web-dashboard",
        "parent_task_id": "CANGJIE-WEB-001",
        "affinity_tag": "web-tui",
        "owner_role": "dev",
        "claim_paths": ["web/src/**", "packages/web-adapter/**"],
        "acceptance": ["WebChat reaches Cangjie runtime"],
        "verify_commands": ["npm run test:e2e:webchat"],
        "source_refs": ["hermes-agent/web"],
    }

    amended = build_gap_task_map_amend(
        base,
        gap_tasks=[gap_task],
        supersedes_task_map_ref=".zf/artifacts/CANGJIE/task_map.json",
        gap_plan_ref="docs/validation/cangjie-gap-task-map.json",
    )

    assert amended["amend"]["gap_task_ids"] == ["CANGJIE-WEB-GAP-001"]
    assert amended["source_refs"]["supersedes_task_map_ref"] == ".zf/artifacts/CANGJIE/task_map.json"
    appended = amended["tasks"][-1]
    assert appended["task_id"] == "CANGJIE-WEB-GAP-001"
    assert appended["parent_task_id"] == "CANGJIE-WEB-001"
    assert appended["allowed_paths"] == ["web/src/**", "packages/web-adapter/**"]
    assert validate_task_map_payload(amended).passed is True


def test_semantic_replan_replaces_superseded_task_in_amended_map() -> None:
    base_commit = "a" * 40
    base = {
        "schema_version": "task-map.v1",
        "feature_id": "ISSUE-1",
        "source_refs": {"source_index_ref": "docs/plans/source-index.json"},
        "tasks": [{
            "task_id": "ISSUE-1-MIXED",
            "title": "mixed task",
            "owner_role": "dev",
            "wave": 0,
            "allowed_paths": ["src/**"],
            "allowed_paths_reason": "initial mixed slice",
            "acceptance": ["issue is fixed"],
        }],
    }
    replacements = [{
        "task_id": "ISSUE-1-CORE",
        "parent_task_id": "ISSUE-1-MIXED",
        "claim_paths": ["src/core/**", "tests/test_core.py"],
        "acceptance": ["core expiry behavior is fixed"],
        "verify_commands": ["uv run pytest tests/test_core.py"],
        "base_commit": base_commit,
        "source_refs": ["docs/issues/1.md", f"git:{base_commit}"],
        "supersedes_task_ids": ["ISSUE-1-MIXED"],
    }]

    amended = build_gap_task_map_amend(
        base,
        gap_tasks=replacements,
        supersedes_task_map_ref="artifacts/ISSUE-1/task_map.json",
    )

    assert [task["task_id"] for task in amended["tasks"]] == ["ISSUE-1-CORE"]
    assert amended["amend"]["superseded_task_ids"] == ["ISSUE-1-MIXED"]
    assert amended["tasks"][0]["base_commit"] == base_commit
    assert validate_task_map_payload(amended).passed is True


def test_gap_amend_preserves_read_only_verification_scope() -> None:
    base_commit = "c" * 40
    base = {
        "schema_version": "task-map.v1",
        "feature_id": "RELEASE",
        "tasks": [{
            "task_id": "RELEASE-EVIDENCE-R10",
            "title": "release evidence",
            "owner_role": "dev",
            "wave": 1,
            "allowed_paths": ["evidence/release/**"],
            "allowed_paths_reason": "release evidence owner",
            "acceptance": ["release evidence exists"],
        }],
    }
    replacement = {
        "task_id": "RELEASE-EVIDENCE-R11",
        "parent_task_id": "RELEASE-EVIDENCE-R10",
        "claim_paths": ["evidence/release/**"],
        "verification_read_paths": [
            "scripts/release/release_gate.py",
            "tests/release/test_release_contract.py",
        ],
        "acceptance": ["release port evidence is current"],
        "verify_commands": [
            "python scripts/release/release_gate.py --check",
            "python -m pytest tests/release/test_release_contract.py -q",
        ],
        "base_commit": base_commit,
        "source_refs": ["reports/release-gap.json", f"git:{base_commit}"],
        "supersedes_task_ids": ["RELEASE-EVIDENCE-R10"],
    }

    amended = build_gap_task_map_amend(
        base,
        gap_tasks=[replacement],
        supersedes_task_map_ref="artifacts/RELEASE/task_map.json",
    )

    task = amended["tasks"][0]
    assert task["allowed_paths"] == ["evidence/release/**"]
    assert task["verification_read_paths"] == [
        "scripts/release/release_gate.py",
        "tests/release/test_release_contract.py",
    ]
    assert validate_task_map_payload(amended).passed is True


def test_assembly_replacement_inherits_parent_mechanical_owner_class() -> None:
    base_commit = "b" * 40
    base = {
        "schema_version": "task-map.v1",
        "feature_id": "PRD-1",
        "tasks": [
            {
                "task_id": "PRD-1-SLICE",
                "title": "slice",
                "owner_role": "dev-lane-0",
                "wave": 1,
                "allowed_paths": ["src/core/**"],
                "allowed_paths_reason": "slice owner",
                "acceptance": ["slice works"],
            },
            {
                "task_id": "PRD-1-ASSEMBLY",
                "title": "assembly",
                "owner_role": "dev-lane-1",
                "affinity_tag": "assembly",
                "root_owner_class": "assembly",
                "wave": 2,
                "allowed_paths": ["src/app.py"],
                "allowed_paths_reason": "assembly owner",
                "acceptance": ["product is assembled"],
            },
        ],
    }
    gap_task = {
        "task_id": "PRD-1-ASSEMBLY-GAP",
        "parent_task_id": "PRD-1-ASSEMBLY",
        "supersedes_task_ids": ["PRD-1-ASSEMBLY"],
        "claim_paths": ["src/app.py", "tests/release/**"],
        "acceptance": ["release assembly closes"],
        "verify_commands": ["python -m pytest tests/release"],
        "base_commit": base_commit,
        "source_refs": ["reports/prd-1-gap.json", f"git:{base_commit}"],
    }

    amended = build_gap_task_map_amend(
        base,
        gap_tasks=[gap_task],
        supersedes_task_map_ref="artifacts/PRD-1/task_map.json",
    )

    replacement = next(
        task for task in amended["tasks"]
        if task["task_id"] == "PRD-1-ASSEMBLY-GAP"
    )
    assert replacement["root_owner_class"] == "assembly"
    assert replacement["affinity_tag"] == "assembly"
    assert replacement["base_commit"] == base_commit
    assert validate_task_map_payload(amended).passed is True


def test_parent_gap_identity_overrides_conflicting_child_goal_identity() -> None:
    tasks = gap_tasks_from_gap_plan_payload({
        "goal_id": "PRD-1",
        "goal_kind": "prd",
        "gap_category": "product_completeness",
        "gap_tasks": [{
            "task_id": "PRD-1-GAP",
            "goal_id": "workflow-run-id",
            "claim_paths": ["src/app.py"],
            "acceptance": ["product closes"],
            "verify_commands": ["python -m pytest"],
            "source_refs": ["reports/prd-1-gap.json"],
            "evidence_contract": {"goal_id": "workflow-run-id"},
        }],
    })

    assert tasks[0]["goal_id"] == "PRD-1"
    amended = build_gap_task_map_amend(
        {
            "schema_version": "task-map.v1",
            "feature_id": "PRD-1",
            "tasks": [],
        },
        gap_tasks=tasks,
        supersedes_task_map_ref="artifacts/PRD-1/task_map.json",
    )
    assert amended["tasks"][0]["evidence_contract"]["goal_id"] == "PRD-1"
