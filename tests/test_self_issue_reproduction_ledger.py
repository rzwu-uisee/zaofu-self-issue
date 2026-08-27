from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.runtime.self_issue_reproduction_ledger import (
    finalize_incomplete_reproductions,
    initialize_reproduction_ledger,
    read_reproduction_ledger,
    record_reproduction_result,
    reserve_reproduction_attempt,
    seed_workspace_reproduction_state,
    sync_workspace_reproduction_state,
)


def test_ledger_keeps_one_three_attempt_budget_across_resume_workspaces(
    tmp_path: Path,
) -> None:
    ledger = initialize_reproduction_ledger(
        tmp_path, draft_id="sid-1", run_id="sie-1",
    )
    first_workspace = tmp_path / "first"
    first_workspace.mkdir()
    first = reserve_reproduction_attempt(ledger, target="subject:tests/a.py")
    seed_workspace_reproduction_state(ledger, workspace_root=first_workspace)
    local = first_workspace / ".assessment-runtime" / "reproductions.json"
    local_body = json.loads(local.read_text(encoding="utf-8"))
    local_body["attempts"][0]["status"] = "failed"
    local.write_text(json.dumps(local_body), encoding="utf-8")
    sync_workspace_reproduction_state(ledger, workspace_root=first_workspace)

    resumed_workspace = tmp_path / "resumed"
    resumed_workspace.mkdir()
    seed_workspace_reproduction_state(ledger, workspace_root=resumed_workspace)
    second = reserve_reproduction_attempt(ledger, target="subject:tests/b.py")
    seed_workspace_reproduction_state(ledger, workspace_root=resumed_workspace)
    record_reproduction_result(
        ledger, attempt=int(second["attempt"]), target="subject:tests/b.py", status="passed",
    )
    third = reserve_reproduction_attempt(ledger, target="subject:tests/c.py")
    record_reproduction_result(
        ledger, attempt=int(third["attempt"]), target="subject:tests/c.py", status="timeout",
    )
    fourth = reserve_reproduction_attempt(ledger, target="subject:tests/d.py")

    body = read_reproduction_ledger(ledger)
    assert first["attempt"] == 1
    assert second["attempt"] == 2
    assert third["attempt"] == 3
    assert fourth == {
        "allowed": False,
        "attempt": 4,
        "target": "subject:tests/d.py",
    }
    assert [item["status"] for item in body["attempts"]] == [
        "failed", "passed", "timeout",
    ]


def test_interrupt_marks_started_attempt_unknown_and_restart_gets_new_budget(
    tmp_path: Path,
) -> None:
    interrupted = initialize_reproduction_ledger(
        tmp_path, draft_id="sid-1", run_id="sie-old",
    )
    attempt = reserve_reproduction_attempt(interrupted, target="subject:tests/a.py")
    record_reproduction_result(
        interrupted,
        attempt=int(attempt["attempt"]),
        target="subject:tests/a.py",
        status="started",
    )

    finalized = finalize_incomplete_reproductions(interrupted)
    restarted = initialize_reproduction_ledger(
        tmp_path, draft_id="sid-1", run_id="sie-new",
    )

    assert finalized["attempts"][0]["status"] == "outcome_unknown"
    assert read_reproduction_ledger(restarted)["attempts"] == []


def test_tampered_workspace_attempt_fails_closed(tmp_path: Path) -> None:
    ledger = initialize_reproduction_ledger(
        tmp_path, draft_id="sid-1", run_id="sie-1",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    reserve_reproduction_attempt(ledger, target="subject:tests/a.py")
    seed_workspace_reproduction_state(ledger, workspace_root=workspace)
    state = workspace / ".assessment-runtime" / "reproductions.json"
    body = json.loads(state.read_text(encoding="utf-8"))
    body["attempts"][0]["target"] = "subject:tests/other.py"
    state.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(ValueError, match="target mismatch"):
        sync_workspace_reproduction_state(ledger, workspace_root=workspace)
