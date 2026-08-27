from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.candidate_success_currentness import (
    evaluate_candidate_success_currentness,
)
from zf.runtime.orchestrator_module_parity import ModuleParityBridgeMixin


RUN_ID = "run-currentness"
GENERATION = "generation-1"
CANDIDATE_REF = "refs/heads/candidate/currentness"
CANDIDATE_HEAD = "a" * 40
PACKAGE_DIGEST = "b" * 64
ANCHOR_TASK = "FLOW-ANCHOR"


def _candidate(event_id: str = "candidate-ready") -> ZfEvent:
    return ZfEvent(
        id=event_id,
        type="candidate.ready",
        correlation_id=RUN_ID,
        payload={
            "schema_version": "candidate-freeze-receipt.v1",
            "workflow_run_id": RUN_ID,
            "task_map_generation": GENERATION,
            "plan_artifact_package_digest": PACKAGE_DIGEST,
            "candidate_ref": CANDIDATE_REF,
            "candidate_head_commit": CANDIDATE_HEAD,
        },
    )


def _operation() -> ZfEvent:
    return ZfEvent(
        id="operation-requested",
        type="workflow.operation.requested",
        task_id=ANCHOR_TASK,
        correlation_id=RUN_ID,
        payload={"operation_id": "operation-verify"},
    )


def _verify() -> ZfEvent:
    return ZfEvent(
        id="verify-passed",
        type="verify.passed",
        correlation_id=RUN_ID,
        payload={
            "workflow_run_id": RUN_ID,
            "operation_id": "operation-verify",
            "verification_owner": "candidate_verify",
            "candidate_anchor_task_id": ANCHOR_TASK,
            "task_map_generation": GENERATION,
            "plan_artifact_package_digest": PACKAGE_DIGEST,
            "candidate_ref": CANDIDATE_REF,
            "candidate_head_commit": CANDIDATE_HEAD,
            "candidate_snapshot_event_id": "candidate-ready",
            "pdd_id": "PDD-1",
            "feature_id": "PDD-1",
        },
    )


def _rework() -> ZfEvent:
    return ZfEvent(
        id="rework-requested",
        type="task.rework.requested",
        task_id=ANCHOR_TASK,
        correlation_id=RUN_ID,
        payload={
            "workflow_run_id": RUN_ID,
            "task_id": ANCHOR_TASK,
            "pdd_id": "PDD-1",
        },
    )


def test_candidate_success_is_current_before_later_rework() -> None:
    verify = _verify()
    verdict = evaluate_candidate_success_currentness(
        [_candidate(), _operation(), verify],
        verify,
    )

    assert verdict.applies is True
    assert verdict.current is True
    assert verdict.issues == ()


def test_candidate_success_is_superseded_when_rework_follows_dispatch() -> None:
    verify = _verify()
    verdict = evaluate_candidate_success_currentness(
        [_candidate(), _operation(), verify, _rework()],
        verify,
    )

    assert verdict.current is False
    assert verdict.superseded_by == "rework-requested"
    assert {issue["code"] for issue in verdict.issues} == {
        "candidate_result_superseded",
    }


def test_candidate_success_rejects_replacement_candidate_identity() -> None:
    verify = _verify()
    replacement = ZfEvent(
        id="candidate-ready-2",
        type="candidate.ready",
        correlation_id=RUN_ID,
        payload={
            "schema_version": "candidate-freeze-receipt.v1",
            "workflow_run_id": RUN_ID,
            "task_map_generation": "generation-2",
            "plan_artifact_package_digest": "c" * 64,
            "candidate_ref": CANDIDATE_REF,
            "candidate_head_commit": "d" * 40,
        },
    )

    verdict = evaluate_candidate_success_currentness(
        [_candidate(), _operation(), verify, replacement],
        verify,
    )

    assert verdict.current is False
    assert {issue["code"] for issue in verdict.issues} >= {
        "stale_task_map_generation",
        "stale_plan_artifact_package",
        "stale_target_commit",
        "stale_candidate_snapshot",
    }


class _Harness(ModuleParityBridgeMixin):
    def __init__(self, state_dir: Path) -> None:
        self.event_log = EventLog(state_dir / "events.jsonl")
        self.event_writer = EventWriter(self.event_log)


def test_superseded_verdict_is_emitted_once(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    verify = _verify()
    for event in (_candidate(), _operation(), verify, _rework()):
        harness.event_log.append(event)

    first = harness._reject_stale_candidate_success(verify)
    second = harness._reject_stale_candidate_success(verify)

    assert first is not None and first.action == "supersede"
    assert second is not None and second.action == "supersede"
    superseded = [
        event
        for event in harness.event_log.read_all()
        if event.type == "candidate.result.superseded"
    ]
    assert len(superseded) == 1
    assert superseded[0].payload["semantic_attempt_incremented"] is False
