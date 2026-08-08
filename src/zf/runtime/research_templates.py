"""Registered Research workflow templates.

The adaptive route is an explicit read-only pilot.  It does not upgrade the
provider capability snapshot or replace the fixed audit route.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResearchTemplate:
    template_id: str
    route_id: str
    pattern_id: str
    tier: str
    child_roles: tuple[str, ...]
    synth_role: str
    rollout: str


ADAPTIVE_RESEARCH_TEMPLATE = ResearchTemplate(
    template_id="research-adaptive.pilot.v1",
    route_id="research:adaptive-pilot",
    pattern_id="research-adaptive",
    tier="adaptive-pilot",
    child_roles=("research_root",),
    synth_role="",
    rollout="opt_in_pilot",
)

FIXED_RESEARCH_TEMPLATE = ResearchTemplate(
    template_id="research-fanout.fixed.v1",
    route_id="research:fixed",
    pattern_id="research-fanout",
    tier="fixed",
    child_roles=(
        "source_researcher",
        "product_analyst",
        "technical_analyst",
        "risk_critic",
    ),
    synth_role="synthesizer",
    rollout="stable_audit",
)

DEFAULT_RESEARCH_TEMPLATE = FIXED_RESEARCH_TEMPLATE
RESEARCH_TEMPLATES = (
    ADAPTIVE_RESEARCH_TEMPLATE,
    FIXED_RESEARCH_TEMPLATE,
)
RESEARCH_TEMPLATES_BY_ID = {
    template.template_id: template
    for template in RESEARCH_TEMPLATES
}
RESEARCH_TEMPLATES_BY_ROUTE = {
    template.route_id: template
    for template in RESEARCH_TEMPLATES
}


def resolve_research_template(
    template_id: str = "",
) -> ResearchTemplate | None:
    requested = str(template_id or "").strip()
    if not requested:
        return DEFAULT_RESEARCH_TEMPLATE
    return RESEARCH_TEMPLATES_BY_ID.get(requested)


def research_root_role(template: ResearchTemplate) -> str:
    """Return the sole Provider-facing root identity for a template."""

    return template.synth_role or template.child_roles[0]


def research_stage_contract_error(
    stage: object | None,
    template: ResearchTemplate,
) -> str:
    """Validate the mechanical stage shape required by a registered route."""

    if stage is None:
        return f"{template.pattern_id} stage is not declared in zf.yaml"
    if str(getattr(stage, "topology", "") or "") != "fanout_reader":
        return f"{template.pattern_id} stage must use fanout_reader topology"
    children = tuple(
        str(getattr(item, "role_instance", "") or "")
        for item in getattr(stage, "children", []) or []
    )
    if children != template.child_roles:
        return (
            f"{template.pattern_id} stage must declare children "
            f"{list(template.child_roles)!r}"
        )
    synth_role = str(
        getattr(getattr(stage, "aggregate", None), "synth_role", "") or ""
    )
    if synth_role == template.synth_role:
        return ""
    if not template.synth_role:
        return (
            f"{template.pattern_id} stage must use direct root "
            "aggregation without aggregate.synth_role"
        )
    return (
        f"{template.pattern_id} stage must declare "
        f"{template.synth_role} as aggregate.synth_role"
    )


__all__ = [
    "ADAPTIVE_RESEARCH_TEMPLATE",
    "DEFAULT_RESEARCH_TEMPLATE",
    "FIXED_RESEARCH_TEMPLATE",
    "RESEARCH_TEMPLATES",
    "RESEARCH_TEMPLATES_BY_ID",
    "RESEARCH_TEMPLATES_BY_ROUTE",
    "ResearchTemplate",
    "research_stage_contract_error",
    "research_root_role",
    "resolve_research_template",
]
