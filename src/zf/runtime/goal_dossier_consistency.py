"""Mechanical consistency checks for terminal Goal Dossier delivery."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.sidecar_refs import SidecarRefError, sidecar_path


def evaluate_goal_dossier_delivery_readiness(
    *,
    state_dir: Path,
    dossier: Mapping[str, Any],
    terminal: ZfEvent | None,
    receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one mechanical delivery verdict shared by every surface."""

    if terminal is None:
        return {
            "schema_version": "goal-dossier-delivery-readiness.v1",
            "status": "incomplete",
            "terminal_status": "",
            "issues": [{
                "code": "run_not_terminal",
                "expected": "run.goal.completed or run.goal.blocked",
            }],
            "source_snapshot": {
                "status": "not_required",
                "sources": [],
            },
        }

    terminal_status = (
        "completed" if terminal.type == "run.goal.completed" else "blocked"
    )
    issues = terminal_goal_dossier_issues(
        state_dir=state_dir,
        dossier=dossier,
        receipt=receipt,
        terminal=terminal,
    )
    source_snapshot = _claim_source_snapshot(
        state_dir=state_dir,
        dossier=dossier,
        receipt=receipt or {},
        terminal=terminal,
    )
    freshness = _mapping(dossier.get("freshness"))
    freshness_status = str(freshness.get("status") or "unknown")
    source_issues: list[dict[str, Any]] = []
    claim_source_ready = source_snapshot["status"] == "ready"
    if claim_source_ready and freshness_status != "ready":
        source_issues.append({
            "code": "dossier_source_not_ready",
            "status": freshness_status,
            "diagnostics": list(freshness.get("diagnostics") or []),
        })
    if (
        terminal_status == "completed"
        and claim_source_ready
        and freshness_status == "ready"
        and receipt is None
    ):
        source_issues.append({
            "code": "completion_receipt_unavailable",
        })
    if terminal_status == "blocked":
        status = "blocked"
    elif not claim_source_ready or source_issues:
        status = "unknown"
        issues = [
            *source_issues,
            {
                "code": "claim_source_unreadable",
                "status": source_snapshot["status"],
                "sources": source_snapshot["sources"],
            } if not claim_source_ready else {},
            *issues,
        ]
        issues = [issue for issue in issues if issue]
    elif issues:
        status = "incomplete"
    else:
        status = "ready"
    return {
        "schema_version": "goal-dossier-delivery-readiness.v1",
        "status": status,
        "terminal_status": terminal_status,
        "issues": _dedupe_issues(issues),
        "source_snapshot": source_snapshot,
    }


def terminal_goal_dossier_issues(
    *,
    state_dir: Path,
    dossier: Mapping[str, Any],
    receipt: Mapping[str, Any] | None,
    terminal: ZfEvent,
) -> list[dict[str, Any]]:
    """Compare one read-side Dossier with immutable terminal truth."""

    terminal_payload = (
        terminal.payload if isinstance(terminal.payload, Mapping) else {}
    )
    issues: list[dict[str, Any]] = []
    expected_run_id = _first_text(
        terminal_payload.get("workflow_run_id"),
        terminal_payload.get("run_id"),
        terminal.correlation_id,
    )
    expected_goal_id = _first_text(
        terminal_payload.get("goal_id"),
        terminal_payload.get("pdd_id"),
        terminal_payload.get("feature_id"),
    )
    _compare_identity(
        issues,
        field="workflow_run_id",
        expected=expected_run_id,
        actuals={
            "dossier": dossier.get("run_id"),
            **({
                "receipt": receipt.get("workflow_run_id"),
            } if receipt is not None else {}),
        },
    )
    _compare_identity(
        issues,
        field="goal_id",
        expected=expected_goal_id,
        actuals={
            "dossier": dossier.get("goal_id"),
            **({
                "receipt": receipt.get("goal_id"),
            } if receipt is not None else {}),
        },
    )

    terminal_projection = _mapping(dossier.get("terminal"))
    expected_status = (
        "completed" if terminal.type == "run.goal.completed" else "blocked"
    )
    if str(terminal_projection.get("status") or "") != expected_status:
        issues.append({
            "code": "terminal_status_mismatch",
            "expected": expected_status,
            "actual": str(terminal_projection.get("status") or ""),
        })
    if terminal.type != "run.goal.completed":
        return issues

    state = _mapping(dossier.get("state"))
    counts = _mapping(state.get("task_counts"))
    tasks = [
        item for item in state.get("tasks", [])
        if isinstance(item, Mapping)
    ]
    done_ids = {
        str(item.get("id") or "")
        for item in tasks
        if str(item.get("status") or "") == "done"
        and str(item.get("id") or "")
    }
    completed_ids = {
        str(item)
        for item in terminal_payload.get("completed_task_ids", []) or []
        if str(item).strip()
    }
    missing_completed_ids = completed_ids - done_ids
    if missing_completed_ids:
        issues.append({
            "code": "completed_task_set_mismatch",
            "expected": sorted(completed_ids),
            "actual": sorted(done_ids),
            "missing": sorted(missing_completed_ids),
        })
    if (
        int(counts.get("total") or 0) != len(tasks)
        or int(counts.get("terminal") or 0) != len(done_ids)
        or int(counts.get("open") or 0) != 0
    ):
        issues.append({
            "code": "completed_task_counts_inconsistent",
            "task_count": len(tasks),
            "done_count": len(done_ids),
            "reported": {
                "total": int(counts.get("total") or 0),
                "terminal": int(counts.get("terminal") or 0),
                "open": int(counts.get("open") or 0),
            },
        })

    claim_matrix = _mapping(dossier.get("claim_to_evidence"))
    claim_summary = _mapping(claim_matrix.get("summary"))
    claim_rows = [
        item for item in claim_matrix.get("rows", [])
        if isinstance(item, Mapping)
    ]
    mandatory_rows = [
        item for item in claim_rows if bool(item.get("mandatory", True))
    ]
    closed_rows = [
        item for item in mandatory_rows
        if str(item.get("verdict") or "") == "closed"
    ]
    mandatory_count = int(claim_summary.get("mandatory_claims") or 0)
    closed_count = int(claim_summary.get("closed_claims") or 0)
    open_gap_count = int(claim_summary.get("open_gaps") or 0)
    readable_claim_source = _claim_source_snapshot(
        state_dir=state_dir,
        dossier=dossier,
        receipt=receipt or {},
        terminal=terminal,
    )["status"] == "ready"
    if readable_claim_source and mandatory_rows and (
        mandatory_count != len(mandatory_rows)
        or closed_count != len(closed_rows)
        or closed_count != mandatory_count
        or open_gap_count
    ):
        issues.append({
            "code": "claim_summary_inconsistent",
            "mandatory_rows": len(mandatory_rows),
            "closed_rows": len(closed_rows),
            "reported": {
                "mandatory_claims": mandatory_count,
                "closed_claims": closed_count,
                "open_gaps": open_gap_count,
            },
        })
    if not claim_rows and readable_claim_source:
        issues.append({
            "code": "claim_matrix_missing",
            "expected": "claim rows from readable planning/claim-set artifacts",
            "actual": 0,
        })
    open_mandatory_claims = [
        str(item.get("goal_claim_id") or "")
        for item in mandatory_rows
        if str(item.get("verdict") or "") != "closed"
    ]
    if readable_claim_source and open_mandatory_claims:
        issues.append({
            "code": "mandatory_claims_open",
            "goal_claim_ids": open_mandatory_claims,
        })

    gaps = dossier.get("gaps")
    if isinstance(gaps, list) and gaps:
        issues.append({
            "code": "completed_dossier_has_open_gaps",
            "gap_count": len(gaps),
        })

    if str(terminal_payload.get("completion_profile") or "") == (
        "artifact_delivery"
    ):
        delivery = _mapping(dossier.get("artifact_delivery"))
        if str(delivery.get("status") or "") != "ready":
            issues.append({
                "code": "artifact_delivery_projection_not_ready",
                "status": str(delivery.get("status") or ""),
            })
        expected_artifacts = [
            item
            for item in terminal_payload.get("required_artifacts") or []
            if isinstance(item, Mapping)
        ]
        actual_artifacts = [
            item
            for item in delivery.get("required_artifacts") or []
            if isinstance(item, Mapping)
        ]
        expected_identity = sorted(
            (
                str(item.get("name") or ""),
                str(item.get("kind") or ""),
                str(item.get("source_ref") or ""),
                str(item.get("ref") or ""),
                str(item.get("sha256") or ""),
            )
            for item in expected_artifacts
        )
        actual_identity = sorted(
            (
                str(item.get("name") or ""),
                str(item.get("kind") or ""),
                str(item.get("source_ref") or ""),
                str(item.get("ref") or ""),
                str(item.get("sha256") or ""),
            )
            for item in actual_artifacts
        )
        if actual_identity != expected_identity:
            issues.append({
                "code": "artifact_delivery_set_mismatch",
                "expected": expected_identity,
                "actual": actual_identity,
            })
        for item in expected_artifacts:
            ref = str(item.get("ref") or "")
            expected_digest = str(item.get("sha256") or "")
            try:
                path = sidecar_path(state_dir, ref)
                actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except (OSError, SidecarRefError):
                actual_digest = ""
            if not actual_digest:
                issues.append({
                    "code": "delivery_artifact_unreadable",
                    "ref": ref,
                })
            elif actual_digest != expected_digest:
                issues.append({
                    "code": "delivery_artifact_digest_mismatch",
                    "ref": ref,
                    "expected": expected_digest,
                    "actual": actual_digest,
                })
        return issues

    receipt_gate = _mapping((receipt or {}).get("completion_gate"))
    expected_target = _first_text(
        terminal_payload.get("target_commit"),
        terminal_payload.get("candidate_head_commit"),
    )
    expected_verified_target = _first_text(
        terminal_payload.get("verified_target_commit"),
        expected_target,
    )
    target_actuals = {
        "terminal_verified": expected_verified_target,
    }
    if receipt is not None:
        target_actuals.update({
            "receipt": receipt_gate.get("target_commit"),
            "receipt_verified": receipt_gate.get("verified_target_commit"),
        })
    _compare_identity(
        issues,
        field="target_commit",
        expected=expected_target,
        actuals=target_actuals,
    )
    return issues


def _claim_source_snapshot(
    *,
    state_dir: Path,
    dossier: Mapping[str, Any],
    receipt: Mapping[str, Any],
    terminal: ZfEvent,
) -> dict[str, Any]:
    terminal_payload = (
        terminal.payload if isinstance(terminal.payload, Mapping) else {}
    )
    roadmap = _mapping(dossier.get("roadmap"))
    goal_closure = _mapping(receipt.get("goal_closure"))
    declared_claim_ref = _first_text(
        terminal_payload.get("goal_claim_set_ref"),
        goal_closure.get("goal_claim_set_ref"),
    )
    declared_claim_digest = _first_text(
        terminal_payload.get("goal_claim_set_digest"),
        goal_closure.get("goal_claim_set_digest"),
    )
    candidates: list[dict[str, str]] = []
    if declared_claim_ref:
        candidates.append({
            "kind": "goal_claim_set",
            "ref": declared_claim_ref,
            "sha256": declared_claim_digest,
            "source": "run_terminal",
        })
    else:
        candidates.extend({
            "kind": "goal_claim_set",
            "ref": str(ref),
            "sha256": "",
            "source": "dossier_roadmap",
        } for ref in roadmap.get("goal_claim_refs", []) or [] if str(ref))
        if not candidates:
            candidates.extend({
                "kind": "task_map",
                "ref": str(ref),
                "sha256": "",
                "source": "historical_fallback",
            } for ref in roadmap.get("task_map_refs", []) or [] if str(ref))

    snapshots: list[dict[str, Any]] = []
    for candidate in candidates:
        snapshot: dict[str, Any] = {
            **candidate,
            "status": "missing",
            "actual_sha256": "",
        }
        try:
            path = sidecar_path(state_dir, candidate["ref"])
            if path.is_symlink() or not path.is_file():
                snapshots.append(snapshot)
                continue
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            snapshot["actual_sha256"] = actual
            expected = candidate["sha256"]
            snapshot["status"] = (
                "digest_mismatch"
                if expected and expected != actual
                else "ready"
            )
        except (OSError, SidecarRefError):
            snapshot["status"] = "unreadable"
        snapshots.append(snapshot)

    status = (
        "missing"
        if not snapshots
        else "ready"
        if all(item["status"] == "ready" for item in snapshots)
        else "unreadable"
    )
    return {
        "status": status,
        "sources": snapshots,
    }


def _dedupe_issues(
    issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for issue in issues:
        code = str(issue.get("code") or "")
        if code in seen:
            continue
        seen.add(code)
        out.append(issue)
    return out


def _compare_identity(
    issues: list[dict[str, Any]],
    *,
    field: str,
    expected: str,
    actuals: Mapping[str, Any],
) -> None:
    if not expected:
        return
    mismatches = {
        source: str(value or "")
        for source, value in actuals.items()
        if str(value or "") != expected
    }
    if mismatches:
        issues.append({
            "code": f"{field}_mismatch",
            "expected": expected,
            "actuals": mismatches,
        })


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_text(*values: Any) -> str:
    return next(
        (str(value) for value in values if value not in (None, "")),
        "",
    )


__all__ = [
    "evaluate_goal_dossier_delivery_readiness",
    "terminal_goal_dossier_issues",
]
