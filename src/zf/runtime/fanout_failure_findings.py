"""Structured failure findings collected from fanout evidence."""

from __future__ import annotations

from typing import Any

from zf.runtime.candidate_rework import candidate_quality_failure_message


def fanout_failure_findings(
    owner: Any,
    manifest: dict[str, Any],
    *,
    extra_payloads: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    child_payloads = {
        str(payload.get("child_id") or ""): payload
        for payload in owner._fanout_child_payloads(manifest)
        if isinstance(payload, dict)
    }
    payloads: list[dict[str, Any]] = []
    for child in manifest.get("children", []) or []:
        if not isinstance(child, dict) or str(child.get("status") or "") != "failed":
            continue
        child_id = str(child.get("child_id") or "")
        enriched = dict(child)
        enriched.update(child_payloads.get(child_id, {}))
        payloads.append(enriched)
    payloads.extend(
        payload for payload in (extra_payloads or []) if isinstance(payload, dict)
    )
    try:
        events = owner.event_log.read_all()
    except Exception:
        events = []
    fanout_id = str(manifest.get("fanout_id") or "")
    for event in events:
        if event.type != "fanout.child.failed":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("fanout_id") or "") == fanout_id:
            payloads.append(payload)

    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for payload in payloads:
        for finding in findings_from_payload(payload):
            key = (
                str(finding.get("child_id") or ""),
                str(finding.get("task_id") or ""),
                str(finding.get("message") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            findings.append(finding)
    return findings


def findings_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_findings = payload.get("findings")
    report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
    if not isinstance(raw_findings, list):
        raw_findings = report.get("findings")
    if not isinstance(raw_findings, list):
        raw_findings = payload.get("blocked_rework_findings")
    if not isinstance(raw_findings, list):
        raw_findings = []

    out: list[dict[str, Any]] = []
    child_id = str(payload.get("child_id") or "")
    task_id = str(payload.get("task_id") or "")
    for index, raw in enumerate(raw_findings):
        if isinstance(raw, dict):
            message = str(
                raw.get("message")
                or raw.get("summary")
                or raw.get("title")
                or raw.get("reason")
                or ""
            ).strip()
            if not message:
                continue
            item = dict(raw)
            item.setdefault("finding_id", f"{child_id or task_id or 'child'}-{index + 1}")
            item.setdefault("severity", "high")
            item.setdefault("category", "verification")
            item.setdefault("child_id", child_id)
            item.setdefault("task_id", task_id)
            item["message"] = message
            out.append(item)
            continue
        message = str(raw).strip()
        if message:
            out.append({
                "finding_id": f"{child_id or task_id or 'child'}-{index + 1}",
                "severity": "high",
                "category": "verification",
                "child_id": child_id,
                "task_id": task_id,
                "message": message,
            })
    if out:
        return out
    reason = str(
        payload.get("reason")
        or payload.get("failure_reason")
        or payload.get("summary")
        or report.get("summary")
        or ""
    ).strip()
    if not reason:
        return []
    return [{
        "finding_id": f"{child_id or task_id or 'child'}-reason",
        "severity": "high",
        "category": "runtime_failure",
        "child_id": child_id,
        "task_id": task_id,
        "message": reason,
    }]


def candidate_failure_findings(
    candidate_payload: dict[str, Any],
    *,
    status: str,
    failed_children: list[str],
) -> list[dict[str, Any]]:
    if status not in {"conflict", "quality_failed", "stale"} and not failed_children:
        return []
    findings: list[dict[str, Any]] = []
    for item in candidate_payload.get("stale_tasks") or []:
        if not isinstance(item, dict):
            continue
        findings.append({
            "finding_id": f"{item.get('task_id') or 'task'}-stale-task-ref",
            "severity": "high",
            "category": "stale_task_ref",
            "task_id": str(item.get("task_id") or ""),
            "message": (
                "candidate task ref is stale: "
                + str(item.get("reason") or "task_index_mismatch")
            ),
        })
    if status == "conflict":
        findings.append({
            "finding_id": "candidate-conflict",
            "severity": "high",
            "category": "candidate_integration",
            "message": str(candidate_payload.get("error") or "candidate conflict"),
            "files_or_scope": list(candidate_payload.get("conflict_files") or []),
        })
    if status == "quality_failed":
        quality = (
            candidate_payload.get("quality")
            if isinstance(candidate_payload.get("quality"), dict)
            else {}
        )
        findings.append({
            "finding_id": "candidate-quality-failed",
            "severity": "high",
            "category": "candidate_quality",
            "message": candidate_quality_failure_message(quality),
            "verification_command": "; ".join(
                str(command)
                for commands in (quality.get("failure_details") or {}).values()
                for command in (commands if isinstance(commands, list) else [])
            ),
            "evidence_refs": list(quality.get("gates_failed") or []),
        })
    for child in failed_children:
        if child.startswith("candidate:"):
            findings.append({
                "finding_id": "candidate-exception",
                "severity": "high",
                "category": "candidate_integration",
                "message": child,
            })
    return findings


__all__ = [
    "candidate_failure_findings",
    "fanout_failure_findings",
    "findings_from_payload",
]
