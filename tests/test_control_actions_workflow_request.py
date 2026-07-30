from __future__ import annotations

import time
from pathlib import Path

from zf.core.config.loader import load_config
from zf.core.events import ZfEvent
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.workflow_requests import load_workflow_request
from zf.runtime.workflow_synthesis import (
    WORKFLOW_SYNTHESIS_RESULT_SCHEMA,
    run_workflow_synthesis as _run_workflow_synthesis,
)


def _service(tmp_path: Path) -> tuple[ControlledActionService, EventLog]:
    config_ref = tmp_path / "zf.yaml"
    config_ref.write_text("""\
apiVersion: zaofu.dev/v1
kind: IssueFlow
metadata: {name: issue-demo}
spec:
  lanes: 1
  backend: mock
  issueRef: docs/intake/bug.md
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  version: "1.0"
  project: {name: demo, state_dir: .zf}
""", encoding="utf-8")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    service = ControlledActionService(
        state_dir,
        EventWriter(log),
        config=load_config(config_ref),
        project_root=tmp_path,
        actor="operator",
        source="kanban-agent",
        surface="web",
    )
    return service, log


def _execute(service: ControlledActionService, action: str, payload: dict) -> dict:
    return service._execute_action(
        action=action,
        requested_action=action,
        payload=payload,
        requested=ZfEvent(type="control.action.requested", actor="test", payload=payload),
    )


def test_request_is_proposed_before_explicit_submit(tmp_path: Path) -> None:
    service, log = _service(tmp_path)

    proposed = _execute(service, "workflow-request", {
        "kind": "issue",
        "objective": "Fix session expiry and add a regression test",
        "backend": "mock",
        "allow_missing_env": True,
    })

    assert proposed["ok"] is True
    assert proposed["status"] == "proposal_ready"
    assert Path(proposed["intake_ref"]).exists()
    assert "workflow.invoke.requested" not in [event.type for event in log.read_all()]

    submitted = _execute(service, "workflow-submit", {
        "intake_ref": proposed["intake_ref"],
        "request_id": proposed["request_id"],
        "proposal_ref": proposed["proposal_ref"],
        "proposal_digest": proposed["proposal_digest"],
        "kind": "issue",
        "allow_missing_env": True,
    })

    assert submitted["ok"] is True
    types = [event.type for event in log.read_all()]
    assert "workflow.request.proposed" in types
    assert "workflow.request.approved" in types
    assert "workflow.submit.accepted" in types
    assert "workflow.invoke.requested" in types

    replay = _execute(service, "workflow-submit", {
        "intake_ref": proposed["intake_ref"],
        "request_id": proposed["request_id"],
        "proposal_ref": proposed["proposal_ref"],
        "proposal_digest": proposed["proposal_digest"],
        "kind": "issue",
        "allow_missing_env": True,
    })
    assert replay["ok"] is True
    assert len([
        event for event in log.read_all()
        if event.type == "workflow.invoke.requested"
    ]) == 1


def test_submit_and_reject_bind_exact_current_proposal(tmp_path: Path) -> None:
    service, log = _service(tmp_path)
    proposed = _execute(service, "workflow-request", {
        "kind": "issue",
        "objective": "Fix session expiry and add a regression test",
        "backend": "mock",
        "allow_missing_env": True,
    })

    stale = _execute(service, "workflow-submit", {
        "intake_ref": proposed["intake_ref"],
        "request_id": proposed["request_id"],
        "proposal_ref": proposed["proposal_ref"],
        "proposal_digest": "f" * 64,
        "kind": "issue",
        "allow_missing_env": True,
    })
    assert stale["ok"] is False
    assert stale["status"] == "stale_proposal"
    assert "workflow.invoke.requested" not in [
        event.type for event in log.read_all()
    ]

    rejected = _execute(service, "workflow-reject", {
        "request_id": proposed["request_id"],
        "proposal_ref": proposed["proposal_ref"],
        "proposal_digest": proposed["proposal_digest"],
        "reason": "scope needs revision",
    })
    assert rejected["ok"] is True
    assert rejected["status"] == "rejected"
    assert "workflow.request.rejected" in [
        event.type for event in log.read_all()
    ]


def test_vague_request_requires_clarification_and_never_invokes(tmp_path: Path) -> None:
    service, log = _service(tmp_path)

    result = _execute(service, "workflow-request", {
        "kind": "issue",
        "objective": "",
        "open_questions": ["Which checkout path is affected?"],
        "backend": "mock",
        "allow_missing_env": True,
    })

    assert result["ok"] is False
    assert result["status"] == "clarification_required"
    blocker_kinds = {item["kind"] for item in result["blockers"]}
    assert "workflow_request_required_fields_missing" in blocker_kinds
    assert "workflow_request_open_questions" in blocker_kinds
    assert "workflow.invoke.requested" not in [event.type for event in log.read_all()]


def test_initialized_idea_to_product_proposes_request_then_submit(tmp_path: Path) -> None:
    service, log = _service(tmp_path)

    result = _execute(service, "idea-to-product", {
        "objective": "Fix session expiry and add a regression test",
        "kind": "issue",
        "backend": "mock",
        "allow_missing_env": True,
    })

    assert result["ok"] is True
    proposal = [
        event for event in log.read_all() if event.type == "operator.action.proposed"
    ][-1]
    assert [item["action"] for item in proposal.payload["proposals"]] == [
        "workflow-request",
        "workflow-submit",
    ]


def test_workflow_request_passes_admitted_synthesis_to_proposal_builder(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service, _log = _service(tmp_path)

    def _synthesize(**kwargs):
        projection = load_workflow_request(
            kwargs["state_dir"],
            kwargs["request_id"],
        )
        candidate = {
            "schema_version": WORKFLOW_SYNTHESIS_RESULT_SCHEMA,
            "request_id": projection["request_id"],
            "request_revision": projection["revision"],
            "requirement_ref": projection["requirement_spec_ref"],
            "requirement_digest": projection["requirement_spec_digest"],
            "selected_flow_family": "IssueFlow",
            "short_flow_spec": {
                "flow_family": "IssueFlow",
                "purpose": "Repair the confirmed issue",
                "parameters": {
                    "lanes": 1,
                    "strictness": "standard",
                    "pattern_id": "issue-triage",
                },
            },
            "decision_rationale": "The request is a bounded issue repair.",
            "assumptions": [],
            "open_questions": [],
            "requested_roles": [],
            "requested_skills": [],
            "requested_profiles": ["direct-v1"],
            "completion_profile": {
                "delivery_policy": "report_only",
                "completion_threshold": "",
                "required_artifacts": [],
            },
            "risk_hints": [],
        }
        return _run_workflow_synthesis(
            **{
                **kwargs,
                "candidate_result": candidate,
            }
        )

    monkeypatch.setattr(
        "zf.runtime.workflow_synthesis.run_workflow_synthesis",
        _synthesize,
    )
    proposed = _execute(service, "workflow-request", {
        "kind": "issue",
        "objective": "Fix session expiry and add a regression test",
        "backend": "mock",
        "synthesis_backend": "mock",
        "allow_missing_env": True,
    })

    assert proposed["ok"] is True
    assert proposed["status"] == "synthesis_queued"
    assert proposed["synthesis_operation_id"]
    assert not proposed.get("proposal")

    from zf.runtime.workflow_synthesis import consume_workflow_synthesis_operations

    consumed = consume_workflow_synthesis_operations(
        state_dir=service.state_dir,
        project_root=service.project_root,
        config=service.config,
        writer=service.writer,
        agent=None,
        limit=1,
    )
    assert consumed == 1
    request = load_workflow_request(service.state_dir, proposed["request_id"])
    assert request["status"] == "proposed"
    from zf.runtime.workflow_proposal import load_workflow_proposal

    proposal = load_workflow_proposal(
        service.state_dir,
        request["proposal_ref"],
    )
    assert proposal["synthesis_result_ref"] == request["synthesis_ref"]
    assert proposal["run_parameters"] == {
        "lanes": 1,
        "strictness": "standard",
        "pattern_id": "issue-triage",
    }
    assert proposal["approval_status"] == "approvable"


def test_workflow_request_enqueue_returns_without_provider_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service, _log = _service(tmp_path)

    def blocking_provider(**kwargs):
        time.sleep(2)
        raise AssertionError("HTTP producer must not call synthesis provider")

    monkeypatch.setattr(
        "zf.runtime.workflow_synthesis.run_workflow_synthesis",
        blocking_provider,
    )
    started = time.monotonic()
    queued = _execute(service, "workflow-request", {
        "request_id": "req-fast-accept",
        "kind": "issue",
        "objective": "Fix session expiry and add a regression test",
        "backend": "mock",
        "synthesis_backend": "mock",
        "allow_missing_env": True,
    })

    assert time.monotonic() - started < 1
    assert queued["status"] == "synthesis_queued"
    assert queued["operation_status"] == "queued"


def test_workflow_synthesis_cancel_is_exact_controlled_and_replayable(
    tmp_path: Path,
) -> None:
    from zf.runtime.workflow_operation import load_workflow_operation

    service, _log = _service(tmp_path)
    queued = _execute(service, "workflow-request", {
        "request_id": "req-cancel",
        "kind": "issue",
        "objective": "Fix session expiry and add a regression test",
        "backend": "mock",
        "synthesis_backend": "mock",
        "allow_missing_env": True,
    })
    payload = {
        "request_id": queued["request_id"],
        "operation_id": queued["synthesis_operation_id"],
        "request_hash": queued["synthesis_request_hash"],
        "reason": "operator changed scope",
    }

    stale = _execute(
        service,
        "workflow-cancel",
        {**payload, "request_hash": "f" * 64},
    )
    assert stale["ok"] is False
    assert stale["status"] == "stale_operation"

    cancelled = _execute(service, "workflow-cancel", payload)
    replayed = _execute(service, "workflow-cancel", payload)

    assert cancelled["ok"] is True
    assert cancelled["status"] == "cancelled"
    assert cancelled["replayed"] is False
    assert replayed["ok"] is True
    assert replayed["replayed"] is True
    operation = load_workflow_operation(
        service.writer.event_log,
        queued["synthesis_operation_id"],
    )
    assert operation is not None
    assert operation["status"] == "cancelled"
