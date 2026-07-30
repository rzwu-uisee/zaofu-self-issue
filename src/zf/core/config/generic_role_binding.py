"""Role-closure binding for Generic Workflow envelopes."""

from __future__ import annotations

from typing import Any


def bind_generic_workflow_roles(
    body: dict[str, Any],
    contract: dict[str, Any],
    *,
    error_type: type[ValueError] = ValueError,
) -> None:
    """Bind each registered Generic Workflow role to the workflow family."""

    raw_roles = body.get("roles", [])
    if not isinstance(raw_roles, list):
        raise error_type("ZfConfig spec.roles must be a list")
    required_roles = {
        str(role_name or "").strip()
        for task in contract.get("tasks", []) or []
        if isinstance(task, dict)
        for role_name in task.get("roles", []) or []
        if str(role_name or "").strip()
    }
    for required_role in sorted(required_roles):
        matches = [
            role
            for role in raw_roles
            if isinstance(role, dict)
            and required_role
            in {
                str(role.get("name") or "").strip(),
                str(role.get("instance_id") or "").strip(),
            }
        ]
        if not matches:
            raise error_type(
                "Generic Workflow references missing role "
                f"{required_role!r}"
            )
        if len(matches) > 1:
            raise error_type(
                "Generic Workflow role reference "
                f"{required_role!r} is ambiguous"
            )
        role = matches[0]
        declared_kind = str(role.get("flow_kind") or "").strip().lower()
        if declared_kind not in {"", "workflow"}:
            raise error_type(
                f"Generic Workflow role {required_role!r} already belongs "
                f"to Flow {declared_kind!r}"
            )
        role["flow_kind"] = "workflow"
