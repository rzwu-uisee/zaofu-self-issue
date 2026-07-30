"""Deterministic routing metadata for task-level semantic replans."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore


SEMANTIC_REPLAN_ACTION = "semantic-replan-request"
SEMANTIC_REPLAN_SAFE_ACTION = "request_semantic_replan"

_PREFERRED_TRIGGER_ORDER = (
    "flow.discovery.requested",
    "verify.parity_scan.requested",
)
_SEMANTIC_REPLAN_SKILLS = {
    "zf-gap-task-synth",
}
_ANCHOR_EVENT_TYPES = {
    "task_map.ready",
    "task_map.amended",
    "product_delivery.task_map.adopted",
    "candidate.ready",
    "fanout.started",
}
_ANCHOR_KEYS = (
    "workflow_run_id",
    "task_map_ref",
    "source_index_ref",
    "source_commit",
    "candidate_base_commit",
    "candidate_ref",
    "candidate_head_commit",
    "target_ref",
    "trace_id",
    "task_map_generation",
    "plan_artifact_package_id",
    "plan_artifact_package_ref",
    "plan_artifact_package_digest",
)
_TASK_HANDOFF_KEYS = (
    "workflow_run_id",
    "task_ref",
    "base_commit",
    "contract_snapshot_ref",
    "contract_snapshot_digest",
    "plan_artifact_package_id",
    "plan_artifact_package_ref",
    "plan_artifact_package_digest",
    "workflow_proposal_ref",
    "workflow_proposal_digest",
    "effective_config_ref",
    "effective_config_digest",
    "run_contract_ref",
    "run_contract_digest",
    "goal_claim_set_ref",
    "goal_claim_set_digest",
)


@dataclass(frozen=True)
class SemanticReplanRoute:
    trigger_event: str
    stage_id: str
    role: str
    flow_kind: str = ""


def resolve_semantic_replan_route(config: ZfConfig) -> SemanticReplanRoute | None:
    """Find the declared gap-planning stage without hard-coding flow kinds."""

    stages = list(getattr(config.workflow, "stages", []) or [])
    for trigger in _PREFERRED_TRIGGER_ORDER:
        for stage in stages:
            if str(getattr(stage, "trigger", "") or "") == trigger:
                roles = list(getattr(stage, "roles", []) or [])
                return SemanticReplanRoute(
                    trigger_event=trigger,
                    stage_id=str(getattr(stage, "id", "") or ""),
                    role=str(roles[0] if roles else ""),
                    flow_kind=str(getattr(stage, "flow_kind", "") or ""),
                )

    skills_by_role = {
        str(getattr(role, "name", "") or ""): set(
            str(value) for value in getattr(role, "skills", []) or []
        )
        for role in getattr(config, "roles", []) or []
    }
    for stage in stages:
        roles = list(getattr(stage, "roles", []) or [])
        for role in roles:
            if skills_by_role.get(str(role), set()) & _SEMANTIC_REPLAN_SKILLS:
                trigger = str(getattr(stage, "trigger", "") or "")
                if trigger:
                    return SemanticReplanRoute(
                        trigger_event=trigger,
                        stage_id=str(getattr(stage, "id", "") or ""),
                        role=str(role),
                        flow_kind=str(getattr(stage, "flow_kind", "") or ""),
                    )
    return None


def enrich_semantic_replan_action(
    action: dict[str, Any],
    *,
    state_dir: Path,
    events: list[ZfEvent],
    config: ZfConfig,
) -> dict[str, Any]:
    """Attach stage and current task-map anchors, or fall back to diagnosis."""

    if str(action.get("action") or "") != SEMANTIC_REPLAN_ACTION:
        return action
    route = resolve_semantic_replan_route(config)
    anchor = _semantic_replan_anchor(
        state_dir,
        events,
        task_id=str(action.get("task_id") or ""),
    )
    if route is None or not anchor.get("task_map_ref") or not anchor.get("pdd_id"):
        reason = "semantic replan requires a declared gap-planning stage and current task-map anchor"
        return {
            **action,
            "action": "diagnose-attention",
            "safe_resume_action": "diagnose_attention",
            "failure_class": "semantic_replan_route_unavailable",
            "action_policy": "needs_diagnosis",
            "intervention_class": "diagnose",
            "summary": reason,
            "expected_downstream_events": [
                "run.manager.autoresearch.requested",
                "run.manager.resident.prompted",
            ],
            "verify_condition": (
                "expected_downstream_event:run.manager.autoresearch.requested,"
                "run.manager.resident.prompted"
            ),
        }
    return {
        **action,
        **anchor,
        "semantic_replan_trigger": route.trigger_event,
        "semantic_replan_stage_id": route.stage_id,
        "semantic_replan_role": route.role,
        "flow_kind": route.flow_kind,
        "stage_id": route.stage_id,
        "action_policy": "auto_decide",
        "owner_route": "run_manager",
        "intervention_class": "semantic_replan",
        "expected_downstream_events": [route.trigger_event],
        "verify_condition": f"expected_downstream_event:{route.trigger_event}",
    }


def _semantic_replan_anchor(
    state_dir: Path,
    events: list[ZfEvent],
    *,
    task_id: str,
) -> dict[str, Any]:
    task = TaskStore(Path(state_dir) / "kanban.json").get(task_id) if task_id else None
    pdd_id = ""
    feature_id = ""
    source_index_ref = ""
    if task is not None:
        feature_id = str(task.contract.feature_id or "")
        pdd_id = feature_id
        source_index_ref = str(task.contract.source_index_ref or "")
        evidence_contract = (
            task.contract.evidence_contract
            if isinstance(task.contract.evidence_contract, dict)
            else {}
        )
        source_refs = (
            evidence_contract.get("source_refs")
            if isinstance(evidence_contract.get("source_refs"), dict)
            else {}
        )
    else:
        evidence_contract = {}
        source_refs = {}
    anchor: dict[str, Any] = {
        "pdd_id": pdd_id,
        "feature_id": feature_id or pdd_id,
        "source_index_ref": source_index_ref,
    }
    for key in (
        "workflow_run_id",
        "task_map_ref",
        "source_index_ref",
        "task_map_generation",
        "plan_artifact_package_id",
        "plan_artifact_package_ref",
        "plan_artifact_package_digest",
    ):
        value = evidence_contract.get(key) or source_refs.get(key)
        if value not in (None, ""):
            anchor[key] = value
    for event in events:
        if event.type not in _ANCHOR_EVENT_TYPES:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        event_pdd = str(payload.get("pdd_id") or payload.get("feature_id") or "")
        if pdd_id and event_pdd and event_pdd != pdd_id:
            continue
        if not pdd_id and event_pdd:
            pdd_id = event_pdd
            anchor["pdd_id"] = pdd_id
            anchor["feature_id"] = str(payload.get("feature_id") or pdd_id)
        for key in _ANCHOR_KEYS:
            value = payload.get(key)
            if value not in (None, ""):
                anchor[key] = value
        if event.type == "candidate.ready" and str(
            payload.get("candidate_head_commit") or ""
        ).strip():
            anchor["candidate_event_id"] = event.id
    if pdd_id and not anchor.get("task_map_ref"):
        fallback = Path(state_dir) / "artifacts" / pdd_id / "task_map.json"
        if fallback.exists():
            anchor["task_map_ref"] = str(fallback)
    if task is not None:
        from zf.runtime.task_contract_snapshot import (
            TaskContractSnapshotError,
            current_task_contract_identity,
        )

        try:
            identity = current_task_contract_identity(
                task,
                task_map_ref=str(anchor.get("task_map_ref") or ""),
            )
        except TaskContractSnapshotError:
            identity = {}
        if identity:
            for event in events:
                payload = event.payload if isinstance(event.payload, dict) else {}
                event_task_id = str(event.task_id or payload.get("task_id") or "")
                if event_task_id != task_id:
                    continue
                incoming_revision = str(payload.get("contract_revision") or "")
                incoming_generation = str(payload.get("task_map_generation") or "")
                if (
                    incoming_revision
                    and incoming_revision != identity["contract_revision"]
                ):
                    continue
                if (
                    incoming_generation
                    and incoming_generation != identity["task_map_generation"]
                ):
                    continue
                for key in _TASK_HANDOFF_KEYS:
                    value = payload.get(key)
                    if value not in (None, "", [], {}):
                        anchor[key] = value
            anchor.update(identity)
    if anchor.get("candidate_head_commit") and anchor.get("candidate_ref"):
        # Parity discovery must inspect the delivered candidate. The task-map
        # baseline remains source_commit/candidate_base_commit provenance.
        anchor["target_ref"] = anchor["candidate_ref"]
    anchor["supersedes_task_ids"] = [task_id] if task_id else []
    anchor["affected_task_ids"] = [task_id] if task_id else []
    return anchor


__all__ = [
    "SEMANTIC_REPLAN_ACTION",
    "SEMANTIC_REPLAN_SAFE_ACTION",
    "SemanticReplanRoute",
    "enrich_semantic_replan_action",
    "resolve_semantic_replan_route",
]
