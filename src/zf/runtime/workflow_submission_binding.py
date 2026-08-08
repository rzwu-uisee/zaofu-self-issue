"""Immutable Run and Goal bindings created during Workflow submission."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.events import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.run_contract import load_run_contract, write_run_contract
from zf.runtime.workflow_preflight import _load_json


def pin_submitted_run_contract(
    *,
    state_dir: Path,
    preview: dict[str, Any],
    writer: EventWriter,
    correlation_id: str,
    task_id: str,
) -> None:
    preflight_ref = str(preview.get("preflight_ref") or "")
    report = _load_json(Path(preflight_ref)) if preflight_ref else {}
    run_contract = report.get("run_contract")
    run_contract = run_contract if isinstance(run_contract, dict) else {}
    contract = run_contract.get("preview")
    if not isinstance(contract, dict) or not str(
        contract.get("contract_digest") or ""
    ):
        raise RuntimeError(
            "accepted workflow submit is missing its run contract preview"
        )
    payload = preview.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    from zf.runtime.run_contract import bind_run_contract_workflow_artifacts

    contract = bind_run_contract_workflow_artifacts(
        contract,
        proposal_ref=(
            payload.get("workflow_proposal_ref")
            if isinstance(payload.get("workflow_proposal_ref"), dict)
            else {}
        ),
        proposal_digest=str(payload.get("workflow_proposal_digest") or ""),
        effective_config_ref=(
            payload.get("effective_config_ref")
            if isinstance(payload.get("effective_config_ref"), dict)
            else {}
        ),
    )
    previous = load_run_contract(state_dir)
    from zf.runtime.run_contract import write_run_contract_snapshot

    snapshot = write_run_contract_snapshot(state_dir, contract)
    path = write_run_contract(state_dir, contract)
    payload["run_contract_ref"] = str(snapshot.get("ref") or "")
    payload["run_contract_digest"] = str(
        contract.get("contract_digest") or ""
    )
    if str((previous or {}).get("contract_digest") or "") == str(
        contract.get("contract_digest") or ""
    ):
        return
    refs = contract.get("refs")
    refs = refs if isinstance(refs, dict) else {}
    manifest_refs = refs.get("workflow_input_manifest")
    manifest_refs = manifest_refs if isinstance(manifest_refs, list) else []
    writer.append(ZfEvent(
        type="config.run_contract.request_bound",
        actor="zf-cli",
        task_id=task_id,
        correlation_id=correlation_id,
        payload={
            "run_id": correlation_id,
            "run_contract_ref": str(snapshot.get("ref") or ""),
            "run_contract_sha256": str(snapshot.get("sha256") or ""),
            "contract_digest": str(contract.get("contract_digest") or ""),
            "active_run_contract_ref": str(path),
            "workflow_input_manifest_ref": str(
                manifest_refs[0] if manifest_refs else ""
            ),
            "workflow_proposal_ref": payload.get("workflow_proposal_ref")
            if isinstance(payload.get("workflow_proposal_ref"), dict) else {},
            "workflow_proposal_digest": str(
                payload.get("workflow_proposal_digest") or ""
            ),
            "effective_config_ref": payload.get("effective_config_ref")
            if isinstance(payload.get("effective_config_ref"), dict) else {},
            "initial_binding": bool(run_contract.get("initial_binding")),
            "prior_terminal_rotation": bool(
                run_contract.get("prior_terminal_rotation")
            ),
            "prior_run_id": str(run_contract.get("prior_run_id") or ""),
            "prior_terminal_event_id": str(
                run_contract.get("prior_terminal_event_id") or ""
            ),
        },
    ))


def pin_artifact_delivery_goal_claim_set(
    *,
    state_dir: Path,
    project_root: Path,
    preview: dict[str, Any],
    writer: EventWriter,
    correlation_id: str,
    task_id: str,
) -> None:
    payload = preview.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    if str(payload.get("completion_profile") or "") != "artifact_delivery":
        return
    workflow_run_id = str(payload.get("run_id") or correlation_id)
    goal_id = str(payload.get("goal_id") or correlation_id)
    generation = str(
        payload.get("workflow_generation")
        or payload.get("workflow_proposal_digest")
        or ""
    )
    if not workflow_run_id or not goal_id or not generation:
        raise RuntimeError(
            "artifact delivery submit is missing Goal/generation identity"
        )
    for event in reversed(writer.event_log.read_all()):
        if event.type != "goal.claim_set.pinned":
            continue
        event_payload = (
            event.payload if isinstance(event.payload, dict) else {}
        )
        if (
            str(event_payload.get("workflow_run_id") or "")
            == workflow_run_id
            and str(event_payload.get("goal_id") or "") == goal_id
            and str(event_payload.get("task_map_generation") or "")
            == generation
        ):
            payload["goal_claim_set_ref"] = str(
                event_payload.get("goal_claim_set_ref") or ""
            )
            payload["goal_claim_set_digest"] = str(
                event_payload.get("goal_claim_set_digest") or ""
            )
            return
    requirement_ref = str(payload.get("requirement_spec_ref") or "")
    requirement_digest = str(
        payload.get("requirement_spec_digest") or ""
    )
    if not requirement_ref or not requirement_digest:
        raise RuntimeError(
            "artifact delivery submit requires a confirmed Requirement"
        )
    from zf.runtime.goal_claim_set import (
        pin_goal_claim_set_from_requirement,
    )

    claim_set, descriptor = pin_goal_claim_set_from_requirement(
        state_dir=state_dir,
        project_root=project_root,
        requirement_ref=requirement_ref,
        requirement_digest=requirement_digest,
        workflow_run_id=workflow_run_id,
        goal_id=goal_id,
        workflow_generation=generation,
    )
    proposal_ref = payload.get("workflow_proposal_ref")
    proposal_ref = proposal_ref if isinstance(proposal_ref, dict) else {}
    pinned = writer.append(ZfEvent(
        type="goal.claim_set.pinned",
        actor="zf-cli",
        task_id=task_id,
        correlation_id=workflow_run_id,
        payload={
            "workflow_run_id": workflow_run_id,
            "goal_id": goal_id,
            "task_map_generation": generation,
            "task_map_ref": str(
                proposal_ref.get("ref") or requirement_ref
            ),
            "goal_claim_set_ref": str(descriptor.get("ref") or ""),
            "goal_claim_set_digest": str(
                descriptor.get("sha256") or ""
            ),
            "goal_claim_set_content_digest": str(
                claim_set.get("claim_set_digest") or ""
            ),
            "claim_count": len(claim_set.get("claims") or []),
            "source": "artifact_delivery_requirement",
        },
    ))
    payload["goal_claim_set_ref"] = str(descriptor.get("ref") or "")
    payload["goal_claim_set_digest"] = str(
        descriptor.get("sha256") or ""
    )
    payload["goal_claim_set_event_id"] = pinned.id


__all__ = [
    "pin_artifact_delivery_goal_claim_set",
    "pin_submitted_run_contract",
]
