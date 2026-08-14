"""Immutable run-history helpers for ``goal-dossier.v1``.

The helpers in this module hydrate admitted refs and derive owner-facing
history.  Current stores are returned as an explicit overlay; they never
silently replace the terminal run facts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.runtime.goal_claim_set import hydrate_pinned_goal_claim_set
from zf.runtime.goal_coverage_graph import build_goal_coverage_graph
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_contract_snapshot import (
    descriptor_from_payload,
    hydrate_task_contract_snapshot,
)
from zf.runtime.terminal_events import is_successful_run_terminal


_TASK_SUCCESS_EVENTS = frozenset({
    "dev.build.done",
    "fanout.child.completed",
    "review.approved",
    "task.attempt.succeeded",
    "task.done.accepted",
    "test.passed",
    "verify.passed",
})
_TASK_FAILURE_EVENTS = frozenset({
    "dev.blocked",
    "dev.failed",
    "fanout.child.failed",
    "review.rejected",
    "task.attempt.deadlettered",
    "task.attempt.failed",
    "task.rework.capped",
    "test.failed",
    "verify.failed",
})
_INSTRUCTION_REF_KEYS = frozenset({
    "briefing_path",
    "briefing_ref",
    "child_briefing_ref",
    "contract_snapshot_ref",
    "goal_closure_contract_snapshot_ref",
    "task_contract_snapshot_ref",
    "objective_ref",
    "plan_artifact_package_ref",
    "prompt_ref",
    "role_briefing_ref",
    "task_ref",
    "workflow_input_manifest_ref",
    "workflow_prompt_ref",
})


def build_goal_dossier_history(
    state_dir: Any,
    *,
    run_id: str,
    goal_id: str,
    events: list[ZfEvent],
    current_tasks: list[dict[str, Any]],
    package_roadmap: Mapping[str, Any],
    project_id: str = "",
    excluded_task_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Build historical tasks, current overlay, claims and instruction refs."""

    diagnostics: list[dict[str, Any]] = []
    advisories: list[dict[str, Any]] = []
    terminal = _latest_terminal(events)
    artifact_delivery = _is_artifact_delivery_run(events)
    task_map, task_map_binding, task_map_diagnostics = _hydrate_task_map(
        state_dir,
        package_roadmap=package_roadmap,
        events=events,
        optional=artifact_delivery,
    )
    if artifact_delivery and not task_map:
        task_map = {
            "schema_version": "artifact-delivery-plan.v1",
            "workflow_run_id": run_id,
            "goal_id": goal_id,
            "tasks": [],
        }
    diagnostics.extend(task_map_diagnostics)
    contracts, contract_diagnostics = _hydrate_task_contracts(state_dir, events)
    historical_tasks = _historical_task_rows(
        events=events,
        task_map=task_map,
        contracts=contracts,
        current_tasks=current_tasks,
        terminal=terminal,
        excluded_task_ids=excluded_task_ids,
    )
    current_generation_ids = _task_map_task_ids(task_map)
    current_generation_tasks = (
        [
            task for task in historical_tasks
            if str(task.get("id") or "") in current_generation_ids
        ]
        if current_generation_ids
        else list(historical_tasks)
    )
    superseded_tasks = [
        {**task, "generation_status": "superseded"}
        for task in historical_tasks
        if current_generation_ids
        and str(task.get("id") or "") not in current_generation_ids
    ]
    authoritative_tasks = _authoritative_task_rows(
        historical_tasks=historical_tasks,
        task_map=task_map,
        terminal=terminal,
    )
    projection_tasks = (
        authoritative_tasks if terminal else current_generation_tasks
    )
    authoritative_task_ids = {
        str(item.get("id") or "")
        for item in projection_tasks
        if str(item.get("id") or "")
    }
    for item in contract_diagnostics:
        if str(item.get("task_id") or "") in authoritative_task_ids:
            diagnostics.append(item)
        else:
            advisories.append(item)
    current_overlay = _current_overlay(
        historical_tasks=projection_tasks,
        current_tasks=current_tasks,
    )
    instruction_context = _instruction_context(events)
    claim_matrix = _claim_matrix(
        state_dir=state_dir,
        task_map=task_map,
        tasks=projection_tasks,
        events=events,
        project_id=project_id,
        goal_id=goal_id,
        task_map_ref=str(task_map_binding.get("ref") or ""),
        authority=terminal,
    )
    if claim_matrix.get("status") == "incomplete":
        diagnostics.extend(claim_matrix.get("diagnostics") or [])
    advisories.extend(claim_matrix.get("advisories") or [])
    return redact_obj({
        "terminal": terminal,
        "authoritative_tasks": authoritative_tasks,
        "historical_tasks": historical_tasks,
        "current_generation_tasks": current_generation_tasks,
        "superseded_tasks": superseded_tasks,
        "current_overlay": current_overlay,
        "task_contracts": contracts,
        "task_map": {
            **task_map_binding,
            "status": (
                "not_required"
                if artifact_delivery and not task_map_binding.get("ref")
                else "ready"
                if task_map
                else "unavailable"
            ),
        },
        "instruction_context": instruction_context,
        "claim_to_evidence": claim_matrix,
        "diagnostics": diagnostics,
        "advisories": advisories,
    })


def _task_map_task_ids(task_map: Mapping[str, Any]) -> set[str]:
    tasks = task_map.get("tasks")
    if not isinstance(tasks, list):
        return set()
    return {
        str(item.get("task_id") or item.get("id") or "").strip()
        for item in tasks
        if isinstance(item, Mapping)
        and str(item.get("task_id") or item.get("id") or "").strip()
    }


def _latest_terminal(events: list[ZfEvent]) -> dict[str, Any]:
    for event in reversed(events):
        if event.type not in {"run.goal.completed", "run.goal.blocked"}:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        return {
            "status": "completed" if is_successful_run_terminal(event) else "blocked",
            "event_id": event.id,
            "event_type": event.type,
            "event_at": event.ts,
            "reason": str(
                payload.get("reason")
                or payload.get("summary")
                or payload.get("message")
                or ""
            ),
            "next_action": str(
                payload.get("next_action")
                or payload.get("recommended_action")
                or payload.get("recommended_route")
                or ""
            ),
            "completed_task_ids": _strings(payload.get("completed_task_ids")),
            "goal_coverage": [
                dict(item)
                for item in payload.get("goal_coverage") or []
                if isinstance(item, Mapping)
            ],
            "workflow_run_id": str(
                payload.get("workflow_run_id")
                or payload.get("run_id")
                or event.correlation_id
                or ""
            ),
            "task_map_generation": str(
                payload.get("task_map_generation")
                or payload.get("workflow_generation")
                or ""
            ),
            "task_map_ref": str(
                payload.get("task_map_snapshot_ref")
                or payload.get("task_map_ref")
                or ""
            ),
            "task_map_digest": str(
                payload.get("task_map_snapshot_digest")
                or payload.get("task_map_digest")
                or ""
            ),
            "goal_claim_set_ref": str(payload.get("goal_claim_set_ref") or ""),
            "goal_claim_set_digest": str(
                payload.get("goal_claim_set_digest") or ""
            ),
            "target_commit": str(
                payload.get("verified_target_commit")
                or payload.get("target_commit")
                or payload.get("candidate_head_commit")
                or ""
            ),
        }
    return {}


def _hydrate_task_map(
    state_dir: Any,
    *,
    package_roadmap: Mapping[str, Any],
    events: list[ZfEvent],
    optional: bool = False,
) -> tuple[dict[str, Any], dict[str, str], list[dict[str, Any]]]:
    binding: dict[str, str] = {}
    for event in reversed(events):
        if event.type not in {"run.goal.completed", "run.goal.blocked"}:
            continue
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        ref = str(
            payload.get("task_map_snapshot_ref")
            or payload.get("task_map_ref")
            or ""
        )
        if ref:
            binding = {
                "ref": ref,
                "sha256": str(
                    payload.get("task_map_snapshot_digest")
                    or payload.get("task_map_digest")
                    or ""
                ),
                "source": "run_terminal",
            }
        break
    current = package_roadmap.get("current_plan_package")
    current = current if isinstance(current, Mapping) else {}
    if not binding.get("ref"):
        for port in current.get("ports") or []:
            if not isinstance(port, Mapping):
                continue
            if str(port.get("logical_name") or "") not in {
                "task_map", "task-map", "task_map_json",
            }:
                continue
            binding = {
                "ref": str(port.get("ref") or ""),
                "sha256": str(port.get("sha256") or ""),
                "source": "plan_artifact_package",
            }
            break
    if not binding.get("ref"):
        for event in reversed(events):
            payload = event.payload if isinstance(event.payload, Mapping) else {}
            ref = str(
                payload.get("task_map_snapshot_ref")
                or payload.get("task_map_ref")
                or ""
            )
            if not ref:
                continue
            binding = {
                "ref": ref,
                "sha256": str(
                    payload.get("task_map_snapshot_digest")
                    or payload.get("task_map_digest")
                    or ""
                ),
                "source": "event_ref",
            }
            break
    if not binding.get("ref"):
        if optional:
            return {}, binding, []
        return {}, binding, [{
            "type": "task_map_ref_missing",
            "reason": "run has no admitted task-map ref",
        }]
    binding["ref"] = _normalize_runtime_sidecar_ref(state_dir, binding["ref"])
    try:
        hydrated = hydrate_sidecar_ref(
            state_dir,
            {
                "ref": binding["ref"],
                "sha256": binding.get("sha256", ""),
                "kind": "task_map",
                "schema_version": "task-map.v1",
                "content_type": "application/json",
                "required": True,
            },
            purpose="goal_dossier_history",
            actor="goal-dossier",
        )
        if not isinstance(hydrated.payload, dict):
            raise ValueError("task-map payload is not an object")
        binding["sha256"] = hydrated.sha256
        return dict(hydrated.payload), binding, []
    except Exception as exc:
        return {}, binding, [{
            "type": "task_map_hydrate_failed",
            "ref": binding["ref"],
            "reason": str(exc),
        }]


def _normalize_runtime_sidecar_ref(state_dir: Any, ref: str) -> str:
    raw = str(ref or "").strip()
    if raw.startswith(".zf/"):
        return raw[len(".zf/"):]
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return raw
    try:
        return path.resolve().relative_to(Path(state_dir).resolve()).as_posix()
    except (OSError, ValueError):
        return raw


def _is_artifact_delivery_run(events: Iterable[ZfEvent]) -> bool:
    for event in events:
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        result = payload.get("artifact_delivery_result")
        if (
            str(payload.get("completion_profile") or "")
            == "artifact_delivery"
            or (
                isinstance(result, Mapping)
                and str(result.get("schema_version") or "")
                == "artifact-delivery-result.v1"
            )
        ):
            return True
    return False


def _hydrate_task_contracts(
    state_dir: Any,
    events: list[ZfEvent],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    contracts: dict[str, dict[str, Any]] = {}
    diagnostics: list[dict[str, Any]] = []
    attempted: set[tuple[str, str]] = set()
    for event in reversed(events):
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        task_id = str(event.task_id or payload.get("task_id") or "")
        if not task_id or task_id in contracts:
            continue
        explicit_ref = str(payload.get("task_contract_snapshot_ref") or "")
        explicit_digest = str(
            payload.get("task_contract_snapshot_digest") or ""
        )
        ref = explicit_ref or str(payload.get("contract_snapshot_ref") or "")
        digest = explicit_digest or str(
            payload.get("contract_snapshot_digest") or ""
        )
        if not explicit_ref and ref and not _is_task_contract_snapshot_ref(ref):
            continue
        if not ref or not digest or (ref, digest) in attempted:
            continue
        attempted.add((ref, digest))
        try:
            snapshot = hydrate_task_contract_snapshot(
                state_dir,
                descriptor_from_payload({
                    **payload,
                    "task_contract_snapshot_ref": ref,
                    "task_contract_snapshot_digest": digest,
                }),
                expected={"task_id": task_id},
            )
        except Exception as exc:
            diagnostics.append({
                "type": "task_contract_hydrate_failed",
                "task_id": task_id,
                "ref": ref,
                "reason": str(exc),
            })
            continue
        contracts[task_id] = {
            "workflow_run_id": str(snapshot.get("workflow_run_id") or ""),
            "task_id": task_id,
            "contract_revision": str(snapshot.get("contract_revision") or ""),
            "task_map_generation": str(snapshot.get("task_map_generation") or ""),
            "base_commit": str(snapshot.get("base_commit") or ""),
            "task_ref": str(snapshot.get("task_ref") or ""),
            "snapshot_ref": ref,
            "snapshot_digest": digest,
            "title": str(snapshot.get("title") or task_id),
            "acceptance_criteria": [
                {
                    "acceptance_id": str(item.get("acceptance_id") or ""),
                    "statement": str(item.get("statement") or item.get("text") or ""),
                    "mandatory": bool(item.get("mandatory", True)),
                    "verification_owner": str(item.get("verification_owner") or ""),
                    "verification_tier": str(item.get("verification_tier") or ""),
                }
                for item in snapshot.get("acceptance_criteria") or []
                if isinstance(item, Mapping)
            ],
            "source_refs": dict(snapshot.get("source_refs") or {}),
        }
    return contracts, diagnostics


def _is_task_contract_snapshot_ref(ref: str) -> bool:
    normalized = str(ref or "").replace("\\", "/").strip()
    return "/artifacts/task-contract-snapshots/" in (
        "/" + normalized.lstrip("/")
    )


def _historical_task_rows(
    *,
    events: list[ZfEvent],
    task_map: Mapping[str, Any],
    contracts: Mapping[str, Mapping[str, Any]],
    current_tasks: list[dict[str, Any]],
    terminal: Mapping[str, Any],
    excluded_task_ids: Iterable[str] = (),
) -> list[dict[str, Any]]:
    planned = task_map.get("tasks") if isinstance(task_map.get("tasks"), list) else []
    planned_by_id = {
        str(item.get("task_id") or item.get("id") or ""): dict(item)
        for item in planned
        if isinstance(item, Mapping)
        and str(item.get("task_id") or item.get("id") or "")
    }
    current_by_id = {
        str(item.get("id") or ""): item
        for item in current_tasks
        if str(item.get("id") or "")
    }
    event_task_ids: list[str] = []
    event_status: dict[str, str] = {}
    for event in events:
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        task_id = str(event.task_id or payload.get("task_id") or "")
        if not task_id:
            continue
        if task_id not in event_task_ids:
            event_task_ids.append(task_id)
        if event.type in _TASK_SUCCESS_EVENTS:
            event_status[task_id] = "done"
        elif event.type in _TASK_FAILURE_EVENTS:
            event_status[task_id] = "blocked"
        elif event.type in {"task.assigned", "task.dispatched"}:
            event_status.setdefault(task_id, "in_progress")
    ordered_ids = list(planned_by_id)
    ordered_ids.extend(task_id for task_id in contracts if task_id not in ordered_ids)
    ordered_ids.extend(task_id for task_id in event_task_ids if task_id not in ordered_ids)
    ordered_ids.extend(
        task_id
        for task_id, current in current_by_id.items()
        if task_id not in ordered_ids and not current.get("missing")
    )
    completed_ids = set(_strings(terminal.get("completed_task_ids")))
    excluded_ids = {
        str(task_id).strip()
        for task_id in excluded_task_ids
        if str(task_id).strip()
    }
    successful_terminal = terminal.get("status") == "completed"
    rows: list[dict[str, Any]] = []
    for task_id in ordered_ids:
        if task_id in excluded_ids:
            continue
        planned_row = planned_by_id.get(task_id, {})
        contract = contracts.get(task_id, {})
        current = current_by_id.get(task_id, {})
        if task_id in completed_ids:
            status = "done"
            status_source = "run_terminal"
        elif task_id in event_status:
            status = event_status[task_id]
            status_source = "event_history"
        elif successful_terminal:
            status = "unknown"
            status_source = "terminal_without_task_fact"
        else:
            status = str(current.get("status") or "unknown")
            status_source = "current_store_fallback"
        owner = str(
            planned_row.get("owner_instance")
            or planned_row.get("owner_role")
            or current.get("assigned_to")
            or ""
        )
        rows.append({
            "id": task_id,
            "title": str(
                planned_row.get("title")
                or contract.get("title")
                or current.get("title")
                or task_id
            ),
            "status": status,
            "status_source": status_source,
            "assigned_to": owner,
            "blocked_by": _strings(
                planned_row.get("blocked_by") or current.get("blocked_by")
            ),
            "contract": {
                "contract_revision": str(contract.get("contract_revision") or ""),
                "goal_claim_ids": _strings(planned_row.get("goal_claim_ids")),
                "owner_role": str(planned_row.get("owner_role") or ""),
            },
            "task_ref": str(contract.get("task_ref") or ""),
            "contract_snapshot_ref": str(contract.get("snapshot_ref") or ""),
            "contract_snapshot_digest": str(contract.get("snapshot_digest") or ""),
        })
    return rows


def _authoritative_task_rows(
    *,
    historical_tasks: list[dict[str, Any]],
    task_map: Mapping[str, Any],
    terminal: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Project terminal currentness without erasing the full run history."""

    if not terminal:
        return historical_tasks
    planned = task_map.get("tasks") if isinstance(task_map.get("tasks"), list) else []
    planned_ids = [
        str(item.get("task_id") or item.get("id") or "")
        for item in planned
        if isinstance(item, Mapping)
        and str(item.get("task_id") or item.get("id") or "")
    ]
    completed_ids = _strings(terminal.get("completed_task_ids"))
    authoritative_ids = list(dict.fromkeys([*planned_ids, *completed_ids]))
    if not authoritative_ids:
        return historical_tasks
    historical_by_id = {
        str(item.get("id") or ""): dict(item)
        for item in historical_tasks
        if str(item.get("id") or "")
    }
    completed = set(completed_ids)
    closed_goal_claim_ids = {
        str(item.get("goal_claim_id") or "").strip()
        for item in terminal.get("goal_coverage") or []
        if isinstance(item, Mapping)
        and str(item.get("status") or item.get("verdict") or "") == "closed"
        and str(item.get("goal_claim_id") or "").strip()
    }
    rows: list[dict[str, Any]] = []
    for task_id in authoritative_ids:
        row = dict(historical_by_id.get(task_id) or {
            "id": task_id,
            "title": task_id,
            "status": "unknown",
            "status_source": "terminal_without_task_fact",
            "assigned_to": "",
            "blocked_by": [],
            "contract": {},
            "task_ref": "",
            "contract_snapshot_ref": "",
            "contract_snapshot_digest": "",
        })
        if task_id in completed:
            row["status"] = "done"
            row["status_source"] = "run_terminal"
        else:
            contract = row.get("contract")
            contract = contract if isinstance(contract, Mapping) else {}
            task_goal_claim_ids = set(_strings(contract.get("goal_claim_ids")))
            if (
                terminal.get("status") == "completed"
                and task_goal_claim_ids
                and task_goal_claim_ids <= closed_goal_claim_ids
            ):
                row["status"] = "done"
                row["status_source"] = "run_goal_coverage"
        rows.append(row)
    return rows


def _current_overlay(
    *,
    historical_tasks: list[dict[str, Any]],
    current_tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    historical = {str(item.get("id") or ""): item for item in historical_tasks}
    current = {str(item.get("id") or ""): item for item in current_tasks}
    drift: list[dict[str, str]] = []
    for task_id in sorted(set(historical) | set(current)):
        historical_status = str(historical.get(task_id, {}).get("status") or "missing")
        current_status = str(current.get(task_id, {}).get("status") or "missing")
        if historical_status == current_status:
            continue
        drift.append({
            "task_id": task_id,
            "historical_status": historical_status,
            "current_status": current_status,
        })
    return {
        "is_current_overlay": True,
        "tasks": current_tasks,
        "drift": drift,
        "drift_count": len(drift),
    }


def _instruction_context(events: list[ZfEvent]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for event in events:
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        for node in _mapping_nodes(payload):
            for key in sorted(_INSTRUCTION_REF_KEYS):
                value = node.get(key)
                for ref in _strings(value):
                    if len(ref) > 1024 or "\n" in ref or "\r" in ref:
                        continue
                    if key == "contract_snapshot_ref" and any(
                        ref in _strings(node.get(typed_key))
                        for typed_key in (
                            "goal_closure_contract_snapshot_ref",
                            "task_contract_snapshot_ref",
                        )
                    ):
                        continue
                    identity = (key, ref)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    base_key = key.removesuffix("_ref").removesuffix("_path")
                    rows.append({
                        "kind": base_key,
                        "ref": ref,
                        "digest": str(node.get(base_key + "_digest") or ""),
                        "event_id": event.id,
                        "event_type": event.type,
                        "task_id": str(
                            event.task_id or payload.get("task_id") or ""
                        ),
                    })
    return rows


def _claim_matrix(
    *,
    state_dir: Any,
    task_map: Mapping[str, Any],
    tasks: list[dict[str, Any]],
    events: list[ZfEvent],
    project_id: str,
    goal_id: str,
    task_map_ref: str,
    authority: Mapping[str, Any],
) -> dict[str, Any]:
    task_lookup = {str(item.get("id") or ""): item for item in tasks}
    coverage_task_map = dict(task_map)
    for field in (
        "workflow_run_id",
        "task_map_generation",
        "task_map_ref",
        "task_map_digest",
        "goal_claim_set_ref",
        "goal_claim_set_digest",
        "target_commit",
    ):
        value = authority.get(field)
        if str(value or "").strip():
            coverage_task_map[field] = value
    if goal_id:
        # A run-scoped Dossier is keyed by the pinned Goal identity. A task-map
        # feature alias must not hide an admitted closure for that Goal.
        coverage_task_map["goal_id"] = goal_id
    try:
        pinned_claim_set = hydrate_pinned_goal_claim_set(
            state_dir=state_dir,
            events=events,
            workflow_run_id=str(
                coverage_task_map.get("workflow_run_id")
                or coverage_task_map.get("run_id")
                or ""
            ),
            goal_id=goal_id,
            task_map_generation=str(
                coverage_task_map.get("task_map_generation") or ""
            ),
        )
        graph = build_goal_coverage_graph(
            task_map=coverage_task_map,
            tasks=task_lookup,
            events=list(enumerate(events, start=1)),
            project_id=project_id,
            feature_id=goal_id,
            task_map_ref=task_map_ref,
            goal_claim_set=pinned_claim_set,
        )
    except Exception as exc:
        return {
            "schema_version": "claim-task-evidence-matrix.v1",
            "status": "incomplete",
            "summary": {},
            "rows": [],
            "diagnostics": [{
                "type": "goal_coverage_graph_failed",
                "reason": str(exc),
            }],
        }
    result_evidence = {
        str(node.get("result_ref") or ""): _strings(node.get("evidence_refs"))
        for node in graph.get("nodes") or []
        if isinstance(node, Mapping)
        and node.get("kind") == "verification_result"
        and str(node.get("result_ref") or "")
    }
    rows: list[dict[str, Any]] = []
    for node in graph.get("nodes") or []:
        if not isinstance(node, Mapping) or node.get("kind") != "goal_claim":
            continue
        task_ids = _strings(node.get("task_ids"))
        result_refs = _strings(node.get("supporting_result_refs"))
        rows.append({
            "goal_claim_id": str(node.get("goal_claim_id") or ""),
            "claim": str(node.get("title") or ""),
            "mandatory": bool(node.get("mandatory", True)),
            "task_ids": task_ids,
            "implementation": [
                _task_implementation(task_lookup[task_id], events)
                for task_id in task_ids
                if task_id in task_lookup
            ],
            "result_refs": result_refs,
            "evidence_refs": list(dict.fromkeys(
                evidence_ref
                for result_ref in result_refs
                for evidence_ref in result_evidence.get(result_ref, [])
            )),
            "verdict": str(node.get("closure") or "unknown"),
            "plan_coverage": str(node.get("plan_coverage") or "unknown"),
            "task_verification": str(node.get("task_verification") or "unverified"),
            "gap_refs": _strings(node.get("gap_refs")),
        })
    graph_diagnostics = [
        dict(item)
        for item in graph.get("diagnostics") or []
        if isinstance(item, Mapping)
    ]
    closed_with_support = {
        str(row.get("goal_claim_id") or "")
        for row in rows
        if str(row.get("verdict") or "") == "closed"
        and (row.get("result_refs") or row.get("evidence_refs"))
    }
    current_result_tasks = {
        str(item.get("task_id") or "")
        for item in graph.get("nodes") or []
        if isinstance(item, Mapping)
        and item.get("kind") == "verification_result"
        and bool(item.get("current"))
        and str(item.get("task_id") or "")
    }
    has_current_closure = any(
        isinstance(item, Mapping) and item.get("kind") == "goal_closure"
        for item in graph.get("nodes") or []
    )
    superseded_diagnostics = [
        item
        for item in graph_diagnostics
        if (
            str(item.get("code") or "") == "stale_goal_closure_result"
            and has_current_closure
        )
        or (
            str(item.get("code") or "") == "stale_verification_result"
            and str(item.get("task_id") or "") in current_result_tasks
        )
    ]
    blocking_diagnostics = [
        item
        for item in graph_diagnostics
        if item not in superseded_diagnostics
        and not (
            str(item.get("code") or "") == "mandatory_claim_uncovered"
            and str(item.get("goal_claim_id") or "") in closed_with_support
        )
    ]
    return {
        "schema_version": "claim-task-evidence-matrix.v1",
        "status": "incomplete" if blocking_diagnostics else "ready",
        "summary": dict(graph.get("summary") or {}),
        "rows": rows,
        "diagnostics": blocking_diagnostics,
        "advisories": [
            item for item in graph_diagnostics if item not in blocking_diagnostics
        ],
    }


def _task_implementation(
    task: Mapping[str, Any],
    events: list[ZfEvent],
) -> dict[str, Any]:
    task_id = str(task.get("id") or "")
    refs: list[str] = []
    commits: list[str] = []
    event_ids: list[str] = []
    for event in events:
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        if str(event.task_id or payload.get("task_id") or "") != task_id:
            continue
        if event.type in _TASK_SUCCESS_EVENTS and event.id:
            event_ids.append(event.id)
        for key, value in _walk(payload):
            if key.endswith("_ref"):
                refs.extend(item for item in _strings(value) if item not in refs)
            if key in {
                "base_commit", "candidate_head_commit", "commit",
                "commit_sha", "head_commit", "target_commit",
            }:
                commits.extend(item for item in _strings(value) if item not in commits)
    return {
        "task_id": task_id,
        "task_ref": str(task.get("task_ref") or ""),
        "contract_snapshot_ref": str(task.get("contract_snapshot_ref") or ""),
        "event_ids": event_ids,
        "artifact_refs": refs,
        "commits": commits,
    }


def _walk(
    value: Any,
    *,
    depth: int = 0,
) -> Iterable[tuple[str, Any]]:
    if depth > 6:
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            text = str(key)
            yield text, child
            yield from _walk(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child, depth=depth + 1)


def _mapping_nodes(
    value: Any,
    *,
    depth: int = 0,
) -> Iterable[Mapping[str, Any]]:
    if depth > 6:
        return
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _mapping_nodes(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _mapping_nodes(child, depth=depth + 1)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


__all__ = ["build_goal_dossier_history"]
