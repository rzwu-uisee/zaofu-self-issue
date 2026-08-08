"""Durable lifecycle facts for authorized self-repair provider processes."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.self_repair_process import RESULT_SCHEMA
from zf.runtime.sidecar_refs import sidecar_path


REPAIR_TERMINATED = "autoresearch.repair.terminated"
REPAIR_SPAWNED = "autoresearch.repair.spawned"


def repair_operation_id(
    fingerprint: str,
    attempt: int,
    contract_digest: str,
) -> str:
    digest = hashlib.sha256(
        f"{fingerprint}|{attempt}|{contract_digest}".encode("utf-8")
    ).hexdigest()[:24]
    return f"self-repair-{digest}"


def process_pid(process: Any) -> int:
    try:
        return int(getattr(process, "pid", 0) or 0)
    except (TypeError, ValueError):
        return 0


def read_repair_process_result(
    state_dir: Path,
    spawned_event: Any,
) -> dict[str, Any] | None:
    payload = (
        spawned_event.payload
        if isinstance(getattr(spawned_event, "payload", None), dict)
        else {}
    )
    operation_id = str(payload.get("operation_id") or "")
    contract_digest = str(payload.get("repair_contract_digest") or "")
    result_ref = str(payload.get("result_ref") or "")
    try:
        result_path = sidecar_path(state_dir, result_ref)
    except Exception as exc:
        return _failure_result(
            operation_id,
            contract_digest,
            f"repair_process_result_ref_invalid:{exc}",
        )
    if not result_path.exists():
        if not _repair_process_result_expired(spawned_event):
            return None
        return _failure_result(
            operation_id,
            contract_digest,
            "repair_process_result_missing_after_deadline",
            returncode=125,
        )
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return _failure_result(
            operation_id,
            contract_digest,
            f"repair_process_result_invalid:{exc}",
        )
    if (
        not isinstance(result, dict)
        or result.get("schema_version") != RESULT_SCHEMA
        or str(result.get("operation_id") or "") != operation_id
        or str(result.get("repair_contract_digest") or "") != contract_digest
    ):
        return _failure_result(
            operation_id,
            contract_digest,
            "repair_process_result_identity_mismatch",
        )
    return result


def emit_repair_terminal(
    writer: Any,
    *,
    dispatched_event: Any,
    spawned_event: Any,
    payload: dict[str, Any],
    status: str,
    reason: str,
    process_result: dict[str, Any],
) -> None:
    spawned_payload = (
        spawned_event.payload
        if isinstance(getattr(spawned_event, "payload", None), dict)
        else {}
    )
    try:
        attempt = int(payload.get("attempt") or 0)
    except (TypeError, ValueError):
        attempt = 0
    writer.append(ZfEvent(
        type=REPAIR_TERMINATED,
        actor="zf-self-repair",
        payload={
            "fingerprint": str(payload.get("fingerprint") or ""),
            "attempt": attempt,
            "candidate_id": str(payload.get("candidate_id") or ""),
            "status": status,
            "reason": reason,
            "backend": str(spawned_payload.get("backend") or ""),
            "operation_id": str(spawned_payload.get("operation_id") or ""),
            "provider_operation_id": str(
                spawned_payload.get("provider_operation_id") or ""
            ),
            "process_result": dict(process_result),
            "repair_contract_ref": payload.get("repair_contract_ref")
            if isinstance(payload.get("repair_contract_ref"), dict)
            else {},
            "repair_contract_digest": str(
                payload.get("repair_contract_digest") or ""
            ),
            "dispatched_event_id": str(
                getattr(dispatched_event, "id", "") or ""
            ),
            "spawned_event_id": str(getattr(spawned_event, "id", "") or ""),
        },
        causation_id=str(getattr(spawned_event, "id", "") or "") or None,
    ))


def _failure_result(
    operation_id: str,
    contract_digest: str,
    reason: str,
    *,
    returncode: int = 2,
) -> dict[str, Any]:
    return {
        "schema_version": RESULT_SCHEMA,
        "operation_id": operation_id,
        "status": "failed",
        "returncode": returncode,
        "reason": reason,
        "repair_contract_digest": contract_digest,
    }


def _repair_process_result_expired(spawned_event: Any) -> bool:
    payload = (
        spawned_event.payload
        if isinstance(getattr(spawned_event, "payload", None), dict)
        else {}
    )
    try:
        timeout_seconds = max(1, int(payload.get("timeout_seconds") or 1800))
    except (TypeError, ValueError):
        timeout_seconds = 1800
    age = _event_age_seconds(str(getattr(spawned_event, "ts", "") or ""))
    if age is None or age <= timeout_seconds + 60:
        return False
    try:
        pid = int(payload.get("supervisor_pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    return not _pid_alive(pid)


def _event_age_seconds(value: str) -> float | None:
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - observed).total_seconds())


def _pid_alive(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


__all__ = [
    "REPAIR_SPAWNED",
    "REPAIR_TERMINATED",
    "emit_repair_terminal",
    "process_pid",
    "read_repair_process_result",
    "repair_operation_id",
]
