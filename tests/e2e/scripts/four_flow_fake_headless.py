#!/usr/bin/env python3
"""Deterministic headless provider for the four-flow browser E2E."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator, Mapping
from typing import Any


FLOW_MARKERS = {
    "FOURFLOW_TASK_PRD": "prd",
    "FOURFLOW_TASK_ISSUE": "issue",
    "FOURFLOW_TASK_REFACTOR": "refactor",
    "FOURFLOW_TASK_GENERAL": "general",
}
FLOW_ROUTES = {
    "prd": "delivery:prd:default",
    "issue": "delivery:issue:default",
    "refactor": "delivery:refactor:default",
    "general": "general:scope",
}
GENERAL_ROLES = (
    "general-scoper",
    "general-collector-a",
    "general-collector-b",
    "general-synthesizer",
    "general-verifier",
)


def _emit(value: Mapping[str, Any]) -> None:
    print(json.dumps(dict(value), ensure_ascii=False), flush=True)


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _prompt(raw: str) -> str:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    strings = list(_strings(payload))
    candidates = [
        text
        for text in strings
        if "ZaoFu Kanban Agent turn" in text
        or "Use the zf-workflow-synthesis method." in text
    ]
    return max(candidates, key=len) if candidates else raw


def _user_message(prompt: str) -> str:
    boundary = "\nUser message:\n"
    return (
        prompt.rsplit(boundary, 1)[1].strip()
        if boundary in prompt
        else prompt
    )


def _session_id(args: list[str]) -> str:
    for flag in ("--resume", "--session-id"):
        if flag not in args:
            continue
        index = args.index(flag)
        if index + 1 < len(args):
            return args[index + 1]
    return "four-flow-headless-session"


def _prompt_context(prompt: str) -> dict[str, Any]:
    for line in prompt.splitlines():
        if not line.startswith("context: "):
            continue
        value = json.loads(line.removeprefix("context: "))
        return dict(value) if isinstance(value, dict) else {}
    return {}


def _synthesis_contract(prompt: str) -> dict[str, Any]:
    marker = "Contract:\n"
    if marker not in prompt:
        raise ValueError("workflow synthesis contract is missing")
    value, _end = json.JSONDecoder().raw_decode(
        prompt.split(marker, 1)[1].strip()
    )
    if not isinstance(value, dict):
        raise ValueError("workflow synthesis contract must be an object")
    return value


def _workflow_synthesis(prompt: str) -> dict[str, Any]:
    contract = _synthesis_contract(prompt)
    allowed_roles = set(contract.get("allowed_roles") or [])
    missing_roles = sorted(set(GENERAL_ROLES) - allowed_roles)
    if missing_roles:
        raise ValueError(
            "generic roles are not registered: " + ", ".join(missing_roles)
        )
    allowed_profiles = list(contract.get("allowed_profiles") or [])
    profile = (
        "direct-v1"
        if "direct-v1" in allowed_profiles
        else str(allowed_profiles[0] if allowed_profiles else "")
    )
    return {
        "schema_version": "workflow-synthesis-result.v1",
        "request_id": contract["request_id"],
        "request_revision": contract["request_revision"],
        "requirement_ref": contract["requirement_ref"],
        "requirement_digest": contract["requirement_digest"],
        "selected_flow_family": "Workflow",
        "short_flow_spec": {
            "flow_family": "Workflow",
            "intent": "research",
            "template": "evidence-synthesis-v1",
            "purpose": "Produce a verified minimal delivery note.",
            "parameters": {
                "scoper_role": GENERAL_ROLES[0],
                "collector_roles": list(GENERAL_ROLES[1:3]),
                "synthesizer_role": GENERAL_ROLES[3],
                "verifier_role": GENERAL_ROLES[4],
                "artifact_name": "report",
                "artifact_kind": "report/markdown",
            },
        },
        "decision_rationale": (
            "The request needs one registered read-only evidence workflow."
        ),
        "assumptions": [],
        "open_questions": [],
        "requested_roles": list(GENERAL_ROLES),
        "requested_skills": [],
        "requested_profiles": [profile] if profile else [],
        "completion_profile": {
            "id": "artifact_delivery",
            "delivery_policy": "report_only",
            "completion_threshold": "verified_artifacts",
            "required_artifacts": ["synthesize.report"],
        },
        "risk_hints": [],
    }


def _channel_setup_plan() -> dict[str, Any]:
    channel_id = os.environ["ZF_FOUR_FLOW_CHANNEL_ID"]
    return {
        "plan_request": {
            "subject_type": "channel_setup",
            "header": "PRD Channel setup",
            "id": "four-flow-prd-channel",
            "question": (
                "Which member set and two-round budget should clarify "
                "FOURFLOW_CHANNEL_SETUP?"
            ),
            "submit_action": "channel-create-and-start",
            "submit_label": "Create & start",
            "options": [
                {
                    "id": "prd-clarification",
                    "label": "PRD clarification (Recommended)",
                    "description": (
                        "Four focused members discuss for at most two rounds."
                    ),
                    "recommended": True,
                    "submit_payload": {
                        "template_id": "prd-clarification",
                        "channel_id": channel_id,
                        "name": "Four-flow minimal PRD",
                        "thread_id": "main",
                        "overrides": {
                            "backend": "fake",
                            "budget": {"max_rounds": 2},
                            "role_overrides": {
                                "security_reviewer": {"enabled": False},
                            },
                        },
                    },
                },
                {
                    "id": "architecture-review",
                    "label": "Architecture review",
                    "description": (
                        "Use a broader architecture review with four rounds."
                    ),
                    "submit_payload": {
                        "template_id": "architecture-review",
                        "channel_id": channel_id,
                        "name": "Four-flow architecture review",
                        "thread_id": "main",
                        "overrides": {
                            "backend": "fake",
                            "budget": {"max_rounds": 4},
                        },
                    },
                },
            ],
            "allow_other": False,
            "reason": (
                "The member roster and turn budget determine collaboration cost."
            ),
        },
    }


def _canonical_prd(context: Mapping[str, Any]) -> dict[str, Any]:
    projection = context.get("canonical_channel_prds")
    items = (
        projection.get("items")
        if isinstance(projection, Mapping)
        else []
    )
    channel_id = os.environ["ZF_FOUR_FLOW_CHANNEL_ID"]
    matches = [
        dict(item)
        for item in items or []
        if isinstance(item, Mapping)
        and str(item.get("channel_id") or "") == channel_id
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected one canonical PRD for {channel_id}, got {len(matches)}"
        )
    item = matches[0]
    for key in ("artifact_ref", "artifact_digest", "source_ref"):
        if not str(item.get(key) or ""):
            raise ValueError(f"canonical PRD has no {key}")
    return item


def _task_proposal(
    *,
    prompt: str,
    marker: str,
    flow_kind: str,
) -> dict[str, Any]:
    context = _prompt_context(prompt)
    prd = _canonical_prd(context)
    catalog = context.get("workflow_route_catalog")
    routes = (
        catalog.get("routes")
        if isinstance(catalog, Mapping)
        else []
    )
    active_route_ids = {
        str(route.get("route_id") or "")
        for route in routes or []
        if isinstance(route, Mapping)
    }
    route_id = FLOW_ROUTES[flow_kind]
    if route_id not in active_route_ids:
        raise ValueError(f"workflow route is not active: {route_id}")

    artifact_ref = str(prd["artifact_ref"])
    artifact_digest = str(prd["artifact_digest"])
    source_ref = str(prd["source_ref"])
    source_refs = {
        "channel_id": str(prd.get("channel_id") or ""),
        "thread_id": str(prd.get("thread_id") or "main"),
        "synthesis_event_id": str(prd.get("synthesis_event_id") or ""),
        "channel_consensus_event_id": str(
            prd.get("consensus_event_id") or ""
        ),
        "channel_prd_ref": artifact_ref,
        "channel_prd_digest": artifact_digest,
    }
    parameters: dict[str, Any] = {
        "backend": "mock",
        "source_ref": artifact_ref,
        "source_refs": source_refs,
        "artifact_refs": [{
            "kind": "channel_prd",
            "ref": artifact_ref,
            "digest": artifact_digest,
        }],
        "channel_id": str(prd.get("channel_id") or ""),
        "thread_id": str(prd.get("thread_id") or "main"),
        "synthesis_event_id": str(prd.get("synthesis_event_id") or ""),
        "target_ref": "HEAD",
        "acceptance": ["README.md remains present."],
        "constraints": ["Use the owner-confirmed canonical Channel PRD."],
        "expected_output": f"A dispatched {flow_kind} workflow.",
    }
    project_root = ""
    for line in prompt.splitlines():
        if line.startswith("project_root: "):
            project_root = line.removeprefix("project_root: ").strip()
            break
    if project_root:
        parameters["target_root"] = project_root
    if flow_kind == "refactor":
        parameters["source_root"] = os.environ[
            "ZF_FOUR_FLOW_SOURCE_ROOT"
        ]

    title = f"Four-flow {flow_kind} {marker}"
    return {
        "action_proposal": {
            "action": "create-task",
            "intent": {
                "decision": "propose_action",
                "source_quote": f"{marker} create a Task",
            },
            "payload": {
                "title": title,
                "priority": 2,
                "contract": {
                    "behavior": (
                        f"Run the {flow_kind} workflow from the canonical PRD."
                    ),
                    "verification": "test -f README.md",
                    "acceptance": "exit_code=0: test -f README.md",
                    "acceptance_criteria": [
                        f"The {flow_kind} workflow dispatch is recorded.",
                    ],
                    "spec_ref": artifact_ref,
                    "source_ref": source_ref,
                    "handoff_artifacts": [artifact_ref],
                    "evidence_contract": {
                        "channel_prd_digest": artifact_digest,
                    },
                },
                "workflow_plan": {
                    "header": f"{flow_kind.title()} workflow route",
                    "question_id": f"{flow_kind}-workflow-route",
                    "question": (
                        f"How should {marker} execute the confirmed PRD?"
                    ),
                    "options": [
                        {
                            "id": f"{flow_kind}-delivery",
                            "label": (
                                f"{flow_kind.title()} delivery (Recommended)"
                            ),
                            "description": (
                                f"Start the active {route_id} route."
                            ),
                            "recommended": True,
                            "route_id": route_id,
                            "parameters": parameters,
                        },
                        {
                            "id": "defer",
                            "label": "Do not start yet",
                            "description": (
                                "Keep the Task tracked without execution."
                            ),
                            "mode": "defer",
                        },
                    ],
                    "allow_other": False,
                    "reason": (
                        "Task creation and workflow ignition require separate "
                        "operator decisions."
                    ),
                },
            },
            "reason": (
                "Create a Task bound to the owner-confirmed Channel PRD."
            ),
        },
    }


def _reply(prompt: str) -> dict[str, Any]:
    if "Use the zf-workflow-synthesis method." in prompt:
        return _workflow_synthesis(prompt)
    message = _user_message(prompt)
    if "FOURFLOW_CHANNEL_SETUP" in message:
        return _channel_setup_plan()
    for marker, flow_kind in FLOW_MARKERS.items():
        if marker in message:
            return _task_proposal(
                prompt=prompt,
                marker=marker,
                flow_kind=flow_kind,
            )
    raise ValueError("unknown four-flow E2E marker")


def main() -> int:
    raw = sys.stdin.read()
    prompt = _prompt(raw)
    session_id = _session_id(sys.argv[1:])
    _emit({"type": "system", "session_id": session_id})
    _emit({
        "type": "assistant",
        "session_id": session_id,
        "message": {
            "content": [{
                "type": "text",
                "text": "Preparing the controlled four-flow action.",
            }],
        },
    })
    try:
        result = json.dumps(_reply(prompt), ensure_ascii=False)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        result = f"FOUR_FLOW_PROVIDER_ERROR: {exc}"
    _emit({
        "type": "result",
        "session_id": session_id,
        "result": result,
        "usage": {"input_tokens": 32, "output_tokens": 64},
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
