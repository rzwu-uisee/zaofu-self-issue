from __future__ import annotations

import json
from pathlib import Path

from tests.e2e.scripts.prod_flow_terminal_audit import audit_terminal_delivery


RUN_ID = "run-terminal-audit"
TERMINAL_ID = "evt-terminal"


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def _events(state_dir: Path, *, terminal_type: str = "run.goal.completed") -> None:
    rows = [{
        "id": TERMINAL_ID,
        "type": terminal_type,
        "correlation_id": RUN_ID,
        "payload": {"workflow_run_id": RUN_ID},
    }, {
        "id": "evt-owner",
        "type": "owner.visible_message.requested",
        "payload": {
            "message_id": "message-1",
            "terminal_event_id": TERMINAL_ID,
        },
    }]
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def _materialize(
    state_dir: Path,
    *,
    status: str = "delivered_requested",
    terminal_type: str = "run.goal.completed",
) -> None:
    root = state_dir / "projections" / "goals" / RUN_ID
    dossier_ref = f"projections/goals/{RUN_ID}/goal-dossier.v1.json"
    receipt_ref = f"projections/goals/{RUN_ID}/goal-completion-receipt.v1.json"
    _write(root / "delivery-materialization.v1.json", {
        "schema_version": "goal-dossier-delivery.v1",
        "run_id": RUN_ID,
        "status": status,
        "terminal_event_id": TERMINAL_ID,
        "terminal_event_type": terminal_type,
        "message_id": "message-1" if status == "delivered_requested" else "",
        "dossier_ref": dossier_ref,
        "completion_receipt_ref": receipt_ref,
        "reason": "fixture inconsistency" if status == "inconsistent" else "",
    })
    _write(root / "goal-dossier.v1.json", {
        "schema_version": "goal-dossier.v1",
        "delivery_readiness": {"status": "ready", "issues": []},
    })
    if terminal_type == "run.goal.completed":
        _write(root / "goal-completion-receipt.v1.json", {
            "schema_version": "goal-completion-receipt.v1",
            "terminal": {
                "event_id": TERMINAL_ID,
                "event_type": terminal_type,
            },
        })


def test_terminal_audit_waits_for_delivery_materialization(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    _events(state_dir)

    result = audit_terminal_delivery(state_dir)

    assert result["status"] == "pending"
    assert "materialization" in result["reason"]


def test_terminal_audit_rejects_inconsistent_delivery(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    _events(state_dir)
    _materialize(state_dir, status="inconsistent")

    result = audit_terminal_delivery(state_dir)

    assert result["status"] == "failed"
    assert result["delivery"]["status"] == "inconsistent"


def test_terminal_audit_accepts_complete_delivery_closure(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    _events(state_dir)
    _materialize(state_dir)

    result = audit_terminal_delivery(state_dir)

    assert result["status"] == "passed"
    assert all(result["checks"].values())


def test_terminal_audit_preserves_blocked_as_failed(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    _events(state_dir, terminal_type="run.goal.blocked")
    _materialize(state_dir, terminal_type="run.goal.blocked")

    result = audit_terminal_delivery(state_dir)

    assert result["status"] == "failed"
    assert result["reason"] == "workflow reached run.goal.blocked"


def test_terminal_audit_can_fail_fast_for_unattended_escalation(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir(parents=True)
    escalation = {
        "id": "evt-escalate",
        "type": "human.escalate",
        "payload": {
            "failure_class": "plan_admission_failed",
            "reason": "operator recovery is required",
        },
    }
    (state_dir / "events.jsonl").write_text(
        json.dumps(escalation) + "\n",
        encoding="utf-8",
    )

    assert audit_terminal_delivery(state_dir)["status"] == "pending"

    result = audit_terminal_delivery(
        state_dir,
        fail_on_human_escalate=True,
    )

    assert result["status"] == "failed"
    assert result["escalation"] == {
        "event_id": "evt-escalate",
        "reason": "operator recovery is required",
        "failure_class": "plan_admission_failed",
    }


def test_terminal_audit_can_fail_fast_for_repeated_child_failure(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir(parents=True)
    rows = [
        {
            "id": f"evt-failed-{index}",
            "type": "fanout.child.failed",
            "payload": {
                "task_id": "TASK-1",
                "reason": "unknown command id 'root-test'",
            },
        }
        for index in range(2)
    ]
    (state_dir / "events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    result = audit_terminal_delivery(
        state_dir,
        fail_on_repeated_child_failure=True,
    )

    assert result["status"] == "failed"
    assert result["failure_signal"] == {
        "kind": "repeated_child_failure",
        "task_id": "TASK-1",
        "reason": "unknown command id 'root-test'",
        "count": 2,
        "event_ids": ["evt-failed-0", "evt-failed-1"],
    }
