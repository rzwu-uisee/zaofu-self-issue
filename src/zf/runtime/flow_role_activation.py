"""Durable, Flow-scoped role activation for multi-Flow runtimes."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.call_result_envelope import (
    canonical_json_bytes,
    write_immutable_json_sidecar,
)
from zf.runtime.flow_roles import (
    FlowRoleBindingError,
    initial_role_configs,
    is_multi_flow_config,
    role_configs_for_flow,
)
from zf.runtime.event_window import read_runtime_events
from zf.runtime.run_admission import (
    fold_terminal_run_scope as _terminal_run_scope,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.transport import (
    transport_error_diagnostics,
    transport_readiness_error,
)
from zf.runtime.workflow_operation import (
    TERMINAL_OPERATION_STATUSES,
    reduce_workflow_operations,
)


ACTIVATION_SCHEMA_VERSION = "flow-role-activation.v1"
_IDENTITY_FIELDS = (
    "workflow_operation_id",
    "workflow_run_id",
    "flow_kind",
    "effective_config_digest",
    "run_contract_digest",
)
_ACTIVATION_SCOPE_FIELDS = (
    "workflow_operation_id",
    "workflow_run_id",
    "flow_kind",
)


@dataclass(frozen=True)
class FlowRoleActivationResult:
    status: str
    activation_id: str = ""
    manifest_ref: dict[str, Any] | None = None
    role_instance_ids: tuple[str, ...] = ()
    recovered_instance_ids: tuple[str, ...] = ()
    deferred_instance_ids: tuple[str, ...] = ()
    reason: str = ""


def flow_role_activation_required(config: Any, payload: Mapping[str, Any]) -> bool:
    return bool(
        is_multi_flow_config(config)
        and str(payload.get("flow_kind") or "").strip()
    )


def active_flow_role_instance_ids(config: Any, events: list[ZfEvent]) -> set[str]:
    """Return resident and durably activated role instances."""

    active = {
        str(getattr(role, "instance_id", "") or "")
        for role in initial_role_configs(config)
    }
    aliases, terminal_runs = _terminal_run_scope(events)
    for event in _latest_activation_state_events(events).values():
        payload = event.payload if isinstance(event.payload, dict) else {}
        if _activation_run_is_terminal(payload, aliases, terminal_runs):
            continue
        if event.type in {
            "flow.roles.activation.applied",
            "flow.roles.activation.recovered",
        }:
            active.update(_strings(payload.get("role_instance_ids")))
    active.discard("")
    return active


def role_is_runtime_active(
    config: Any,
    events: list[ZfEvent],
    instance_id: str,
) -> bool:
    if not is_multi_flow_config(config):
        return True
    return str(instance_id or "") in active_flow_role_instance_ids(config, events)


def flow_role_activation_projection(
    config: Any,
    events: list[ZfEvent],
    *,
    active_instance_ids: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Project declared, required, activated, and failed role states."""

    active_ids = set(active_instance_ids or ())
    multi_flow = is_multi_flow_config(config)
    resident_ids = {
        str(getattr(role, "instance_id", "") or "")
        for role in initial_role_configs(config)
    }
    aliases, terminal_runs = _terminal_run_scope(events)
    projection: dict[str, dict[str, Any]] = {}
    for role in getattr(config, "roles", ()) or ():
        instance_id = str(getattr(role, "instance_id", "") or "")
        if not instance_id:
            continue
        required = instance_id in resident_ids if multi_flow else True
        projection[instance_id] = {
            "declared": True,
            "required": required,
            "active": instance_id in active_ids,
            "failed": False,
            "activation_state": "required" if required else "declared",
            "activation_reason": (
                "resident_control_plane"
                if multi_flow and required
                else "static_flow_start"
                if required
                else ""
            ),
            "activation_id": "",
            "workflow_operation_id": "",
            "workflow_run_id": "",
            "flow_kind": str(getattr(role, "flow_kind", "") or ""),
            "failure_reason": "",
        }

    activation_events = {
        "flow.roles.activation.requested",
        "flow.roles.activation.applied",
        "flow.roles.activation.failed",
        "flow.roles.activation.recovered",
    }
    for event in events:
        if event.type not in activation_events:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if _activation_run_is_terminal(payload, aliases, terminal_runs):
            continue
        role_ids = _strings(payload.get("role_instance_ids"))
        failed_id = str(payload.get("failed_instance_id") or "")
        if failed_id and failed_id not in role_ids:
            role_ids.append(failed_id)
        for instance_id in role_ids:
            item = projection.setdefault(instance_id, {
                "declared": False,
                "required": False,
                "active": False,
                "failed": False,
                "activation_state": "declared",
                "activation_reason": "",
                "activation_id": "",
                "workflow_operation_id": "",
                "workflow_run_id": "",
                "flow_kind": "",
                "failure_reason": "",
            })
            item.update({
                "required": True,
                "activation_id": str(payload.get("activation_id") or ""),
                "workflow_operation_id": str(
                    payload.get("workflow_operation_id") or ""
                ),
                "workflow_run_id": str(
                    payload.get("workflow_run_id") or ""
                ),
                "flow_kind": str(payload.get("flow_kind") or ""),
                "activation_reason": str(payload.get("reason") or ""),
            })
            if event.type == "flow.roles.activation.failed":
                if not failed_id or instance_id == failed_id:
                    item["failed"] = True
                    item["active"] = False
                    item["failure_reason"] = str(
                        payload.get("reason") or ""
                    )
            elif event.type in {
                "flow.roles.activation.applied",
                "flow.roles.activation.recovered",
            }:
                item["failed"] = False
                item["active"] = True
                item["failure_reason"] = ""

    active_activation_by_role: dict[str, ZfEvent] = {}
    latest_activation_events = _latest_activation_state_events(events)
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        activation_id = str(payload.get("activation_id") or "")
        if (
            event.type not in {
                "flow.roles.activation.applied",
                "flow.roles.activation.recovered",
            }
            or latest_activation_events.get(activation_id) is not event
            or _activation_run_is_terminal(payload, aliases, terminal_runs)
        ):
            continue
        for instance_id in _strings(payload.get("role_instance_ids")):
            active_activation_by_role[instance_id] = event

    for instance_id, item in projection.items():
        active_activation = active_activation_by_role.get(instance_id)
        if active_activation is not None:
            payload = (
                active_activation.payload
                if isinstance(active_activation.payload, dict)
                else {}
            )
            item.update({
                "required": True,
                "active": True,
                "failed": False,
                "activation_id": str(payload.get("activation_id") or ""),
                "workflow_operation_id": str(
                    payload.get("workflow_operation_id") or ""
                ),
                "workflow_run_id": str(
                    payload.get("workflow_run_id") or ""
                ),
                "flow_kind": str(payload.get("flow_kind") or ""),
                "activation_reason": str(payload.get("reason") or ""),
                "failure_reason": "",
            })
        if instance_id in active_ids:
            item["active"] = True
        item["activation_state"] = (
            "failed"
            if item["failed"]
            else "active"
            if item["active"]
            else "required"
            if item["required"]
            else "declared"
        )
    return projection


def activate_flow_roles(
    orchestrator: Any,
    *,
    payload: Mapping[str, Any],
    source_event_id: str = "",
    correlation_id: str = "",
    recovery: bool = False,
) -> FlowRoleActivationResult:
    """Materialize the exact role closure and allocate required processes.

    The manifest is immutable and keyed by confirmed workflow identity. Replays
    repair only eager roles or on-demand roles with active resume obligations;
    dormant on-demand roles remain logical capacity without a pane/process.
    """

    if not flow_role_activation_required(orchestrator.config, payload):
        return FlowRoleActivationResult(status="not_required")

    identity = _activation_identity(payload)
    flow_kind = identity["flow_kind"]
    roles = role_configs_for_flow(orchestrator.config, flow_kind)
    if not roles:
        raise FlowRoleBindingError(
            "flow_role_closure_missing",
            f"Flow {flow_kind!r} has no configured roles",
        )

    role_entries = [
        {
            "role": str(getattr(role, "name", "") or ""),
            "instance_id": str(getattr(role, "instance_id", "") or ""),
            "role_kind": str(getattr(role, "role_kind", "") or ""),
            "backend": str(getattr(role, "backend", "") or ""),
            "role_config_digest": _role_config_digest(role),
            **identity,
        }
        for role in roles
    ]
    if any(not entry["role"] or not entry["instance_id"] for entry in role_entries):
        raise FlowRoleBindingError(
            "flow_role_identity_invalid",
            f"Flow {flow_kind!r} contains an unnamed role instance",
        )

    activation_seed = {
        "schema_version": ACTIVATION_SCHEMA_VERSION,
        **identity,
        "roles": role_entries,
    }
    activation_id = hashlib.sha256(
        canonical_json_bytes(activation_seed),
    ).hexdigest()
    manifest = {
        **activation_seed,
        "activation_reason": "confirmed_workflow_invoke",
    }
    manifest["activation_id"] = activation_id
    events = orchestrator.event_log.read_all()
    conflicting_activation = next((
        event
        for event in reversed(events)
        if event.type == "flow.roles.activation.applied"
        and _same_activation_scope(event.payload or {}, identity)
        and str((event.payload or {}).get("activation_id") or "")
        != activation_id
    ), None)
    if conflicting_activation is not None:
        if _activation_event(
            events,
            activation_id,
            "flow.roles.activation.failed",
        ) is None:
            orchestrator.event_writer.append(ZfEvent(
                type="flow.roles.activation.failed",
                actor="zf-cli",
                payload={
                    **identity,
                    "activation_id": activation_id,
                    "conflicting_activation_id": str(
                        (conflicting_activation.payload or {}).get(
                            "activation_id",
                        )
                        or ""
                    ),
                    "role_instance_ids": [
                        entry["instance_id"] for entry in role_entries
                    ],
                    "error_type": "FlowRoleBindingError",
                    "reason": (
                        "configured Flow role closure differs from the "
                        "durable activation manifest"
                    ),
                    "source_event_id": source_event_id,
                },
                causation_id=source_event_id or conflicting_activation.id,
                correlation_id=(
                    correlation_id or identity["workflow_run_id"]
                ),
            ))
        raise FlowRoleBindingError(
            "flow_role_activation_config_drift",
            f"Flow {flow_kind!r} role closure changed after activation",
        )
    descriptor = write_immutable_json_sidecar(
        orchestrator.state_dir,
        manifest,
        root=f"flow-role-activations/{flow_kind}",
        kind="flow_role_activation_manifest",
        schema_version=ACTIVATION_SCHEMA_VERSION,
        created_by="kernel",
        source_event_id=source_event_id,
    )

    applied = _activation_event(events, activation_id, "flow.roles.activation.applied")
    requested = _activation_event(
        events,
        activation_id,
        "flow.roles.activation.requested",
    )
    if requested is None:
        orchestrator.event_writer.append(ZfEvent(
            type="flow.roles.activation.requested",
            actor="zf-cli",
            payload={
                **identity,
                "activation_id": activation_id,
                "activation_manifest_ref": descriptor,
                "role_instance_ids": [
                    entry["instance_id"] for entry in role_entries
                ],
                "reason": (
                    "runtime_resume" if recovery else manifest["activation_reason"]
                ),
                "source_event_id": source_event_id,
            },
            causation_id=source_event_id or None,
            correlation_id=correlation_id or identity["workflow_run_id"],
        ))

    spawned: list[str] = []
    deferred: list[str] = []
    try:
        for role in roles:
            if _is_alive(orchestrator, role.instance_id):
                continue
            resume_required, resume_task_id = _on_demand_resume_obligation(
                orchestrator,
                role,
            )
            if _is_on_demand(role) and not resume_required:
                _prepare_dormant_role(
                    orchestrator,
                    role,
                    activation_id=activation_id,
                )
                deferred.append(role.instance_id)
                continue
            if _is_on_demand(role):
                orchestrator._ensure_role_active(
                    role,
                    task_id=resume_task_id or None,
                )
            else:
                _prepare_and_spawn_role(
                    orchestrator,
                    role,
                    activation_id=activation_id,
                )
            spawned.append(role.instance_id)
    except Exception as exc:
        diagnostics = transport_error_diagnostics(exc)
        orchestrator.event_writer.append(ZfEvent(
            type="flow.roles.activation.failed",
            actor="zf-cli",
            payload={
                **identity,
                "activation_id": activation_id,
                "activation_manifest_ref": descriptor,
                "role_instance_ids": [
                    entry["instance_id"] for entry in role_entries
                ],
                "failed_instance_id": str(
                    getattr(locals().get("role"), "instance_id", "") or ""
                ),
                "error_type": type(exc).__name__,
                "reason": str(exc)[:400],
                "source_event_id": source_event_id,
                **diagnostics,
            },
            causation_id=source_event_id or None,
            correlation_id=correlation_id or identity["workflow_run_id"],
        ))
        raise FlowRoleBindingError(
            "flow_role_activation_failed",
            f"Flow {flow_kind!r} role activation failed: {exc}",
        ) from exc

    role_ids = tuple(entry["instance_id"] for entry in role_entries)
    if applied is None:
        orchestrator.event_writer.append(ZfEvent(
            type="flow.roles.activation.applied",
            actor="zf-cli",
            payload={
                **identity,
                "activation_id": activation_id,
                "activation_manifest_ref": descriptor,
                "role_instance_ids": list(role_ids),
                "spawned_instance_ids": spawned,
                "deferred_instance_ids": deferred,
                "reason": manifest["activation_reason"],
                "source_event_id": source_event_id,
            },
            causation_id=source_event_id or None,
            correlation_id=correlation_id or identity["workflow_run_id"],
        ))
        status = "applied"
    elif spawned:
        orchestrator.event_writer.append(ZfEvent(
            type="flow.roles.activation.recovered",
            actor="zf-cli",
            payload={
                **identity,
                "activation_id": activation_id,
                "activation_manifest_ref": descriptor,
                "role_instance_ids": list(role_ids),
                "recovered_instance_ids": spawned,
                "deferred_instance_ids": deferred,
                "reason": "missing runtime role restored from activation truth",
                "source_event_id": source_event_id,
            },
            causation_id=source_event_id or applied.id,
            correlation_id=correlation_id or identity["workflow_run_id"],
        ))
        status = "recovered"
    else:
        status = "replay"

    return FlowRoleActivationResult(
        status=status,
        activation_id=activation_id,
        manifest_ref=descriptor,
        role_instance_ids=role_ids,
        recovered_instance_ids=tuple(spawned) if applied is not None else (),
        deferred_instance_ids=tuple(deferred),
    )


def restore_flow_role_activations(orchestrator: Any) -> list[FlowRoleActivationResult]:
    """Repair role processes required by durable activation facts."""

    if not is_multi_flow_config(orchestrator.config):
        return []
    results: list[FlowRoleActivationResult] = []
    seen: set[str] = set()
    events = orchestrator.event_log.read_all()
    aliases, terminal_runs = _terminal_run_scope(events)
    for event in events:
        if event.type != "flow.roles.activation.applied":
            continue
        event_payload = event.payload if isinstance(event.payload, dict) else {}
        if _activation_run_is_terminal(event_payload, aliases, terminal_runs):
            continue
        activation_id = str(event_payload.get("activation_id") or "")
        if not activation_id or activation_id in seen:
            continue
        seen.add(activation_id)
        descriptor = event_payload.get("activation_manifest_ref")
        if not isinstance(descriptor, dict):
            raise FlowRoleBindingError(
                "flow_role_activation_manifest_missing",
                f"activation {activation_id!r} has no manifest descriptor",
            )
        hydrated = hydrate_sidecar_ref(
            orchestrator.state_dir,
            descriptor,
            purpose="flow_role_activation_restore",
            actor="kernel",
        )
        manifest = hydrated.payload
        if (
            not isinstance(manifest, dict)
            or str(manifest.get("schema_version") or "")
            != ACTIVATION_SCHEMA_VERSION
            or str(manifest.get("activation_id") or "") != activation_id
        ):
            raise FlowRoleBindingError(
                "flow_role_activation_manifest_invalid",
                f"activation manifest {activation_id!r} is invalid",
            )
        results.append(activate_flow_roles(
            orchestrator,
            payload=manifest,
            source_event_id=event.id,
            correlation_id=event.correlation_id or "",
            recovery=True,
        ))
    return results


def _activation_identity(payload: Mapping[str, Any]) -> dict[str, str]:
    identity = {
        field: str(payload.get(field) or "").strip()
        for field in _IDENTITY_FIELDS
    }
    missing = [field for field, value in identity.items() if not value]
    if missing:
        raise FlowRoleBindingError(
            "flow_role_activation_identity_incomplete",
            "activation identity is missing " + ", ".join(missing),
        )
    return identity


def _role_config_digest(role: Any) -> str:
    if is_dataclass(role):
        payload: Any = asdict(role)
    else:
        payload = {
            key: getattr(role, key, None)
            for key in (
                "name",
                "instance_id",
                "backend",
                "role_kind",
                "flow_kind",
                "model",
                "allowed_tools",
                "permission_mode",
                "transport",
                "plugins",
                "skills",
                "agent",
            )
        }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _same_activation_scope(
    payload: Mapping[str, Any],
    identity: Mapping[str, str],
) -> bool:
    return all(
        str(payload.get(field) or "").strip() == identity[field]
        for field in _ACTIVATION_SCOPE_FIELDS
    )


def _latest_activation_state_events(
    events: list[ZfEvent],
) -> dict[str, ZfEvent]:
    latest: dict[str, ZfEvent] = {}
    for event in events:
        if event.type not in {
            "flow.roles.activation.applied",
            "flow.roles.activation.failed",
            "flow.roles.activation.recovered",
        }:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        activation_id = str(payload.get("activation_id") or "")
        if activation_id:
            latest[activation_id] = event
    return latest


def _activation_run_is_terminal(
    payload: Mapping[str, Any],
    aliases: Mapping[str, str],
    terminal_runs: set[str],
) -> bool:
    run_id = str(payload.get("workflow_run_id") or "").strip()
    return bool(run_id and aliases.get(run_id, run_id) in terminal_runs)


def _activation_event(
    events: list[ZfEvent],
    activation_id: str,
    event_type: str,
) -> ZfEvent | None:
    return next((
        event
        for event in reversed(events)
        if event.type == event_type
        and str((event.payload or {}).get("activation_id") or "") == activation_id
    ), None)


def _is_alive(orchestrator: Any, instance_id: str) -> bool:
    try:
        return bool(orchestrator.transport.is_alive(instance_id))
    except Exception:
        return False


def _is_on_demand(role: Any) -> bool:
    lifecycle = getattr(role, "lifecycle", None)
    return str(getattr(lifecycle, "mode", "") or "") == "on_demand"


def _on_demand_resume_obligation(
    orchestrator: Any,
    role: Any,
) -> tuple[bool, str]:
    if not _is_on_demand(role):
        return False, ""
    targets = {
        str(getattr(role, "instance_id", "") or ""),
        str(getattr(role, "name", "") or ""),
    }
    try:
        for task in orchestrator.task_store.list_all():
            if (
                str(getattr(task, "status", "") or "") == "in_progress"
                and str(getattr(task, "assigned_to", "") or "") in targets
            ):
                return True, str(getattr(task, "id", "") or "")
    except Exception:
        pass

    try:
        runtime_events = read_runtime_events(
            orchestrator.event_log,
            orchestrator.state_dir,
        )
        operations = reduce_workflow_operations(runtime_events)
        aliases, terminal_runs = _terminal_run_scope(runtime_events)
    except Exception:
        operations = {}
        aliases, terminal_runs = {}, set()
    for operation in operations.values():
        workflow_run_id = str(
            operation.get("workflow_run_id")
            or operation.get("run_id")
            or ""
        ).strip()
        if (
            str(operation.get("role_instance") or "")
            == str(getattr(role, "instance_id", "") or "")
            and str(operation.get("status") or "")
            not in TERMINAL_OPERATION_STATUSES
            and (
                not workflow_run_id
                or aliases.get(workflow_run_id, workflow_run_id)
                not in terminal_runs
            )
        ):
            return True, str(operation.get("task_id") or "")

    try:
        registry = RoleSessionRegistry(
            orchestrator.state_dir / "role_sessions.yaml",
            project_root=str(orchestrator.project_root),
        )
        _heartbeat_at, heartbeat = registry.get_last_heartbeat(role.instance_id)
    except Exception:
        heartbeat = None
    if isinstance(heartbeat, dict) and (
        str(heartbeat.get("current_task_id") or "")
        and str(heartbeat.get("state") or "") in {"busy", "in_progress"}
    ):
        try:
            still_owned = orchestrator._heartbeat_current_task_still_owned(
                registry,
                role.instance_id,
            )
        except Exception:
            still_owned = False
        if still_owned:
            return True, str(heartbeat.get("current_task_id") or "")
    return False, ""


def _prepare_dormant_role(
    orchestrator: Any,
    role: Any,
    *,
    activation_id: str,
) -> None:
    coordinator = orchestrator._get_spawn_coordinator()
    prepare_provider = getattr(coordinator, "prepare_provider_session", None)
    if callable(prepare_provider):
        prepare_provider(role)
    _write_flow_role_instructions(orchestrator, role, skill_entries=[])

    registry = RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(orchestrator.project_root),
    )
    meta = registry.instance_meta().get(role.instance_id, {})
    previous = str(meta.get("lifecycle_state") or "")
    if previous in {"dormant", "suspended"}:
        return
    event = orchestrator.event_writer.append(ZfEvent(
        type="role.lifecycle.dormant",
        actor="orchestrator",
        payload={
            "schema_version": "role-lifecycle.v1",
            "role": role.name,
            "instance_id": role.instance_id,
            "backend": role.backend,
            "from": previous,
            "to": "dormant",
            "activation_id": activation_id,
            "preserve_session": role.lifecycle.preserve_session,
            "preserve_workdir": role.lifecycle.preserve_workdir,
        },
        correlation_id=orchestrator._current_run_id() or None,
    ))
    registry.update_instance_meta(
        role.instance_id,
        lifecycle_state="dormant",
        lifecycle_transition_at=event.ts,
        lifecycle_active_at="",
        lifecycle_suspended_at="",
    )
    orchestrator._set_worker_state(
        role.instance_id,
        "dormant",
        reason="Flow role registered without provider process",
        force=True,
    )


def _prepare_and_spawn_role(
    orchestrator: Any,
    role: Any,
    *,
    activation_id: str,
) -> None:
    spawn_cwd = orchestrator._role_spawn_cwd(
        role,
        source=f"flow_activation:{activation_id}",
    )
    skill_entries: list[Any] = []
    if role.skills:
        from zf.core.skills import (
            build_skill_lock_entries,
            materialize_role_skills,
            upsert_skills_lockfile,
        )

        materialized = materialize_role_skills(
            config=orchestrator.config,
            project_root=orchestrator.project_root,
            state_dir=orchestrator.state_dir,
            role=role,
        )
        materialized_paths = (
            materialized.materialized_paths_under(orchestrator.project_root)
            if materialized is not None
            else {}
        )
        skill_entries = build_skill_lock_entries(
            project_root=orchestrator.project_root,
            state_dir=orchestrator.state_dir,
            role=role,
            config=orchestrator.config,
            materialized_paths=materialized_paths,
        )
        upsert_skills_lockfile(
            state_dir=orchestrator.state_dir,
            entries=skill_entries,
        )
        if materialized is not None:
            orchestrator.event_writer.append(ZfEvent(
                type="skills.materialized",
                actor="zf-cli",
                payload={
                    **materialized.to_payload(),
                    "activation_id": activation_id,
                    "source": "flow_activation",
                },
            ))

    orchestrator._get_spawn_coordinator().spawn(role, cwd=spawn_cwd)
    if not orchestrator._wait_role_ready(role):
        raise transport_readiness_error(
            orchestrator.transport,
            role.instance_id,
            backend=role.backend,
        )

    _write_flow_role_instructions(
        orchestrator,
        role,
        skill_entries=skill_entries,
    )
    orchestrator._set_worker_state(
        role.instance_id,
        "idle",
        reason=f"Flow role activated by {activation_id}",
        force=True,
    )
    _attach_session_tailer(orchestrator, role, spawn_cwd=spawn_cwd)


def _write_flow_role_instructions(
    orchestrator: Any,
    role: Any,
    *,
    skill_entries: list[Any],
) -> None:
    from zf.runtime.injection import generate_role_instructions

    instructions = generate_role_instructions(
        orchestrator.config,
        role,
        skill_entries=skill_entries,
        state_dir_ref=orchestrator.state_dir,
        project_root=orchestrator.project_root,
    )
    instructions_dir = orchestrator.state_dir / "instructions"
    instructions_dir.mkdir(parents=True, exist_ok=True)
    (instructions_dir / f"{role.instance_id}.md").write_text(
        instructions,
        encoding="utf-8",
    )


def _attach_session_tailer(
    orchestrator: Any,
    role: Any,
    *,
    spawn_cwd: Path | None,
) -> None:
    registry = RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(orchestrator.project_root),
    )
    session_id = registry.get(role.instance_id)
    if session_id is None:
        return
    if role.backend == "claude-code":
        tailer = getattr(orchestrator, "_claude_session_tailer", None)
        if tailer is None:
            return
        from zf.runtime.session_tailer import claude_session_path

        tailer.tail(
            role.instance_id,
            claude_session_path(
                str(spawn_cwd or orchestrator.project_root),
                str(session_id),
            ),
        )
    elif role.backend == "codex":
        tailer = getattr(orchestrator, "_codex_session_tailer", None)
        if tailer is None:
            return
        from zf.runtime.session_tailer import codex_session_path

        session_path = registry.get_path(role.instance_id)
        if session_path is None:
            session_path = codex_session_path(str(session_id))
        if session_path is not None:
            tailer.tail(role.instance_id, session_path)


def _strings(value: Any) -> list[str]:
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value if str(item)]
    if value:
        return [str(value)]
    return []


__all__ = [
    "ACTIVATION_SCHEMA_VERSION",
    "FlowRoleActivationResult",
    "activate_flow_roles",
    "active_flow_role_instance_ids",
    "flow_role_activation_required",
    "flow_role_activation_projection",
    "restore_flow_role_activations",
    "role_is_runtime_active",
]
