"""Evidence extraction helpers for candidate-level rework planning."""

from __future__ import annotations

from typing import Any

from zf.runtime.candidate_rework_generation import task_ids_from_payload
from zf.runtime.verification_result import (
    verification_findings_from_payload,
    verification_result_from_payload,
    verification_rework_items_from_payload,
)


def feedback_lines_from_payload(payload: dict[str, Any]) -> list[str]:
    findings = payload.get("findings")
    if not isinstance(findings, list):
        report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
        findings = report.get("findings")
    if not isinstance(findings, list):
        findings = verification_findings_from_payload(payload)
    lines: list[str] = []
    seen: set[str] = set()
    for item in findings:
        if isinstance(item, dict):
            task_id = str(item.get("task_id") or item.get("child_id") or "").strip()
            message = str(
                item.get("message")
                or item.get("summary")
                or item.get("title")
                or item.get("reason")
                or ""
            ).strip()
            command = str(item.get("verification_command") or "").strip()
            category = str(item.get("category") or "").strip()
            parts = [part for part in (task_id, category) if part]
            prefix = " / ".join(parts)
            line = f"{prefix}: {message}" if prefix else message
            if command:
                line = f"{line} (verify: {command})"
        else:
            line = str(item).strip()
        if not line or line in seen:
            continue
        seen.add(line)
        lines.append(line)
    for item in verification_rework_items_from_payload(payload):
        rework_id = str(item.get("rework_item_id") or "rework").strip()
        delta = str(
            item.get("required_delta")
            or item.get("observed")
            or item.get("expected")
            or ""
        ).strip()
        done_when = str(item.get("done_when") or "").strip()
        line = f"{rework_id}: {delta}" if delta else ""
        if line and done_when:
            line = f"{line} (done when: {done_when})"
        if line and line not in seen:
            seen.add(line)
            lines.append(line)
    return lines


def candidate_failure_task_ids(payload: dict[str, Any]) -> set[str]:
    task_ids = task_ids_from_payload(payload)
    result = verification_result_from_payload(payload)
    if str(result.get("verification_owner") or "") != "candidate_verify":
        return task_ids
    candidate_ids = {
        str(payload.get(key) or "").strip()
        for key in ("task_id", "parent_task_id", "pdd_id", "feature_id")
    }
    candidate_ids.add(str(result.get("task_id") or "").strip())
    return task_ids - {item for item in candidate_ids if item}


def plan_rejection_feedback(payload: dict[str, Any]) -> list[str]:
    """Preserve an OA revision delta as bounded synth feedback."""

    lines: list[str] = []
    reason = str(payload.get("reason") or "").strip()
    if reason:
        lines.append(f"plan-rejection: {reason}")
    reason_codes = payload.get("reason_codes")
    if isinstance(reason_codes, list):
        lines.extend(
            f"plan-rejection-code: {str(code).strip()}"
            for code in reason_codes
            if str(code or "").strip()
        )
    for directive_id, action in plan_rejection_required_actions(payload):
        lines.append(f"{directive_id}: {action}")
    return list(dict.fromkeys(lines))


def plan_rejection_required_actions(
    payload: dict[str, Any],
) -> list[tuple[str, str]]:
    """Return ordered owner directives from a plan rejection delta."""

    delta = payload.get("orchestration_delta")
    directives = delta.get("directives") if isinstance(delta, dict) else []
    if not isinstance(directives, list):
        return []
    out: list[tuple[str, str]] = []
    for directive in directives:
        if not isinstance(directive, dict):
            continue
        directive_id = str(directive.get("directive_id") or "revision").strip()
        required_actions = directive.get("required_actions")
        if not isinstance(required_actions, list):
            continue
        for action in required_actions:
            text = str(action or "").strip()
            item = (directive_id, text)
            if text and item not in out:
                out.append(item)
    return out


def gap_tasks_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    findings = payload.get("findings")
    if not isinstance(findings, list):
        report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
        findings = report.get("findings")
    out: list[dict[str, Any]] = []
    if isinstance(findings, list):
        for item in findings:
            if not isinstance(item, dict):
                continue
            gap_task = item.get("gap_task")
            if isinstance(gap_task, dict):
                out.append(dict(gap_task))
            gap_tasks = item.get("gap_tasks")
            if isinstance(gap_tasks, list):
                out.extend(dict(task) for task in gap_tasks if isinstance(task, dict))
    payload_gap_tasks = payload.get("gap_tasks")
    if isinstance(payload_gap_tasks, list):
        out.extend(dict(task) for task in payload_gap_tasks if isinstance(task, dict))
    return out


def dedupe_gap_tasks(gap_tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for task in gap_tasks:
        task_id = str(task.get("task_id") or task.get("id") or "").strip()
        key = task_id or repr(sorted(task.items()))
        if key in seen:
            continue
        seen.add(key)
        out.append(task)
    return out


__all__ = [
    "candidate_failure_task_ids",
    "dedupe_gap_tasks",
    "feedback_lines_from_payload",
    "gap_tasks_from_payload",
    "plan_rejection_feedback",
    "plan_rejection_required_actions",
]
