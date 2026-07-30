"""Build a submit preview from an admitted Workflow Synthesis result."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.runtime.sidecar_refs import hydrate_sidecar_ref


class WorkflowSynthesisProposalError(ValueError):
    pass


def build_synthesis_proposal(
    *,
    state_dir: Path,
    result: Mapping[str, Any],
    result_ref: Mapping[str, Any],
    operation_context: Mapping[str, Any],
    actor: str,
) -> dict[str, Any]:
    from zf.cli.flow import build_flow_submit_preview

    family = str(result.get("selected_flow_family") or "")
    flow_kind = {
        "IssueFlow": "issue",
        "PrdFlow": "prd",
        "RefactorFlow": "refactor",
        "Workflow": "workflow",
    }.get(family, "")
    if not flow_kind:
        raise WorkflowSynthesisProposalError(
            f"unsupported admitted Flow family: {family!r}"
        )
    short_spec = hydrate_sidecar_ref(
        state_dir,
        dict(result["short_flow_spec_ref"]),
    ).payload
    parameters = (
        dict(short_spec.get("parameters") or {})
        if isinstance(short_spec, Mapping)
        else {}
    )
    generic_workflow_spec = (
        short_spec.get("generic_workflow_spec")
        if isinstance(short_spec, Mapping)
        else {}
    )
    generic_entry = (
        str(generic_workflow_spec.get("entry") or "")
        if isinstance(generic_workflow_spec, Mapping)
        else ""
    )
    return build_flow_submit_preview(
        config_path=Path(str(operation_context.get("config_ref") or "")),
        intake_path=Path(str(operation_context.get("intake_ref") or "")),
        flow_kind=flow_kind,
        task_id=str(operation_context.get("task_id") or ""),
        pattern_id=str(
            operation_context.get("pattern_id")
            or parameters.get("pattern_id")
            or generic_entry
            or ""
        ),
        requested_by=str(operation_context.get("requested_by") or actor),
        reason=str(
            operation_context.get("reason")
            or "workflow synthesis proposal"
        ),
        allow_missing_env=bool(
            operation_context.get("allow_missing_env")
        ),
        synthesis_result_ref=dict(result_ref),
    )


__all__ = [
    "WorkflowSynthesisProposalError",
    "build_synthesis_proposal",
]
