"""Pure normalization helpers for versioned Workflow requirement specs."""

from __future__ import annotations

from typing import Any

from zf.runtime.workflow_request_io import now_iso, strings


def build_requirement_spec(
    manifest: dict[str, Any],
    intake: dict[str, Any],
    *,
    revision: int,
    confirmed: bool,
) -> dict[str, Any]:
    return normalize_requirement_spec(
        {
            "schema_version": "requirement-spec.v1",
            "request_id": str(manifest.get("request_id") or ""),
            "project_id": str(manifest.get("project_id") or ""),
            "kind": str(
                manifest.get("kind") or intake.get("effective_kind") or "issue"
            ),
            "revision": revision,
            "objective": str(
                manifest.get("objective") or intake.get("objective") or ""
            ),
            "source_ref": str(manifest.get("source_ref") or ""),
            "source_refs": dict(manifest.get("source_refs") or {}),
            "artifact_refs": list(manifest.get("artifact_refs") or []),
            "task_input_binding": dict(
                manifest.get("task_input_binding") or {}
            ),
            "task_input_contract_ref": str(
                manifest.get("task_input_contract_ref") or ""
            ),
            "task_input_contract_digest": str(
                manifest.get("task_input_contract_digest") or ""
            ),
            "source_root": str(
                manifest.get("source_root") or intake.get("source_root") or ""
            ),
            "target_root": str(
                manifest.get("target_root") or intake.get("target_root") or ""
            ),
            "scope": strings(intake.get("scope") or manifest.get("scope")),
            "acceptance": strings(
                intake.get("acceptance") or manifest.get("acceptance")
            ),
            "constraints": strings(
                intake.get("constraints") or manifest.get("constraints")
            ),
            "open_questions": strings(
                intake.get("open_questions") or manifest.get("open_questions")
            ),
            "clarification_answers": normalize_clarification_answers(
                intake.get("clarification_answers")
                or manifest.get("clarification_answers")
            ),
            "confirmed": confirmed,
            "created_at": str(manifest.get("created_at") or now_iso()),
            "updated_at": now_iso(),
        }
    )


def normalize_requirement_spec(spec: dict[str, Any]) -> dict[str, Any]:
    out = dict(spec)
    for key in ("acceptance", "constraints", "open_questions", "scope"):
        out[key] = strings(out.get(key))
    out["clarification_answers"] = normalize_clarification_answers(
        out.get("clarification_answers")
    )
    return out


def normalize_clarification_answers(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    answers: list[dict[str, str]] = []
    positions: dict[str, int] = {}
    for raw in value:
        if not isinstance(raw, dict):
            continue
        question = str(raw.get("question") or "").strip()
        answer = str(raw.get("answer") or "").strip()
        if not question or not answer:
            continue
        item = {"question": question, "answer": answer}
        if question in positions:
            answers[positions[question]] = item
        elif len(answers) < 32:
            positions[question] = len(answers)
            answers.append(item)
    return answers


def merge_clarification_answers(
    current: object,
    updates: object,
) -> list[dict[str, str]]:
    return normalize_clarification_answers(
        [
            *normalize_clarification_answers(current),
            *normalize_clarification_answers(updates),
        ]
    )


__all__ = [
    "build_requirement_spec",
    "merge_clarification_answers",
    "normalize_clarification_answers",
    "normalize_requirement_spec",
]
