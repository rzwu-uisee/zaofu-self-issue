"""Mechanical consistency checks for terminal Goal Dossier delivery."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.sidecar_refs import SidecarRefError, sidecar_path


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
    if completed_ids and done_ids != completed_ids:
        issues.append({
            "code": "completed_task_set_mismatch",
            "expected": sorted(completed_ids),
            "actual": sorted(done_ids),
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
    readable_claim_source = _has_readable_claim_source(
        state_dir=state_dir,
        dossier=dossier,
        receipt=receipt or {},
    )
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

    gaps = dossier.get("gaps")
    if isinstance(gaps, list) and gaps:
        issues.append({
            "code": "completed_dossier_has_open_gaps",
            "gap_count": len(gaps),
        })

    receipt_gate = _mapping((receipt or {}).get("completion_gate"))
    expected_target = _first_text(
        terminal_payload.get("target_commit"),
        terminal_payload.get("candidate_head_commit"),
    )
    expected_verified_target = _first_text(
        terminal_payload.get("verified_target_commit"),
        expected_target,
    )
    _compare_identity(
        issues,
        field="target_commit",
        expected=expected_target,
        actuals={
            "receipt": receipt_gate.get("target_commit"),
            "terminal_verified": expected_verified_target,
            "receipt_verified": receipt_gate.get("verified_target_commit"),
        },
    )
    return issues


def _has_readable_claim_source(
    *,
    state_dir: Path,
    dossier: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> bool:
    roadmap = _mapping(dossier.get("roadmap"))
    refs = [
        str(item) for item in roadmap.get("task_map_refs", []) or []
        if str(item).strip()
    ]
    goal_closure = _mapping(receipt.get("goal_closure"))
    claim_ref = str(goal_closure.get("goal_claim_set_ref") or "")
    if claim_ref:
        refs.append(claim_ref)
    for ref in refs:
        try:
            if sidecar_path(state_dir, ref).is_file():
                return True
        except SidecarRefError:
            continue
    return False


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


__all__ = ["terminal_goal_dossier_issues"]
