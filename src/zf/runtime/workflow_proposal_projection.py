"""Pure projections used while compiling a Workflow Proposal."""

from __future__ import annotations

from typing import Any, Mapping


def selected_flow_spec(
    documents: list[dict[str, Any]],
    family: str,
) -> dict[str, Any]:
    matches = [
        item.get("spec")
        for item in documents
        if str(item.get("kind") or "") == family
        and isinstance(item.get("spec"), Mapping)
    ]
    return dict(matches[0]) if len(matches) == 1 else {}


def stage_graph(config: Mapping[str, Any]) -> dict[str, Any]:
    workflow = (
        config.get("workflow")
        if isinstance(config.get("workflow"), Mapping)
        else {}
    )
    stages = workflow.get("stages")
    nodes: list[dict[str, Any]] = []
    for stage in stages if isinstance(stages, list) else []:
        if not isinstance(stage, Mapping):
            continue
        nodes.append({
            key: stage.get(key)
            for key in (
                "id",
                "trigger",
                "topology",
                "operation",
                "role",
                "roles",
                "dependencies",
                "dependency_events",
                "dependency_failure_events",
                "dependency_barrier_id",
                "dependency_barrier_digest",
                "input_ports",
                "output_ports",
                "gate_profile",
                "timeout_seconds",
            )
            if stage.get(key) not in (None, "", [], {})
        })
    return {"nodes": nodes, "node_count": len(nodes)}


def role_skill_profile_closure(
    config: Mapping[str, Any],
) -> dict[str, Any]:
    from zf.core.config.schema import ExecutionProfileConfig
    from zf.runtime.execution_profiles import (
        execution_profile_to_primitive,
        profile_digest_from_primitive,
    )

    roles = config.get("roles")
    roles = roles if isinstance(roles, list) else []
    workflow = (
        config.get("workflow")
        if isinstance(config.get("workflow"), Mapping)
        else {}
    )
    raw_profiles = workflow.get("execution_profiles")
    raw_profiles = (
        raw_profiles if isinstance(raw_profiles, Mapping) else {}
    )
    direct_profile = execution_profile_to_primitive(ExecutionProfileConfig())
    profiles = {
        "direct-v1": {
            "digest": profile_digest_from_primitive(direct_profile),
            "profile": direct_profile,
        },
        **{
            str(profile_id): {
                "digest": profile_digest_from_primitive(profile),
                "profile": dict(profile),
            }
            for profile_id, profile in raw_profiles.items()
            if isinstance(profile, Mapping)
        },
    }
    return {
        "roles": [
            {
                **{
                    key: role.get(key)
                    for key in ("name", "instance_id", "backend", "skills")
                    if role.get(key) not in (None, "", [], {})
                },
                "execution": {
                    key: execution.get(key)
                    for key in ("default_profile", "profile_allowlist")
                    if execution.get(key) not in (None, "", [], {})
                },
            }
            for role in roles
            if isinstance(role, Mapping)
            for execution in [
                role.get("execution")
                if isinstance(role.get("execution"), Mapping)
                else {}
            ]
        ],
        "execution_profiles": profiles,
    }


def completion_profile(
    config: Mapping[str, Any],
    preflight: Mapping[str, Any],
) -> dict[str, Any]:
    metadata = (
        preflight.get("effective_flow_metadata")
        if isinstance(preflight.get("effective_flow_metadata"), Mapping)
        else rendered_flow_metadata(config)
    )
    return {
        "id": str(metadata.get("completion_profile") or "software_delivery"),
        "intent": str(metadata.get("intent") or ""),
        "template": str(metadata.get("workflow_template") or ""),
        "generic_workflow_contract_digest": str(
            metadata.get("generic_workflow_contract_digest") or ""
        ),
        "delivery_policy": str(metadata.get("delivery_policy") or "report_only"),
        "completion_threshold": str(metadata.get("completion_threshold") or ""),
        "required_delivery_artifacts": list(
            metadata.get("required_delivery_artifacts") or []
        ),
    }


def estimate(
    config: Mapping[str, Any],
    source_docs: list[dict[str, Any]],
    flow_family: str,
) -> dict[str, Any]:
    roles = config.get("roles")
    roles = roles if isinstance(roles, list) else []
    graph = stage_graph(config)
    flow_spec = selected_flow_spec(source_docs, flow_family)
    return {
        "roles": len(roles),
        "stages": int(graph.get("node_count") or 0),
        "lanes": int(flow_spec.get("lanes") or 0),
    }


def approval_policy(config: Mapping[str, Any]) -> str:
    metadata = rendered_flow_metadata(config)
    return str(metadata.get("approval_policy") or "manual")


def rendered_flow_metadata(
    config: Mapping[str, Any],
) -> Mapping[str, Any]:
    workflow = (
        config.get("workflow")
        if isinstance(config.get("workflow"), Mapping)
        else {}
    )
    for key in ("flow_metadata", "_flow_metadata"):
        metadata = workflow.get(key)
        if isinstance(metadata, Mapping):
            return metadata
    return {}


def flow_purpose(flow_kind: str) -> str:
    return {
        "issue": "software_fix",
        "prd": "software_delivery",
        "refactor": "software_refactor",
        "feat": "software_delivery",
        "workflow": "artifact_delivery",
    }.get(str(flow_kind or "").lower(), "software_delivery")


__all__ = [
    "approval_policy",
    "completion_profile",
    "estimate",
    "flow_purpose",
    "rendered_flow_metadata",
    "role_skill_profile_closure",
    "selected_flow_spec",
    "stage_graph",
]
