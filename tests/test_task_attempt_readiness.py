from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.task_attempt_readiness import build_task_attempt_readiness


def _settled_attempt(
    store: TaskAttemptStore,
    *,
    run_id: str,
    task_id: str,
    ordinal: int,
) -> dict:
    row = store.ensure_for_dispatch(
        run_id=run_id,
        task_id=task_id,
        dispatch_id=f"dispatch-{ordinal}",
        role="dev",
        instance_id=f"dev-{ordinal}",
        operation_id=f"operation-{ordinal}",
        briefing_ref=f"briefing-{ordinal}.md",
        created_at="2026-07-26T10:00:00+00:00",
        lease_expires_at="2026-07-26T11:00:00+00:00",
        max_attempts=3,
    ).attempt
    settled = store.update(
        row["attempt_id"],
        status="succeeded",
        updated_at="2026-07-26T10:05:00+00:00",
        terminal_event_id=f"terminal-{ordinal}",
    )
    assert settled is not None
    return settled


def _comparison(row: dict, *, match: bool = True) -> ZfEvent:
    return ZfEvent(
        type="task.attempt.shadow.compared",
        task_id=row["task_id"],
        payload={
            "workflow_run_id": row["run_id"],
            "attempt_id": row["attempt_id"],
            "match": match,
        },
    )


def test_readiness_reports_candidate_only_at_quiet_matched_point(
    tmp_path: Path,
) -> None:
    store = TaskAttemptStore(tmp_path / "task_attempts.json")
    first = _settled_attempt(
        store,
        run_id="RUN-1",
        task_id="TASK-1",
        ordinal=1,
    )
    second = _settled_attempt(
        store,
        run_id="RUN-1",
        task_id="TASK-2",
        ordinal=2,
    )

    report = build_task_attempt_readiness(
        tmp_path,
        [_comparison(first), _comparison(second)],
        mode="shadow",
        min_comparisons=2,
        now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc),
    )

    assert report["decision"] == "candidate"
    assert report["promotion_candidate"] is True
    assert report["automatic_apply"] is False
    assert report["summary"]["matched_comparisons"] == 2
    assert report["blockers"] == []


def test_readiness_blocks_mismatch_active_and_uncompared_terminal(
    tmp_path: Path,
) -> None:
    store = TaskAttemptStore(tmp_path / "task_attempts.json")
    settled = _settled_attempt(
        store,
        run_id="RUN-1",
        task_id="TASK-1",
        ordinal=1,
    )
    active = store.ensure_for_dispatch(
        run_id="RUN-1",
        task_id="TASK-2",
        dispatch_id="dispatch-active",
        role="dev",
        instance_id="dev-active",
        operation_id="operation-active",
        briefing_ref="active.md",
        created_at="2026-07-26T10:00:00+00:00",
        lease_expires_at="2026-07-26T13:00:00+00:00",
        max_attempts=3,
    ).attempt

    report = build_task_attempt_readiness(
        tmp_path,
        [_comparison(settled, match=False)],
        mode="shadow",
        min_comparisons=1,
        now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc),
    )

    assert report["decision"] == "blocked"
    assert report["promotion_candidate"] is False
    assert {
        item["code"] for item in report["blockers"]
    } >= {"shadow_mismatch", "active_attempts"}
    assert report["evidence"]["active_attempt_ids"] == [active["attempt_id"]]


def test_doctor_task_attempt_json_can_require_readiness(
    tmp_path: Path,
    capsys,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (tmp_path / "zf.yaml").write_text(
        "project:\n"
        "  name: readiness\n"
        "  state_dir: .zf\n"
        "roles:\n"
        "  - name: dev\n"
        "    backend: mock\n"
        "workflow:\n"
        "  task_attempt:\n"
        "    mode: shadow\n"
        "    max_attempts: 3\n",
        encoding="utf-8",
    )
    store = TaskAttemptStore(state_dir / "task_attempts.json")
    row = _settled_attempt(
        store,
        run_id="RUN-1",
        task_id="TASK-1",
        ordinal=1,
    )
    EventLog(state_dir / "events.jsonl").append(_comparison(row))

    result = main([
        "doctor",
        "task-attempt",
        "--path",
        str(tmp_path / "zf.yaml"),
        "--min-comparisons",
        "1",
        "--require-ready",
        "--json",
    ])

    assert result == 0
    report = json.loads(capsys.readouterr().out)
    assert report["decision"] == "candidate"
    assert report["state_dir"] == str(state_dir)
