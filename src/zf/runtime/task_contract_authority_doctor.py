"""Read-only consistency diagnostics for canonical Task contract authority."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.core.task.authority import authority_revision_for
from zf.core.task.store import TaskStore
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_contract_authority import task_execution_binding


def build_task_contract_authority_report(
    state_dir: Path,
    events: list[ZfEvent],
) -> dict[str, Any]:
    """Report receipt, Store lineage, and compatibility binding drift."""

    state_dir = Path(state_dir)
    store = TaskStore(state_dir / "kanban.json")
    records = store.list_all_with_archive()
    tasks = [
        current
        for task_id in dict.fromkeys(task.id for task in records)
        if (current := store.get(task_id)) is not None
    ]
    prepared: dict[tuple[str, str], ZfEvent] = {}
    terminal_receipts: set[tuple[str, str]] = set()
    applied_revisions: set[tuple[str, str]] = set()
    descriptors: dict[tuple[str, str], dict[str, str]] = {}
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for event in events:
        body = event.payload if isinstance(event.payload, dict) else {}
        task_id = str(event.task_id or body.get("task_id") or "")
        revision = str(body.get("contract_authority_revision") or "")
        ref = str(body.get("contract_mutation_ref") or "")
        digest = str(body.get("contract_mutation_digest") or "")
        key = (task_id, revision)
        if event.type == "task.contract.mutation.prepared" and all(key):
            prepared[key] = event
        if event.type in {
            "task.contract.revision.applied",
            "task.contract.change.rejected",
        } and all(key):
            terminal_receipts.add(key)
        if event.type == "task.contract.revision.applied" and all(key):
            applied_revisions.add(key)
        if ref and digest:
            descriptors[(ref, digest)] = {
                "task_id": task_id,
                "authority_revision": revision,
            }

    for key, event in prepared.items():
        if key not in terminal_receipts:
            issues.append(_issue(
                "prepared_without_terminal_receipt",
                task_id=key[0],
                authority_revision=key[1],
                event_id=event.id,
            ))

    for (ref, digest), expected in descriptors.items():
        try:
            hydrated = hydrate_sidecar_ref(
                state_dir,
                {"ref": ref, "sha256": digest},
            )
            payload = hydrated.payload if isinstance(hydrated.payload, dict) else {}
            if (
                str(payload.get("schema_version") or "")
                != "task-contract-mutation.v1"
                or str(payload.get("task_id") or "") != expected["task_id"]
                or str(payload.get("authority_revision") or "")
                != expected["authority_revision"]
            ):
                raise ValueError("mutation receipt identity mismatch")
        except (OSError, ValueError) as exc:
            issues.append(_issue(
                "mutation_receipt_invalid",
                task_id=expected["task_id"],
                authority_revision=expected["authority_revision"],
                ref=ref,
                message=str(exc),
            ))

    stamped_count = 0
    legacy_count = 0
    for task in tasks:
        revision = str(getattr(task, "contract_authority_revision", "") or "")
        sequence = int(getattr(task, "contract_authority_sequence", 0) or 0)
        binding = task_execution_binding(task)
        if not revision:
            legacy_count += 1
            if any(vars(binding).values()):
                warnings.append(_issue(
                    "legacy_binding_fallback",
                    task_id=task.id,
                ))
            continue
        stamped_count += 1
        expected_revision = authority_revision_for(
            task_id=task.id,
            contract=task.contract,
            execution_binding=binding,
            sequence=sequence,
        )
        if revision != expected_revision:
            issues.append(_issue(
                "store_authority_revision_mismatch",
                task_id=task.id,
                authority_revision=revision,
                expected_authority_revision=expected_revision,
            ))
        if (task.id, revision) not in applied_revisions:
            issues.append(_issue(
                "store_authority_receipt_missing",
                task_id=task.id,
                authority_revision=revision,
            ))
        evidence = (
            task.contract.evidence_contract
            if isinstance(task.contract.evidence_contract, dict)
            else {}
        )
        legacy_values = {
            "owner": str(evidence.get("execution_owner") or ""),
            "request_id": str(evidence.get("workflow_request_id") or ""),
            "request_revision": int(evidence.get("workflow_request_revision") or 0),
            "workflow_run_id": str(evidence.get("workflow_run_id") or ""),
            "origin_binding_digest": str(
                evidence.get("workflow_origin_binding_digest") or ""
            ),
            "origin_task_digest": str(
                evidence.get("workflow_origin_task_digest") or ""
            ),
        }
        for field, legacy_value in legacy_values.items():
            current_value = getattr(binding, field)
            if current_value and current_value != legacy_value:
                issues.append(_issue(
                    "execution_binding_drift",
                    task_id=task.id,
                    field=field,
                    first_class=str(current_value),
                    legacy=str(legacy_value),
                ))

    return {
        "schema_version": "task-contract-authority-doctor.v1",
        "ok": not issues,
        "task_count": len(tasks),
        "stamped_task_count": stamped_count,
        "legacy_task_count": legacy_count,
        "prepared_count": len(prepared),
        "applied_count": len(applied_revisions),
        "issue_count": len(issues),
        "warning_count": len(warnings),
        "issues": issues,
        "warnings": warnings,
    }


def _issue(code: str, **values: Any) -> dict[str, Any]:
    return {"code": code, **values}


__all__ = ["build_task_contract_authority_report"]
