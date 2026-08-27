from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from zf.runtime.skill_dispatch_treatment import (
    SkillDispatchTreatmentError,
    freeze_skill_dispatch_treatment,
)


def _entry(*, digest: str, source: str = "evolution-overlay://zf-plan"):
    return SimpleNamespace(
        name="zf-plan",
        sha256=digest,
        source=source,
        status="resolved",
    )


def _manifest(*, digest: str, source: str = "evolution-overlay://zf-plan"):
    return {
        "skills": [{
            "name": "zf-plan",
            "sha256": digest,
            "source": source,
            "status": "resolved",
            "materialized_to": ".codex/skills/zf-plan",
        }],
    }


def test_freeze_skill_dispatch_treatment_binds_selected_overlay(tmp_path) -> None:
    digest = "a" * 64

    descriptor = freeze_skill_dispatch_treatment(
        state_dir=tmp_path,
        role_instance="planner-0",
        task_id="ISSUE-1",
        run_id="run-1",
        selected_overlays=[{
            "skill_name": "zf-plan",
            "asset_id": "asset-1",
            "version": 2,
            "digest": digest,
        }],
        lock_entries=[_entry(digest=digest)],
        manifest_payload=_manifest(digest=digest),
    )

    body = json.loads((tmp_path / descriptor["ref"]).read_text(encoding="utf-8"))
    assert body["task_id"] == "ISSUE-1"
    assert body["selected_overlays"][0]["candidate_digest"] == digest
    assert body["materialized_skills"][0]["digest"] == digest


@pytest.mark.parametrize(
    ("task_id", "lock_digest", "manifest_digest", "source"),
    [
        ("", "a" * 64, "a" * 64, "evolution-overlay://zf-plan"),
        ("ISSUE-1", "b" * 64, "a" * 64, "evolution-overlay://zf-plan"),
        ("ISSUE-1", "a" * 64, "b" * 64, "evolution-overlay://zf-plan"),
        ("ISSUE-1", "a" * 64, "a" * 64, "skills/zf-plan/SKILL.md"),
    ],
)
def test_freeze_skill_dispatch_treatment_rejects_identity_drift(
    tmp_path,
    task_id: str,
    lock_digest: str,
    manifest_digest: str,
    source: str,
) -> None:
    with pytest.raises(SkillDispatchTreatmentError):
        freeze_skill_dispatch_treatment(
            state_dir=tmp_path,
            role_instance="planner-0",
            task_id=task_id,
            run_id="run-1",
            selected_overlays=[{
                "skill_name": "zf-plan",
                "asset_id": "asset-1",
                "version": 2,
                "digest": "a" * 64,
            }],
            lock_entries=[_entry(digest=lock_digest, source=source)],
            manifest_payload=_manifest(
                digest=manifest_digest,
                source=source,
            ),
        )
