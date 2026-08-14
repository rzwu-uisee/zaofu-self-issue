from __future__ import annotations

import json
from pathlib import Path

from zf.core.config.schema import RoleConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.skill_invocation_projection import project_skill_invocations


def _fixture(tmp_path: Path) -> tuple[Path, ZfConfig, Path]:
    state_dir = tmp_path / ".zf"
    skill_dir = state_dir / "workdirs/dev-1/codex-home/skills/review-method"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("# method\n", encoding="utf-8")
    manifest = state_dir / "workdirs/dev-1/runtime/skills-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "instance_id": "dev-1",
        "task_id": "T-1",
        "skills": [{
            "name": "review-method",
            "status": "resolved",
            "materialized_to": str(skill_dir),
            "sha256": "abc",
            "auto_inject": True,
            "collision_candidates": ["project:skills/review-method/SKILL.md"],
        }],
    }), encoding="utf-8")
    config = ZfConfig(roles=[RoleConfig(
        name="dev",
        instance_id="dev-1",
        backend="codex",
        skills=["review-method", "configured-only"],
    )])
    return state_dir, config, skill_path


def test_configured_and_auto_injected_are_not_inferred_as_invoked(
    tmp_path: Path,
) -> None:
    state_dir, config, _skill_path = _fixture(tmp_path)

    projection = project_skill_invocations(
        state_dir,
        config=config,
        project_root=tmp_path,
    )
    rows = {row["skill"]: row for row in projection["skills"]}

    assert rows["configured-only"]["considered"] is True
    assert rows["configured-only"]["loaded"] is False
    assert rows["configured-only"]["observation"] == "not_materialized"
    assert rows["review-method"]["loaded"] is True
    assert rows["review-method"]["auto_injected"] is True
    assert rows["review-method"]["invoked"] is False
    assert rows["review-method"]["observation"] == "loaded_unobserved"


def test_only_current_dispatch_exact_materialized_read_counts(
    tmp_path: Path,
) -> None:
    state_dir, config, skill_path = _fixture(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    old_dispatch = ZfEvent(
        type="task.dispatched",
        id="dispatch-old",
        task_id="T-1",
        payload={"assignee": "dev-1", "attempt_id": "attempt-old"},
    )
    current_dispatch = ZfEvent(
        type="task.dispatched",
        id="dispatch-current",
        task_id="T-1",
        payload={"assignee": "dev-1", "attempt_id": "attempt-current"},
    )
    log.append(old_dispatch)
    log.append(ZfEvent(
        type="agent.tool.use",
        actor="dev-1",
        causation_id=old_dispatch.id,
        payload={"tool": "Read", "input": {"path": str(skill_path)}},
    ))
    log.append(current_dispatch)
    log.append(ZfEvent(
        type="agent.tool.use",
        actor="dev-2",
        causation_id=current_dispatch.id,
        payload={"tool": "Read", "input": {"path": str(skill_path)}},
    ))
    log.append(ZfEvent(
        type="agent.tool.use",
        actor="dev-1",
        causation_id=current_dispatch.id,
        payload={
            "tool": "Read",
            "input": {"path": str(tmp_path / "skills/review-method/SKILL.md")},
        },
    ))

    before = project_skill_invocations(
        state_dir,
        config=config,
        project_root=tmp_path,
        task_id="T-1",
        role_instance="dev-1",
    )
    assert before["summary"]["invoked_count"] == 0

    exact = ZfEvent(
        type="codex.hook.pre_tool_use",
        actor="dev-1",
        causation_id=current_dispatch.id,
        payload={"tool_name": "Read", "tool_input": {"file_path": str(skill_path)}},
    )
    log.append(exact)
    after = project_skill_invocations(
        state_dir,
        config=config,
        project_root=tmp_path,
        task_id="T-1",
        role_instance="dev-1",
    )
    row = next(row for row in after["skills"] if row["skill"] == "review-method")
    assert row["invoked"] is True
    assert row["dispatch_event_id"] == "dispatch-current"
    assert row["evidence"] == [{
        "event_id": exact.id,
        "event_type": "codex.hook.pre_tool_use",
        "tool": "Read",
        "kind": "materialized_skill_read",
        "dispatch_event_id": "dispatch-current",
    }]


def test_stream_dispatch_identity_and_controlled_skill_tool_are_supported(
    tmp_path: Path,
) -> None:
    state_dir, config, _skill_path = _fixture(tmp_path)
    events = [
        ZfEvent(
            type="task.dispatched",
            id="dispatch-1",
            task_id="T-1",
            payload={"assignee": "dev-1", "dispatch_id": "delivery-1"},
        ),
        ZfEvent(
            type="agent.tool.use",
            actor="dev-1",
            task_id="T-1",
            payload={
                "dispatch_id": "delivery-1",
                "tool": "Skill",
                "input": {"skill": "review-method"},
            },
        ),
    ]

    projection = project_skill_invocations(
        state_dir,
        config=config,
        project_root=tmp_path,
        events=events,
    )

    row = next(row for row in projection["skills"] if row["skill"] == "review-method")
    assert row["invoked"] is True
    assert row["evidence"][0]["kind"] == "controlled_skill_tool"
