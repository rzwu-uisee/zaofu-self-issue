from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.events import EventLog, EventWriter
from zf.runtime.self_issue_liveness import self_issue_runtime_status
from zf.runtime.self_issue_runtime import schedule_pending_self_issue_assessment
from zf.runtime.self_issue_service import SelfIssueService


def _answers() -> dict[str, object]:
    return {
        "title": "Runtime-owned Self-Issue assessment",
        "bug_description": "The board stopped updating.",
        "reproduction_steps": "Open the board and wait for the next event.",
        "expected_behavior": "The board updates.",
        "attachments_context": "",
        "environment": {"os": "Linux", "version": "24.04"},
        "zaofu_version": "0.0.3",
        "additional_context": "",
    }


def _service(tmp_path: Path) -> tuple[SelfIssueService, EventLog]:
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    (state_dir / "session.yaml").write_text("session_id: test\n", encoding="utf-8")
    event_log = EventLog(state_dir / "events.jsonl")
    service = SelfIssueService(
        state_dir, EventWriter(event_log), project_root=tmp_path,
    )
    return service, event_log


def _draft(service: SelfIssueService) -> dict:
    intake = service.capture({
        "target_binding": {"provider": "gitlab", "project": "a/b"},
    })["intake"]
    return service.submit_intake({
        "intake_id": intake["intake_id"], "answers": _answers(),
    })["draft"]


def _mark_live(state_dir: Path) -> None:
    guard = state_dir / "processes" / "watcher.pid.json"
    guard.parent.mkdir(parents=True, exist_ok=True)
    guard.write_text(json.dumps({"owner_pid": os.getpid()}), encoding="utf-8")


def test_runtime_status_requires_a_live_watcher_owner(tmp_path: Path) -> None:
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    assert self_issue_runtime_status(state_dir) == "unknown"

    (state_dir / "session.yaml").write_text("session_id: test\n", encoding="utf-8")
    assert self_issue_runtime_status(state_dir) == "stopped"

    _mark_live(state_dir)
    assert self_issue_runtime_status(state_dir) == "live"

    (state_dir / "processes" / "watcher.pid.json").write_text(
        json.dumps({"owner_pid": 999_999_999}), encoding="utf-8",
    )
    assert self_issue_runtime_status(state_dir) == "stopped"


def test_stopped_runtime_collects_static_evidence_without_semantic_assessment(
    tmp_path: Path,
) -> None:
    service, event_log = _service(tmp_path)
    draft = _draft(service)

    result = service.start_evidence({
        "draft_id": draft["draft_id"], "revision": draft["revision"],
    })

    assert result["status"] == "evidence_waiting_for_runtime"
    assert result["scheduled"] is False
    assert result["draft"]["runtime_status"] == "stopped"
    assert result["draft"]["evidence_status"] == "waiting_for_runtime"
    assert result["draft"]["evidence_collection_mode"] == "static_only"
    assert result["draft"]["assessment_status"] == "waiting_for_runtime"
    assert "self_issue.assessment.claimed" not in {
        event.type for event in event_log.read_all()
    }


def test_limited_offline_report_is_publishable_and_discloses_the_gap(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path)
    draft = _draft(service)
    waiting = service.start_evidence({
        "draft_id": draft["draft_id"], "revision": draft["revision"],
    })

    limited = service.continue_limited({"draft_id": draft["draft_id"]})
    preview = service.preview({"draft_id": draft["draft_id"]})

    assert waiting["draft"]["assessment_status"] == "waiting_for_runtime"
    assert limited["draft"]["evidence_status"] == "completed"
    assert limited["draft"]["evidence_collection_mode"] == "limited"
    assert limited["draft"]["assessment_status"] == "skipped"
    assert limited["draft"]["assessment_confidence"] == "low"
    body = preview["preview"]["body"]
    assert "Evidence collection was limited." in body
    assert "Project runtime was stopped and the user chose to continue." in body
    assert "A semantic Orchestrator assessment was not performed" in body


def test_runtime_scheduler_claims_each_pending_assessment_once(
    tmp_path: Path, monkeypatch,
) -> None:
    service, event_log = _service(tmp_path)
    draft = _draft(service)
    _mark_live(service.state_dir)
    requested = service.start_evidence({
        "draft_id": draft["draft_id"], "revision": draft["revision"],
    })
    assert requested["status"] == "assessment_requested"

    started_threads: list[object] = []

    class DeferredThread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self) -> None:
            started_threads.append(self)

    monkeypatch.setattr("zf.runtime.self_issue_runtime.threading.Thread", DeferredThread)
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=".state"),
        roles=[RoleConfig(name="orchestrator", backend="claude-code")],
    )
    orchestrator = SimpleNamespace(
        state_dir=service.state_dir,
        event_writer=EventWriter(event_log),
        project_root=tmp_path,
        config=config,
    )

    assert schedule_pending_self_issue_assessment(orchestrator) is True
    assert schedule_pending_self_issue_assessment(orchestrator) is False
    assert len(started_threads) == 1
    stored = service.drafts.get(draft["draft_id"])
    assert stored is not None
    assert stored.assessment_status == "running"
    assert stored.assessment_claim_id.startswith("siac-")

