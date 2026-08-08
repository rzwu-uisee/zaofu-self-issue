"""Level-triggered shadow planning for the v4 Task Pipeline."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from zf.core.state.atomic_io import atomic_write_text


TASK_PIPELINE_SHADOW_SCHEMA = "task-pipeline-shadow.v1"
_ACTIVE_OPERATION_STATUSES = frozenset({"reserved", "running", "suspended"})
_ACTIVE_ATTEMPT_STATUSES = frozenset({"prepared", "delivering", "sent"})


class TaskPipelineReconciler:
    """Derive a dispatch plan without mutating any canonical store."""

    def reconcile(
        self,
        *,
        policy: Mapping[str, Any],
        tasks: Iterable[Any],
        operations: Iterable[Mapping[str, Any]],
        attempts: Iterable[Mapping[str, Any]],
        terminal_task_ids: Iterable[str] = (),
        impl_rework_requests: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        normalized_tasks = sorted(
            (_task_row(task) for task in tasks),
            key=_task_order,
        )
        normalized_operations = sorted(
            (dict(operation) for operation in operations),
            key=lambda row: (
                str(row.get("task_id") or ""),
                _operation_stage(row),
                int(row.get("operation_generation") or 1),
                str(row.get("operation_id") or ""),
            ),
        )
        normalized_attempts = sorted(
            (dict(attempt) for attempt in attempts),
            key=lambda row: str(row.get("attempt_id") or ""),
        )
        terminal_ids = {
            str(task_id).strip()
            for task_id in terminal_task_ids
            if str(task_id).strip()
        }
        terminal_ids.update(
            row["id"]
            for row in normalized_tasks
            if row["status"] == "done"
        )
        normalized_rework_requests = {
            str(task_id): dict(request)
            for task_id, request in sorted(
                (impl_rework_requests or {}).items(),
                key=lambda item: str(item[0]),
            )
            if str(task_id).strip() and isinstance(request, Mapping)
        }
        operation_by_task: dict[str, list[dict[str, Any]]] = {}
        for operation in normalized_operations:
            task_id = str(operation.get("task_id") or "")
            if task_id:
                operation_by_task.setdefault(task_id, []).append(operation)

        task_views = [
            _task_view(
                task,
                operations=operation_by_task.get(task["id"], []),
                terminal_task_ids=terminal_ids,
                policy=policy,
                impl_rework_request=normalized_rework_requests.get(task["id"]),
            )
            for task in normalized_tasks
            if task["status"] not in {"done", "cancelled"}
        ]
        active_attempts = [
            row
            for row in normalized_attempts
            if str(row.get("status") or "") in _ACTIVE_ATTEMPT_STATUSES
        ]
        pools = {
            name: dict(value)
            for name, value in dict(policy.get("pools") or {}).items()
            if isinstance(value, Mapping)
        }
        active_operations = [
            operation
            for operation in normalized_operations
            if str(operation.get("status") or "") in _ACTIVE_OPERATION_STATUSES
            and _operation_stage(operation) in pools
        ]
        occupancy = {
            name: sum(
                1 for operation in active_operations
                if _operation_stage(operation) == name
                and (
                    not _pool_role_instances(pool)
                    or not str(operation.get("role_instance") or "").strip()
                    or str(operation.get("role_instance") or "")
                    in _pool_role_instances(pool)
                )
            )
            for name, pool in pools.items()
        }
        managed_task_ids = {row["id"] for row in normalized_tasks}
        active_pipeline_ids = sorted({
            str(operation.get("task_id") or "")
            for operation in active_operations
            if str(operation.get("task_id") or "") in managed_task_ids
        } | {
            view["task_id"]
            for view in task_views
            if view["stage"] not in {
                "impl_ready",
                "dependency_blocked",
                "blocked",
                "admission_blocked",
                "replan_requested",
            }
        })
        backpressure_policy = dict(policy.get("backpressure") or {})
        unverified_count = sum(
            1
            for view in task_views
            if view["stage"] in {
                "impl_active",
                "verify_ready",
                "verify_active",
                "acceptance_review_ready",
                "acceptance_review_active",
            }
        )
        integration_queue_count = sum(
            1 for view in task_views if view["stage"] == "integration_ready"
        )
        unverified_blocked = unverified_count >= int(
            backpressure_policy.get("max_unverified_tasks") or 0
        )
        integration_blocked = integration_queue_count >= int(
            backpressure_policy.get("max_integration_queue") or 0
        )
        max_active = int(policy.get("max_active_task_pipelines") or 0)
        global_available = max(0, max_active - len(active_pipeline_ids))

        dispatchable: dict[str, list[dict[str, Any]]] = {
            name: [] for name in pools
        }
        for stage_name, ready_stages in (
            ("verify", {"verify_ready"}),
            ("acceptance_review", {"acceptance_review_ready"}),
            ("impl", {"impl_ready", "impl_rework_ready"}),
        ):
            if stage_name not in pools:
                continue
            available = max(
                0,
                int(pools[stage_name].get("capacity") or 0)
                - occupancy.get(stage_name, 0),
            )
            candidates = [
                view for view in task_views if view["stage"] in ready_stages
            ]
            if stage_name == "impl":
                candidates = _bounded_rework_order(candidates)
            fresh_impl_dispatched = 0
            used_roles = {
                str(attempt.get("instance_id") or "")
                for attempt in active_attempts
                if str(attempt.get("instance_id") or "")
            }
            for view in candidates:
                if len(dispatchable[stage_name]) >= available:
                    break
                fresh_impl = (
                    stage_name == "impl" and view["stage"] == "impl_ready"
                )
                if fresh_impl and (
                    fresh_impl_dispatched >= global_available
                    or unverified_blocked
                    or integration_blocked
                ):
                    continue
                role = _select_role(
                    pools[stage_name],
                    required_capabilities=view["required_capabilities"],
                    used_roles=used_roles,
                )
                if not role:
                    view["blockers"].append("no_compatible_idle_role")
                    continue
                assignment: dict[str, Any] = {
                    "task_id": view["task_id"],
                    "stage": stage_name,
                    "role_instance": role,
                    "operation_generation": str(
                        view["next_operation_generation"]
                    ),
                }
                if (
                    stage_name == "impl"
                    and view["stage"] == "impl_rework_ready"
                    and view.get("impl_rework_request")
                ):
                    assignment["impl_rework_request"] = dict(
                        view["impl_rework_request"]
                    )
                dispatchable[stage_name].append(assignment)
                if fresh_impl:
                    fresh_impl_dispatched += 1
                used_roles.add(role)

        integration_ready = [
            view["task_id"]
            for view in task_views
            if view["stage"] == "integration_ready"
        ]
        input_payload = {
            "policy_digest": str(policy.get("profile_digest") or ""),
            "tasks": normalized_tasks,
            "operations": normalized_operations,
            "attempts": normalized_attempts,
            "terminal_task_ids": sorted(terminal_ids),
            "impl_rework_requests": normalized_rework_requests,
        }
        projection: dict[str, Any] = {
            "schema_version": TASK_PIPELINE_SHADOW_SCHEMA,
            "profile_id": str(policy.get("profile_id") or ""),
            "profile_digest": str(policy.get("profile_digest") or ""),
            "mode": str(policy.get("mode") or "shadow"),
            "input_digest": _digest(input_payload),
            "tasks": task_views,
            "queues": {
                "impl_ready": [
                    view["task_id"] for view in task_views
                    if view["stage"] in {"impl_ready", "impl_rework_ready"}
                ],
                "verify_ready": [
                    view["task_id"] for view in task_views
                    if view["stage"] == "verify_ready"
                ],
                "acceptance_review_ready": [
                    view["task_id"] for view in task_views
                    if view["stage"] == "acceptance_review_ready"
                ],
                "integration_ready": integration_ready,
            },
            "occupancy": {
                "active_task_pipelines": len(active_pipeline_ids),
                "active_task_ids": active_pipeline_ids,
                "pools": occupancy,
                "active_attempts": len(active_attempts),
            },
            "capacity": {
                "max_active_task_pipelines": max_active,
                "available_task_pipelines": global_available,
                "pools": {
                    name: {
                        "capacity": int(pool.get("capacity") or 0),
                        "available": max(
                            0,
                            int(pool.get("capacity") or 0)
                            - occupancy.get(name, 0),
                        ),
                    }
                    for name, pool in pools.items()
                },
            },
            "backpressure": {
                "unverified_count": unverified_count,
                "integration_queue_count": integration_queue_count,
                "unverified_limit_reached": unverified_blocked,
                "integration_limit_reached": integration_blocked,
            },
            "dispatchable": dispatchable,
            "fairness": {
                "ordering": "priority,created_at,task_id",
                "ordered_task_ids": [view["task_id"] for view in task_views],
            },
        }
        projection["projection_digest"] = _digest(projection)
        return projection


def refresh_task_pipeline_projection(
    orchestrator: Any,
    *,
    policy: Mapping[str, Any] | None = None,
) -> Path | None:
    """Refresh the v4 projection when an immutable profile is selected."""

    selected_policy = dict(policy) if policy is not None else task_pipeline_policy(
        orchestrator.config
    )
    if selected_policy is None:
        return None
    from zf.runtime.task_attempt_runtime import task_attempt_store
    from zf.runtime.workflow_operation import reduce_workflow_operations

    tasks = orchestrator.task_store.list_all()
    terminal_ids: set[str] = set()
    for task in tasks:
        for dependency_id in task.blocked_by:
            dependency = orchestrator.task_store.get(dependency_id)
            if dependency is not None and dependency.status == "done":
                terminal_ids.add(dependency_id)
    events = orchestrator.event_log.read_all()
    projection = TaskPipelineReconciler().reconcile(
        policy=selected_policy,
        tasks=tasks,
        operations=reduce_workflow_operations(events).values(),
        attempts=task_attempt_store(orchestrator).current_rows(),
        terminal_task_ids=terminal_ids,
    )
    path = Path(orchestrator.state_dir) / "projections" / "task-pipeline-shadow.json"
    atomic_write_text(
        path,
        json.dumps(projection, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return path


def task_pipeline_policy(
    config: Any,
    *,
    flow_kind: str = "",
) -> dict[str, Any] | None:
    """Return the immutable Task Pipeline policy for one Flow kind.

    A composite controller config may carry Issue, PRD, and Refactor profiles
    at the same time.  In that shape the current workflow kind is required;
    silently treating the set as ambiguous would route the run back through
    the legacy lane scheduler.
    """

    workflow = getattr(config, "workflow", None)
    if workflow is None:
        return None
    metadata = getattr(workflow, "flow_metadata", {}) or {}
    policy = metadata.get("task_pipeline") if isinstance(metadata, dict) else None
    if isinstance(policy, dict):
        return dict(policy)
    by_kind = getattr(workflow, "flow_metadata_by_kind", {}) or {}
    selected_kind = str(flow_kind or "").strip().lower()
    if selected_kind:
        selected = by_kind.get(selected_kind)
        if isinstance(selected, dict):
            selected_policy = selected.get("task_pipeline")
            if isinstance(selected_policy, dict):
                return dict(selected_policy)
        return None
    candidates = [
        value.get("task_pipeline")
        for _, value in sorted(by_kind.items())
        if isinstance(value, dict) and isinstance(value.get("task_pipeline"), dict)
    ]
    return dict(candidates[0]) if len(candidates) == 1 else None


def task_pipeline_blocking(config: Any, *, flow_kind: str = "") -> bool:
    policy = task_pipeline_policy(config, flow_kind=flow_kind)
    return bool(policy and str(policy.get("mode") or "") == "blocking")


def task_pipeline_policies(config: Any) -> list[dict[str, Any]]:
    """Return every configured Task Pipeline policy without selecting one."""

    workflow = getattr(config, "workflow", None)
    if workflow is None:
        return []
    policies: list[dict[str, Any]] = []
    metadata = getattr(workflow, "flow_metadata", {}) or {}
    direct = metadata.get("task_pipeline") if isinstance(metadata, dict) else None
    if isinstance(direct, dict):
        policies.append(dict(direct))
    by_kind = getattr(workflow, "flow_metadata_by_kind", {}) or {}
    for _, value in sorted(by_kind.items()):
        if not isinstance(value, dict):
            continue
        policy = value.get("task_pipeline")
        if isinstance(policy, dict):
            policies.append(dict(policy))
    return policies


def task_pipeline_any_blocking(config: Any) -> bool:
    return any(
        str(policy.get("mode") or "") == "blocking"
        for policy in task_pipeline_policies(config)
    )


def task_pipeline_policy_for_contexts(
    config: Any,
    contexts: Mapping[str, Mapping[str, Any]],
    *,
    task_ids: Iterable[str] | None = None,
) -> dict[str, Any] | None:
    """Resolve one policy from generation truth without guessing a Flow kind."""

    selected_ids = None if task_ids is None else {
        str(task_id).strip() for task_id in task_ids if str(task_id).strip()
    }
    selected = [
        context
        for task_id, context in contexts.items()
        if selected_ids is None or str(task_id) in selected_ids
    ]
    flow_kinds = {
        str(context.get("flow_kind") or "").strip().lower()
        for context in selected
        if str(context.get("flow_kind") or "").strip()
    }
    if len(flow_kinds) == 1:
        return task_pipeline_policy(config, flow_kind=next(iter(flow_kinds)))
    if len(flow_kinds) > 1:
        return None
    return task_pipeline_policy(config)


def task_pipeline_policy_partitions(
    config: Any,
    contexts: Mapping[str, Mapping[str, Any]],
    *,
    task_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Partition admitted Tasks by their immutable Flow policy identity."""

    selected_ids = None if task_ids is None else {
        str(task_id).strip() for task_id in task_ids if str(task_id).strip()
    }
    groups: dict[tuple[str, str, str], list[str]] = {}
    policies: dict[tuple[str, str, str], dict[str, Any]] = {}
    for task_id, context in sorted(contexts.items()):
        normalized_task_id = str(task_id).strip()
        if not normalized_task_id or (
            selected_ids is not None and normalized_task_id not in selected_ids
        ):
            continue
        flow_kind = str(context.get("flow_kind") or "").strip().lower()
        policy = task_pipeline_policy(config, flow_kind=flow_kind)
        if policy is None:
            continue
        admitted_profile_id = str(context.get("profile_id") or "").strip()
        admitted_profile_digest = str(
            context.get("profile_digest") or ""
        ).strip()
        profile_id = str(policy.get("profile_id") or "").strip()
        profile_digest = str(policy.get("profile_digest") or "").strip()
        if admitted_profile_id and admitted_profile_id != profile_id:
            raise ValueError(
                f"Task {normalized_task_id} admitted profile_id "
                f"{admitted_profile_id!r} does not match configured {profile_id!r}"
            )
        if admitted_profile_digest and admitted_profile_digest != profile_digest:
            raise ValueError(
                f"Task {normalized_task_id} admitted profile_digest does not "
                "match the configured Task Pipeline policy"
            )
        key = (flow_kind, profile_id, profile_digest)
        groups.setdefault(key, []).append(normalized_task_id)
        policies[key] = policy
    return [
        {
            "flow_kind": flow_kind,
            "profile_id": profile_id,
            "profile_digest": profile_digest,
            "task_ids": tuple(sorted(groups[key])),
            "policy": dict(policies[key]),
        }
        for key in sorted(groups)
        for flow_kind, profile_id, profile_digest in [key]
    ]


def _task_row(task: Any) -> dict[str, Any]:
    if is_dataclass(task):
        raw = asdict(task)
    elif isinstance(task, Mapping):
        raw = dict(task)
    else:
        raw = dict(vars(task))
    contract = raw.get("contract")
    if is_dataclass(contract):
        contract = asdict(contract)
    if not isinstance(contract, Mapping):
        contract = {}
    evidence_contract = contract.get("evidence_contract")
    if not isinstance(evidence_contract, Mapping):
        evidence_contract = {}
    required_capabilities = evidence_contract.get("required_capabilities")
    if not isinstance(required_capabilities, list):
        required_capabilities = []
    return {
        "id": str(raw.get("id") or raw.get("task_id") or ""),
        "status": str(raw.get("status") or "backlog"),
        "priority": int(raw.get("priority") or 3),
        "created_at": str(raw.get("created_at") or ""),
        "blocked_by": sorted({
            str(item) for item in raw.get("blocked_by", []) if str(item).strip()
        }),
        "required_capabilities": sorted({
            str(item) for item in required_capabilities if str(item).strip()
        }),
        "integration_admission_profile": str(
            contract.get("integration_admission_profile") or ""
        ),
        "risk_class": str(contract.get("risk_class") or ""),
    }


def _task_order(row: Mapping[str, Any]) -> tuple[int, str, str]:
    return (
        int(row.get("priority") or 3),
        str(row.get("created_at") or ""),
        str(row.get("id") or ""),
    )


def _task_view(
    task: Mapping[str, Any],
    *,
    operations: list[dict[str, Any]],
    terminal_task_ids: set[str],
    policy: Mapping[str, Any],
    impl_rework_request: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    blockers = [
        dependency
        for dependency in task.get("blocked_by", [])
        if dependency not in terminal_task_ids
    ]
    stage = "impl_ready"
    next_generation = 1
    rework_count = 0
    if task.get("status") == "blocked":
        stage = "blocked"
    elif blockers:
        stage = "dependency_blocked"
    else:
        current_by_stage: dict[str, dict[str, Any]] = {}
        for operation in operations:
            operation_stage = _operation_stage(operation)
            if operation_stage not in {"impl", "verify", "acceptance_review", "integration"}:
                continue
            previous = current_by_stage.get(operation_stage)
            if previous is None or _operation_order(operation) > _operation_order(previous):
                current_by_stage[operation_stage] = operation
        stage, next_generation, rework_count = _derive_stage(
            current_by_stage,
            task=task,
            policy=policy,
            impl_rework_request=impl_rework_request,
        )
        failed = [
            operation_stage
            for operation_stage, operation in current_by_stage.items()
            if str(operation.get("status") or "") in {"failed", "blocked"}
        ]
        if failed:
            blockers.extend(f"{item}_operation_failed" for item in sorted(failed))
        if stage == "admission_blocked":
            blockers.append("integration_admission_not_admitted")
        elif stage == "replan_requested":
            blockers.append("integration_acceptance_replan_required")
    return {
        "task_id": str(task.get("id") or ""),
        "stage": stage,
        "status": str(task.get("status") or ""),
        "blocked_by": list(task.get("blocked_by") or []),
        "blockers": blockers,
        "required_capabilities": list(task.get("required_capabilities") or []),
        "next_operation_generation": next_generation,
        "rework_count": rework_count,
        "impl_rework_request": (
            dict(impl_rework_request)
            if stage == "impl_rework_ready" and impl_rework_request
            else {}
        ),
    }


def _derive_stage(
    current: Mapping[str, Mapping[str, Any]],
    *,
    task: Mapping[str, Any],
    policy: Mapping[str, Any],
    impl_rework_request: Mapping[str, Any] | None = None,
) -> tuple[str, int, int]:
    max_rework = int(policy.get("max_rework_attempts") or 0)
    impl = current.get("impl")
    if impl is None:
        return "impl_ready", 1, 0
    impl_generation = int(impl.get("operation_generation") or 1)
    impl_status = str(impl.get("status") or "")
    if impl_status == "requested":
        return "impl_ready", impl_generation, max(0, impl_generation - 1)
    if impl_status in _ACTIVE_OPERATION_STATUSES:
        return "impl_active", impl_generation, max(0, impl_generation - 1)
    if impl_status in {"failed", "blocked"}:
        return "blocked", impl_generation, max(0, impl_generation - 1)
    if impl_status != "settled" or not _semantic_passed(impl):
        return "blocked", impl_generation, max(0, impl_generation - 1)
    requested_generation = int(
        (impl_rework_request or {}).get("operation_generation") or 0
    )
    if requested_generation == impl_generation:
        if impl_generation <= max_rework:
            return (
                "impl_rework_ready",
                impl_generation + 1,
                impl_generation,
            )
        return "blocked", impl_generation, impl_generation

    verify = current.get("verify")
    if (
        verify is None
        or int(verify.get("operation_generation") or 1) < impl_generation
    ):
        return "verify_ready", impl_generation, max(0, impl_generation - 1)
    verify_generation = int(verify.get("operation_generation") or 1)
    verify_status = str(verify.get("status") or "")
    if verify_status == "requested":
        return "verify_ready", verify_generation, max(0, verify_generation - 1)
    if verify_status in _ACTIVE_OPERATION_STATUSES:
        return "verify_active", verify_generation, max(0, verify_generation - 1)
    if verify_status in {"failed", "blocked"}:
        return "blocked", verify_generation, max(0, verify_generation - 1)
    if verify_status != "settled":
        return "verify_ready", verify_generation, max(0, verify_generation - 1)
    if not _semantic_passed(verify):
        if verify_generation <= max_rework:
            return (
                "impl_rework_ready",
                verify_generation + 1,
                verify_generation,
            )
        return "blocked", verify_generation, verify_generation

    admission = dict(policy.get("integration_admission") or {})
    requested_profile = str(task.get("integration_admission_profile") or "")
    effective_profile = requested_profile or str(admission.get("default") or "verify_admitted")
    if effective_profile not in {"verify_admitted", "risk_review"}:
        return "admission_blocked", verify_generation, max(0, verify_generation - 1)
    if effective_profile == "risk_review":
        risk_policy = dict(admission.get("risk_review") or {})
        if (
            not bool(risk_policy.get("enabled"))
            or str(task.get("risk_class") or "")
            not in set(risk_policy.get("for_risks") or [])
            or "acceptance_review" not in dict(policy.get("pools") or {})
        ):
            return (
                "admission_blocked",
                verify_generation,
                max(0, verify_generation - 1),
            )
        review = current.get("acceptance_review")
        if (
            review is None
            or int(review.get("operation_generation") or 1) < verify_generation
        ):
            return (
                "acceptance_review_ready",
                verify_generation,
                max(0, verify_generation - 1),
            )
        review_status = str(review.get("status") or "")
        if review_status == "requested":
            return (
                "acceptance_review_ready",
                verify_generation,
                max(0, verify_generation - 1),
            )
        if review_status in _ACTIVE_OPERATION_STATUSES:
            return (
                "acceptance_review_active",
                verify_generation,
                max(0, verify_generation - 1),
            )
        if review_status in {"failed", "blocked"}:
            return "blocked", verify_generation, max(0, verify_generation - 1)
        if review_status != "settled":
            return (
                "acceptance_review_ready",
                verify_generation,
                max(0, verify_generation - 1),
            )
        review_verdict = str(
            review.get("semantic_verdict") or ""
        ).strip().lower()
        if review_verdict == "revise":
            if verify_generation <= max_rework:
                return (
                    "impl_rework_ready",
                    verify_generation + 1,
                    verify_generation,
                )
            return "blocked", verify_generation, verify_generation
        if review_verdict == "replan":
            return (
                "replan_requested",
                verify_generation,
                max(0, verify_generation - 1),
            )
        if review_verdict == "block":
            return "blocked", verify_generation, max(0, verify_generation - 1)
        if review_verdict != "admit":
            return "blocked", verify_generation, max(0, verify_generation - 1)
    integration = current.get("integration")
    if integration is not None:
        integration_generation = int(
            integration.get("operation_generation") or verify_generation
        )
        if integration_generation >= verify_generation:
            status = str(integration.get("status") or "")
            if status in _ACTIVE_OPERATION_STATUSES:
                return (
                    "integration_active",
                    integration_generation,
                    max(0, verify_generation - 1),
                )
            if status == "settled" and _semantic_passed(integration):
                return (
                    "integrated",
                    integration_generation,
                    max(0, verify_generation - 1),
                )
            if status in {"failed", "blocked", "settled"}:
                return (
                    "blocked",
                    integration_generation,
                    max(0, verify_generation - 1),
                )
    return "integration_ready", verify_generation, max(0, verify_generation - 1)


def _semantic_passed(operation: Mapping[str, Any]) -> bool:
    verdict = str(operation.get("semantic_verdict") or "").strip().lower()
    return verdict in {"", "passed", "pass", "approved", "approve", "admit"}


def _bounded_rework_order(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Alternate rework and fresh FIFO rows so neither class starves."""

    rework = [row for row in candidates if int(row.get("rework_count") or 0) > 0]
    fresh = [row for row in candidates if int(row.get("rework_count") or 0) == 0]
    if not rework or not fresh:
        return candidates
    ordered: list[dict[str, Any]] = []
    prefer_rework = candidates[0] in rework
    while rework or fresh:
        primary = rework if prefer_rework else fresh
        secondary = fresh if prefer_rework else rework
        if primary:
            ordered.append(primary.pop(0))
        elif secondary:
            ordered.append(secondary.pop(0))
        prefer_rework = not prefer_rework
    return ordered


def _operation_stage(operation: Mapping[str, Any]) -> str:
    explicit = str(operation.get("task_pipeline_stage") or "").strip()
    if explicit:
        return explicit
    parent = str(operation.get("parent_stage_id") or "").strip()
    for stage in ("acceptance_review", "integration", "verify", "impl"):
        if parent == stage or parent.endswith(f":{stage}") or parent.endswith(f"-{stage}"):
            return stage
    return ""


def _operation_order(operation: Mapping[str, Any]) -> tuple[int, str]:
    return (
        int(operation.get("operation_generation") or 1),
        str(operation.get("operation_id") or ""),
    )


def _select_role(
    pool: Mapping[str, Any],
    *,
    required_capabilities: list[str],
    used_roles: set[str],
) -> str:
    roles = sorted({str(item) for item in pool.get("role_instances", []) if str(item)})
    profiles = {
        str(item.get("role") or ""): set(
            str(value) for value in item.get("capabilities", []) if str(value)
        )
        for item in pool.get("worker_profiles", [])
        if isinstance(item, Mapping)
    }
    pool_capabilities = set(
        str(item) for item in pool.get("capabilities", []) if str(item)
    )
    required = set(required_capabilities)
    for role in roles:
        if role in used_roles:
            continue
        capabilities = profiles.get(role, pool_capabilities)
        if not required or required <= capabilities:
            return role
    return ""


def _pool_role_instances(pool: Mapping[str, Any]) -> set[str]:
    return {
        str(item).strip()
        for item in pool.get("role_instances", [])
        if str(item).strip()
    }


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "TASK_PIPELINE_SHADOW_SCHEMA",
    "TaskPipelineReconciler",
    "refresh_task_pipeline_projection",
    "task_pipeline_blocking",
    "task_pipeline_policy",
    "task_pipeline_policy_partitions",
]
