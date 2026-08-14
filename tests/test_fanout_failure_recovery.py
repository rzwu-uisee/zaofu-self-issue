from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.runtime.fanout_failure_findings import (
    fanout_failure_recovery,
    findings_from_payload,
)


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


def test_canonical_plan_handoff_failure_routes_only_to_plan(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    payload = {
        "fanout_id": "fanout-impl",
        "child_id": "impl-TASK-1",
        "task_id": "TASK-1",
        "failure_scope": "plan_contract",
        "handoff_failure_fingerprint": "writer-handoff-plan",
        "redispatch_allowed": False,
    }
    manifest = {
        "fanout_id": "fanout-impl",
        "children": [{
            "child_id": "impl-TASK-1",
            "task_id": "TASK-1",
            "status": "failed",
        }],
    }

    recovery = fanout_failure_recovery(_Owner(log, payload), manifest)

    assert recovery["recovery_action"] == "return_to_plan"
    assert recovery["recovery_owner"] == "planner"
    assert recovery["redispatch_allowed"] is False


def test_repeated_worker_handoff_projects_bounded_safe_halt(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    payload = {
        "fanout_id": "fanout-impl",
        "child_id": "impl-TASK-1",
        "task_id": "TASK-1",
        "failure_scope": "worker_result",
        "handoff_failure_fingerprint": "writer-handoff-worker",
        "redispatch_allowed": False,
        "no_progress": True,
    }
    manifest = {
        "fanout_id": "fanout-impl",
        "children": [{
            "child_id": "impl-TASK-1",
            "task_id": "TASK-1",
            "status": "failed",
        }],
    }

    recovery = fanout_failure_recovery(_Owner(log, payload), manifest)

    assert recovery["no_progress"] is True
    assert recovery["redispatch_allowed"] is False
    assert recovery["bounded_recovery_decision"]["status"] == "safe_halt"
    assert recovery["bounded_recovery_decision"][
        "max_additional_writer_attempts"
    ] == 0


def test_nested_verification_rejection_preserves_requirement_findings() -> None:
    findings = findings_from_payload({
        "child_id": "verify-candidate",
        "task_id": "PRD-1",
        "semantic_verdict": "rejected",
        "report": {
            "schema_version": "verification-result.v1",
            "verdict": "rejected",
            "requirement_results": [{
                "acceptance_id": "AC25",
                "status": "failed",
                "evidence_refs": ["evidence://layout"],
                "reproduction_commands": ["npm run e2e"],
                "findings": [{
                    "severity": "high",
                    "path": "src/styles/mobility.css",
                    "line": 256,
                    "message": "hidden surface still computes to display:grid",
                }],
            }],
            "rework_items": [{
                "rework_item_id": "RW-AC25",
                "acceptance_id": "AC25",
                "required_delta": "make inactive surfaces display:none",
            }],
        },
    })

    assert findings == [{
        "severity": "high",
        "path": "src/styles/mobility.css",
        "line": 256,
        "message": "hidden surface still computes to display:grid",
        "acceptance_id": "AC25",
        "evidence_refs": ["evidence://layout"],
        "verification_command": "npm run e2e",
        "category": "verification",
        "finding_id": "verify-candidate-1",
        "child_id": "verify-candidate",
        "task_id": "PRD-1",
    }]


def test_semantic_rejection_reason_never_falls_back_to_runtime_failure() -> None:
    findings = findings_from_payload({
        "child_id": "verify-candidate",
        "semantic_verdict": "rejected",
        "reason": "semantic verdict: rejected",
    })

    assert findings[0]["category"] == "verification"
