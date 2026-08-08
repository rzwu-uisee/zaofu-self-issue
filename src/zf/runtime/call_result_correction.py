"""Field-level repair guidance for invalid provider control results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def repair_instruction(issue: Mapping[str, Any]) -> str:
    field = str(issue.get("field") or "control_result")
    code = str(issue.get("code") or "schema_invalid")
    message = str(issue.get("message") or "").strip()
    if code == "missing_required":
        return f"Provide a non-empty value at {field}."
    if code == "workflow_output_missing":
        source_ref = message or field
        output_name = source_ref.rsplit(".", 1)[-1]
        return (
            f"Provide the non-placeholder artifact body at outputs.{output_name} "
            f"for declared port {source_ref}."
        )
    if code == "semantic_submit_required":
        return "Use the exact result submit command for the existing operation."
    detail = f" ({message})" if message else ""
    return f"Replace {field} with a value matching the required schema{detail}."


def build_correction_briefing(
    *,
    state_dir: Path,
    source_payload: Mapping[str, Any],
    task_id: str,
    operation_id: str,
    request_hash: str,
    correction_ref: Mapping[str, Any],
    issues: list[dict[str, str]],
) -> str:
    submit_command, scratch_path = _result_submit_command(
        state_dir=state_dir,
        source_payload=source_payload,
        operation_id=operation_id,
    )
    issue_rows = [
        {
            "field": str(issue.get("field") or "control_result"),
            "code": str(issue.get("code") or "schema_invalid"),
            "message": str(issue.get("message") or ""),
            "required_change": repair_instruction(issue),
        }
        for issue in issues
    ]
    submit_lines = [
        "Emit one corrected terminal result using the same "
        "task/attempt/dispatch identity."
    ]
    if submit_command:
        submit_lines = [
            f"Edit the existing result scratch in place: `{scratch_path}`",
            "Resubmit that corrected file exactly once:",
            "```bash",
            submit_command,
            "```",
        ]
    return "\n".join([
        f"Active task: {task_id or '(none)'}",
        "",
        "# Call Result Protocol Correction",
        "",
        "The previous provider turn completed work but returned an invalid control result.",
        "Do not redo implementation or change verdict/evidence semantics.",
        f"Correction packet: `{correction_ref.get('ref', '')}`",
        f"Operation: `{operation_id}`",
        f"Request hash: `{request_hash}`",
        "",
        "Exact schema issues from the latest result:",
        "```json",
        json.dumps(issue_rows, ensure_ascii=False, indent=2),
        "```",
        "",
        *submit_lines,
        "Do not merely change finding severity unless an issue above explicitly requires it.",
        "",
    ])


def _result_submit_command(
    *,
    state_dir: Path,
    source_payload: Mapping[str, Any],
    operation_id: str,
) -> tuple[str, str]:
    scratch_ref = str(source_payload.get("result_scratch_ref") or "")
    if not scratch_ref or not operation_id:
        return "", ""
    try:
        from zf.runtime.cli_command import zf_cli_cmd
        from zf.runtime.stage_execution_card import prepare_result_file_command

        command, scratch = prepare_result_file_command(
            state_dir=state_dir,
            result_scratch_ref=scratch_ref,
            operation_id=operation_id,
            cli_command=zf_cli_cmd(),
            semantic_template={},
        )
    except (OSError, ValueError):
        return "", ""
    return command, str(scratch)


__all__ = ["build_correction_briefing", "repair_instruction"]
