"""Normalize Agent-authored workflow choices after a Task is created."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import re
from typing import Any

from zf.core.security.redaction import redact_obj
from zf.core.task.schema import Task
from zf.runtime.kanban_plan_requests import (
    PLAN_REQUEST_SCHEMA_VERSION,
    plan_request_digest,
    plan_request_id,
)
from zf.runtime.workflow_route_catalog import (
    resolve_workflow_route,
    workflow_route_catalog,
)


TASK_WORKFLOW_PLAN_SCHEMA_VERSION = "task-workflow-plan.v1"
_PARAMETER_KEYS = frozenset({
    "acceptance",
    "artifact_refs",
    "backend",
    "channel_id",
    "constraints",
    "expected_output",
    "open_questions",
    "risk",
    "scope",
    "source_ref",
    "source_refs",
    "source_root",
    "strictness",
    "synthesis_event_id",
    "target_ref",
    "target_root",
    "thread_id",
    "topic",
})


def build_task_workflow_plan_request(
    raw_plan: object,
    *,
    task: Task,
    task_event_id: str,
    config: Any | None,
    context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Bind proposed route choices to one real Task and active config."""
    if not isinstance(raw_plan, dict):
        return None, "workflow_plan must be a mapping"
    catalog = workflow_route_catalog(config)
    config_digest = str(catalog.get("config_digest") or "")
    if not config_digest:
        return None, "workflow route catalog is unavailable"

    raw_options = raw_plan.get("options")
    if not isinstance(raw_options, list) or not 2 <= len(raw_options) <= 3:
        return None, "workflow_plan requires two or three options"

    options: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    errors: list[str] = []
    for index, raw_option in enumerate(raw_options, start=1):
        if not isinstance(raw_option, dict):
            errors.append(f"option {index} must be a mapping")
            continue
        label = str(raw_option.get("label") or "").strip()
        if not label:
            errors.append(f"option {index} label is required")
            continue
        option_id = _unique_id(
            _slug(str(raw_option.get("id") or label)) or f"option-{index}",
            used_ids,
        )
        route_id = str(raw_option.get("route_id") or "").strip()
        mode = str(raw_option.get("mode") or "").strip().lower()
        if not route_id and mode not in {"continue", "defer"}:
            errors.append(f"option {index} route_id is required")
            continue
        option: dict[str, Any] = {
            "id": option_id,
            "label": label,
            "description": str(raw_option.get("description") or "").strip(),
            "recommended": bool(raw_option.get("recommended")),
        }
        if not route_id:
            option["submit_mode"] = "continue"
            options.append(option)
            continue

        route = resolve_workflow_route(
            config,
            route_id,
            expected_config_digest=config_digest,
        )
        if route is None:
            errors.append(
                f"option {index} route {route_id!r} is not active"
            )
            continue
        raw_parameters = raw_option.get("parameters")
        nested_objective = ""
        if isinstance(raw_parameters, dict) and "objective" in raw_parameters:
            raw_parameters = dict(raw_parameters)
            nested_objective = str(
                raw_parameters.pop("objective") or ""
            ).strip()
        parameters, parameter_error = _normalize_parameters(raw_parameters)
        if parameter_error:
            errors.append(f"option {index}: {parameter_error}")
            continue
        objective = str(
            raw_option.get("objective")
            or nested_objective
            or raw_plan.get("objective")
            or task.title
        ).strip()
        option.update({
            "submit_mode": "propose",
            "submit_action": "workflow-start",
            "submit_payload": {
                "task_id": task.id,
                "route_id": route_id,
                "objective": objective,
                "config_digest": config_digest,
                "task_contract_digest": task_workflow_binding_digest(task),
                "parameters": parameters,
            },
            "submit_details": {
                "route_id": route_id,
                "family": str(route.get("family") or ""),
                "kind": str(route.get("kind") or ""),
                "tier": str(route.get("tier") or ""),
                "topology": str(route.get("topology") or ""),
                "roles": list(route.get("roles") or []),
                "writer_roles": list(route.get("writer_roles") or []),
                "verify_roles": list(route.get("verify_roles") or []),
                "lane_count": int(route.get("lane_count") or 0),
                "output_profile": str(
                    route.get("output_profile") or ""
                ),
            },
        })
        options.append(option)
    if errors:
        return None, "; ".join(errors)
    if not any(
        option.get("submit_action") == "workflow-start"
        for option in options
    ):
        return None, "workflow_plan requires at least one active workflow route"
    if not any(bool(option.get("recommended")) for option in options):
        options[0]["recommended"] = True
    recommended_seen = False
    for option in options:
        if not option.get("recommended"):
            continue
        if recommended_seen:
            option["recommended"] = False
        recommended_seen = True
    recommended_index = next(
        (
            index for index, option in enumerate(options)
            if bool(option.get("recommended"))
        ),
        0,
    )
    if recommended_index:
        options.insert(0, options.pop(recommended_index))

    source = context or {}
    question_id = (
        _slug(str(raw_plan.get("question_id") or "workflow-route"))
        or "workflow-route"
    )
    header = str(raw_plan.get("header") or "Workflow plan").strip()
    question = str(
        raw_plan.get("question")
        or f"How should {task.id} run?"
    ).strip()
    allow_other = bool(raw_plan.get("allow_other", True))
    request: dict[str, Any] = {
        "schema_version": PLAN_REQUEST_SCHEMA_VERSION,
        "workflow_plan_schema_version": TASK_WORKFLOW_PLAN_SCHEMA_VERSION,
        "interaction_mode": "plan",
        "subject_type": "task_workflow",
        "revision": 1,
        "expires_at": str(raw_plan.get("expires_at") or ""),
        "header": header,
        "question_id": question_id,
        "question": question,
        "options": options,
        "allow_other": allow_other,
        "questions": [{
            "id": question_id,
            "header": header,
            "question": question,
            "options": options,
            "allow_other": allow_other,
        }],
        "reason": str(
            raw_plan.get("reason")
            or raw_plan.get("summary")
            or ""
        ).strip(),
        "project_id": str(source.get("project_id") or ""),
        "task_id": task.id,
        "task_event_id": task_event_id,
        "task_contract_digest": task_workflow_binding_digest(task),
        "config_digest": config_digest,
        "conversation_id": str(source.get("conversation_id") or ""),
        "thread_key": str(
            source.get("thread_key")
            or source.get("thread_id")
            or ""
        ),
        "turn_id": str(source.get("turn_id") or ""),
        "backend": str(source.get("backend") or ""),
        "provider_session_id": str(
            source.get("provider_session_id") or ""
        ),
        "originating_message_event_id": str(
            source.get("originating_message_event_id")
            or task_event_id
        ),
        "originating_message_event_ids": [
            str(item)
            for item in source.get(
                "originating_message_event_ids", []
            )
            if str(item)
        ],
        "requirement_digest": str(
            source.get("requirement_digest") or ""
        ),
        "valid": True,
        "validation_error": "",
    }
    request["request_digest"] = plan_request_digest(request)
    request["request_id"] = plan_request_id(request)
    return redact_obj(request), ""


def _normalize_parameters(
    raw: object,
) -> tuple[dict[str, Any], str]:
    if raw in (None, ""):
        return {}, ""
    if not isinstance(raw, dict):
        return {}, "parameters must be a mapping"
    unknown = sorted(set(raw) - _PARAMETER_KEYS)
    if unknown:
        return (
            {},
            "unsupported parameter field(s): " + ", ".join(unknown),
        )
    return {
        str(key): value
        for key, value in raw.items()
        if value not in (None, "", [], {})
    }, ""


def task_workflow_binding_digest(task: Task) -> str:
    contract = asdict(task.contract)
    evidence_contract = contract.get("evidence_contract")
    if isinstance(evidence_contract, dict):
        evidence_contract = dict(evidence_contract)
        evidence_contract.pop("execution_owner", None)
        contract["evidence_contract"] = evidence_contract
    encoded = json.dumps(
        {
            "id": task.id,
            "key": task.key,
            "title": task.title,
            "contract": contract,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _slug(value: str) -> str:
    normalized = re.sub(
        r"[^A-Za-z0-9_-]+",
        "-",
        str(value or "").strip().lower(),
    )
    return normalized.strip("-")[:64]


def _unique_id(candidate: str, used: set[str]) -> str:
    value = candidate
    suffix = 2
    while value in used:
        value = f"{candidate}-{suffix}"
        suffix += 1
    used.add(value)
    return value


__all__ = [
    "TASK_WORKFLOW_PLAN_SCHEMA_VERSION",
    "build_task_workflow_plan_request",
    "task_workflow_binding_digest",
]
