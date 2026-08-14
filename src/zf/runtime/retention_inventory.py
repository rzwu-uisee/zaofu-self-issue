"""Read-only, fail-closed retention inventory for one runtime state tree."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from zf.runtime.sidecar_refs import (
    KNOWN_SIDECAR_ROOTS,
    SidecarRefError,
    iter_sidecar_ref_descriptors,
    normalize_sidecar_ref_descriptor,
)


SCHEMA_VERSION = "runtime-retention-inventory.v1"

_EVENT_TERMINAL_TYPES = frozenset({
    "workdir.retired",
    "autoscale.scale_down.completed",
    "run.completed",
    "run.cancelled",
    "run.abandoned",
    "run.goal.completed",
    "run.goal.blocked",
    "workflow.operation.settled",
    "workflow.operation.failed",
    "workflow.operation.blocked",
    "workflow.operation.superseded",
    "workflow.operation.cancelled",
})
_ACTIVE_REFERENCE_TYPES = frozenset({
    "task.dispatched",
    "fanout.child.dispatched",
    "fanout.synth.dispatched",
    "workflow.operation.requested",
    "workflow.operation.reserved",
    "workflow.operation.started",
    "workflow.operation.retry_started",
    "workflow.operation.interrupted",
    "human.escalate",
    "approval.requested",
    "plan.approval.requested",
    "run.manager.action.effect.pending",
    "run.manager.action.blocked",
})
_CANONICAL_TOP_LEVEL = frozenset({
    "config",
    "feature_list.json",
    "goal-dossiers",
    "kanban",
    "kanban.json",
    "last-shutdown",
    "memory",
    "refs",
    "role_sessions.yaml",
    "runs",
    "session.yaml",
    "task-attempts",
    "web-actions",
})
_REBUILDABLE_TOP_LEVEL_FILES = frozenset({
    "cost.jsonl",
    "event_index.json",
    "progress.md",
})
_AUDIT_RETENTION_CLASSES = frozenset({
    "audit_required",
    "handoff_required",
    "workflow_required",
    "required",
})
_TRANSCRIPT_MARKERS = frozenset({
    "operator/threads",
    "transcripts",
})
_ACTIVE_ROLE_STATES = frozenset({
    "active",
    "busy",
    "blocked",
    "working",
    "refreshing",
    "stopping",
})
_TERMINAL_TASK_STATES = frozenset({"done", "cancelled", "archived"})


def build_retention_inventory(state_dir: Path) -> dict[str, Any]:
    """Classify files without writing state, caches, locks, or timestamps."""

    root = Path(state_dir).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return _missing_state_projection(root)

    event_scan = _scan_events(root)
    canonical_scan = _scan_canonical_state(root)
    parse_issues = [*event_scan["issues"], *canonical_scan["issues"]]
    truth_scan_complete = not any(
        bool(issue.get("truth_scan_incomplete")) for issue in parse_issues
    )
    all_refs = set(event_scan["all_refs"]) | set(canonical_scan["all_refs"])
    active_refs = set(event_scan["active_refs"]) | set(canonical_scan["active_refs"])
    descriptors = dict(event_scan["descriptors"])
    active_assignees = set(canonical_scan["active_assignees"])
    role_meta = dict(canonical_scan["role_meta"])
    workdir_lifecycle = dict(event_scan["workdir_lifecycle"])

    items: list[dict[str, Any]] = []
    consumed: set[str] = set()
    forced_blocked = _manifest_parse_failures(root)
    parse_issues.extend({
        "code": "manifest_parse_failed",
        "path": path,
        "truth_scan_incomplete": False,
    } for path in forced_blocked)
    workdir_root = root / "workdirs"
    if workdir_root.exists():
        for workdir in sorted(workdir_root.iterdir(), key=lambda path: path.name):
            if not workdir.is_dir() or workdir.is_symlink():
                continue
            workdir_items, paths = _classify_workdir(
                root=root,
                workdir=workdir,
                role_meta=role_meta,
                active_assignees=active_assignees,
                lifecycle=workdir_lifecycle,
                active_refs=active_refs,
                truth_scan_complete=truth_scan_complete,
                manifest_blocked=bool(
                    forced_blocked.get(f"workdirs/{workdir.name}/meta.json")
                ),
            )
            items.extend(workdir_items)
            consumed.update(paths)

    for path in _iter_files(root):
        rel = path.relative_to(root).as_posix()
        if rel in consumed:
            continue
        size = _safe_size(path)
        category, status, reason = _classify_file(
            rel=rel,
            descriptors=descriptors,
            all_refs=all_refs,
            truth_scan_complete=truth_scan_complete,
        )
        if rel in forced_blocked:
            category, status, reason = "unknown", "blocked", forced_blocked[rel]
        items.append(_item(
            path=rel,
            category=category,
            status=status,
            reason=reason,
            file_count=1,
            byte_count=size,
        ))

    existing = {path.relative_to(root).as_posix() for path in _iter_files(root)}
    dangling = sorted(ref for ref in descriptors if ref not in existing)
    for ref in dangling:
        items.append(_item(
            path=ref,
            category="unknown",
            status="blocked",
            reason="dangling_sidecar_ref",
            file_count=0,
            byte_count=0,
        ))
        parse_issues.append({
            "code": "dangling_sidecar_ref",
            "path": ref,
            "truth_scan_incomplete": False,
        })

    if not truth_scan_complete:
        items = [
            {
                **item,
                "status": "blocked",
                "reason": "truth_reference_scan_incomplete",
            }
            if item["status"] == "eligible"
            else item
            for item in items
        ]

    items = _coalesce_items(items)
    categories = _category_summary(items)
    totals = _totals(items)
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "dry-run",
        "status": "degraded" if parse_issues else "ready",
        "state_dir": str(root),
        "delete_supported": False,
        "truth_reference_scan_complete": truth_scan_complete,
        "totals": totals,
        "categories": categories,
        "candidates": [item for item in items if item["status"] == "eligible"],
        "protected": [item for item in items if item["status"] == "protected"],
        "blocked": [item for item in items if item["status"] == "blocked"],
        "issues": sorted(
            parse_issues,
            key=lambda issue: (
                str(issue.get("path") or ""),
                str(issue.get("code") or ""),
            ),
        ),
        "policy": {
            "truth_sources": [
                "events.jsonl + events/*.jsonl",
                "kanban.json",
                "feature_list.json",
                "session.yaml",
                "role_sessions.yaml",
            ],
            "eligible_classes": [
                "unreferenced_rebuildable_projection",
                "proven_terminal_workdir_non_transcript",
            ],
            "fail_closed": True,
            "provider_transcripts_default": "audit_required",
        },
    }


def _scan_events(root: Path) -> dict[str, Any]:
    paths = sorted((root / "events").rglob("*.jsonl")) if (root / "events").exists() else []
    active = root / "events.jsonl"
    if active.exists():
        paths.append(active)
    all_refs: set[str] = set()
    active_refs: set[str] = set()
    descriptors: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, Any]] = []
    workdir_lifecycle: dict[str, str] = {}
    for path in paths:
        rel = path.relative_to(root).as_posix()
        try:
            handle = path.open("r", encoding="utf-8")
        except OSError as exc:
            issues.append({
                "code": "event_segment_unreadable",
                "path": rel,
                "detail": str(exc),
                "truth_scan_incomplete": True,
            })
            continue
        with handle:
            for line_no, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    decoded = json.loads(line)
                except json.JSONDecodeError as exc:
                    issues.append({
                        "code": "event_line_invalid_json",
                        "path": f"{rel}:{line_no}",
                        "detail": str(exc),
                        "truth_scan_incomplete": True,
                    })
                    continue
                event = decoded.get("event") if isinstance(decoded, Mapping) and isinstance(decoded.get("event"), Mapping) else decoded
                if not isinstance(event, Mapping) or not isinstance(event.get("type"), str):
                    issues.append({
                        "code": "event_line_invalid_shape",
                        "path": f"{rel}:{line_no}",
                        "truth_scan_incomplete": True,
                    })
                    continue
                event_type = str(event.get("type") or "")
                payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
                for descriptor in iter_sidecar_ref_descriptors(payload):
                    ref = str(descriptor.get("ref") or "")
                    if ref:
                        descriptors[ref] = dict(descriptor)
                        all_refs.add(ref)
                for invalid in _invalid_descriptors(payload):
                    issues.append({
                        "code": "sidecar_descriptor_invalid",
                        "path": f"{rel}:{line_no}",
                        "detail": invalid,
                        "truth_scan_incomplete": True,
                    })
                event_refs = _path_refs(payload, root)
                all_refs.update(event_refs)
                if event_type in _ACTIVE_REFERENCE_TYPES:
                    active_refs.update(event_refs)
                if event_type in {"workdir.retired", "workdir.retire_failed"}:
                    instance_id = str(payload.get("instance_id") or payload.get("role") or "")
                    if instance_id:
                        workdir_lifecycle[instance_id] = event_type
    return {
        "all_refs": all_refs,
        "active_refs": active_refs,
        "descriptors": descriptors,
        "issues": issues,
        "workdir_lifecycle": workdir_lifecycle,
    }


def _scan_canonical_state(root: Path) -> dict[str, Any]:
    all_refs: set[str] = set()
    active_refs: set[str] = set()
    active_assignees: set[str] = set()
    role_meta: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, Any]] = []

    for name in ("kanban.json", "feature_list.json"):
        path = root / name
        if not path.exists():
            continue
        value, issue = _read_json(path, root)
        if issue:
            issues.append(issue)
            continue
        all_refs.update(_path_refs(value, root))
        active_refs.update(_path_refs(value, root))
        if name == "kanban.json":
            tasks = _task_rows(value)
            for task in tasks:
                if str(task.get("status") or "") in _TERMINAL_TASK_STATES:
                    continue
                assignee = str(task.get("assigned_to") or "").strip()
                if assignee:
                    active_assignees.add(assignee)

    for name in ("session.yaml", "role_sessions.yaml"):
        path = root / name
        if not path.exists():
            continue
        value, issue = _read_yaml(path, root)
        if issue:
            issues.append(issue)
            continue
        all_refs.update(_path_refs(value, root))
        if name == "session.yaml":
            active_refs.update(_path_refs(value, root))
            continue
        raw_meta = value.get("instance_meta") if isinstance(value, Mapping) else {}
        if isinstance(raw_meta, Mapping):
            role_meta = {
                str(instance): dict(meta) if isinstance(meta, Mapping) else {}
                for instance, meta in raw_meta.items()
            }
        for instance, meta in role_meta.items():
            heartbeat = meta.get("last_heartbeat_payload")
            heartbeat = heartbeat if isinstance(heartbeat, Mapping) else {}
            state = str(heartbeat.get("state") or meta.get("status") or "").lower()
            current_task = str(heartbeat.get("current_task_id") or "").strip()
            if state in _ACTIVE_ROLE_STATES or current_task:
                active_assignees.add(instance)
                active_refs.update(_path_refs(meta, root))
    return {
        "all_refs": all_refs,
        "active_refs": active_refs,
        "active_assignees": active_assignees,
        "role_meta": role_meta,
        "issues": issues,
    }


def _classify_workdir(
    *,
    root: Path,
    workdir: Path,
    role_meta: Mapping[str, Mapping[str, Any]],
    active_assignees: set[str],
    lifecycle: Mapping[str, str],
    active_refs: set[str],
    truth_scan_complete: bool,
    manifest_blocked: bool,
) -> tuple[list[dict[str, Any]], set[str]]:
    instance_id = workdir.name
    rel_root = workdir.relative_to(root).as_posix()
    files = list(_iter_files(workdir))
    consumed = {path.relative_to(root).as_posix() for path in files}
    transcript_files = [path for path in files if _is_provider_transcript(path.relative_to(root).as_posix())]
    ordinary = [path for path in files if path not in transcript_files]
    meta = role_meta.get(instance_id, {})
    heartbeat = meta.get("last_heartbeat_payload") if isinstance(meta, Mapping) else {}
    heartbeat = heartbeat if isinstance(heartbeat, Mapping) else {}
    heartbeat_state = str(heartbeat.get("state") or "").lower()
    current_task = str(heartbeat.get("current_task_id") or "").strip()
    active = (
        instance_id in active_assignees
        or bool(current_task)
        or heartbeat_state in _ACTIVE_ROLE_STATES
        or _path_or_descendant_referenced(rel_root, active_refs)
    )
    retired = (
        str(meta.get("status") or "").lower() == "retired"
        and lifecycle.get(instance_id) == "workdir.retired"
    )
    meta_path = workdir / "meta.json"
    meta_valid = True
    if meta_path.exists():
        value, issue = _read_json(meta_path, root, truth_scan_incomplete=False)
        meta_valid = not issue and isinstance(value, Mapping)
    else:
        meta_valid = False
    if manifest_blocked or not meta_valid:
        category, status, reason = "unknown", "blocked", "workdir_manifest_parse_failed"
    elif active:
        category, status, reason = "active_workdir", "protected", "active_role_or_task_reference"
    elif retired and meta_valid and truth_scan_complete:
        category, status, reason = "terminal_workdir", "eligible", "retired_role_and_workdir_proven"
    elif lifecycle.get(instance_id) == "workdir.retire_failed" or not retired:
        category, status, reason = "recoverable_workdir", "protected", "workdir_may_be_resumable"
    else:
        category, status, reason = "terminal_workdir", "blocked", "terminal_workdir_manifest_unproven"
    rows: list[dict[str, Any]] = []
    if ordinary:
        rows.append(_item(
            path=f"{rel_root}/**",
            category=category,
            status=status,
            reason=reason,
            file_count=len(ordinary),
            byte_count=sum(_safe_size(path) for path in ordinary),
        ))
    if transcript_files:
        rows.append(_item(
            path=f"{rel_root}/**/provider-transcripts",
            category="provider_transcript",
            status="protected",
            reason="provider_transcript_audit_required",
            file_count=len(transcript_files),
            byte_count=sum(_safe_size(path) for path in transcript_files),
        ))
    return rows, consumed


def _classify_file(
    *,
    rel: str,
    descriptors: Mapping[str, Mapping[str, Any]],
    all_refs: set[str],
    truth_scan_complete: bool,
) -> tuple[str, str, str]:
    path = PurePosixPath(rel)
    top = path.parts[0] if path.parts else rel
    if rel == "events.jsonl" or (top == "events" and rel.endswith(".jsonl")):
        return "event_log", "protected", "append_only_event_truth"
    if rel.endswith(".lock") or rel == "loop.lock":
        return "runtime_coordination", "protected", "runtime_coordination_file"
    if _is_provider_transcript(rel):
        return "provider_transcript", "protected", "provider_transcript_audit_required"
    if top in KNOWN_SIDECAR_ROOTS:
        descriptor = descriptors.get(rel, {})
        retention = descriptor.get("retention") if isinstance(descriptor, Mapping) else {}
        retention = retention if isinstance(retention, Mapping) else {}
        retention_class = str(retention.get("class") or "audit_required")
        if retention_class in _AUDIT_RETENTION_CLASSES:
            return "audit_required_sidecar", "protected", f"retention:{retention_class}"
        if rel in all_refs:
            return "sidecar", "protected", "referenced_by_event_truth"
        return "sidecar", "blocked", "orphan_sidecar_retention_unproven"
    if rel == "events/manifest.json":
        if rel in all_refs:
            return "rebuildable_projection", "protected", "projection_referenced_by_truth"
        return "rebuildable_projection", "eligible", "event_manifest_rebuildable"
    if top == "projections" or rel in _REBUILDABLE_TOP_LEVEL_FILES:
        if rel in all_refs:
            return "rebuildable_projection", "protected", "projection_referenced_by_truth"
        if not truth_scan_complete:
            return "rebuildable_projection", "blocked", "truth_reference_scan_incomplete"
        return "rebuildable_projection", "eligible", "unreferenced_rebuildable_projection"
    if top == "logs":
        return "rebuildable_projection", "blocked", "log_retention_policy_unproven"
    if top in _CANONICAL_TOP_LEVEL or rel in _CANONICAL_TOP_LEVEL:
        return "canonical_store", "protected", "canonical_runtime_state"
    return "unknown", "blocked", "retention_class_unproven"


def _manifest_parse_failures(root: Path) -> dict[str, str]:
    failures: dict[str, str] = {}
    for path in _iter_files(root):
        rel = path.relative_to(root).as_posix()
        if path.name not in {"manifest.json", "meta.json"}:
            continue
        if not (rel.startswith("workdirs/") or rel == "events/manifest.json"):
            continue
        _value, issue = _read_json(path, root, truth_scan_incomplete=False)
        if issue:
            failures[rel] = "manifest_parse_failed"
    return failures


def _invalid_descriptors(value: Any) -> list[str]:
    invalid: list[str] = []
    if isinstance(value, Mapping):
        descriptor_like = (
            str(value.get("ref_schema_version") or "") == "sidecar-ref.v1"
            or (
                isinstance(value.get("retention"), Mapping)
                and any(key in value for key in ("ref", "raw_ref", "path"))
            )
        )
        if descriptor_like:
            try:
                normalize_sidecar_ref_descriptor(dict(value))
            except SidecarRefError as exc:
                invalid.append(f"{exc.code}:{exc}")
        for item in value.values():
            invalid.extend(_invalid_descriptors(item))
    elif isinstance(value, list):
        for item in value:
            invalid.extend(_invalid_descriptors(item))
    return invalid


def _path_refs(value: Any, root: Path) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, Mapping):
        for item in value.values():
            refs.update(_path_refs(item, root))
    elif isinstance(value, list):
        for item in value:
            refs.update(_path_refs(item, root))
    elif isinstance(value, str):
        normalized = _normalize_ref(value, root)
        if normalized:
            refs.add(normalized)
    return refs


def _normalize_ref(value: str, root: Path) -> str:
    text = str(value or "").strip()
    if not text or "\n" in text or "\r" in text:
        return ""
    candidate = Path(text).expanduser()
    if candidate.is_absolute():
        try:
            return candidate.resolve(strict=False).relative_to(root).as_posix()
        except ValueError:
            return ""
    normalized = text.removeprefix("./").removeprefix(".zf/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return ""
    if not path.parts:
        return ""
    known = set(KNOWN_SIDECAR_ROOTS) | {
        "events",
        "workdirs",
        "projections",
        "runs",
        "refs",
        "kanban",
        "logs",
    }
    if path.parts[0] in known or normalized in _REBUILDABLE_TOP_LEVEL_FILES:
        return path.as_posix()
    return ""


def _read_json(
    path: Path,
    root: Path,
    *,
    truth_scan_incomplete: bool = True,
) -> tuple[Any, dict[str, Any] | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, {
            "code": "json_parse_failed",
            "path": path.relative_to(root).as_posix(),
            "detail": str(exc),
            "truth_scan_incomplete": truth_scan_incomplete,
        }


def _read_yaml(path: Path, root: Path) -> tuple[Any, dict[str, Any] | None]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return None, {
            "code": "yaml_parse_failed",
            "path": path.relative_to(root).as_posix(),
            "detail": str(exc),
            "truth_scan_incomplete": True,
        }
    if not isinstance(value, Mapping):
        return None, {
            "code": "yaml_shape_invalid",
            "path": path.relative_to(root).as_posix(),
            "truth_scan_incomplete": True,
        }
    return value, None


def _task_rows(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, Mapping)]
    if isinstance(value, Mapping):
        rows = value.get("tasks")
        if isinstance(rows, list):
            return [item for item in rows if isinstance(item, Mapping)]
        return [item for item in value.values() if isinstance(item, Mapping)]
    return []


def _iter_files(root: Path) -> Iterable[Path]:
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            name for name in dirs
            if not (Path(current) / name).is_symlink()
        )
        for name in sorted(files):
            yield Path(current) / name


def _is_provider_transcript(rel: str) -> bool:
    lowered = rel.lower()
    if any(lowered == marker or lowered.startswith(f"{marker}/") for marker in _TRANSCRIPT_MARKERS):
        return True
    return bool(
        "/sessions/" in lowered
        or "/transcripts/" in lowered
        or "/rollout-" in lowered
        or lowered.endswith("/transcript.jsonl")
        or lowered.endswith("/transcript.json")
    )


def _path_or_descendant_referenced(path: str, refs: set[str]) -> bool:
    prefix = path.rstrip("/") + "/"
    return any(ref == path or ref.startswith(prefix) for ref in refs)


def _safe_size(path: Path) -> int:
    try:
        return int(path.lstat().st_size)
    except OSError:
        return 0


def _item(
    *,
    path: str,
    category: str,
    status: str,
    reason: str,
    file_count: int,
    byte_count: int,
) -> dict[str, Any]:
    return {
        "path": path,
        "category": category,
        "status": status,
        "reason": reason,
        "file_count": int(file_count),
        "bytes": int(byte_count),
    }


def _coalesce_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in items:
        path = str(item["path"])
        category = str(item["category"])
        status = str(item["status"])
        reason = str(item["reason"])
        group_path = path
        if category in {"audit_required_sidecar", "sidecar", "provider_transcript", "unknown"}:
            parts = PurePosixPath(path).parts
            if path.startswith("workdirs/") and len(parts) >= 2:
                group_path = "/".join(parts[:2]) + "/**"
            elif parts:
                group_path = f"{parts[0]}/**"
            if category == "provider_transcript" and path.startswith("workdirs/"):
                group_path = "/".join(parts[:2]) + "/**/provider-transcripts"
        key = (group_path, category, status, reason)
        row = grouped.setdefault(key, _item(
            path=group_path,
            category=category,
            status=status,
            reason=reason,
            file_count=0,
            byte_count=0,
        ))
        row["file_count"] += int(item.get("file_count") or 0)
        row["bytes"] += int(item.get("bytes") or 0)
    return sorted(
        grouped.values(),
        key=lambda item: (
            str(item["status"]),
            str(item["category"]),
            str(item["path"]),
            str(item["reason"]),
        ),
    )


def _category_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, dict[str, int]] = defaultdict(lambda: {
        "file_count": 0,
        "bytes": 0,
        "eligible_file_count": 0,
        "eligible_bytes": 0,
        "protected_file_count": 0,
        "protected_bytes": 0,
        "blocked_file_count": 0,
        "blocked_bytes": 0,
    })
    for item in items:
        row = summary[str(item["category"])]
        count = int(item.get("file_count") or 0)
        size = int(item.get("bytes") or 0)
        status = str(item.get("status") or "blocked")
        row["file_count"] += count
        row["bytes"] += size
        row[f"{status}_file_count"] += count
        row[f"{status}_bytes"] += size
    return {key: summary[key] for key in sorted(summary)}


def _totals(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "file_count": sum(int(item.get("file_count") or 0) for item in items),
        "bytes": sum(int(item.get("bytes") or 0) for item in items),
        "eligible_file_count": sum(
            int(item.get("file_count") or 0)
            for item in items
            if item.get("status") == "eligible"
        ),
        "estimated_reclaim_bytes": sum(
            int(item.get("bytes") or 0)
            for item in items
            if item.get("status") == "eligible"
        ),
        "protected_bytes": sum(
            int(item.get("bytes") or 0)
            for item in items
            if item.get("status") == "protected"
        ),
        "blocked_bytes": sum(
            int(item.get("bytes") or 0)
            for item in items
            if item.get("status") == "blocked"
        ),
    }


def _missing_state_projection(root: Path) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "dry-run",
        "status": "missing",
        "state_dir": str(root),
        "delete_supported": False,
        "truth_reference_scan_complete": False,
        "totals": {
            "file_count": 0,
            "bytes": 0,
            "eligible_file_count": 0,
            "estimated_reclaim_bytes": 0,
            "protected_bytes": 0,
            "blocked_bytes": 0,
        },
        "categories": {},
        "candidates": [],
        "protected": [],
        "blocked": [],
        "issues": [{
            "code": "state_dir_missing",
            "path": str(root),
            "truth_scan_incomplete": True,
        }],
        "policy": {"fail_closed": True},
    }


__all__ = ["SCHEMA_VERSION", "build_retention_inventory"]
