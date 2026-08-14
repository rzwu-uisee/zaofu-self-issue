from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DECLARATION = "> required_prompt_refs:"


def _required_prompt_refs(text: str) -> list[str]:
    for line in text.splitlines():
        if line.startswith(DECLARATION):
            value = line.removeprefix(DECLARATION).strip()
            parsed = json.loads(value)
            assert isinstance(parsed, list)
            return [str(item) for item in parsed]
    return []


def test_tracked_prompt_required_refs_are_in_git_source_closure() -> None:
    tracked = set(subprocess.check_output(
        ["git", "ls-files", "prompt/*.md"],
        cwd=ROOT,
        text=True,
    ).splitlines())

    for relative in sorted(tracked):
        path = ROOT / relative
        for required_ref in _required_prompt_refs(path.read_text(encoding="utf-8")):
            assert required_ref in tracked, (
                f"{relative} requires untracked prompt source {required_ref}"
            )
            assert (ROOT / required_ref).is_file(), (
                f"{relative} requires missing prompt source {required_ref}"
            )


def test_five_workflow_prompt_is_standalone_in_fresh_worktree() -> None:
    relative = "prompt/2026-08-05-1559-kanban-agent-five-workflow-real-e2e.md"
    path = ROOT / relative
    if not path.exists():
        pytest.skip(f"{relative} is not part of this source checkout")
    text = path.read_text(encoding="utf-8")

    assert _required_prompt_refs(text) == []
    assert "2026-08-04-0922-new-project-kanban-channel-workflow-real-e2e" not in text
    assert "[FIVE-E2E-P]" in text
    assert "Workflow requirement clarification" in text
