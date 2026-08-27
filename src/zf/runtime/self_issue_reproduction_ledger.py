"""Kernel-owned reproduction budget ledger for one Self-Issue evidence run."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from zf.core.self_issue.models import utc_now
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path


SCHEMA_VERSION = "self-issue-reproduction-ledger.v1"
MAX_ATTEMPTS = 3
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_SAFE_TARGET = re.compile(
    r"^(?:repository|subject|harness):[A-Za-z0-9_./:-]{1,300}$",
)
_STATUSES = frozenset({
    "requested", "started", "passed", "failed", "timeout", "unavailable",
    "source_mutated", "outcome_unknown",
})


def reproduction_ledger_path(state_dir: Path, *, draft_id: str, run_id: str) -> Path:
    if not _SAFE_ID.fullmatch(draft_id) or not _SAFE_ID.fullmatch(run_id):
        raise ValueError("invalid Self-Issue reproduction ledger identity")
    return (
        Path(state_dir) / "self-issues" / "evidence-runs" / draft_id / run_id
        / "reproductions.json"
    )


def initialize_reproduction_ledger(
    state_dir: Path, *, draft_id: str, run_id: str,
) -> Path:
    path = reproduction_ledger_path(state_dir, draft_id=draft_id, run_id=run_id)
    with locked_path(path):
        if path.exists():
            _validate_ledger(_read(path), draft_id=draft_id, run_id=run_id)
            return path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        body = {
            "schema_version": SCHEMA_VERSION,
            "draft_id": draft_id,
            "run_id": run_id,
            "max_attempts": MAX_ATTEMPTS,
            "attempts": [],
            "updated_at": utc_now(),
        }
        _write(path, body)
    return path


def read_reproduction_ledger(path: Path) -> dict[str, Any]:
    body = _read(Path(path))
    return _validate_ledger(
        body,
        draft_id=str(body.get("draft_id") or ""),
        run_id=str(body.get("run_id") or ""),
    )


def reserve_reproduction_attempt(path: Path, *, target: str) -> dict[str, Any]:
    if not _SAFE_TARGET.fullmatch(target):
        raise ValueError("invalid reproduction target")
    path = Path(path)
    with locked_path(path):
        body = read_reproduction_ledger(path)
        attempts = body["attempts"]
        attempt = len(attempts) + 1
        if attempt > MAX_ATTEMPTS:
            return {"allowed": False, "attempt": attempt, "target": target}
        entry = {
            "attempt": attempt,
            "target": target,
            "status": "requested",
            "updated_at": utc_now(),
        }
        attempts.append(entry)
        body["updated_at"] = utc_now()
        _write(path, body)
        return {"allowed": True, **entry}


def seed_workspace_reproduction_state(path: Path, *, workspace_root: Path) -> None:
    body = read_reproduction_ledger(path)
    destination = Path(workspace_root) / ".assessment-runtime" / "reproductions.json"
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write_text(destination, json.dumps(body, sort_keys=True) + "\n")
    destination.chmod(0o600)


def sync_workspace_reproduction_state(
    path: Path, *, workspace_root: Path,
) -> dict[str, Any]:
    source = Path(workspace_root) / ".assessment-runtime" / "reproductions.json"
    if not source.is_file():
        return read_reproduction_ledger(path)
    try:
        workspace_body = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("invalid workspace reproduction state") from exc
    raw_attempts = workspace_body.get("attempts") if isinstance(workspace_body, dict) else None
    if not isinstance(raw_attempts, list) or len(raw_attempts) > MAX_ATTEMPTS:
        raise ValueError("invalid workspace reproduction attempts")
    normalized = [_normalize_attempt(item, expected=index) for index, item in enumerate(
        raw_attempts, start=1,
    )]
    path = Path(path)
    with locked_path(path):
        body = read_reproduction_ledger(path)
        attempts = body["attempts"]
        for incoming in normalized:
            index = incoming["attempt"] - 1
            if index < len(attempts):
                current = attempts[index]
                if current["target"] != incoming["target"]:
                    raise ValueError("reproduction ledger target mismatch")
                current.update(incoming)
            elif index == len(attempts):
                attempts.append(incoming)
            else:
                raise ValueError("reproduction ledger sequence gap")
        body["updated_at"] = utc_now()
        _write(path, body)
        return body


def record_reproduction_result(
    path: Path, *, attempt: int, target: str, status: str,
) -> dict[str, Any]:
    if not 1 <= attempt <= MAX_ATTEMPTS or not _SAFE_TARGET.fullmatch(target):
        raise ValueError("invalid reproduction result identity")
    if status not in _STATUSES - {"requested"}:
        raise ValueError("invalid reproduction result status")
    path = Path(path)
    with locked_path(path):
        body = read_reproduction_ledger(path)
        attempts = body["attempts"]
        if attempt > len(attempts):
            if attempt != len(attempts) + 1:
                raise ValueError("reproduction ledger sequence gap")
            attempts.append({
                "attempt": attempt,
                "target": target,
                "status": status,
                "updated_at": utc_now(),
            })
        else:
            current = attempts[attempt - 1]
            if current["target"] != target:
                raise ValueError("reproduction ledger target mismatch")
            current["status"] = status
            current["updated_at"] = utc_now()
        body["updated_at"] = utc_now()
        _write(path, body)
        return body


def finalize_incomplete_reproductions(path: Path) -> dict[str, Any]:
    path = Path(path)
    with locked_path(path):
        body = read_reproduction_ledger(path)
        changed = False
        for attempt in body["attempts"]:
            if attempt["status"] in {"requested", "started"}:
                attempt["status"] = "outcome_unknown"
                attempt["updated_at"] = utc_now()
                changed = True
        if changed:
            body["updated_at"] = utc_now()
            _write(path, body)
        return body


def _normalize_attempt(value: object, *, expected: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("invalid reproduction attempt")
    attempt = int(value.get("attempt") or 0)
    target = str(value.get("target") or "")
    status = str(value.get("status") or "")
    if attempt != expected or not _SAFE_TARGET.fullmatch(target) or status not in _STATUSES:
        raise ValueError("invalid reproduction attempt")
    return {
        "attempt": attempt,
        "target": target,
        "status": status,
        "updated_at": str(value.get("updated_at") or utc_now())[:40],
    }


def _validate_ledger(
    value: object, *, draft_id: str, run_id: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("invalid reproduction ledger")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("draft_id") != draft_id
        or value.get("run_id") != run_id
        or value.get("max_attempts") != MAX_ATTEMPTS
    ):
        raise ValueError("invalid reproduction ledger identity")
    attempts = value.get("attempts")
    if not isinstance(attempts, list) or len(attempts) > MAX_ATTEMPTS:
        raise ValueError("invalid reproduction ledger attempts")
    value["attempts"] = [
        _normalize_attempt(item, expected=index)
        for index, item in enumerate(attempts, start=1)
    ]
    return value


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("reproduction ledger is unavailable") from exc
    return value if isinstance(value, dict) else {}


def _write(path: Path, body: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    path.chmod(0o600)
