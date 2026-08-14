"""Read-only projection of configured, materialized, and invoked Skills."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any, Iterable, Mapping

from zf.core.config.schema import ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.skills.provenance import LOCKFILE_NAME
from zf.core.task.store import TaskStore


SCHEMA_VERSION = "skill-invocation-projection.v1"
_DISPATCH_EVENTS = frozenset({
    "task.dispatched",
    "fanout.child.dispatched",
    "fanout.synth.dispatched",
})
_TOOL_EVENTS = frozenset({"agent.tool.use"})
_SHELL_READERS = frozenset({"cat", "head", "less", "more", "sed", "tail"})
_DIRECT_PATH_KEYS = frozenset({"file", "file_path", "filename", "path"})


def project_skill_invocations(
    state_dir: Path,
    *,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
    task_id: str = "",
    role_instance: str = "",
    events: Iterable[ZfEvent | tuple[int, ZfEvent]] | None = None,
) -> dict[str, Any]:
    """Project Skill use from manifests plus current-dispatch tool evidence.

    The result is deliberately reconstructible. It never writes a usage marker
    and never infers invocation from briefing text or Skill configuration.
    """

    state_dir = Path(state_dir)
    project_root = Path(project_root or state_dir.parent)
    ordered = [
        item[1] if isinstance(item, tuple) else item
        for item in (
            events
            if events is not None
            else EventLog(state_dir / "events.jsonl").read_all()
        )
    ]
    lock_entries = _lock_entries(state_dir)
    manifests = _manifests(state_dir)
    selected_dispatches = _selected_dispatches(
        ordered,
        task_id=task_id,
        role_instance=role_instance,
    )
    task = TaskStore(state_dir / "kanban.json").get(task_id) if task_id else None

    role_names: set[str] = set(selected_dispatches)
    role_names.update(str(item.get("instance_id") or item.get("role") or "") for item in lock_entries)
    role_names.update(manifests)
    if config is not None:
        role_names.update(role.instance_id for role in config.roles)
    role_names.discard("")
    if role_instance:
        role_names = {role_instance}

    rows: list[dict[str, Any]] = []
    for role in sorted(role_names):
        dispatch = selected_dispatches.get(role)
        dispatch_task_id = _event_task_id(dispatch) if dispatch is not None else ""
        scope_task_id = task_id or dispatch_task_id
        manifest = manifests.get(role, {})
        manifest_task_id = str(manifest.get("task_id") or "")
        manifest_items = {
            str(item.get("name") or ""): item
            for item in manifest.get("skills", []) or []
            if isinstance(item, Mapping) and str(item.get("name") or "")
            and (not task_id or not manifest_task_id or manifest_task_id == task_id)
        }
        role_locks = [
            item for item in lock_entries
            if str(item.get("instance_id") or item.get("role") or "") == role
            and (
                not scope_task_id
                or not str(item.get("task_id") or "")
                or str(item.get("task_id") or "") == scope_task_id
            )
        ]
        lock_by_name: dict[str, Mapping[str, Any]] = {}
        for item in role_locks:
            name = str(item.get("name") or "")
            if name:
                lock_by_name[name] = item

        considered = set(manifest_items) | set(lock_by_name)
        configured = _configured_skills(config, role)
        considered.update(configured)
        if task is not None and _task_matches_role(task, role):
            considered.update(str(name) for name in task.skills_required if str(name))

        for name in sorted(considered):
            manifest_item = manifest_items.get(name, {})
            lock_item = lock_by_name.get(name, {})
            materialized_to = str(
                manifest_item.get("materialized_to")
                or lock_item.get("materialized_to")
                or ""
            )
            static_status = str(
                manifest_item.get("status")
                or lock_item.get("status")
                or ("configured" if name in configured else "unknown")
            )
            loaded = bool(materialized_to) and static_status not in {"missing"}
            auto_injected = bool(
                manifest_item.get("auto_inject")
                if "auto_inject" in manifest_item
                else lock_item.get("auto_inject", False)
            )
            evidence = _invocation_evidence(
                ordered,
                dispatch=dispatch,
                role_instance=role,
                skill_name=name,
                materialized_to=materialized_to,
                state_dir=state_dir,
                project_root=project_root,
            )
            invoked = bool(evidence)
            missing = static_status == "missing"
            rows.append({
                "role": role,
                "task_id": scope_task_id,
                "run_id": _dispatch_value(dispatch, "run_id"),
                "attempt_id": _dispatch_value(dispatch, "attempt_id"),
                "dispatch_id": (
                    _dispatch_value(dispatch, "dispatch_id")
                    or (dispatch.id if dispatch is not None else "")
                ),
                "dispatch_event_id": dispatch.id if dispatch is not None else "",
                "skill": name,
                "considered": True,
                "loaded": loaded,
                "auto_injected": auto_injected,
                "missing": missing,
                "invoked": invoked,
                "observation": (
                    "invoked"
                    if invoked
                    else "missing"
                    if missing
                    else "loaded_unobserved"
                    if loaded
                    else "not_materialized"
                ),
                "status": static_status,
                "source": str(
                    manifest_item.get("source")
                    or lock_item.get("source")
                    or ""
                ),
                "sha256": str(
                    manifest_item.get("sha256")
                    or lock_item.get("sha256")
                    or ""
                ),
                "materialized_to": materialized_to,
                "collision_candidates": list(
                    manifest_item.get("collision_candidates")
                    or lock_item.get("collision_candidates")
                    or []
                ),
                "evidence": evidence,
            })

    return {
        "schema_version": SCHEMA_VERSION,
        "scope": {
            "task_id": task_id,
            "role": role_instance,
            "selection": "latest_dispatch_per_role",
        },
        "summary": {
            "considered_count": len(rows),
            "loaded_count": sum(bool(row["loaded"]) for row in rows),
            "auto_injected_count": sum(bool(row["auto_injected"]) for row in rows),
            "missing_count": sum(bool(row["missing"]) for row in rows),
            "invoked_count": sum(bool(row["invoked"]) for row in rows),
            "unobserved_count": sum(
                row["observation"] == "loaded_unobserved" for row in rows
            ),
        },
        "dispatches": [
            {
                "role": role,
                "task_id": _event_task_id(event),
                "run_id": _dispatch_value(event, "run_id"),
                "attempt_id": _dispatch_value(event, "attempt_id"),
                "dispatch_id": _dispatch_value(event, "dispatch_id") or event.id,
                "dispatch_event_id": event.id,
            }
            for role, event in sorted(selected_dispatches.items())
        ],
        "skills": rows,
    }


def _configured_skills(config: ZfConfig | None, role_instance: str) -> set[str]:
    if config is None:
        return set()
    for role in config.roles:
        if role.instance_id == role_instance:
            return {str(name) for name in role.skills if str(name)}
    return set()


def _task_matches_role(task: Any, role_instance: str) -> bool:
    assignee = str(getattr(task, "assigned_to", "") or "")
    owner = str(getattr(getattr(task, "contract", None), "owner_role", "") or "")
    return role_instance in {assignee, owner}


def _lock_entries(state_dir: Path) -> list[dict[str, Any]]:
    payload = _read_json(state_dir / LOCKFILE_NAME)
    return [dict(item) for item in payload.get("skills", []) or [] if isinstance(item, Mapping)]


def _manifests(state_dir: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted((state_dir / "workdirs").glob("*/runtime/skills-manifest.json")):
        payload = _read_json(path)
        role = str(payload.get("instance_id") or payload.get("role") or path.parents[1].name)
        if role:
            result[role] = payload
    return result


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _selected_dispatches(
    events: list[ZfEvent],
    *,
    task_id: str,
    role_instance: str,
) -> dict[str, ZfEvent]:
    selected: dict[str, ZfEvent] = {}
    for event in events:
        if event.type not in _DISPATCH_EVENTS:
            continue
        role = _dispatch_role(event)
        if not role or (role_instance and role != role_instance):
            continue
        if task_id and _event_task_id(event) != task_id:
            continue
        selected[role] = event
    return selected


def _dispatch_role(event: ZfEvent) -> str:
    payload = event.payload if isinstance(event.payload, Mapping) else {}
    return str(
        payload.get("role_instance")
        or payload.get("assignee")
        or payload.get("instance_id")
        or ""
    )


def _event_task_id(event: ZfEvent | None) -> str:
    if event is None:
        return ""
    payload = event.payload if isinstance(event.payload, Mapping) else {}
    child = payload.get("payload") if isinstance(payload.get("payload"), Mapping) else {}
    return str(event.task_id or payload.get("task_id") or child.get("task_id") or "")


def _dispatch_value(event: ZfEvent | None, key: str) -> str:
    if event is None or not isinstance(event.payload, Mapping):
        return ""
    return str(event.payload.get(key) or "")


def _invocation_evidence(
    events: list[ZfEvent],
    *,
    dispatch: ZfEvent | None,
    role_instance: str,
    skill_name: str,
    materialized_to: str,
    state_dir: Path,
    project_root: Path,
) -> list[dict[str, str]]:
    if dispatch is None:
        return []
    targets = _materialized_skill_paths(
        materialized_to,
        role_instance=role_instance,
        state_dir=state_dir,
        project_root=project_root,
    )
    evidence: list[dict[str, str]] = []
    for event in events:
        if not _event_matches_dispatch(event, dispatch, role_instance):
            continue
        tool, tool_input = _tool_call(event)
        if not tool:
            continue
        evidence_kind = ""
        if _is_controlled_skill_call(tool, tool_input, skill_name):
            evidence_kind = "controlled_skill_tool"
        elif targets and _tool_reads_target(tool, tool_input, targets):
            evidence_kind = "materialized_skill_read"
        if evidence_kind:
            evidence.append({
                "event_id": event.id,
                "event_type": event.type,
                "tool": tool,
                "kind": evidence_kind,
                "dispatch_event_id": dispatch.id,
            })
    return evidence


def _event_matches_dispatch(
    event: ZfEvent,
    dispatch: ZfEvent,
    role_instance: str,
) -> bool:
    if event.actor != role_instance:
        return False
    if event.type not in _TOOL_EVENTS and not event.type.endswith((
        ".pre_tool_use",
        ".post_tool_use",
    )):
        return False
    if str(event.causation_id or "") == dispatch.id:
        return True
    payload = event.payload if isinstance(event.payload, Mapping) else {}
    dispatch_payload = dispatch.payload if isinstance(dispatch.payload, Mapping) else {}
    for key in ("dispatch_id", "attempt_id"):
        expected = str(dispatch_payload.get(key) or "")
        if expected and str(payload.get(key) or "") == expected:
            return True
    return False


def _tool_call(event: ZfEvent) -> tuple[str, Any]:
    payload = event.payload if isinstance(event.payload, Mapping) else {}
    return (
        str(payload.get("tool") or payload.get("tool_name") or ""),
        payload.get("input") if "input" in payload else payload.get("tool_input"),
    )


def _is_controlled_skill_call(tool: str, tool_input: Any, skill_name: str) -> bool:
    if tool.strip().lower() not in {"skill", "skills", "skill.invoke"}:
        return False
    value = _mapping_input(tool_input)
    requested = str(value.get("skill") or value.get("name") or "")
    return requested == skill_name


def _tool_reads_target(tool: str, tool_input: Any, targets: set[str]) -> bool:
    lower = tool.strip().lower()
    value = _mapping_input(tool_input)
    if "write" not in lower and "read" in lower:
        return any(
            _normalized_path(str(item)) in targets
            for key, item in value.items()
            if key in _DIRECT_PATH_KEYS and isinstance(item, (str, Path))
        )
    if lower not in {"bash", "shell", "exec_command", "functions.exec_command"}:
        return False
    command = str(value.get("command") or value.get("cmd") or "").strip()
    if not command:
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens or Path(tokens[0]).name not in _SHELL_READERS:
        return False
    return any(_normalized_path(token) in targets for token in tokens[1:])


def _mapping_input(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"command": value}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _materialized_skill_paths(
    materialized_to: str,
    *,
    role_instance: str,
    state_dir: Path,
    project_root: Path,
) -> set[str]:
    if not materialized_to:
        return set()
    directory = Path(materialized_to)
    if not directory.is_absolute():
        directory = project_root / directory
    target = directory.absolute() / "SKILL.md"
    variants = {_normalized_path(str(target))}
    roots = (
        project_root.absolute(),
        (state_dir / "workdirs" / role_instance / "project").absolute(),
        (state_dir / "workdirs" / role_instance / "codex-home").absolute(),
        (state_dir / "workdirs" / role_instance / "runtime").absolute(),
    )
    for root in roots:
        try:
            variants.add(_normalized_path(str(target.relative_to(root))))
        except ValueError:
            continue
    return variants


def _normalized_path(value: str) -> str:
    normalized = Path(value).as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.rstrip("/")


__all__ = ["SCHEMA_VERSION", "project_skill_invocations"]
