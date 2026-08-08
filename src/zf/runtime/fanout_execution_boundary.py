"""Render the kernel-pinned execution boundary for fanout briefings."""

from __future__ import annotations

from typing import Any

from zf.core.config.schema import ExecutionProfileConfig, RoleConfig
from zf.runtime.execution_profiles import (
    DIRECT_PROFILE_ID,
    resolve_execution_profile,
)


def fanout_task_id(child_payload: dict, trigger_payload: dict) -> str:
    for value in (
        child_payload.get("task_id"),
        child_payload.get("parent_task_id"),
        trigger_payload.get("task_id"),
        trigger_payload.get("parent_task_id"),
    ):
        task_id = str(value or "").strip()
        if task_id:
            return task_id
    return ""


def render_execution_boundary(
    config: Any,
    role: RoleConfig,
    payload: dict,
) -> list[str]:
    profile_id = str(
        payload.get("execution_profile_id")
        or role.execution.default_profile
        or DIRECT_PROFILE_ID
    ).strip()
    digest = str(payload.get("execution_profile_digest") or "").strip()
    shadow = payload.get("execution_profile_shadow")
    profile = ExecutionProfileConfig()
    if config is not None:
        try:
            resolved = resolve_execution_profile(
                config,
                role_instance=role.instance_id,
                contract=payload,
            )
        except (LookupError, TypeError, ValueError):
            resolved = None
        if resolved is not None:
            profile_id = resolved.profile_id
            digest = resolved.profile_digest
            profile = resolved.profile
            shadow = {
                "verdict": resolved.shadow_verdict,
                "reason": resolved.shadow_reason,
            }
    limits = profile.limits
    shadow_verdict = (
        str(shadow.get("verdict") or "")
        if isinstance(shadow, dict)
        else ""
    )
    profile_line = (
        f"- profile_id: `{profile_id}`; digest: `{digest or '(not pinned)'}`; "
        f"mode: `{profile.strategy}/{profile.continuation}/{profile.collaboration}`; "
        f"access: `{profile.access}`; limits: max_children=`{limits.max_children}`, "
        f"max_depth=`{limits.max_depth}`, timeout_seconds=`{limits.timeout_seconds:g}`, "
        f"max_usage_samples=`{limits.max_usage_samples}`, "
        f"token_budget=`{limits.token_budget}`, "
        f"cost_budget_usd=`{limits.cost_budget_usd:g}`"
    )
    if shadow_verdict:
        profile_line += f"; admission_shadow: `{shadow_verdict}`"
    lines = [
        "## Execution Boundary (kernel-pinned)",
        "",
        profile_line,
    ]
    if (
        profile.strategy == "direct"
        and profile.collaboration == "single"
        and limits.max_children == 0
    ):
        lines.append(
            "Do not spawn or delegate to any provider-native sub-agent; "
            "execute this child directly in the current role session."
        )
    if limits.max_usage_samples:
        lines.extend([
            (
                f"Finish within `{limits.max_usage_samples}` usage samples; each "
                "tool round-trip counts. Reserve the final two samples for the "
                "prefilled result and submit; stop when evidence is sufficient."
            ),
            (
                "Do not perform external web research unless the acceptance contract "
                "explicitly requires current external facts."
            ),
        ])
    lines.append("")
    return lines


__all__ = ["fanout_task_id", "render_execution_boundary"]
