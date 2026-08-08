#!/usr/bin/env python3
"""Audit Product Flow terminal delivery closure for real-provider E2E."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "prod-flow-terminal-delivery-audit.v1"
PENDING_EXIT = 10
FAILED_EXIT = 20
_TERMINAL_TYPES = frozenset({"run.goal.completed", "run.goal.blocked"})


def audit_terminal_delivery(
    state_dir: Path,
    *,
    fail_on_human_escalate: bool = False,
    fail_on_repeated_child_failure: bool = False,
) -> dict[str, Any]:
    state_dir = Path(state_dir).resolve()
    events = _read_events(state_dir / "events.jsonl")
    terminal = next(
        (event for event in reversed(events) if event.get("type") in _TERMINAL_TYPES),
        None,
    )
    if terminal is None:
        escalation = next(
            (
                event
                for event in reversed(events)
                if event.get("type") == "human.escalate"
            ),
            None,
        )
        if fail_on_human_escalate and escalation is not None:
            payload = escalation.get("payload")
            payload = payload if isinstance(payload, Mapping) else {}
            reason = str(payload.get("reason") or "operator intervention required")
            return _result(
                status="failed",
                reason=f"unattended workflow escalated: {reason}",
                escalation={
                    "event_id": str(escalation.get("id") or ""),
                    "reason": reason,
                    "failure_class": str(payload.get("failure_class") or ""),
                },
            )
        if fail_on_repeated_child_failure:
            repeated = _repeated_child_failure(events)
            if repeated:
                return _result(
                    status="failed",
                    reason=(
                        "unattended workflow repeated a child failure "
                        f"without progress: {repeated['reason']}"
                    ),
                    failure_signal=repeated,
                )
        return _result(status="pending", reason="run terminal not emitted")

    payload = terminal.get("payload")
    payload = payload if isinstance(payload, Mapping) else {}
    terminal_id = str(terminal.get("id") or "")
    terminal_type = str(terminal.get("type") or "")
    run_id = str(
        payload.get("workflow_run_id")
        or payload.get("run_id")
        or payload.get("request_id")
        or terminal.get("correlation_id")
        or ""
    ).strip()
    terminal_summary = {
        "event_id": terminal_id,
        "event_type": terminal_type,
        "run_id": run_id,
    }
    if not run_id:
        return _result(
            status="failed",
            reason="terminal event has no run identity",
            terminal=terminal_summary,
        )

    projection_dir = (
        state_dir / "projections" / "goals" / _safe_segment(run_id)
    )
    materialization_path = projection_dir / "delivery-materialization.v1.json"
    materialization = _read_object(materialization_path)
    if not materialization:
        return _result(
            status="pending",
            reason="terminal delivery materialization not written",
            terminal=terminal_summary,
        )

    deliveries = materialization.get("deliveries")
    delivery = (
        deliveries.get(terminal_id)
        if isinstance(deliveries, Mapping)
        and isinstance(deliveries.get(terminal_id), Mapping)
        else materialization
    )
    delivery_status = str(delivery.get("status") or "")
    delivery_summary = {
        "status": delivery_status,
        "reason": str(delivery.get("reason") or ""),
        "message_id": str(delivery.get("message_id") or ""),
        "materialization_ref": materialization_path.relative_to(state_dir).as_posix(),
        "dossier_ref": str(delivery.get("dossier_ref") or ""),
        "completion_receipt_ref": str(
            delivery.get("completion_receipt_ref") or ""
        ),
    }
    if str(delivery.get("terminal_event_id") or "") != terminal_id:
        return _result(
            status="pending",
            reason="materialization belongs to an earlier terminal",
            terminal=terminal_summary,
            delivery=delivery_summary,
        )
    if delivery_status in {"failed", "inconsistent"}:
        return _result(
            status="failed",
            reason=f"terminal delivery is {delivery_status}",
            terminal=terminal_summary,
            delivery=delivery_summary,
        )
    if delivery_status != "delivered_requested":
        return _result(
            status="pending",
            reason=f"terminal delivery is {delivery_status or 'not ready'}",
            terminal=terminal_summary,
            delivery=delivery_summary,
        )

    dossier_path = _resolve_state_ref(state_dir, delivery_summary["dossier_ref"])
    dossier = _read_object(dossier_path)
    checks = {
        "dossier_exists": bool(dossier),
        "dossier_ready": (
            isinstance(dossier.get("delivery_readiness"), Mapping)
            and dossier["delivery_readiness"].get("status") == "ready"
        ),
        "owner_message_requested": _has_owner_request(
            events,
            message_id=delivery_summary["message_id"],
            terminal_id=terminal_id,
        ),
        "completion_receipt_exists": False,
        "completion_receipt_matches_terminal": False,
    }
    if terminal_type == "run.goal.completed":
        receipt_path = _resolve_state_ref(
            state_dir,
            delivery_summary["completion_receipt_ref"],
        )
        receipt = _read_object(receipt_path)
        receipt_terminal = (
            receipt.get("terminal")
            if isinstance(receipt.get("terminal"), Mapping)
            else {}
        )
        checks["completion_receipt_exists"] = (
            receipt.get("schema_version") == "goal-completion-receipt.v1"
        )
        checks["completion_receipt_matches_terminal"] = (
            receipt_terminal.get("event_id") == terminal_id
            and receipt_terminal.get("event_type") == terminal_type
        )
    else:
        checks["completion_receipt_exists"] = True
        checks["completion_receipt_matches_terminal"] = True

    failed_checks = [name for name, passed in checks.items() if not passed]
    if failed_checks:
        return _result(
            status="failed",
            reason="terminal delivery checks failed: " + ", ".join(failed_checks),
            terminal=terminal_summary,
            delivery=delivery_summary,
            checks=checks,
        )
    if terminal_type == "run.goal.blocked":
        return _result(
            status="failed",
            reason="workflow reached run.goal.blocked",
            terminal=terminal_summary,
            delivery=delivery_summary,
            checks=checks,
        )
    return _result(
        status="passed",
        reason="completed terminal has consistent owner delivery closure",
        terminal=terminal_summary,
        delivery=delivery_summary,
        checks=checks,
    )


def _result(
    *,
    status: str,
    reason: str,
    terminal: Mapping[str, Any] | None = None,
    delivery: Mapping[str, Any] | None = None,
    checks: Mapping[str, bool] | None = None,
    escalation: Mapping[str, Any] | None = None,
    failure_signal: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "terminal": dict(terminal or {}),
        "delivery": dict(delivery or {}),
        "checks": dict(checks or {}),
        "escalation": dict(escalation or {}),
        "failure_signal": dict(failure_signal or {}),
    }


def _repeated_child_failure(
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    seen: dict[tuple[str, str], list[str]] = {}
    for event in events:
        if event.get("type") != "fanout.child.failed":
            continue
        payload = event.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        task_id = str(payload.get("task_id") or event.get("task_id") or "")
        reason = str(payload.get("reason") or "").strip()
        if not task_id or not reason:
            continue
        event_ids = seen.setdefault((task_id, reason), [])
        event_ids.append(str(event.get("id") or ""))
        if len(event_ids) >= 2:
            return {
                "kind": "repeated_child_failure",
                "task_id": task_id,
                "reason": reason,
                "count": len(event_ids),
                "event_ids": event_ids,
            }
    return {}


def _read_events(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _read_object(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _resolve_state_ref(state_dir: Path, ref: str) -> Path | None:
    if not ref:
        return None
    candidate = Path(ref)
    if not candidate.is_absolute():
        candidate = state_dir / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(state_dir)
    except (OSError, ValueError):
        return None
    return resolved


def _has_owner_request(
    events: list[dict[str, Any]],
    *,
    message_id: str,
    terminal_id: str,
) -> bool:
    if not message_id:
        return False
    return any(
        event.get("type") == "owner.visible_message.requested"
        and isinstance(event.get("payload"), Mapping)
        and event["payload"].get("message_id") == message_id
        and event["payload"].get("terminal_event_id") == terminal_id
        for event in events
    )


def _safe_segment(value: str) -> str:
    text = "".join(
        character if character.isalnum() or character in "._-" else "-"
        for character in str(value or "unscoped")
    ).strip(".-")
    return text or "unscoped"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fail-on-human-escalate", action="store_true")
    parser.add_argument(
        "--fail-on-repeated-child-failure",
        action="store_true",
    )
    args = parser.parse_args()
    result = audit_terminal_delivery(
        args.state_dir,
        fail_on_human_escalate=args.fail_on_human_escalate,
        fail_on_repeated_child_failure=args.fail_on_repeated_child_failure,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if result["status"] == "passed":
        return 0
    if result["status"] == "pending":
        return PENDING_EXIT
    return FAILED_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
