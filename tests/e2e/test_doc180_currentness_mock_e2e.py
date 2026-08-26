from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import ProjectConfig, ZfConfig
from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator import Orchestrator


class _Transport:
    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        raise AssertionError("stale Candidate success must not dispatch work")

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


def test_late_admitted_verify_cannot_advance_after_rework(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    runtime = Orchestrator(
        state_dir,
        ZfConfig(project=ProjectConfig(name="doc180", state_dir=str(state_dir))),
        _Transport(),
        project_root=tmp_path,
    )
    run_id = "run-doc180-currentness"
    generation = "generation-r1"
    candidate_ref = "refs/heads/candidate/doc180"
    candidate_head = "a" * 40
    package_digest = "b" * 64
    anchor_task = "DOC180-ANCHOR"
    candidate = ZfEvent(
        id="evt-candidate-r1",
        type="candidate.ready",
        correlation_id=run_id,
        payload={
            "schema_version": "candidate-freeze-receipt.v1",
            "workflow_run_id": run_id,
            "task_map_generation": generation,
            "plan_artifact_package_digest": package_digest,
            "candidate_ref": candidate_ref,
            "candidate_head_commit": candidate_head,
        },
    )
    operation = ZfEvent(
        id="evt-verify-operation",
        type="workflow.operation.requested",
        task_id=anchor_task,
        correlation_id=run_id,
        payload={"operation_id": "op-doc180-verify"},
    )
    verify = ZfEvent(
        id="evt-verify-r1",
        type="verify.passed",
        actor="zf-cli",
        correlation_id=run_id,
        payload={
            "workflow_run_id": run_id,
            "operation_id": "op-doc180-verify",
            "verification_owner": "candidate_verify",
            "candidate_currentness_required": True,
            "candidate_anchor_task_id": anchor_task,
            "candidate_snapshot_event_id": candidate.id,
            "task_map_generation": generation,
            "plan_artifact_package_digest": package_digest,
            "candidate_ref": candidate_ref,
            "candidate_head_commit": candidate_head,
            "pdd_id": "DOC180",
            "feature_id": "DOC180",
        },
    )
    rework = ZfEvent(
        id="evt-rework-after-verify",
        type="task.rework.requested",
        task_id=anchor_task,
        correlation_id=run_id,
        payload={
            "workflow_run_id": run_id,
            "task_id": anchor_task,
            "pdd_id": "DOC180",
        },
    )
    for event in (candidate, operation, verify, rework):
        runtime.event_log.append(event)

    decisions = runtime.run_once(events=[verify])

    assert [decision.action for decision in decisions] == ["supersede"]
    events = runtime.event_log.read_all()
    verdict = next(
        event for event in events if event.type == "candidate.result.superseded"
    )
    assert verdict.causation_id == verify.id
    assert verdict.payload["superseded_by"] == rework.id
    assert not [
        event
        for event in events
        if event.type in {
            "flow.discovery.requested",
            "flow.goal.closed",
            "judge.passed",
            "run.goal.completed",
        }
    ]
