"""Deterministic continuation anchors for successor task-map entries."""

from __future__ import annotations

import re
from typing import Any


def task_map_supersedes_task_ids(raw: dict[str, Any]) -> list[str]:
    """Return replacement ids from every supported task-map container."""

    out: list[str] = []
    for container in _task_contract_containers(raw):
        for task_id in _string_list(container.get("supersedes_task_ids")):
            if task_id not in out:
                out.append(task_id)
    return out


def task_map_successor_base_commit(raw: dict[str, Any]) -> str:
    """Return the explicitly declared immutable continuation baseline."""

    for container in _task_contract_containers(raw):
        value = str(container.get("base_commit") or "").strip()
        if value:
            return value
    return ""


def successor_base_errors(raw: dict[str, Any], *, task_id: str) -> list[str]:
    if any(
        str(container.get("implementation_base_commit") or "").strip()
        for container in _task_contract_containers(raw)
    ):
        return [
            f"{task_id}.implementation_base_commit is unsupported; "
            "use the canonical top-level base_commit field"
        ]
    supersedes = task_map_supersedes_task_ids(raw)
    base_commit = task_map_successor_base_commit(raw)
    if not base_commit:
        if not supersedes:
            return []
        return [
            f"{task_id}.base_commit is required for successor task "
            f"superseding {', '.join(supersedes)}"
        ]
    if re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", base_commit) is None:
        return [f"{task_id}.base_commit must be a full immutable Git commit"]
    expected_ref = f"git:{base_commit}"
    if expected_ref not in _task_source_refs(raw):
        return [
            f"{task_id}.base_commit must be bound by source_refs entry "
            f"{expected_ref!r}"
        ]
    return []


def _task_source_refs(raw: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for container in _task_contract_containers(raw):
        for ref in _flatten_source_refs(container.get("source_refs")):
            if ref not in out:
                out.append(ref)
    return out


def _task_contract_containers(raw: dict[str, Any]) -> list[dict[str, Any]]:
    containers = [raw]
    for key in ("payload", "evidence_contract"):
        value = raw.get(key)
        if isinstance(value, dict):
            containers.append(value)
    return containers


def _flatten_source_refs(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_flatten_source_refs(item))
        return out
    if isinstance(value, dict):
        out = []
        for item in value.values():
            out.extend(_flatten_source_refs(item))
        return out
    return []


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
