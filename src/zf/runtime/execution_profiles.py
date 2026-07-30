"""Provider-neutral execution profile normalization and E0 shadow admission."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from zf.core.config.schema import (
    ExecutionProfileConfig,
    RoleConfig,
)
from zf.runtime.backend import BackendCapabilities, get_adapter


EXECUTION_PROFILE_SCHEMA = "execution-profile.v1"
DIRECT_PROFILE_ID = "direct-v1"
_DIRECT_PROFILE = ExecutionProfileConfig()


class ExecutionProfileAdmissionError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedExecutionProfile:
    profile_id: str
    profile_digest: str
    profile: ExecutionProfileConfig
    role: str
    backend: str
    shadow_verdict: str
    shadow_reason: str

    def projection(self) -> dict[str, Any]:
        return {
            "schema_version": EXECUTION_PROFILE_SCHEMA,
            "profile_id": self.profile_id,
            "profile_digest": self.profile_digest,
            "profile": execution_profile_to_primitive(self.profile),
            "role": self.role,
            "backend": self.backend,
            "shadow": {
                "verdict": self.shadow_verdict,
                "reason": self.shadow_reason,
                "dispatch_effect": "none",
            },
        }


def execution_profile_to_primitive(
    profile: ExecutionProfileConfig,
) -> dict[str, Any]:
    return asdict(profile)


def profile_digest(profile: ExecutionProfileConfig) -> str:
    return profile_digest_from_primitive(execution_profile_to_primitive(profile))


def profile_digest_from_primitive(profile: Mapping[str, Any]) -> str:
    body = json.dumps(
        dict(profile),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def execution_profile_catalog(config: Any) -> dict[str, ExecutionProfileConfig]:
    configured = getattr(
        getattr(config, "workflow", None),
        "execution_profiles",
        {},
    )
    catalog = {DIRECT_PROFILE_ID: _DIRECT_PROFILE}
    if isinstance(configured, Mapping):
        catalog.update({
            str(profile_id): profile
            for profile_id, profile in configured.items()
            if isinstance(profile, ExecutionProfileConfig)
        })
    return catalog


def execution_profile_catalog_projection(config: Any) -> dict[str, Any]:
    catalog = execution_profile_catalog(config)
    profiles = {
        profile_id: {
            "digest": profile_digest(profile),
            "profile": execution_profile_to_primitive(profile),
        }
        for profile_id, profile in sorted(catalog.items())
    }
    roles: dict[str, Any] = {}
    for role in getattr(config, "roles", []) or []:
        role_id = str(role.instance_id or role.name)
        roles[role_id] = {
            "role": role.name,
            "backend": role.backend,
            "default_profile": role.execution.default_profile,
            "profile_allowlist": list(role.execution.profile_allowlist),
        }
    return {
        "schema_version": "execution-profile-catalog.v1",
        "profiles": profiles,
        "roles": roles,
    }


def resolve_execution_profile(
    config: Any,
    *,
    role_instance: str,
    contract: Any | None = None,
    capabilities: BackendCapabilities | None = None,
) -> ResolvedExecutionProfile:
    """Resolve one role/task profile and compute a non-operative E0 verdict."""

    role = _find_role(config, role_instance)
    requested_id = _contract_value(contract, "execution_profile_id")
    pinned_digest = _contract_value(contract, "execution_profile_digest")
    if role is None:
        if requested_id and requested_id != DIRECT_PROFILE_ID:
            raise ExecutionProfileAdmissionError(
                "non-direct execution profile requires a known role"
            )
        profile_id = DIRECT_PROFILE_ID
        backend = "python"
        role_name = role_instance
        allowlist = {DIRECT_PROFILE_ID}
    else:
        profile_id = requested_id or role.execution.default_profile
        backend = str(role.backend or "python")
        role_name = str(role.instance_id or role.name)
        allowlist = set(role.execution.profile_allowlist)
    if profile_id not in allowlist:
        raise ExecutionProfileAdmissionError(
            f"execution profile {profile_id!r} is not allowed for role "
            f"{role_name!r}"
        )
    catalog = execution_profile_catalog(config)
    profile = catalog.get(profile_id)
    if profile is None:
        raise ExecutionProfileAdmissionError(
            f"unknown execution profile {profile_id!r}"
        )
    digest = profile_digest(profile)
    if pinned_digest and pinned_digest != digest:
        raise ExecutionProfileAdmissionError(
            f"execution profile digest mismatch for {profile_id!r}"
        )
    if profile.strategy == "direct":
        verdict, reason = "supported", "direct_profile"
    else:
        caps = capabilities or get_adapter(backend).capabilities
        missing = _missing_capabilities(profile, caps)
        if not missing:
            verdict, reason = "supported", "provider_capabilities_satisfied"
        elif profile.capability_policy == "fallback_direct":
            verdict = "fallback"
            reason = "missing_capabilities:" + ",".join(missing)
        else:
            verdict = "rejected"
            reason = "missing_capabilities:" + ",".join(missing)
    return ResolvedExecutionProfile(
        profile_id=profile_id,
        profile_digest=digest,
        profile=profile,
        role=role_name,
        backend=backend,
        shadow_verdict=verdict,
        shadow_reason=reason,
    )


def _find_role(config: Any, role_instance: str) -> RoleConfig | None:
    roles = list(getattr(config, "roles", []) or [])
    if role_instance:
        for role in roles:
            if role_instance in {role.instance_id, role.name}:
                return role
    return None


def _contract_value(contract: Any | None, key: str) -> str:
    if isinstance(contract, Mapping):
        return str(contract.get(key) or "").strip()
    return str(getattr(contract, key, "") or "").strip()


def _missing_capabilities(
    profile: ExecutionProfileConfig,
    capabilities: BackendCapabilities,
) -> list[str]:
    missing: list[str] = []
    if profile.continuation == "goal" and not capabilities.native_goal:
        missing.append("native_goal")
    if profile.collaboration == "adaptive":
        if not capabilities.native_multi_agent:
            missing.append("native_multi_agent")
        if capabilities.child_lineage == "none":
            missing.append("child_lineage")
        if capabilities.child_permission_isolation != "enforced":
            missing.append("child_permission_isolation")
        if not capabilities.root_only_result_channel:
            missing.append("root_only_result_channel")
    if (
        profile.continuation == "goal"
        and capabilities.compound_resume == "none"
    ):
        missing.append("compound_resume")
    return missing


__all__ = [
    "DIRECT_PROFILE_ID",
    "EXECUTION_PROFILE_SCHEMA",
    "ExecutionProfileAdmissionError",
    "ResolvedExecutionProfile",
    "execution_profile_catalog",
    "execution_profile_catalog_projection",
    "execution_profile_to_primitive",
    "profile_digest",
    "profile_digest_from_primitive",
    "resolve_execution_profile",
]
