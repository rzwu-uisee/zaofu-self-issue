"""Read-only catalog of workflow routes shared by every control surface."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from zf.runtime.research_templates import (
    ADAPTIVE_RESEARCH_TEMPLATE,
    FIXED_RESEARCH_TEMPLATE,
    RESEARCH_TEMPLATES,
)

WORKFLOW_ROUTE_CATALOG_SCHEMA_VERSION = "workflow-route-catalog.v1"
ADAPTIVE_RESEARCH_ROUTE_ID = ADAPTIVE_RESEARCH_TEMPLATE.route_id
ADAPTIVE_RESEARCH_PATTERN_ID = ADAPTIVE_RESEARCH_TEMPLATE.pattern_id
FIXED_RESEARCH_ROUTE_ID = FIXED_RESEARCH_TEMPLATE.route_id
FIXED_RESEARCH_PATTERN_ID = FIXED_RESEARCH_TEMPLATE.pattern_id


def workflow_route_catalog(config: Any | None) -> dict[str, Any]:
    """Project active workflow entries without creating a second config truth."""
    if config is None:
        return {
            "schema_version": WORKFLOW_ROUTE_CATALOG_SCHEMA_VERSION,
            "is_derived_projection": True,
            "config_digest": "",
            "routes": [],
        }

    stages = list(getattr(getattr(config, "workflow", None), "stages", []) or [])
    roles = list(getattr(config, "roles", []) or [])
    stage_by_id = {
        str(getattr(stage, "id", "") or ""): stage
        for stage in stages
        if str(getattr(stage, "id", "") or "")
    }
    role_kind_by_id: dict[str, str] = {}
    for role in roles:
        kind = str(getattr(role, "role_kind", "") or "")
        for identity in (
            str(getattr(role, "instance_id", "") or ""),
            str(getattr(role, "name", "") or ""),
        ):
            if identity:
                role_kind_by_id[identity] = kind

    routes: list[dict[str, Any]] = []
    claimed_entries: set[str] = set()
    kind_routes = dict(
        getattr(getattr(config, "workflow", None), "kind_routes", {}) or {}
    )
    for kind, route in sorted(kind_routes.items()):
        route_kind = str(kind).strip().lower()
        canonical_kind, resolved = _resolve_kind_route(
            kind_routes,
            route_kind,
            route,
        )
        if resolved is None or canonical_kind != route_kind:
            continue
        targets = _route_targets(resolved)
        for tier, pattern_id in targets:
            stage = stage_by_id.get(pattern_id)
            if stage is None:
                continue
            claimed_entries.add(pattern_id)
            route_stages = _delivery_route_stages(
                stages,
                kind=canonical_kind,
                entry_pattern_id=pattern_id,
            )
            route_roles = _stage_roles(route_stages)
            lane_count = _delivery_lane_count(
                config,
                kind=canonical_kind,
                route_roles=route_roles,
                role_kind_by_id=role_kind_by_id,
            )
            routes.append({
                "route_id": f"delivery:{canonical_kind}:{tier or 'default'}",
                "family": "delivery",
                "kind": canonical_kind,
                "tier": tier,
                "entry_pattern_id": pattern_id,
                "topology": (
                    "multi_lane"
                    if lane_count > 1
                    else "single_lane"
                    if lane_count == 1
                    else str(getattr(stage, "topology", "") or "")
                ),
                "stages": [
                    str(getattr(item, "id", "") or "")
                    for item in route_stages
                ],
                "roles": route_roles,
                "writer_roles": _roles_of_kind(
                    route_roles,
                    role_kind_by_id,
                    "writer",
                ),
                "verify_roles": [
                    role for role in route_roles
                    if "verify" in role.lower() or "test" in role.lower()
                ],
                "lane_count": lane_count,
                "output_profile": "candidate_and_evidence",
                "start_adapter": "delivery_request_submit",
                "available": True,
            })

    for template in RESEARCH_TEMPLATES:
        research_stage = stage_by_id.get(template.pattern_id)
        if research_stage is None or not _is_reader_entry(
            research_stage,
            role_kind_by_id=role_kind_by_id,
        ):
            continue
        claimed_entries.add(template.pattern_id)
        research_roles = _stage_roles([research_stage])
        routes.append({
            "route_id": template.route_id,
            "family": "research",
            "kind": "research",
            "tier": template.tier,
            "template_id": template.template_id,
            "entry_pattern_id": template.pattern_id,
            "topology": str(
                getattr(research_stage, "topology", "") or "fanout_reader"
            ),
            "stages": [template.pattern_id],
            "roles": research_roles,
            "writer_roles": [],
            "verify_roles": [
                role for role in research_roles
                if "critic" in role.lower()
            ],
            "lane_count": 0,
            "output_profile": "research_report",
            "start_adapter": (
                "adaptive_research"
                if template is ADAPTIVE_RESEARCH_TEMPLATE
                else "fixed_research"
            ),
            "rollout": template.rollout,
            "task_binding_policy": "workflow_request_for_channel_prd",
            "available": True,
        })

    for stage in stages:
        stage_id = str(getattr(stage, "id", "") or "")
        if (
            not stage_id
            or stage_id in claimed_entries
            or str(getattr(stage, "trigger", "") or "")
            != "workflow.invoke.requested"
            or not _is_reader_entry(stage, role_kind_by_id=role_kind_by_id)
        ):
            continue
        general_roles = _stage_roles([stage])
        routes.append({
            "route_id": f"general:{stage_id}",
            "family": "general",
            "kind": "workflow",
            "tier": "",
            "entry_pattern_id": stage_id,
            "topology": str(getattr(stage, "topology", "") or "single_reader"),
            "stages": [stage_id],
            "roles": general_roles,
            "writer_roles": [],
            "verify_roles": [
                role for role in general_roles
                if "verify" in role.lower() or "critic" in role.lower()
            ],
            "lane_count": 0,
            "output_profile": "artifact",
            "start_adapter": "registered_general",
            "available": True,
        })

    routes.sort(key=lambda item: str(item["route_id"]))
    config_digest = _catalog_digest(routes)
    return {
        "schema_version": WORKFLOW_ROUTE_CATALOG_SCHEMA_VERSION,
        "is_derived_projection": True,
        "config_digest": config_digest,
        "routes": routes,
    }


def resolve_workflow_route(
    config: Any | None,
    route_id: str,
    *,
    expected_config_digest: str = "",
) -> dict[str, Any] | None:
    catalog = workflow_route_catalog(config)
    if (
        expected_config_digest
        and expected_config_digest != str(catalog.get("config_digest") or "")
    ):
        return None
    wanted = str(route_id or "").strip()
    return next(
        (
            dict(route)
            for route in catalog["routes"]
            if str(route.get("route_id") or "") == wanted
            and bool(route.get("available"))
        ),
        None,
    )


def _resolve_kind_route(
    routes: dict[str, Any],
    kind: str,
    route: Any,
) -> tuple[str, Any | None]:
    current_kind = str(kind or "").strip().lower()
    current = route
    seen: set[str] = set()
    while current is not None:
        alias = str(getattr(current, "alias", "") or "").strip().lower()
        if not alias:
            return current_kind, current
        if alias in seen:
            return current_kind, None
        seen.add(alias)
        current_kind = alias
        current = routes.get(alias)
    return current_kind, None


def _route_targets(route: Any) -> list[tuple[str, str]]:
    default_tier = str(getattr(route, "default_tier", "") or "").strip()
    pattern_id = str(getattr(route, "pattern_id", "") or "").strip()
    tier_routes = dict(getattr(route, "tier_routes", {}) or {})
    targets: list[tuple[str, str]] = []
    if pattern_id:
        targets.append((default_tier or "default", pattern_id))
    for tier, target in sorted(tier_routes.items()):
        value = str(target or "").strip()
        if value and (str(tier), value) not in targets:
            targets.append((str(tier), value))
    return targets


def _delivery_route_stages(
    stages: list[Any],
    *,
    kind: str,
    entry_pattern_id: str,
) -> list[Any]:
    scoped = [
        stage
        for stage in stages
        if str(getattr(stage, "flow_kind", "") or "") == kind
    ]
    if not scoped:
        prefix = f"{kind}-"
        scoped = [
            stage
            for stage in stages
            if str(getattr(stage, "id", "") or "").startswith(prefix)
        ]
    entry = next(
        (
            stage for stage in stages
            if str(getattr(stage, "id", "") or "") == entry_pattern_id
        ),
        None,
    )
    if entry is not None and entry not in scoped:
        scoped.insert(0, entry)
    return scoped or ([entry] if entry is not None else [])


def _stage_roles(stages: list[Any]) -> list[str]:
    roles: list[str] = []
    for stage in stages:
        for role in list(getattr(stage, "roles", []) or []):
            value = str(role or "").strip()
            if value and value not in roles:
                roles.append(value)
    return roles


def _roles_of_kind(
    roles: list[str],
    role_kind_by_id: dict[str, str],
    wanted: str,
) -> list[str]:
    return [
        role for role in roles
        if role_kind_by_id.get(role) == wanted
    ]


def _delivery_lane_count(
    config: Any,
    *,
    kind: str,
    route_roles: list[str],
    role_kind_by_id: dict[str, str],
) -> int:
    lane_profiles = dict(
        getattr(getattr(config, "workflow", None), "affinity_lanes", {}) or {}
    )
    counts = [
        len(list(getattr(profile, "lanes", []) or []))
        for profile_id, profile in lane_profiles.items()
        if str(profile_id or "").startswith(f"{kind}-")
    ]
    writer_count = len(_roles_of_kind(
        route_roles,
        role_kind_by_id,
        "writer",
    ))
    return max([writer_count, *counts], default=0)


def _is_reader_entry(
    stage: Any,
    *,
    role_kind_by_id: dict[str, str],
) -> bool:
    topology = str(getattr(stage, "topology", "") or "")
    roles = _stage_roles([stage])
    return (
        topology in {"", "single_reader", "fanout_reader"}
        and bool(roles)
        and all(role_kind_by_id.get(role, "reader") != "writer" for role in roles)
    )


def _catalog_digest(routes: list[dict[str, Any]]) -> str:
    encoded = json.dumps(
        routes,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


__all__ = [
    "ADAPTIVE_RESEARCH_PATTERN_ID",
    "ADAPTIVE_RESEARCH_ROUTE_ID",
    "FIXED_RESEARCH_PATTERN_ID",
    "FIXED_RESEARCH_ROUTE_ID",
    "WORKFLOW_ROUTE_CATALOG_SCHEMA_VERSION",
    "resolve_workflow_route",
    "workflow_route_catalog",
]
