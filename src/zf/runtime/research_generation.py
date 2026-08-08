"""Immutable identity and stale-run fencing for Research workflows."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Mapping

from zf.core.config.render import renderable_config_to_primitive
from zf.core.events import EventWriter, ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.task.store import TaskStore
from zf.runtime.call_result_envelope import (
    canonical_json_sha256,
    write_immutable_json_sidecar,
)
from zf.runtime.research_templates import (
    ResearchTemplate,
    resolve_research_template,
)
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.run_contract import (
    RUN_CONTRACT_SCHEMA,
    load_run_contract_snapshot,
    stable_json_sha256,
    write_run_contract_snapshot,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_workflow_plans import task_workflow_binding_digest
from zf.runtime.workflow_route_catalog import resolve_workflow_route


RESEARCH_EFFECTIVE_CONFIG_SCHEMA = "research-effective-config.v1"
RESEARCH_GENERATION_SCHEMA = "research-generation.v1"
RESEARCH_GENERATION_HANDOFF_KEYS = (
    "research_generation_contract_ref",
    "research_template_id",
    "research_route_digest",
    "research_template_digest",
    "research_role_activation_digest",
    "research_prompt_contract_digest",
    "restart_boundary",
)


class ResearchGenerationError(ValueError):
    """Research generation identity is incomplete, stale, or unverifiable."""


def materialize_research_generation(
    state_dir: Path,
    *,
    config: Any,
    project_root: Path,
    task: Any,
    template: ResearchTemplate,
    workflow_run_id: str,
    request_payload: Mapping[str, Any],
    prompt_ref: Mapping[str, Any],
    source_event_id: str,
) -> dict[str, Any]:
    """Freeze one Research generation and return invoke identity fields."""

    binding = _research_generation_binding(
        config=config,
        task=task,
        template=template,
        request_payload=request_payload,
        prompt_ref=prompt_ref,
    )
    effective_ref = write_immutable_json_sidecar(
        state_dir,
        binding["effective_config"],
        root="workflow/research/effective-configs",
        kind="research_effective_config",
        schema_version=RESEARCH_EFFECTIVE_CONFIG_SCHEMA,
        created_by="research-generation",
        source_event_id=source_event_id,
    )
    if str(effective_ref.get("sha256") or "") != binding["effective_config_digest"]:
        raise ResearchGenerationError("research effective-config digest drift")

    generation = binding["workflow_generation"]
    contract = {
        "schema_version": RUN_CONTRACT_SCHEMA,
        "project": {
            "name": str(getattr(getattr(config, "project", None), "name", "") or ""),
            "state_dir": str(Path(state_dir).resolve(strict=False)),
            "root": str(Path(project_root).resolve(strict=False)),
        },
        "workflow": {
            "kind": "research",
            "intent": "research",
            "template": template.template_id,
            "route_id": template.route_id,
            "pattern_id": template.pattern_id,
            "workflow_generation": generation,
            "restart_boundary": "workflow_start",
        },
        "config": {
            "effective_snapshot_ref": effective_ref,
            "effective_snapshot_digest": binding["effective_config_digest"],
        },
        "refs": {
            "workflow_prompt_ref": dict(prompt_ref),
        },
        "research_generation": {
            "schema_version": RESEARCH_GENERATION_SCHEMA,
            "workflow_run_id": workflow_run_id,
            "workflow_generation": generation,
            "task_id": str(getattr(task, "id", "") or ""),
            "task_contract_digest": binding["task_contract_digest"],
            "route_contract_digest": binding["route_contract_digest"],
            "template_digest": binding["template_digest"],
            "role_activation_digest": binding["role_activation_digest"],
            "prompt_contract_digest": binding["prompt_contract_digest"],
            "prompt_ref": dict(prompt_ref),
            "role_bindings": binding["role_bindings"],
        },
    }
    contract["contract_digest"] = stable_json_sha256(contract)
    contract_ref = write_run_contract_snapshot(
        state_dir,
        contract,
        source_event_id=source_event_id,
    )
    return {
        "route_id": template.route_id,
        "research_template_id": template.template_id,
        "research_route_digest": binding["route_contract_digest"],
        "research_template_digest": binding["template_digest"],
        "research_role_activation_digest": binding["role_activation_digest"],
        "research_prompt_contract_digest": binding["prompt_contract_digest"],
        "workflow_generation": generation,
        "expected_generation": generation,
        "effective_config_ref": effective_ref,
        "effective_config_digest": binding["effective_config_digest"],
        "run_contract_ref": str(contract_ref.get("ref") or ""),
        "run_contract_digest": str(contract_ref.get("contract_digest") or ""),
        "research_generation_contract_ref": contract_ref,
        "restart_boundary": "workflow_start",
        "safe_resume_action": "restart_from_admission",
    }


def research_generation_binding_error(
    state_dir: Path,
    *,
    config: Any,
    task: Any,
    payload: Mapping[str, Any],
) -> str:
    """Return a stable fail-closed reason for a Research invoke binding."""

    if str(payload.get("request_kind") or "") != "research":
        return ""
    template_id = str(
        payload.get("research_template_id")
        or _source_refs(payload).get("template_id")
        or ""
    )
    template = resolve_research_template(template_id)
    if template is None:
        return "research_generation_template_unknown"
    contract_ref = payload.get("research_generation_contract_ref")
    effective_ref = payload.get("effective_config_ref")
    if not isinstance(contract_ref, Mapping) or not isinstance(effective_ref, Mapping):
        return "research_generation_contract_missing"
    try:
        effective = hydrate_sidecar_ref(
            Path(state_dir),
            dict(effective_ref),
            purpose="research_generation_effective_config",
            actor="kernel",
        )
        snapshot = load_run_contract_snapshot(Path(state_dir), dict(contract_ref))
    except Exception:
        return "research_generation_contract_unverifiable"
    if not isinstance(effective.payload, dict):
        return "research_generation_effective_config_invalid"
    contract = snapshot.get("contract")
    if not isinstance(contract, dict):
        return "research_generation_contract_invalid"
    generation = contract.get("research_generation")
    if not isinstance(generation, dict):
        return "research_generation_contract_invalid"
    prompt_ref = generation.get("prompt_ref")
    if not isinstance(prompt_ref, Mapping):
        return "research_generation_prompt_missing"
    try:
        hydrate_sidecar_ref(
            Path(state_dir),
            dict(prompt_ref),
            purpose="research_generation_prompt",
            actor="kernel",
        )
    except Exception:
        return "research_generation_prompt_unverifiable"

    try:
        expected = _research_generation_binding(
            config=config,
            task=task,
            template=template,
            request_payload=payload,
            prompt_ref=prompt_ref,
        )
    except ResearchGenerationError:
        return "research_generation_current_config_invalid"
    checks = {
        "workflow_generation": expected["workflow_generation"],
        "task_contract_digest": expected["task_contract_digest"],
        "route_contract_digest": expected["route_contract_digest"],
        "template_digest": expected["template_digest"],
        "role_activation_digest": expected["role_activation_digest"],
        "prompt_contract_digest": expected["prompt_contract_digest"],
    }
    for key, expected_value in checks.items():
        actual = str(
            payload.get(key)
            if key == "workflow_generation"
            else generation.get(key)
            or ""
        )
        if actual != expected_value:
            return f"research_generation_{key}_stale"
    if str(payload.get("effective_config_digest") or "") != expected[
        "effective_config_digest"
    ]:
        return "research_generation_effective_config_stale"
    if str(effective.sha256 or "") != expected["effective_config_digest"]:
        return "research_generation_effective_config_stale"
    if str(payload.get("run_contract_digest") or "") != str(
        snapshot.get("contract_digest") or ""
    ):
        return "research_generation_run_contract_stale"
    if str(payload.get("run_contract_ref") or "") != str(
        contract_ref.get("ref") or ""
    ):
        return "research_generation_run_contract_ref_mismatch"
    payload_checks = {
        "research_template_id": template.template_id,
        "research_route_digest": expected["route_contract_digest"],
        "research_template_digest": expected["template_digest"],
        "research_role_activation_digest": expected["role_activation_digest"],
        "research_prompt_contract_digest": expected["prompt_contract_digest"],
        "task_contract_digest": expected["task_contract_digest"],
        "expected_generation": expected["workflow_generation"],
    }
    for key, expected_value in payload_checks.items():
        if str(payload.get(key) or "") != expected_value:
            return f"research_generation_{key}_mismatch"
    if str(generation.get("workflow_run_id") or "") != str(
        payload.get("workflow_run_id") or ""
    ):
        return "research_generation_run_id_mismatch"
    return ""


def supersede_active_research_generations(
    writer: EventWriter,
    *,
    task_id: str,
    new_generation: str,
    new_run_id: str,
    source_event_id: str,
) -> list[str]:
    """Terminate older Research runs before admitting one replacement run."""

    events = writer.event_log.read_all()
    cancelled: list[str] = []
    for invoke in _active_research_invocations(events, task_id=task_id):
        payload = invoke.payload if isinstance(invoke.payload, dict) else {}
        old_run_id = str(payload.get("workflow_run_id") or "")
        if not old_run_id or old_run_id == new_run_id:
            continue
        _emit_superseded(
            writer,
            invoke=invoke,
            old_run_id=old_run_id,
            old_generation=str(payload.get("workflow_generation") or ""),
            new_generation=new_generation,
            new_run_id=new_run_id,
            source_event_id=source_event_id,
            reason="research generation replaced from workflow admission",
        )
        cancelled.append(old_run_id)
    return cancelled


def reconcile_stale_research_generations(
    *,
    config: Any,
    state_dir: Path,
    writer: EventWriter,
) -> list[str]:
    """Fence legacy/config-drifted Research runs before startup dispatch."""

    events = writer.event_log.read_all()
    store = TaskStore(Path(state_dir) / "kanban.json")
    cancelled: list[str] = []
    for invoke in _active_research_invocations(events):
        payload = invoke.payload if isinstance(invoke.payload, dict) else {}
        task_id = str(invoke.task_id or payload.get("task_id") or "")
        task = store.get(task_id) if task_id else None
        reason = (
            "research_generation_task_missing"
            if task is None
            else research_generation_binding_error(
                Path(state_dir),
                config=config,
                task=task,
                payload=payload,
            )
        )
        if not reason:
            continue
        old_run_id = str(payload.get("workflow_run_id") or "")
        if not old_run_id:
            continue
        _emit_superseded(
            writer,
            invoke=invoke,
            old_run_id=old_run_id,
            old_generation=str(payload.get("workflow_generation") or ""),
            new_generation="",
            new_run_id="",
            source_event_id=invoke.id,
            reason=reason,
        )
        cancelled.append(old_run_id)
    return cancelled


def _research_generation_binding(
    *,
    config: Any,
    task: Any,
    template: ResearchTemplate,
    request_payload: Mapping[str, Any],
    prompt_ref: Mapping[str, Any],
) -> dict[str, Any]:
    route = resolve_workflow_route(config, template.route_id)
    if route is None:
        raise ResearchGenerationError(
            f"Research route {template.route_id!r} is not available"
        )
    stage = next((
        item
        for item in getattr(getattr(config, "workflow", None), "stages", []) or []
        if str(getattr(item, "id", "") or "") == template.pattern_id
    ), None)
    if stage is None:
        raise ResearchGenerationError(
            f"Research stage {template.pattern_id!r} is not configured"
        )
    role_bindings = _research_role_bindings(config, template)
    route_contract = {
        "route": route,
        "stage": _primitive(stage),
    }
    template_body = _primitive(template)
    role_activation_digest = canonical_json_sha256({
        "schema_version": "research-role-activation.v1",
        "roles": role_bindings,
    })
    prompt_contract_digest = canonical_json_sha256(
        _prompt_contract(request_payload)
    )
    effective_config = {
        "schema_version": RESEARCH_EFFECTIVE_CONFIG_SCHEMA,
        "config": redact_obj(renderable_config_to_primitive(config)),
    }
    effective_config_digest = canonical_json_sha256(effective_config)
    task_digest = task_workflow_binding_digest(task)
    route_digest = canonical_json_sha256(route_contract)
    template_digest = canonical_json_sha256(template_body)
    generation_seed = {
        "schema_version": RESEARCH_GENERATION_SCHEMA,
        "effective_config_digest": effective_config_digest,
        "route_contract_digest": route_digest,
        "template_digest": template_digest,
        "role_activation_digest": role_activation_digest,
        "prompt_contract_digest": prompt_contract_digest,
        "prompt_digest": str(prompt_ref.get("sha256") or ""),
        "task_contract_digest": task_digest,
    }
    return {
        "effective_config": effective_config,
        "effective_config_digest": effective_config_digest,
        "route_contract_digest": route_digest,
        "template_digest": template_digest,
        "role_activation_digest": role_activation_digest,
        "prompt_contract_digest": prompt_contract_digest,
        "task_contract_digest": task_digest,
        "role_bindings": role_bindings,
        "workflow_generation": canonical_json_sha256(generation_seed),
    }


def _research_role_bindings(
    config: Any,
    template: ResearchTemplate,
) -> list[dict[str, str]]:
    roles = list(getattr(config, "roles", []) or [])
    bindings: list[dict[str, str]] = []
    for identity in (*template.child_roles, template.synth_role):
        if not identity:
            continue
        role = next((
            item for item in roles
            if identity in {
                str(getattr(item, "name", "") or ""),
                str(getattr(item, "instance_id", "") or ""),
            }
        ), None)
        if role is None:
            raise ResearchGenerationError(
                f"Research role {identity!r} is not configured"
            )
        bindings.append({
            "role": str(getattr(role, "name", "") or ""),
            "instance_id": str(
                getattr(role, "instance_id", "")
                or getattr(role, "name", "")
                or ""
            ),
            "role_config_digest": canonical_json_sha256(_primitive(role)),
        })
    return bindings


def _prompt_contract(payload: Mapping[str, Any]) -> dict[str, Any]:
    refs = _source_refs(payload)
    artifact_refs = [
        dict(item)
        for item in payload.get("artifact_refs", []) or []
        if isinstance(item, Mapping)
        and str(item.get("kind") or "") not in {
            "workflow_prompt",
            "research_generation_contract",
        }
    ]
    return {
        "schema_version": "research-prompt-contract.v1",
        "task_id": str(payload.get("task_id") or ""),
        "pattern_id": str(payload.get("pattern_id") or ""),
        "route_id": str(
            payload.get("route_id")
            or refs.get("route_id")
            or ""
        ),
        "template_id": str(
            payload.get("research_template_id")
            or refs.get("template_id")
            or ""
        ),
        "topic": str(refs.get("topic") or payload.get("topic") or ""),
        "reason": str(payload.get("reason") or ""),
        "expected_output": str(payload.get("expected_output") or ""),
        "risk": str(payload.get("risk") or ""),
        "scope": [str(item) for item in payload.get("scope", []) or []],
        "target_ref": str(payload.get("target_ref") or ""),
        "open_questions": [
            str(item) for item in payload.get("open_questions", []) or []
        ],
        "request_id": str(payload.get("request_id") or ""),
        "request_revision": _safe_int(payload.get("request_revision")),
        "origin_binding": dict(payload.get("origin_binding") or {})
        if isinstance(payload.get("origin_binding"), Mapping)
        else {},
        "artifact_refs": redact_obj(artifact_refs),
    }


def _active_research_invocations(
    events: list[ZfEvent],
    *,
    task_id: str = "",
) -> list[ZfEvent]:
    projection = build_run_admission_projection(events)
    rejected_sources = {
        str((event.payload or {}).get("source_event_id") or "")
        for event in events
        if event.type == "workflow.invoke.rejected"
        and isinstance(event.payload, dict)
    }
    active: list[ZfEvent] = []
    for event in events:
        if event.type != "workflow.invoke.requested":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        refs = _source_refs(payload)
        if not (
            str(payload.get("request_kind") or "") == "research"
            or str(refs.get("template_id") or "")
        ):
            continue
        event_task_id = str(event.task_id or payload.get("task_id") or "")
        if task_id and event_task_id != task_id:
            continue
        if event.id in rejected_sources:
            continue
        run_id = str(payload.get("workflow_run_id") or "")
        entry = projection.runs.get(run_id)
        if entry is not None and entry.terminal:
            continue
        if _run_cancelled(events, run_id):
            continue
        active.append(event)
    return active


def _emit_superseded(
    writer: EventWriter,
    *,
    invoke: ZfEvent,
    old_run_id: str,
    old_generation: str,
    new_generation: str,
    new_run_id: str,
    source_event_id: str,
    reason: str,
) -> None:
    events = writer.event_log.read_all()
    if _run_cancelled(events, old_run_id):
        return
    body = invoke.payload if isinstance(invoke.payload, dict) else {}
    task_id = str(invoke.task_id or body.get("task_id") or "")
    superseded = writer.emit(
        "workflow.generation.superseded",
        actor="kernel",
        task_id=task_id or None,
        causation_id=source_event_id or invoke.id,
        correlation_id=old_run_id,
        payload={
            "schema_version": "workflow-generation-superseded.v1",
            "family": "research",
            "task_id": task_id,
            "workflow_run_id": old_run_id,
            "workflow_generation": old_generation,
            "replacement_run_id": new_run_id,
            "replacement_generation": new_generation,
            "source_event_id": invoke.id,
            "reason": reason,
            "restart_boundary": "workflow_start",
            "safe_resume_action": "restart_from_admission",
        },
    )
    writer.emit(
        "run.cancelled",
        actor="kernel",
        causation_id=superseded.id,
        correlation_id=old_run_id,
        payload={
            "schema_version": "run-cancelled.v1",
            "workflow_run_id": old_run_id,
            "run_id": old_run_id,
            "root_task_id": task_id,
            "reason": reason,
            "source": "research_generation_reconciliation",
            "source_event_id": invoke.id,
            "replacement_run_id": new_run_id,
            "replacement_generation": new_generation,
            "safe_resume_action": "restart_from_admission",
        },
    )


def _run_cancelled(events: list[ZfEvent], run_id: str) -> bool:
    return any(
        event.type == "run.cancelled"
        and str(
            (event.payload or {}).get("workflow_run_id")
            or (event.payload or {}).get("run_id")
            or event.correlation_id
            or ""
        ) == run_id
        for event in events
        if isinstance(event.payload, dict)
    )


def _source_refs(payload: Mapping[str, Any]) -> dict[str, Any]:
    refs = payload.get("source_refs")
    return dict(refs) if isinstance(refs, Mapping) else {}


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _primitive(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_primitive(item) for item in value]
    return value


__all__ = [
    "RESEARCH_EFFECTIVE_CONFIG_SCHEMA",
    "RESEARCH_GENERATION_HANDOFF_KEYS",
    "RESEARCH_GENERATION_SCHEMA",
    "ResearchGenerationError",
    "materialize_research_generation",
    "reconcile_stale_research_generations",
    "research_generation_binding_error",
    "supersede_active_research_generations",
]
