#!/usr/bin/env python3
"""Audit Product Flow context handoff and evidence closure."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from zf.core.cost.tracker import CostTracker
from zf.core.events.log import EventLog
from zf.runtime.orchestrator_agent_metrics import (
    build_orchestrator_agent_metrics,
)
from zf.runtime.sidecar_refs import (
    SidecarRefError,
    hydrate_sidecar_ref,
    verify_sidecar_ref,
)


SCHEMA_VERSION = "prod-flow-context-audit.v1"
PENDING_EXIT = 10
FAILED_EXIT = 20
_TERMINAL_TYPES = frozenset({"run.goal.completed", "run.goal.blocked"})
_HANDOFF_SCHEMAS = frozenset({
    "implementation-result.v1",
    "verification-result.v1",
})


def audit_product_flow_context(state_dir: Path) -> dict[str, Any]:
    state_dir = Path(state_dir).resolve()
    events = _read_events(state_dir / "events.jsonl")
    terminal = next(
        (
            event
            for event in reversed(events)
            if event.get("type") in _TERMINAL_TYPES
        ),
        None,
    )
    if terminal is None:
        return _result(status="pending", reasons=["run terminal not emitted"])

    terminal_payload = _payload(terminal)
    run_id = _run_id(terminal)
    if not run_id:
        return _result(
            status="failed",
            reasons=["terminal event has no run identity"],
        )
    relevant = [event for event in events if _belongs_to_run(event, run_id)]
    reasons: list[str] = []

    plan_event = _latest(relevant, "plan.artifact_package.admitted")
    plan_payload = _payload(plan_event)
    plan_ref = str(plan_payload.get("package_ref") or "")
    plan_digest = str(plan_payload.get("package_digest") or "")
    generation = str(plan_payload.get("task_map_generation") or "")
    plan_ok, plan_body = _hydrate(
        state_dir,
        {"ref": plan_ref, "sha256": plan_digest},
        reasons,
        label="plan artifact package",
    )
    produced = (
        plan_body.get("produced")
        if isinstance(plan_body, Mapping)
        and isinstance(plan_body.get("produced"), list)
        else []
    )
    produced_names = {
        str(item.get("logical_name") or "")
        for item in produced
        if isinstance(item, Mapping)
    }
    required_ports = (
        plan_body.get("required_ports")
        if isinstance(plan_body, Mapping)
        and isinstance(plan_body.get("required_ports"), list)
        else []
    )
    ports_closed = bool(required_ports) and all(
        str(name or "") in produced_names for name in required_ports
    )
    if plan_ok and not ports_closed:
        reasons.append("plan artifact package required ports are incomplete")

    task_map_event = _latest(relevant, "task_map.ready")
    task_map_payload = _payload(task_map_event)
    task_map_closed = bool(task_map_event) and all((
        str(task_map_payload.get("plan_artifact_package_ref") or "")
        == plan_ref,
        str(task_map_payload.get("plan_artifact_package_digest") or "")
        == plan_digest,
        str(task_map_payload.get("task_map_generation") or "")
        == generation,
    ))
    if not task_map_closed:
        reasons.append("task_map.ready does not bind the current Plan Package")

    contract_rows = _task_contract_rows(relevant)
    contract_failures: list[str] = []
    contract_bindings: list[dict[str, str]] = []
    for row in contract_rows:
        payload = _payload(row)
        task_id = str(row.get("task_id") or payload.get("task_id") or "")
        contract = payload.get("contract")
        contract = contract if isinstance(contract, Mapping) else {}
        contract_revision = str(contract.get("contract_revision") or "")
        snapshot_event = _typed_task_snapshot_event(
            relevant,
            task_id=task_id,
            plan_ref=plan_ref,
            plan_digest=plan_digest,
            generation=generation,
        )
        snapshot_payload = _payload(snapshot_event)
        snapshot_ref = str(
            snapshot_payload.get("task_contract_snapshot_ref") or ""
        )
        snapshot_digest = str(
            snapshot_payload.get("task_contract_snapshot_digest") or ""
        )
        snapshot_revision = str(snapshot_payload.get("contract_revision") or "")
        snapshot_ok, snapshot = _hydrate(
            state_dir,
            {
                "ref": snapshot_ref,
                "sha256": snapshot_digest,
            },
            contract_failures,
            label=f"task contract snapshot {task_id}",
        )
        evidence = contract.get("evidence_contract")
        evidence = evidence if isinstance(evidence, Mapping) else {}
        source_refs = evidence.get("source_refs")
        source_refs = source_refs if isinstance(source_refs, Mapping) else {}
        materialized_exact = all((
            str(source_refs.get("plan_artifact_package_ref") or "")
            == plan_ref,
            str(source_refs.get("plan_artifact_package_digest") or "")
            == plan_digest,
            str(source_refs.get("task_map_generation") or "") == generation,
        ))
        snapshot_exact = snapshot_ok and all((
            str(snapshot.get("schema_version") or "")
            == "task-contract-snapshot.v1",
            str(snapshot.get("task_id") or "") == task_id,
            bool(snapshot_revision),
            str(snapshot.get("contract_revision") or "") == snapshot_revision,
            str(snapshot.get("plan_artifact_package_ref") or "") == plan_ref,
            str(snapshot.get("plan_artifact_package_digest") or "")
            == plan_digest,
            str(snapshot.get("task_map_generation") or "") == generation,
        ))
        if not snapshot_ok or not materialized_exact or not snapshot_exact:
            contract_failures.append(
                f"task contract {task_id} is not bound to the current Plan Package"
            )
        contract_bindings.append({
            "task_id": task_id,
            "materialized_contract_revision": contract_revision,
            "snapshot_contract_revision": snapshot_revision,
            "snapshot_ref": snapshot_ref,
            "snapshot_digest": snapshot_digest,
        })
    contracts_closed = bool(contract_rows) and not contract_failures
    reasons.extend(contract_failures)

    operation_rows: list[dict[str, Any]] = []
    operation_failures: list[str] = []
    schema_counts: dict[str, int] = {}
    for event in relevant:
        if event.get("type") != "workflow.call.result.admitted":
            continue
        payload = _payload(event)
        operation_id = str(payload.get("operation_id") or "")
        schema = str(payload.get("control_result_schema") or "")
        schema_counts[schema] = schema_counts.get(schema, 0) + 1
        envelope_ok, envelope = _hydrate(
            state_dir,
            payload.get("envelope_ref"),
            operation_failures,
            label=f"call envelope {operation_id}",
        )
        control_ok = _verify(
            state_dir,
            payload.get("control_result_ref"),
            operation_failures,
            label=f"control result {operation_id}",
        )
        identity = (
            envelope.get("identity")
            if isinstance(envelope, Mapping)
            and isinstance(envelope.get("identity"), Mapping)
            else {}
        )
        consumption = (
            envelope.get("input_consumption")
            if isinstance(envelope, Mapping)
            and isinstance(envelope.get("input_consumption"), Mapping)
            else {}
        )
        reads_not_required = consumption.get("status") == "not_required"
        ledger_ok = reads_not_required or _verify(
            state_dir,
            payload.get("read_ledger_ref"),
            operation_failures,
            label=f"read ledger {operation_id}",
        )
        reads_closed = reads_not_required or (
            ledger_ok and consumption.get("status") == "satisfied"
        )
        exact_handoff: bool | None = None
        if schema in _HANDOFF_SCHEMAS:
            exact_handoff = all((
                str(identity.get("plan_artifact_package_ref") or "")
                == plan_ref,
                str(identity.get("plan_artifact_package_digest") or "")
                == plan_digest,
                str(identity.get("task_map_generation") or "") == generation,
                bool(identity.get("contract_snapshot_ref")),
                bool(identity.get("contract_snapshot_digest")),
            ))
            if not exact_handoff:
                operation_failures.append(
                    f"operation {operation_id} has shifted Plan/Task context"
                )
        if not reads_closed:
            operation_failures.append(
                f"operation {operation_id} did not close required reads"
            )
        operation_rows.append({
            "operation_id": operation_id,
            "schema_version": schema,
            "envelope_valid": envelope_ok,
            "control_result_valid": control_ok,
            "required_reads_closed": reads_closed,
            "exact_handoff": exact_handoff,
            "task_id": str(identity.get("task_id") or ""),
            "producer_stage_id": str(
                identity.get("producer_stage_id") or ""
            ),
        })
    reasons.extend(operation_failures)

    checkpoint_rows = [
        event
        for event in relevant
        if event.get("type")
        == "orchestrator.semantic.checkpoint.requested"
    ]
    skipped_rows = [
        event
        for event in relevant
        if event.get("type") == "orchestrator.semantic.checkpoint.skipped"
    ]
    stage_cards_closed = bool(checkpoint_rows or skipped_rows)
    for event in checkpoint_rows:
        operation_id = str(_payload(event).get("operation_id") or "")
        if not _verify(
            state_dir,
            _payload(event).get("stage_execution_card_ref"),
            reasons,
            label=f"OA stage execution card {operation_id}",
        ):
            stage_cards_closed = False

    evidence_refs = _terminal_evidence_refs(terminal_payload)
    checks = {
        "plan_artifact_package": plan_ok,
        "plan_required_ports": ports_closed,
        "task_map_binding": task_map_closed,
        "typed_task_contract_snapshots": contracts_closed,
        "required_read_ledgers": bool(operation_rows)
        and all(row["required_reads_closed"] for row in operation_rows),
        "impl_result": schema_counts.get("implementation-result.v1", 0) > 0,
        "verify_result": schema_counts.get("verification-result.v1", 0) > 0,
        "judge_result": schema_counts.get("goal-closure-result.v1", 0) > 0,
        "impl_verify_exact_handoff": bool(operation_rows)
        and all(
            row["exact_handoff"] is not False for row in operation_rows
        ),
        "oa_stage_card_or_explicit_skip": stage_cards_closed,
        "terminal_evidence_refs": bool(evidence_refs),
    }
    for name, passed in checks.items():
        if not passed:
            reasons.append(f"context closure check failed: {name}")

    usage = CostTracker(state_dir / "cost.jsonl").usage_totals()
    oa_metrics = build_orchestrator_agent_metrics(
        EventLog(state_dir / "events.jsonl").read_all()
    )
    terminal_type = str(terminal.get("type") or "")
    status = "passed" if all(checks.values()) else "failed"
    if terminal_type == "run.goal.blocked":
        status = "failed"
        reasons.append("workflow reached run.goal.blocked")
    return _result(
        status=status,
        reasons=reasons,
        run={
            "workflow_run_id": run_id,
            "flow_kind": str(terminal_payload.get("flow_kind") or ""),
            "terminal_event_id": str(terminal.get("id") or ""),
            "terminal_event_type": terminal_type,
            "task_map_generation": generation,
        },
        plan={
            "package_ref": plan_ref,
            "package_digest": plan_digest,
            "required_ports": list(required_ports),
            "produced_ports": sorted(produced_names),
        },
        checks=checks,
        task_contracts={
            "count": len(contract_rows),
            "task_ids": sorted({
                str(row.get("task_id") or _payload(row).get("task_id") or "")
                for row in contract_rows
            }),
            "bindings": contract_bindings,
        },
        operations=operation_rows,
        schema_counts=schema_counts,
        oa={
            "checkpoint_requested": len(checkpoint_rows),
            "checkpoint_skipped": len(skipped_rows),
            "metrics": oa_metrics["summary"],
            "checkpoint_metrics": oa_metrics["checkpoints"],
            "operations": oa_metrics["operations"],
        },
        usage=usage,
        evidence_refs=evidence_refs,
    )


def _result(
    *,
    status: str,
    reasons: list[str],
    **details: Any,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reasons": list(dict.fromkeys(reason for reason in reasons if reason)),
        **details,
    }


def _read_events(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _payload(event: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(event, Mapping):
        return {}
    value = event.get("payload")
    return value if isinstance(value, Mapping) else {}


def _run_id(event: Mapping[str, Any]) -> str:
    payload = _payload(event)
    return str(
        payload.get("workflow_run_id")
        or payload.get("run_id")
        or payload.get("request_id")
        or event.get("correlation_id")
        or ""
    ).strip()


def _belongs_to_run(event: Mapping[str, Any], run_id: str) -> bool:
    payload = _payload(event)
    contract = payload.get("contract")
    contract = contract if isinstance(contract, Mapping) else {}
    evidence = contract.get("evidence_contract")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    candidates = {
        str(event.get("correlation_id") or ""),
        str(payload.get("workflow_run_id") or ""),
        str(payload.get("run_id") or ""),
        str(payload.get("request_id") or ""),
        str(evidence.get("workflow_run_id") or ""),
    }
    return run_id in candidates


def _latest(
    events: list[dict[str, Any]],
    event_type: str,
) -> dict[str, Any] | None:
    return next(
        (event for event in reversed(events) if event.get("type") == event_type),
        None,
    )


def _task_contract_rows(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("type") != "task.contract.update":
            continue
        payload = _payload(event)
        task_id = str(event.get("task_id") or payload.get("task_id") or "")
        if task_id and payload.get("source") == "task_map_materialization":
            latest[task_id] = event
    return list(latest.values())


def _typed_task_snapshot_event(
    events: list[dict[str, Any]],
    *,
    task_id: str,
    plan_ref: str,
    plan_digest: str,
    generation: str,
) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("type") not in {
            "fanout.child.dispatched",
            "task.pipeline.stage.dispatched",
        }:
            continue
        payload = _payload(event)
        if str(payload.get("task_id") or event.get("task_id") or "") != task_id:
            continue
        if not all((
            str(payload.get("task_contract_snapshot_ref") or ""),
            str(payload.get("task_contract_snapshot_digest") or ""),
        )):
            continue
        if not all((
            str(payload.get("plan_artifact_package_ref") or "") == plan_ref,
            str(payload.get("plan_artifact_package_digest") or "")
            == plan_digest,
            str(payload.get("task_map_generation") or "") == generation,
        )):
            continue
        return event
    return None


def _hydrate(
    state_dir: Path,
    descriptor: Any,
    reasons: list[str],
    *,
    label: str,
) -> tuple[bool, Mapping[str, Any]]:
    if not isinstance(descriptor, Mapping):
        reasons.append(f"{label} descriptor is missing")
        return False, {}
    try:
        hydrated = hydrate_sidecar_ref(state_dir, dict(descriptor))
    except (OSError, SidecarRefError) as exc:
        reasons.append(f"{label} is invalid: {exc}")
        return False, {}
    payload = hydrated.payload
    return True, payload if isinstance(payload, Mapping) else {}


def _verify(
    state_dir: Path,
    descriptor: Any,
    reasons: list[str],
    *,
    label: str,
) -> bool:
    if not isinstance(descriptor, Mapping):
        reasons.append(f"{label} descriptor is missing")
        return False
    try:
        verify_sidecar_ref(state_dir, dict(descriptor))
    except (OSError, SidecarRefError) as exc:
        reasons.append(f"{label} is invalid: {exc}")
        return False
    return True


def _terminal_evidence_refs(payload: Mapping[str, Any]) -> list[str]:
    refs: list[str] = []
    coverage = payload.get("goal_coverage")
    if isinstance(coverage, list):
        for row in coverage:
            if not isinstance(row, Mapping):
                continue
            for ref in row.get("supporting_result_refs") or []:
                value = str(ref or "")
                if value:
                    refs.append(value)
    for key in ("result_refs", "evidence_refs"):
        for value in payload.get(key) or []:
            if isinstance(value, Mapping):
                value = value.get("ref")
            text = str(value or "")
            if text:
                refs.append(text)
    return list(dict.fromkeys(refs))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit_product_flow_context(args.state_dir)
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
