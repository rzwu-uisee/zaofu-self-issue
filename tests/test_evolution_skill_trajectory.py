from __future__ import annotations

import json

from zf.runtime.evolution_skill_provider import skill_load_evidence
from zf.runtime.evolution_skill_trajectory import (
    evaluate_trajectory_behavior,
    normalize_provider_trajectory,
)


def _line(event_type: str, *, command: str = "", status: str = "completed") -> str:
    return json.dumps(
        {
            "type": event_type,
            "item": {
                "type": "command_execution",
                "command": command,
                "status": status,
            },
        }
    )


def test_outcome_success_does_not_imply_skill_behavior() -> None:
    trajectory = normalize_provider_trajectory(
        case_id="case-1",
        backend="codex",
        stdout=_line("item.completed", command="python scripts/check.py"),
        stderr="",
        final="CORRECT ANSWER",
        workspace_root="/tmp/work",
    )

    verdict = evaluate_trajectory_behavior(
        {
            "case_id": "case-1",
            "behavior_expectations": [
                {"id": "read-method", "metric": "activation", "value": True},
            ],
        },
        trajectory,
    )

    assert verdict["behavior_followed"] is False
    assert verdict["checks"][0]["observed"] is False


def test_skill_read_and_order_are_derived_from_trajectory_steps() -> None:
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "trial-1"}),
            _line(
                "item.completed", command="cat /tmp/work/.codex/skills/demo/SKILL.md"
            ),
            _line("item.completed", command="python scripts/check.py"),
        ]
    )
    trajectory = normalize_provider_trajectory(
        case_id="case-2",
        backend="codex",
        stdout=stdout,
        stderr="",
        final="WRONG ANSWER",
        skill_load_evidence=[{"line": "2", "digest": "a" * 64}],
        workspace_root="/tmp/work",
    )

    verdict = evaluate_trajectory_behavior(
        {
            "case_id": "case-2",
            "behavior_expectations": [
                {"metric": "activation", "value": True},
                {"metric": "skill_read_before_action", "value": True},
                {"metric": "script_execution_count", "operator": "gte", "value": 1},
                {"metric": "security_clear", "value": True},
            ],
        },
        trajectory,
    )

    assert verdict["behavior_followed"] is True
    assert all(check["trajectory_step_refs"] for check in verdict["checks"][:3])


def test_unobservable_behavior_is_null_instead_of_guessed() -> None:
    trajectory = normalize_provider_trajectory(
        case_id="case-3",
        backend="claude-code",
        stdout=json.dumps({"result": "done"}),
        stderr="",
        final="done",
    )

    verdict = evaluate_trajectory_behavior({"case_id": "case-3"}, trajectory)

    assert verdict["behavior_followed"] is None
    assert verdict["checks"] == []


def test_codex_explicit_skill_injection_is_pre_action_evidence(tmp_path) -> None:
    skill_dir = tmp_path / ".codex" / "skills" / "demo-method"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("demo", encoding="utf-8")
    evidence = skill_load_evidence(
        stdout=_line("item.completed", command="python scripts/check.py"),
        stderr="",
        skill_name="demo-method",
        target_path=str(skill_dir),
        backend="codex",
        prompt="Use $demo-method for this task.",
    )

    assert [item["kind"] for item in evidence] == ["provider_skill_injected"]
    trajectory = normalize_provider_trajectory(
        case_id="case-explicit",
        backend="codex",
        stdout=_line("item.completed", command="python scripts/check.py"),
        stderr="",
        final="done",
        skill_load_evidence=evidence,
        workspace_root=str(tmp_path),
    )

    assert trajectory["metrics"]["activation"] is True
    assert trajectory["metrics"]["skill_read_before_action"] is True
    assert trajectory["steps"][0]["event_type"] == "provider_skill_injected"


def test_explicit_injection_requires_codex_materialization_and_exact_mention(
    tmp_path,
) -> None:
    skill_dir = tmp_path / ".codex" / "skills" / "demo-method"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("demo", encoding="utf-8")

    assert (
        skill_load_evidence(
            stdout="",
            stderr="",
            skill_name="demo-method",
            target_path=str(skill_dir),
            backend="claude-code",
            prompt="Use $demo-method.",
        )
        == []
    )
    assert (
        skill_load_evidence(
            stdout="",
            stderr="",
            skill_name="demo-method",
            target_path=str(skill_dir),
            backend="codex",
            prompt="Use $demo-method-extra.",
        )
        == []
    )
    assert (
        skill_load_evidence(
            stdout="",
            stderr="",
            skill_name="demo-method",
            target_path=str(tmp_path / "missing"),
            backend="codex",
            prompt="Use $demo-method.",
        )
        == []
    )


def test_system_shell_executable_is_not_a_workspace_escape(tmp_path) -> None:
    command = "/bin/bash -lc \"sed -n '1,40p' .codex/skills/demo/SKILL.md\""
    trajectory = normalize_provider_trajectory(
        case_id="case-system-shell",
        backend="codex",
        stdout=_line("item.completed", command=command),
        stderr="",
        final="done",
        workspace_root=str(tmp_path),
    )

    assert trajectory["metrics"]["workspace_escape_count"] == 0
    assert trajectory["metrics"]["security_clear"] is True


def test_system_shell_does_not_hide_an_external_data_path(tmp_path) -> None:
    trajectory = normalize_provider_trajectory(
        case_id="case-external-data",
        backend="codex",
        stdout=_line("item.completed", command="/bin/bash -lc 'cat /etc/passwd'"),
        stderr="",
        final="done",
        workspace_root=str(tmp_path),
    )

    assert trajectory["metrics"]["workspace_escape_count"] == 1
    assert trajectory["metrics"]["security_clear"] is False
