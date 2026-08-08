"""Compatibility normalization for Plan critic feedback."""

from __future__ import annotations

from typing import Any, Mapping


def normalize_plan_synth_findings(value: Any) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, Mapping):
            findings.append(dict(item))
            continue
        message = str(item or "").strip()
        if message:
            findings.append({
                "severity": "high",
                "category": "plan-review",
                "path": "",
                "message": message,
            })
    for finding in findings:
        if str(finding.get("line", "")).strip() == "0":
            finding.pop("line", None)
    return findings


def normalize_plan_synth_fix_items(value: Any) -> list[dict[str, Any]]:
    fixes: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, Mapping):
            fixes.append(dict(item))
            continue
        message = str(item or "").strip()
        if message:
            fixes.append({
                "severity": "high",
                "category": "plan-fix-item",
                "message": message,
                "required_change": message,
            })
    return fixes


def normalize_plan_synth_owner_decision_items(
    value: Any,
) -> list[dict[str, Any]]:
    """Normalize explicit owner questions without interpreting prose."""

    decisions: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, Mapping):
            continue
        decision_id = str(
            item.get("decision_id") or item.get("id") or ""
        ).strip()
        question = str(item.get("question") or "").strip()
        if not decision_id or not question:
            continue
        options = [
            dict(option)
            for option in item.get("options", [])
            if isinstance(option, Mapping)
            and str(option.get("option_id") or option.get("id") or "").strip()
            and str(option.get("label") or "").strip()
        ]
        decisions.append({
            **dict(item),
            "decision_id": decision_id,
            "question": question,
            "options": options,
            "blocking": item.get("blocking") is not False,
            "evidence_refs": [
                str(ref)
                for ref in item.get("evidence_refs", [])
                if str(ref).strip()
            ],
        })
    return decisions


__all__ = [
    "normalize_plan_synth_findings",
    "normalize_plan_synth_fix_items",
    "normalize_plan_synth_owner_decision_items",
]
