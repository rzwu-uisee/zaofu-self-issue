from __future__ import annotations

import hashlib
from pathlib import Path

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SelfIssueConfig,
    SessionConfig,
    ZfConfig,
)
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.state.session import SessionStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.self_issue_auto_trigger import (
    auto_trigger_from_event,
    classify_self_issue_signal,
)
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


def _config() -> ZfConfig:
    return ZfConfig(self_issue=SelfIssueConfig(
        enabled=True,
        target_locked=True,
        target_project="owner/central",
        automatic_detection_enabled=True,
    ))


def test_only_allowlisted_strong_kernel_signals_create_local_intake(tmp_path: Path) -> None:
    state = tmp_path / ".state"
    state.mkdir()
    writer = EventWriter(EventLog(state / "events.jsonl"))
    weak = ZfEvent(type="test.failed", actor="worker", payload={"reason": "project test"})
    strong = ZfEvent(
        type="kernel.housekeeping.failed",
        actor="kernel",
        payload={"step": "projection", "reason": "store invariant rejected"},
    )

    assert classify_self_issue_signal(weak) is None
    result = auto_trigger_from_event(
        event=strong, state_dir=state, writer=writer,
        project_root=tmp_path, config=_config(),
    )

    assert result is not None
    assert result["status"] == "auto_intake_created"
    intake = result["intake"]
    assert intake["origin"] == "system_detected"
    assert intake["status"] == "awaiting_user_review"
    assert intake["target_binding"] == {"provider": "gitlab", "project": "owner/central"}
    assert intake["answers"]["title"] == "Kernel housekeeping failed"
    assert not (state / "self-issues" / "drafts.json").exists()


def test_worker_signal_requires_immutable_reporter_evidence_and_deduplicates(
    tmp_path: Path,
) -> None:
    state = tmp_path / ".state"
    evidence = state / "artifacts" / "worker" / "incident.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text('{"safe":"bounded worker context"}\n', encoding="utf-8")
    ref = {
        "ref": "artifacts/worker/incident.json",
        "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
        "byte_count": evidence.stat().st_size,
        "kind": "worker_self_issue_report",
        "created_by": "planner",
        "source_event_id": "evt-worker-source",
    }
    writer = EventWriter(EventLog(state / "events.jsonl"))
    missing = ZfEvent(
        type="worker.self_issue.detected", actor="planner", payload={"title": "blocked"},
    )
    assert classify_self_issue_signal(missing) is None

    first = ZfEvent(
        type="worker.self_issue.detected", actor="planner", task_id="TASK-1",
        payload={
            "title": "Worker dispatch is stuck",
            "summary": "The same internal dispatch transition was rejected 17 times.",
            "classification": "kernel/state", "severity": "P1",
            "evidence_refs": [ref],
        },
    )
    second = ZfEvent(
        type="worker.self_issue.detected", actor="planner", task_id="TASK-1",
        payload={
            "title": "Worker dispatch is stuck",
            "summary": "The same internal dispatch transition was rejected 42 times.",
            "classification": "kernel/state", "severity": "P1",
            "evidence_refs": [ref],
        },
    )
    created = auto_trigger_from_event(
        event=first, state_dir=state, writer=writer,
        project_root=tmp_path, config=_config(),
    )
    updated = auto_trigger_from_event(
        event=second, state_dir=state, writer=writer,
        project_root=tmp_path, config=_config(),
    )

    assert created is not None and updated is not None
    assert created["status"] == "auto_intake_created"
    assert updated["status"] == "auto_intake_updated"
    assert updated["intake"]["occurrence_count"] == 2
    assert updated["intake"]["notification_due"] is False
    assert updated["intake"]["reporter_context"]["reported_by"] == "worker"

    escalated_event = ZfEvent(
        type="worker.self_issue.detected", actor="planner", task_id="TASK-1",
        payload={
            "title": "Worker dispatch is stuck",
            "summary": "The same internal dispatch transition was rejected 99 times.",
            "classification": "kernel/state", "severity": "P0",
            "evidence_refs": [ref],
        },
    )
    escalated = auto_trigger_from_event(
        event=escalated_event, state_dir=state, writer=writer,
        project_root=tmp_path, config=_config(),
    )

    assert escalated is not None
    assert escalated["status"] == "auto_intake_updated"
    assert escalated["intake"]["notification_due"] is True
    assert escalated["intake"]["detection"]["severity"] == "P0"


def test_automatic_detection_can_be_disabled_without_affecting_manual_policy(
    tmp_path: Path,
) -> None:
    state = tmp_path / ".state"
    state.mkdir()
    writer = EventWriter(EventLog(state / "events.jsonl"))
    config = _config()
    config.self_issue.automatic_detection_enabled = False

    result = auto_trigger_from_event(
        event=ZfEvent(
            type="kernel.housekeeping.failed", actor="kernel",
            payload={"reason": "projection failed"},
        ),
        state_dir=state, writer=writer, project_root=tmp_path, config=config,
    )

    assert result is None
    assert not (state / "self-issues" / "intakes.json").exists()


def test_dismissed_automatic_fingerprint_is_suppressed_for_24_hours(tmp_path: Path) -> None:
    state = tmp_path / ".state"
    state.mkdir()
    writer = EventWriter(EventLog(state / "events.jsonl"))
    event = ZfEvent(
        type="orchestrator.tick.failed", actor="kernel", payload={"reason": "tick crashed"},
    )
    created = auto_trigger_from_event(
        event=event, state_dir=state, writer=writer,
        project_root=tmp_path, config=_config(),
    )
    assert created is not None
    from zf.runtime.self_issue_service import SelfIssueService

    service = SelfIssueService(
        state, writer, project_root=tmp_path, policy=_config().self_issue,
    )
    service.dismiss_intake({"intake_id": created["intake"]["intake_id"]})
    repeated = auto_trigger_from_event(
        event=ZfEvent(
            type="orchestrator.tick.failed", actor="kernel",
            payload={"reason": "tick crashed"},
        ),
        state_dir=state, writer=writer, project_root=tmp_path, config=_config(),
    )
    assert repeated is not None
    assert repeated["status"] == "auto_intake_suppressed"


def test_orchestrator_housekeeping_wires_strong_signal_to_same_intake_store(
    tmp_path: Path,
) -> None:
    state = tmp_path / ".state"
    state.mkdir()
    (state / "memory").mkdir()
    (state / "kanban.json").write_text("[]\n", encoding="utf-8")
    EventLog(state / "events.jsonl").append(ZfEvent(type="loop.started", actor="zf-cli"))
    SessionStore(state / "session.yaml").create(project_root=str(tmp_path))
    config = _config()
    config.project = ProjectConfig(name="test")
    config.session = SessionConfig(tmux_session="test")
    config.roles = [RoleConfig(name="dev", backend="mock")]
    orchestrator = Orchestrator(
        state,
        config,
        TmuxTransport(TmuxSession(session_name="test", dry_run=True)),
    )

    orchestrator._apply_housekeeping(ZfEvent(
        type="kernel.housekeeping.failed",
        actor="orchestrator",
        payload={"step": "progress", "reason": "projection crashed"},
    ))

    rows = (state / "self-issues" / "intakes.json").read_text(encoding="utf-8")
    assert "system_detected" in rows
    assert "awaiting_user_review" in rows
