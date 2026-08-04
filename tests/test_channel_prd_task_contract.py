from __future__ import annotations

import hashlib
from pathlib import Path

from zf.core.config.schema import ProjectConfig, ZfConfig
from zf.core.events import EventLog, EventWriter
from zf.core.task.contract_validation import validate_task_contract
from zf.core.task.store import TaskStore
from zf.runtime.channel_contract_artifacts import (
    persist_channel_conclusion,
    persist_channel_prd,
    persist_channel_prd_readiness,
)
from zf.runtime.channel_reply_prompt import channel_reply_response_contract
from zf.runtime.control_actions import ControlledActionService


def _execute(
    service: ControlledActionService,
    writer: EventWriter,
    action: str,
    payload: dict,
) -> dict:
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        payload={"action": action, "request": payload},
    )
    return service.execute(
        action=action,
        requested_action=action,
        payload=payload,
        requested=requested,
    )


def _ready_prd_fixture(
    tmp_path: Path,
    *,
    implementation_start: bool,
) -> tuple[Path, EventWriter, ControlledActionService, dict[str, object]]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    config = ZfConfig(project=ProjectConfig(name="channel-prd-task"))
    service = ControlledActionService(
        state_dir,
        writer,
        config=config,
        project_root=tmp_path,
        actor="web",
        source="channel",
        surface="web",
    )
    assert _execute(service, writer, "channel-create", {
        "channel_id": "ch-prd-task",
        "name": "PRD Task",
        "owner_actor_ref": "web",
    })["ok"]
    assert _execute(service, writer, "channel-invite-member", {
        "channel_id": "ch-prd-task",
        "member_id": "leader-1",
        "provider": "fake",
        "channel_role": "product_pm",
        "permission_profile": "read_only",
        "permissions": ["read", "message", "summarize", "propose_workflow"],
    })["ok"]
    assert _execute(service, writer, "channel-set-leader", {
        "channel_id": "ch-prd-task",
        "leader_member_id": "leader-1",
        "expected_revision": 0,
        "idempotency_key": "leader-r1",
    })["ok"]

    spec_ref = "channel-artifacts/ch-prd-task/spec-r1.md"
    spec_path = state_dir / spec_ref
    spec_path.parent.mkdir(parents=True)
    markdown = (
        "# Canonical PRD\n\n"
        "## Acceptance Criteria\n"
        "- The product contract passes `python -m pytest -q`.\n\n"
        "## Out of Scope\n"
        "- YAML output is excluded.\n"
    )
    spec_path.write_text(markdown, encoding="utf-8")
    spec_digest = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    readiness = persist_channel_prd_readiness(
        state_dir,
        channel_id="ch-prd-task",
        thread_id="main",
        revision=1,
        body={
            "verdict": "ready",
            "implementation_start": implementation_start,
            "gaps": [],
            "risks": [],
            "evidence_refs": ["event:requirement"],
        },
        created_by="leader-1",
        source_event_id="evt-synthesis-reply",
    )
    semantic = {
        "summary": "Implement strict JSON output from the confirmed PRD.",
        "acceptance_criteria": [
            "Strict JSON behavior passes the declared verification command.",
        ],
        "out_of_scope": ["YAML output"],
        "scope": ["src/**", "tests/**"],
        "affected_files": ["src/**", "tests/**"],
        "shared_files": ["pyproject.toml"],
        "exclusive_files": ["src/**"],
    }
    prd = persist_channel_prd(
        state_dir,
        channel_id="ch-prd-task",
        thread_id="main",
        revision=1,
        previous_ref="",
        previous_digest="",
        body={
            "summary": semantic["summary"],
            "title": "Strict JSON delivery",
            "synthesis": semantic,
            "markdown": markdown,
            "spec_path": spec_ref,
            "spec_digest": spec_digest,
            "source_refs": ["event:requirement"],
            "evidence_refs": [],
        },
        readiness_descriptor=readiness,
        created_by="leader-1",
        source_event_id="evt-synthesis-reply",
    )
    conclusion = persist_channel_conclusion(
        state_dir,
        channel_id="ch-prd-task",
        thread_id="main",
        revision=1,
        prd_descriptor=prd,
        readiness_descriptor=readiness,
        summary=str(semantic["summary"]),
        source_refs=["event:requirement"],
        created_by="leader-1",
        source_event_id="evt-synthesis-reply",
    )
    artifact_payload = {
        "channel_id": "ch-prd-task",
        "thread_id": "main",
        "artifact_ref": prd["ref"],
        "artifact_digest": prd["sha256"],
        "prd_ref": prd["ref"],
        "prd_digest": prd["sha256"],
        "prd_revision": 1,
        "readiness_ref": readiness["ref"],
        "readiness_digest": readiness["sha256"],
        "readiness_verdict": "ready",
        "implementation_start": implementation_start,
        "conclusion_ref": conclusion["ref"],
        "conclusion_digest": conclusion["sha256"],
        "spec_path": spec_ref,
        "spec_digest": spec_digest,
        "source_refs": ["event:requirement"],
    }
    writer.emit(
        "channel.synthesis.proposed",
        actor="leader-1",
        correlation_id="ch-prd-task",
        payload={**artifact_payload, "summary": semantic["summary"]},
    )
    writer.emit(
        "channel.consensus.proposed",
        actor="leader-1",
        correlation_id="ch-prd-task",
        payload={
            **artifact_payload,
            "required_signers": ["leader-1"],
            "proposed_by": "leader-1",
        },
    )
    writer.emit(
        "channel.consensus.reached",
        actor="web",
        correlation_id="ch-prd-task",
        payload={**artifact_payload, "confirmed_by": "web"},
    )
    authority: dict[str, object] = {
        "channel_id": "ch-prd-task",
        "thread_id": "main",
        "channel_member_id": "leader-1",
        "leader_revision": 1,
        "prd_revision": 1,
        "source_ref": prd["ref"],
        "source_digest": prd["sha256"],
    }
    return state_dir, writer, service, authority


def _task_payload(authority: dict[str, object]) -> dict:
    return {
        "task_id": "TASK-CHANNEL-PRD",
        "title": "Deliver strict JSON",
        "priority": 2,
        "execution_mode": "workflow",
        "channel_authority": authority,
        "contract": {
            "schema_version": "task-contract.v1",
            "behavior": "Untrusted option text must not replace the PRD.",
            "source_mode": "channel_prd",
            "source_ref": authority["source_ref"],
            "source_revision": "1",
            "evidence_contract": {
                "channel_id": authority["channel_id"],
                "thread_id": authority["thread_id"],
                "channel_member_id": authority["channel_member_id"],
                "leader_revision": authority["leader_revision"],
                "prd_revision": authority["prd_revision"],
                "source_digest": authority["source_digest"],
            },
        },
    }


def test_non_ready_channel_prd_cannot_create_task(tmp_path: Path) -> None:
    state_dir, writer, service, authority = _ready_prd_fixture(
        tmp_path,
        implementation_start=False,
    )

    result = _execute(service, writer, "create-task", _task_payload(authority))

    assert result["ok"] is False
    assert result["status"] == "channel_prd_not_ready"
    assert "implementation_start=False" in result["reason"]
    assert TaskStore(state_dir / "kanban.json").list_all() == []
    assert not any(
        event.type == "task.created"
        for event in writer.event_log.read_all()
    )


def test_ready_channel_prd_compiles_strict_workflow_parent_contract(
    tmp_path: Path,
) -> None:
    state_dir, writer, service, authority = _ready_prd_fixture(
        tmp_path,
        implementation_start=True,
    )
    payload = _task_payload(authority)
    payload.pop("execution_mode")

    result = _execute(service, writer, "create-task", payload)

    assert result["ok"] is True, result
    task = TaskStore(state_dir / "kanban.json").get("TASK-CHANNEL-PRD")
    assert task is not None
    assert task.contract.behavior.startswith("Implement strict JSON")
    assert task.contract.verification == "python -m pytest -q"
    assert task.contract.validation["commands"][0]["command"] == (
        "python -m pytest -q"
    )
    assert task.contract.verification_tiers == ["runtime"]
    assert task.contract.product_contract_ref == authority["source_ref"]
    assert task.contract.spec_ref.endswith("spec-r1.md")
    assert task.contract.exclusions == ["YAML output"]
    assert task.contract.scope == ["src/**", "tests/**"]
    assert task.contract.affected_files == ["src/**", "tests/**"]
    assert task.contract.shared_files == ["pyproject.toml"]
    assert task.contract.exclusive_files == ["src/**"]
    assert task.contract.evidence_contract["execution_owner"] == "workflow"
    assert task.contract.evidence_contract["implementation_start"] is True
    assert validate_task_contract(
        task,
        config=service.config,
        project_root=tmp_path,
    ) == []


def test_channel_synthesis_prompt_requires_readiness_and_verification() -> None:
    prompt = channel_reply_response_contract(
        {},
        {},
        {"refs": {"synthesis_request_id": "synth-1"}},
    )

    assert "verification_commands" in prompt
    assert "readiness" in prompt
    assert "implementation_start=true only" in prompt
