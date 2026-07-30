"""Briefing contract for the opt-in adaptive Research root pilot."""

from __future__ import annotations


ADAPTIVE_RESEARCH_STAGE_ID = "research-adaptive"


def apply_child_contract(
    completion_payload: dict,
    child_payload: dict,
    instruction_lines: list[str],
    stage_id: str,
    operation_id: str,
) -> None:
    """Add the adaptive Root contract to the single outer child."""

    if stage_id != ADAPTIVE_RESEARCH_STAGE_ID:
        return
    _merge_completion_fields(
        completion_payload,
        adaptive_research_completion_fields(
            workflow_run_id=str(child_payload.get("workflow_run_id") or ""),
            operation_id=operation_id,
        ),
    )
    instruction_lines.extend(adaptive_research_briefing_lines())


def adaptive_research_completion_fields(
    *,
    workflow_run_id: str,
    operation_id: str,
) -> dict:
    provider_summary = {
        "schema_version": "provider-operation-summary.v1",
        "workflow_run_id": workflow_run_id,
        "operation_id": operation_id,
        "provider_session_id": "",
        "settlement": "settled",
        "child_count": 0,
        "child_status_counts": {"completed": 0},
        "active_child_count": 0,
        "peak_parallel_agents": 0,
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
        },
        "cost_usd": 0,
        "measurement": "unavailable",
        "children": [],
    }
    return {
        "evidence_refs": [],
        "open_questions": [],
        "provider_operation_summary": provider_summary,
        "report": {
            "findings": [],
            "architecture": {},
            "acceptance_matrix": [],
            "test_matrix": [],
            "task_map": [],
            "evidence_refs": [],
            "open_questions": [],
            "prd_prompt_input": "",
            "refactor_prompt_input": "",
            "provider_operation_summary": provider_summary,
        },
    }


def adaptive_research_briefing_lines() -> list[str]:
    return [
        "## Adaptive Research Root Contract",
        "",
        "This route is an opt-in Provider-native read-only pilot.",
        "Use zero to four Provider-native Explore children at depth one. "
        "Give every child a distinct objective and join every started child.",
        "Children must not call zf, write files, mutate runtime state, "
        "create canonical Tasks, or spawn another child.",
        "Only research_root may use the completion command. Emit it once.",
        "Replace every placeholder in provider_operation_summary. If token "
        "usage or cost is unavailable, keep numeric zero, set measurement to "
        "`unavailable`, and explain the telemetry gap; never label it measured.",
        "Preserve findings, architecture, acceptance_matrix, test_matrix, "
        "task_map, evidence_refs, open_questions, prompt inputs, and child "
        "provenance whenever they apply.",
        "",
    ]


def _merge_completion_fields(target: dict, fields: dict) -> None:
    target.update({key: value for key, value in fields.items() if key != "report"})
    target["report"].update(fields["report"])


__all__ = [
    "ADAPTIVE_RESEARCH_STAGE_ID",
    "apply_child_contract",
    "adaptive_research_briefing_lines",
    "adaptive_research_completion_fields",
]
