from pathlib import Path

from zf.runtime.self_issue_evidence_activity import EvidenceActivityStore, read_evidence_activity


def test_activity_projection_exposes_actor_phases_without_agent_payloads(tmp_path: Path) -> None:
    store = EvidenceActivityStore(tmp_path, draft_id="sid-1", run_id="run-1")
    store.start(actor="kernel")
    store.phase("planner", "reporter_context", "Planner supplied a bounded artifact")
    store.phase("orchestrator", "assessing", "Assessing incident evidence and impact")
    store.complete(actor="kernel")

    value = read_evidence_activity(tmp_path, "sid-1")
    assert value is not None
    assert value["schema_version"] == "self-issue-evidence-activity.v1"
    assert value["status"] == "completed"
    assert [(item["actor"], item["phase"]) for item in value["entries"]][-2:] == [
        ("orchestrator", "assessing"), ("kernel", "completed"),
    ]
    assert "raw" not in str(value).lower()


def test_activity_projection_fails_closed_for_mismatched_draft(tmp_path: Path) -> None:
    store = EvidenceActivityStore(tmp_path, draft_id="sid-1", run_id="run-1")
    store.start(actor="kernel")
    assert read_evidence_activity(tmp_path, "sid-2") is None
