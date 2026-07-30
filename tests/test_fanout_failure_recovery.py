from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.runtime.fanout_failure_findings import fanout_failure_recovery


class _Owner:
    def __init__(self, event_log: EventLog, payload: dict) -> None:
        self.event_log = event_log
        self.payload = payload

    def _fanout_child_payloads(self, manifest: dict) -> list[dict]:
        return [self.payload]


def _payload(*, allowed_scope: list[str]) -> dict:
    return {
        "fanout_id": "fanout-verify",
        "child_id": "verify-1-TASK-1",
        "task_id": "TASK-1",
        "control_result_schema": "verification-result.v1",
        "control_result_ref": {
            "ref": "artifacts/call-results/control/result.json",
            "sha256": "a" * 64,
        },
        "admitted_call_result_ref": {
            "ref": "artifacts/call-results/envelopes/result.json",
            "sha256": "b" * 64,
        },
        "verification_result": {
            "schema_version": "verification-result.v1",
            "failure_class": "dependency_blocked",
            "task_id": "TASK-1",
            "rework_items": [{
                "rework_item_id": "RW-1",
                "owner": "untrusted-free-form-owner",
                "allowed_scope": allowed_scope,
            }],
        },
    }


def test_plan_port_dependency_projects_planner_recovery(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    manifest = {
        "fanout_id": "fanout-verify",
        "children": [{
            "child_id": "verify-1-TASK-1",
            "status": "failed",
        }],
    }

    recovery = fanout_failure_recovery(
        _Owner(log, _payload(allowed_scope=["plan_ports:test_matrix"])),
        manifest,
    )

    assert recovery == {
        "failure_class": "dependency_blocked",
        "recovery_action": "replan",
        "rework_scope": "plan_ports",
        "recovery_owner": "planner",
        "failed_task_ids": ["TASK-1"],
        "rework_item_ids": ["RW-1"],
        "semantic_result_refs": [
            "artifacts/call-results/control/result.json",
            "artifacts/call-results/envelopes/result.json",
        ],
    }


def test_non_admitted_verification_body_cannot_select_rework_owner(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    payload = _payload(allowed_scope=["plan_ports:test_matrix"])
    payload.pop("control_result_ref")
    manifest = {
        "fanout_id": "fanout-verify",
        "children": [{
            "child_id": "verify-1-TASK-1",
            "status": "failed",
        }],
    }

    assert fanout_failure_recovery(_Owner(log, payload), manifest) == {}
