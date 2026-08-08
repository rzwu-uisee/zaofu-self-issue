#!/usr/bin/env python3
"""Terminal-aware evidence runner for the five real Workflow E2E families.

The runner is deliberately read-only. It freezes suite/case identity before
approval, observes canonical events, and captures diagnostics on the first
terminal failure. It never creates Tasks, approves plans, emits runtime facts,
or mutates workflow state.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any, Mapping, Sequence

from zf.core.config.loader import load_config
from zf.core.config.render import renderable_config_to_primitive
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.task.store import TaskStore
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.task_workflow_plans import task_workflow_binding_digest
from zf.runtime.workflow_operation import reduce_workflow_operations
from zf.runtime.workflow_route_catalog import (
    resolve_workflow_route,
    workflow_route_catalog,
)

try:
    from tests.e2e.five_workflow_runner_support import (
        capture_screenshot,
        collect_refs,
        diagnostic_paths,
        git_snapshot,
        host_readiness,
        read_json,
        read_yaml,
        write_json,
    )
except ModuleNotFoundError:  # Direct `python tests/e2e/...py` execution.
    from five_workflow_runner_support import (
        capture_screenshot,
        collect_refs,
        diagnostic_paths,
        git_snapshot,
        host_readiness,
        read_json,
        read_yaml,
        write_json,
    )


SCHEMA_VERSION = "five-workflow-terminal-runner.v1"
CASE_MANIFEST_SCHEMA = "five-workflow-case-manifest.v1"
SUITE_MANIFEST_SCHEMA = "five-workflow-suite-manifest.v1"

FAMILIES = frozenset({"general", "issue", "prd", "refactor", "research"})
DELIVERY_FAMILIES = frozenset({"issue", "prd", "refactor"})
FRESH_STATE_EVENTS = frozenset({
    "task.created",
    "workflow.invoke.requested",
    "run.admission.requested",
    "task.dispatched",
    "fanout.started",
})
FAILURE_TERMINALS = frozenset({
    "workflow.invoke.rejected",
    "run.admission.rejected",
    "run.goal.blocked",
    "run.failed",
    "run.cancelled",
    "run.abandoned",
    "fanout.timed_out",
    "fanout.cancelled",
    "research.fanout.failed",
    "research.adaptive.failed",
    "human.escalate",
})
CASE_RUNTIME_CONTRACT_FIELDS = frozenset({
    "acceptance_evidence",
    "capsule_revision",
    "contract_revision",
    "critic_dispatch_id",
    "critic_event_id",
    "critic_gate_ref",
    "dispatch_id",
    "evidence_doc_ref",
    "handoff_artifacts",
    "owner_instance",
    "owner_role",
    "progress_doc_ref",
    "reviewed_arch_event_id",
    "source_arch_dispatch_id",
    "source_doc_ref",
    "source_revision",
    "task_doc_ref",
    "wave",
})
CASE_RUNTIME_EVIDENCE_FIELDS = frozenset({
    "execution_owner",
    "workflow_origin_binding_digest",
    "workflow_request_id",
    "workflow_request_revision",
})


class RunnerPreflightError(ValueError):
    """The E2E seed or case identity is not safe to run."""


@dataclass(frozen=True)
class TerminalObservation:
    status: str
    family: str
    reason: str
    workflow_run_id: str = ""
    event_id: str = ""
    event_type: str = ""
    event: dict[str, Any] | None = None

    @property
    def terminal(self) -> bool:
        return self.status in {"passed", "failed"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            **asdict(self),
        }


def build_suite_preflight_manifest(
    *,
    project_root: Path,
    state_dir: Path,
    config_path: Path,
    implementation_root: Path,
    require_backend: str = "codex",
    check_host: bool = False,
    playwright_image: str = "mcp/playwright:latest",
) -> dict[str, Any]:
    """Validate the immutable implementation and fresh multi-kind seed."""

    project_root = Path(project_root).resolve()
    state_dir = Path(state_dir).resolve()
    config_path = Path(config_path).resolve()
    implementation_root = Path(implementation_root).resolve()
    errors: list[str] = []
    checks: dict[str, Any] = {}

    implementation_git = git_snapshot(implementation_root)
    project_git = git_snapshot(project_root)
    checks["implementation_git"] = implementation_git
    checks["project_git"] = project_git
    if implementation_git["dirty"]:
        errors.append("implementation checkout is dirty")
    if project_git["dirty"]:
        errors.append("project seed is dirty")

    events = _read_events(state_dir)
    stale = [event.type for event in events if event.type in FRESH_STATE_EVENTS]
    checks["fresh_state"] = {
        "event_count": len(events),
        "stale_event_types": stale,
    }
    if stale:
        errors.append("state directory already contains workflow/task execution events")

    config = load_config(config_path)
    catalog = workflow_route_catalog(config)
    routes = [_route_snapshot(config, route) for route in catalog["routes"]]
    matrix = _route_matrix(routes)
    checks["route_matrix"] = matrix
    for family in sorted(FAMILIES):
        if not matrix.get(family):
            errors.append(f"active catalog has no {family} route")
    if require_backend:
        wrong_backends = sorted({
            f"{binding['role']}={binding['backend']}"
            for route in routes
            for binding in route["role_bindings"]
            if binding["backend"] != require_backend
        })
        checks["required_backend"] = {
            "backend": require_backend,
            "mismatches": wrong_backends,
        }
        if wrong_backends:
            errors.append(
                "route roles do not use required backend: "
                + ", ".join(wrong_backends)
            )
    if check_host:
        host = host_readiness(playwright_image=playwright_image)
        checks["host"] = host
        errors.extend(host["errors"])

    manifest = {
        "schema_version": SUITE_MANIFEST_SCHEMA,
        "created_at": _now(),
        "status": "failed" if errors else "passed",
        "errors": errors,
        "project_root": str(project_root),
        "state_dir": str(state_dir),
        "config_path": str(config_path),
        "implementation_root": str(implementation_root),
        "implementation_commit": implementation_git["head"],
        "seed_commit": project_git["head"],
        "effective_config_digest": _effective_config_digest(config),
        "catalog_config_digest": str(catalog.get("config_digest") or ""),
        "routes": routes,
        "checks": checks,
    }
    return redact_obj(manifest)


def build_case_manifest(
    *,
    suite_manifest: Mapping[str, Any],
    family: str,
    task_id: str,
    route_id: str,
    source_root: Path | None = None,
    target_root: Path | None = None,
) -> dict[str, Any]:
    """Freeze one exact Task/route identity immediately before approval."""

    family = str(family or "").strip().lower()
    if family not in FAMILIES:
        raise RunnerPreflightError(f"unsupported Workflow family: {family!r}")
    if str(suite_manifest.get("status") or "") != "passed":
        raise RunnerPreflightError("suite preflight did not pass")
    project_root = Path(str(suite_manifest["project_root"])).resolve()
    state_dir = Path(str(suite_manifest["state_dir"])).resolve()
    config_path = Path(str(suite_manifest["config_path"])).resolve()
    config = load_config(config_path)
    catalog = workflow_route_catalog(config)
    route = resolve_workflow_route(
        config,
        route_id,
        expected_config_digest=str(catalog.get("config_digest") or ""),
    )
    if route is None:
        raise RunnerPreflightError(f"route is not active: {route_id}")
    if not _route_matches_family(route, family):
        raise RunnerPreflightError(
            f"route {route_id} does not match requested family {family}"
        )

    task = TaskStore(state_dir / "kanban.json").get(task_id)
    if task is None:
        raise RunnerPreflightError(f"Task does not exist: {task_id}")
    events = _read_events(state_dir)
    prior_invokes = [
        event for event in events
        if event.type == "workflow.invoke.requested"
        and _event_task_id(event) == task_id
    ]
    if prior_invokes:
        raise RunnerPreflightError(
            f"Task {task_id} already has a workflow invoke"
        )

    roots = _root_binding(
        family=family,
        project_root=project_root,
        source_root=source_root,
        target_root=target_root,
    )
    route_snapshot = _route_snapshot(config, route)
    return redact_obj({
        "schema_version": CASE_MANIFEST_SCHEMA,
        "created_at": _now(),
        "family": family,
        "task_id": task_id,
        "route_id": route_id,
        "entry_pattern_id": str(route.get("entry_pattern_id") or ""),
        "project_root": str(project_root),
        "state_dir": str(state_dir),
        "config_path": str(config_path),
        "implementation_commit": str(
            suite_manifest.get("implementation_commit") or ""
        ),
        "seed_commit": str(suite_manifest.get("seed_commit") or ""),
        "event_cursor": len(events),
        "effective_config_digest": _effective_config_digest(config),
        "catalog_config_digest": str(catalog.get("config_digest") or ""),
        "route_digest": route_snapshot["route_digest"],
        "role_bindings": route_snapshot["role_bindings"],
        "task_contract_digest": task_workflow_binding_digest(task),
        "task_semantic_identity_digest": _task_semantic_identity_digest(task),
        "root_binding": roots,
        "source_status": git_snapshot(project_root),
        "require_task_terminal": family in DELIVERY_FAMILIES | {"research"},
    })


def validate_case_identity(
    manifest: Mapping[str, Any],
    *,
    events: Sequence[ZfEvent] | None = None,
) -> list[str]:
    """Return current config/route/Task drift reasons without side effects."""

    errors: list[str] = []
    config_path = Path(str(manifest.get("config_path") or ""))
    state_dir = Path(str(manifest.get("state_dir") or ""))
    try:
        config = load_config(config_path)
    except Exception as exc:
        return [f"effective config is unreadable: {exc}"]
    if _effective_config_digest(config) != str(
        manifest.get("effective_config_digest") or ""
    ):
        errors.append("effective config digest changed after case freeze")
    catalog = workflow_route_catalog(config)
    if str(catalog.get("config_digest") or "") != str(
        manifest.get("catalog_config_digest") or ""
    ):
        errors.append("active route catalog digest changed after case freeze")
    route = resolve_workflow_route(config, str(manifest.get("route_id") or ""))
    if route is None:
        errors.append("frozen route is no longer active")
    else:
        current = _route_snapshot(config, route)
        if current["route_digest"] != str(manifest.get("route_digest") or ""):
            errors.append("route contract digest changed after case freeze")
        if current["role_bindings"] != list(manifest.get("role_bindings") or []):
            errors.append("route role activation changed after case freeze")
    task_id = str(manifest.get("task_id") or "")
    task = TaskStore(state_dir / "kanban.json").get(task_id)
    start_bindings = _workflow_start_bindings(
        list(events or [])[max(_int(manifest.get("event_cursor")), 0):],
        task_id=task_id,
    )
    exact_start_binding = any(
        not _workflow_start_binding_errors(binding, manifest)
        for binding in start_bindings
    )
    if task is None:
        errors.append("frozen Task is missing")
    else:
        semantic_digest = str(
            manifest.get("task_semantic_identity_digest") or ""
        )
        if (
            semantic_digest
            and _task_semantic_identity_digest(task) != semantic_digest
        ):
            errors.append("Task semantic identity changed after case freeze")
        if task_workflow_binding_digest(task) != str(
            manifest.get("task_contract_digest") or ""
        ) and not exact_start_binding:
            errors.append("Task contract digest changed after case freeze")
    if start_bindings and not exact_start_binding:
        errors.extend(_workflow_start_binding_errors(start_bindings[0], manifest))
    current_roots = _root_binding_from_manifest(manifest)
    if current_roots != dict(manifest.get("root_binding") or {}):
        errors.append("Refactor root binding changed after case freeze")
    return errors


def observe_case_terminal(
    events: Sequence[ZfEvent],
    manifest: Mapping[str, Any],
    *,
    task_status: str = "",
) -> TerminalObservation:
    """Classify the first family terminal after the frozen event cursor."""

    family = str(manifest.get("family") or "")
    task_id = str(manifest.get("task_id") or "")
    pattern_id = str(manifest.get("entry_pattern_id") or "")
    cursor = max(_int(manifest.get("event_cursor")), 0)
    new_events = list(events[cursor:])
    invokes = [
        event for event in new_events
        if event.type == "workflow.invoke.requested"
        and _event_task_id(event) == task_id
    ]
    if len(invokes) > 1:
        return _failure(
            family,
            invokes[1],
            "more than one workflow.invoke.requested was accepted for the case",
        )
    invoke = invokes[0] if invokes else None
    run_id = _workflow_run_id(invoke) if invoke is not None else ""
    if invoke is not None:
        payload = _payload(invoke)
        start_binding = next((
            binding for binding in _workflow_start_bindings(
                new_events,
                task_id=task_id,
            )
            if not _workflow_start_binding_errors(binding, manifest)
        ), {})
        submit = next((
            event for event in new_events
            if event.type == "workflow.submit.requested"
            and _event_task_id(event) == task_id
            and (
                not run_id
                or _workflow_run_id(event) == run_id
            )
        ), None)
        submit_payload = _payload(submit)
        supplied_pattern = str(
            payload.get("pattern_id")
            or submit_payload.get("pattern_id")
            or ""
        )
        if supplied_pattern != pattern_id:
            return _failure(
                family,
                invoke,
                "workflow invoke entry pattern does not match the frozen route",
                run_id=run_id,
            )
        supplied_task_digest = str(
            payload.get("task_contract_digest")
            or start_binding.get("task_contract_digest")
            or ""
        )
        if supplied_task_digest and supplied_task_digest != str(
            manifest.get("task_contract_digest") or ""
        ):
            return _failure(
                family,
                invoke,
                "workflow invoke Task contract digest is stale",
                run_id=run_id,
            )
        supplied_route = str(
            payload.get("route_id")
            or start_binding.get("route_id")
            or ""
        )
        if supplied_route and supplied_route != str(manifest.get("route_id") or ""):
            return _failure(
                family,
                invoke,
                "workflow invoke route does not match the frozen route",
                run_id=run_id,
            )

    if family == "research":
        return _observe_research_terminal(
            new_events,
            manifest=manifest,
            task_id=task_id,
            run_id=run_id,
            task_status=task_status,
        )

    pending_success: ZfEvent | None = None
    for event in new_events:
        if not _event_belongs_to_case(event, task_id=task_id, run_id=run_id):
            continue
        if event.type in FAILURE_TERMINALS or _blocked_task_event(event):
            reason = str(
                _payload(event).get("reason")
                or _payload(event).get("status")
                or event.type
            )
            return _failure(family, event, reason, run_id=run_id)
        if event.type != "run.goal.completed":
            continue
        if not bool(manifest.get("require_task_terminal")) or task_status == "done":
            return TerminalObservation(
                status="passed",
                family=family,
                reason="family run goal and required Task terminal converged",
                workflow_run_id=run_id,
                event_id=event.id,
                event_type=event.type,
                event=_event_dict(event),
            )
        pending_success = event
    if pending_success is not None:
        return TerminalObservation(
            status="pending",
            family=family,
            reason="run goal completed; Task terminal projection is pending",
            workflow_run_id=run_id,
            event_id=pending_success.id,
            event_type=pending_success.type,
            event=_event_dict(pending_success),
        )
    return TerminalObservation(
        status="pending",
        family=family,
        reason="waiting for run.goal.completed",
        workflow_run_id=run_id,
    )


def wait_for_case_terminal(
    manifest: Mapping[str, Any],
    *,
    timeout_seconds: float,
    poll_seconds: float = 1.0,
    evidence_dir: Path | None = None,
    screenshot_argv: Sequence[str] = (),
) -> TerminalObservation:
    """Poll until a family terminal, drift, or timeout is observed."""

    state_dir = Path(str(manifest.get("state_dir") or ""))
    task_id = str(manifest.get("task_id") or "")
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    while True:
        events = _read_events(state_dir)
        drift = validate_case_identity(manifest, events=events)
        if drift:
            observation = TerminalObservation(
                status="failed",
                family=str(manifest.get("family") or ""),
                reason="; ".join(drift),
                event_type="runner.identity_drift",
            )
            _maybe_capture(
                state_dir=state_dir,
                manifest=manifest,
                observation=observation,
                evidence_dir=evidence_dir,
                screenshot_argv=screenshot_argv,
            )
            return observation
        task = TaskStore(state_dir / "kanban.json").get(task_id)
        observation = observe_case_terminal(
            events,
            manifest,
            task_status=str(getattr(task, "status", "") or ""),
        )
        if observation.terminal:
            if observation.status == "failed":
                _maybe_capture(
                    state_dir=state_dir,
                    manifest=manifest,
                    observation=observation,
                    evidence_dir=evidence_dir,
                    screenshot_argv=screenshot_argv,
                )
            elif evidence_dir is not None:
                write_json(Path(evidence_dir) / "terminal-result.json", observation.to_dict())
            return observation
        if time.monotonic() >= deadline:
            timeout = TerminalObservation(
                status="failed",
                family=str(manifest.get("family") or ""),
                reason=(
                    f"terminal timeout after {timeout_seconds:g}s; "
                    f"last state: {observation.reason}"
                ),
                workflow_run_id=observation.workflow_run_id,
                event_id=observation.event_id,
                event_type="runner.timeout",
                event=observation.event,
            )
            _maybe_capture(
                state_dir=state_dir,
                manifest=manifest,
                observation=timeout,
                evidence_dir=evidence_dir,
                screenshot_argv=screenshot_argv,
            )
            return timeout
        time.sleep(max(poll_seconds, 0.01))


def capture_failure_snapshot(
    *,
    state_dir: Path,
    manifest: Mapping[str, Any],
    observation: TerminalObservation,
    out_dir: Path,
    screenshot_argv: Sequence[str] = (),
) -> Path:
    """Persist a bounded, redacted failure bundle for one exact case."""

    state_dir = Path(state_dir).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    events = _read_events(state_dir)
    task_id = str(manifest.get("task_id") or "")
    run_id = observation.workflow_run_id or _manifest_run_id(events, manifest)
    cursor = max(_int(manifest.get("event_cursor")), 0)
    related = [
        event for event in events[cursor:]
        if _event_belongs_to_case(event, task_id=task_id, run_id=run_id)
    ]
    task = TaskStore(state_dir / "kanban.json").get(task_id)
    admission = build_run_admission_projection(events)
    operations = reduce_workflow_operations(
        events,
        workflow_run_id=run_id,
        task_id=task_id,
    )
    attempts_projection = read_json(
        state_dir / "projections" / "tasks" / "task_attempts.json"
    )
    role_sessions = read_yaml(state_dir / "role_sessions.yaml")
    refs = collect_refs([_event_dict(event) for event in related])

    write_json(out_dir / "terminal.json", observation.to_dict())
    write_json(out_dir / "case-manifest.json", dict(manifest))
    write_json(
        out_dir / "related-events.json",
        {"event_count": len(related), "events": [_event_dict(e) for e in related]},
    )
    write_json(out_dir / "task.json", _primitive(task) if task is not None else {})
    write_json(
        out_dir / "run-admission.json",
        _primitive(admission.runs.get(run_id)) if run_id else {},
    )
    write_json(out_dir / "workflow-operations.json", operations)
    write_json(out_dir / "task-attempts.json", attempts_projection)
    write_json(out_dir / "role-sessions.json", role_sessions)
    write_json(out_dir / "artifact-refs.json", {"refs": refs})
    write_json(
        out_dir / "diagnostic-files.json",
        {"paths": diagnostic_paths(state_dir, task_id=task_id, run_id=run_id)},
    )
    capture_screenshot(screenshot_argv, out_dir=out_dir)
    return out_dir


def _maybe_capture(
    *,
    state_dir: Path,
    manifest: Mapping[str, Any],
    observation: TerminalObservation,
    evidence_dir: Path | None,
    screenshot_argv: Sequence[str],
) -> None:
    if evidence_dir is None:
        return
    capture_failure_snapshot(
        state_dir=state_dir,
        manifest=manifest,
        observation=observation,
        out_dir=evidence_dir,
        screenshot_argv=screenshot_argv,
    )


def _research_success(
    events: Sequence[ZfEvent],
    *,
    task_id: str,
    run_id: str,
) -> ZfEvent | None:
    by_id = {event.id: event for event in events}
    for result in events:
        payload = _payload(result)
        if (
            result.type != "workflow.result.available"
            or str(payload.get("result_kind") or "") != "research_report"
            or str(payload.get("status") or "") != "available"
            or _event_task_id(result) != task_id
            or (run_id and str(payload.get("workflow_run_id") or "") != run_id)
        ):
            continue
        terminal_id = str(payload.get("terminal_event_id") or "")
        terminal = by_id.get(terminal_id)
        terminal_payload = _payload(terminal) if terminal is not None else {}
        digest = str(payload.get("artifact_digest") or "").removeprefix("sha256:")
        descriptor = next((
            item for item in terminal_payload.get("artifact_refs") or []
            if isinstance(item, Mapping)
            and str(item.get("kind") or "") == "research_report"
            and str(item.get("ref") or item.get("path") or "")
            == str(payload.get("artifact_ref") or "")
            and str(item.get("sha256") or item.get("hash") or "")
            .removeprefix("sha256:") == digest
            and str(item.get("task_id") or "") == task_id
        ), None)
        if (
            terminal is not None
            and terminal.type == "fanout.aggregate.completed"
            and str(terminal_payload.get("status") or "") == "completed"
            and result.causation_id == terminal.id
            and len(digest) == 64
            and descriptor is not None
        ):
            return result
    return None


def _observe_research_terminal(
    events: Sequence[ZfEvent],
    *,
    manifest: Mapping[str, Any],
    task_id: str,
    run_id: str,
    task_status: str,
) -> TerminalObservation:
    pending_result: ZfEvent | None = None
    for index, event in enumerate(events):
        if not _event_belongs_to_case(event, task_id=task_id, run_id=run_id):
            continue
        if event.type in FAILURE_TERMINALS or _blocked_task_event(event):
            reason = str(
                _payload(event).get("reason")
                or _payload(event).get("status")
                or event.type
            )
            return _failure("research", event, reason, run_id=run_id)
        result = _research_success(
            events[:index + 1],
            task_id=task_id,
            run_id=run_id,
        )
        if result is None or result.id != event.id:
            continue
        if not bool(manifest.get("require_task_terminal")) or task_status == "done":
            return TerminalObservation(
                status="passed",
                family="research",
                reason="verified Research result lineage reached its family terminal",
                workflow_run_id=run_id,
                event_id=result.id,
                event_type=result.type,
                event=_event_dict(result),
            )
        pending_result = result
    if pending_result is not None:
        return TerminalObservation(
            status="pending",
            family="research",
            reason="Research result is available; Task terminal projection is pending",
            workflow_run_id=run_id,
            event_id=pending_result.id,
            event_type=pending_result.type,
            event=_event_dict(pending_result),
        )
    return TerminalObservation(
        status="pending",
        family="research",
        reason="waiting for verified Research result availability",
        workflow_run_id=run_id,
    )


def _route_snapshot(config: Any, route: Mapping[str, Any]) -> dict[str, Any]:
    role_by_identity: dict[str, Any] = {}
    for role in getattr(config, "roles", []) or []:
        for identity in (
            str(getattr(role, "name", "") or ""),
            str(getattr(role, "instance_id", "") or ""),
        ):
            if identity:
                role_by_identity[identity] = role
    bindings = []
    for identity in route.get("roles") or []:
        role = role_by_identity.get(str(identity))
        if role is None:
            bindings.append({
                "role": str(identity),
                "backend": "",
                "model": "",
                "role_config_digest": "",
            })
            continue
        bindings.append({
            "role": str(identity),
            "backend": str(getattr(role, "backend", "") or ""),
            "model": str(getattr(role, "model", "") or ""),
            "role_config_digest": _sha256_json(_primitive(role)),
        })
    route_body = redact_obj(dict(route))
    return {
        **route_body,
        "route_digest": _sha256_json(route_body),
        "role_bindings": bindings,
    }


def _route_matrix(routes: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    matrix = {family: [] for family in sorted(FAMILIES)}
    for route in routes:
        family = str(route.get("family") or "")
        kind = str(route.get("kind") or "")
        bucket = kind if family == "delivery" else family
        if bucket in matrix:
            matrix[bucket].append(str(route.get("route_id") or ""))
    return matrix


def _route_matches_family(route: Mapping[str, Any], family: str) -> bool:
    route_family = str(route.get("family") or "")
    route_kind = str(route.get("kind") or "")
    if family in DELIVERY_FAMILIES:
        return route_family == "delivery" and route_kind == family
    return route_family == family


def _root_binding(
    *,
    family: str,
    project_root: Path,
    source_root: Path | None,
    target_root: Path | None,
) -> dict[str, str]:
    if family != "refactor":
        return {}
    if source_root is None or target_root is None:
        raise RunnerPreflightError("Refactor case requires source_root and target_root")
    source = _resolve_root(project_root, source_root)
    target = _resolve_root(project_root, target_root)
    if not source.exists() or not target.exists():
        raise RunnerPreflightError("Refactor roots must exist before approval")
    if source == target or source.is_relative_to(target) or target.is_relative_to(source):
        raise RunnerPreflightError("Refactor roots must be fully disjoint after symlink resolution")
    body = {
        "schema_version": "workflow-root-binding.v1",
        "source_root": str(source),
        "target_root": str(target),
    }
    return {**body, "digest": _sha256_json(body)}


def _root_binding_from_manifest(manifest: Mapping[str, Any]) -> dict[str, str]:
    root = manifest.get("root_binding")
    if not isinstance(root, Mapping) or not root:
        return {}
    project_root = Path(str(manifest.get("project_root") or ""))
    try:
        return _root_binding(
            family="refactor",
            project_root=project_root,
            source_root=Path(str(root.get("source_root") or "")),
            target_root=Path(str(root.get("target_root") or "")),
        )
    except RunnerPreflightError:
        return {}


def _resolve_root(project_root: Path, value: Path) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = project_root / candidate
    return candidate.resolve(strict=False)


def _event_belongs_to_case(event: ZfEvent, *, task_id: str, run_id: str) -> bool:
    payload = _payload(event)
    explicit_run_id = str(
        payload.get("workflow_run_id") or payload.get("run_id") or ""
    ).strip()
    if run_id and explicit_run_id:
        return explicit_run_id == run_id
    if run_id and event.type.startswith("run.") and event.correlation_id:
        return str(event.correlation_id) == run_id
    if _event_task_id(event) == task_id:
        return True
    identities = {
        str(event.correlation_id or ""),
        str(payload.get("workflow_run_id") or ""),
        str(payload.get("run_id") or ""),
        str(payload.get("request_id") or ""),
    }
    return bool(run_id and run_id in identities)


def _workflow_start_bindings(
    events: Sequence[ZfEvent],
    *,
    task_id: str,
) -> list[dict[str, Any]]:
    """Owner-approved workflow-start inputs after the frozen event cursor."""

    bindings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        if event.type not in {
            "web.action.requested",
            "runtime.action.attempt.started",
        }:
            continue
        payload = _payload(event)
        action = str(
            payload.get("requested_action") or payload.get("action") or ""
        ).strip()
        if action != "workflow-start":
            continue
        raw = payload.get("request")
        if not isinstance(raw, Mapping):
            raw = payload.get("payload")
        if not isinstance(raw, Mapping):
            continue
        binding = {str(key): value for key, value in raw.items()}
        if str(binding.get("task_id") or event.task_id or "") != task_id:
            continue
        digest = _sha256_json(binding)
        if digest in seen:
            continue
        seen.add(digest)
        bindings.append(binding)
    return bindings


def _workflow_start_binding_errors(
    binding: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> list[str]:
    expected = {
        "task_id": str(manifest.get("task_id") or ""),
        "route_id": str(manifest.get("route_id") or ""),
        "task_contract_digest": str(
            manifest.get("task_contract_digest") or ""
        ),
        "config_digest": str(manifest.get("catalog_config_digest") or ""),
    }
    labels = {
        "task_id": "Task id",
        "route_id": "route id",
        "task_contract_digest": "Task contract digest",
        "config_digest": "catalog config digest",
    }
    errors: list[str] = []
    for key, wanted in expected.items():
        actual = str(binding.get(key) or "")
        if actual != wanted:
            errors.append(
                f"workflow start {labels[key]} does not match the frozen case"
            )
    return errors


def _blocked_task_event(event: ZfEvent) -> bool:
    return (
        event.type == "task.status_changed"
        and str(_payload(event).get("to") or "") == "blocked"
    )


def _failure(
    family: str,
    event: ZfEvent,
    reason: str,
    *,
    run_id: str = "",
) -> TerminalObservation:
    return TerminalObservation(
        status="failed",
        family=family,
        reason=reason,
        workflow_run_id=run_id or _workflow_run_id(event),
        event_id=event.id,
        event_type=event.type,
        event=_event_dict(event),
    )


def _manifest_run_id(
    events: Sequence[ZfEvent],
    manifest: Mapping[str, Any],
) -> str:
    task_id = str(manifest.get("task_id") or "")
    cursor = max(_int(manifest.get("event_cursor")), 0)
    invoke = next((
        event for event in events[cursor:]
        if event.type == "workflow.invoke.requested"
        and _event_task_id(event) == task_id
    ), None)
    return _workflow_run_id(invoke) if invoke is not None else ""


def _workflow_run_id(event: ZfEvent | None) -> str:
    if event is None:
        return ""
    payload = _payload(event)
    return str(
        payload.get("workflow_run_id")
        or payload.get("run_id")
        or (payload.get("request_id") if event.type == "workflow.invoke.requested" else "")
        or event.correlation_id
        or ""
    ).strip()


def _event_task_id(event: ZfEvent) -> str:
    payload = _payload(event)
    return str(
        event.task_id
        or payload.get("task_id")
        or payload.get("parent_task_id")
        or payload.get("root_task_id")
        or ""
    ).strip()


def _task_semantic_identity_digest(task: Any) -> str:
    """Freeze requirement-bearing Task fields, excluding runtime enrichment."""

    contract = asdict(task.contract)
    for field in CASE_RUNTIME_CONTRACT_FIELDS:
        contract.pop(field, None)
    evidence_contract = contract.get("evidence_contract")
    if isinstance(evidence_contract, dict):
        evidence_contract = dict(evidence_contract)
        for field in CASE_RUNTIME_EVIDENCE_FIELDS:
            evidence_contract.pop(field, None)
        contract["evidence_contract"] = evidence_contract
    body = {
        "id": task.id,
        "key": task.key,
        "title": task.title,
        "contract": contract,
    }
    return f"sha256:{_sha256_json(body)}"


def _effective_config_digest(config: Any) -> str:
    return _sha256_json(redact_obj(renderable_config_to_primitive(config)))


def _read_events(state_dir: Path) -> list[ZfEvent]:
    return EventLog(Path(state_dir) / "events.jsonl").read_all()


def _event_dict(event: ZfEvent) -> dict[str, Any]:
    return redact_obj(asdict(event))


def _payload(event: ZfEvent | None) -> dict[str, Any]:
    if event is None or not isinstance(event.payload, dict):
        return {}
    return event.payload


def _primitive(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_primitive(item) for item in value]
    return value


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    suite = subparsers.add_parser("suite-preflight")
    suite.add_argument("--project-root", type=Path, required=True)
    suite.add_argument("--state-dir", type=Path, required=True)
    suite.add_argument("--config", type=Path, required=True)
    suite.add_argument("--implementation-root", type=Path, required=True)
    suite.add_argument("--require-backend", default="codex")
    suite.add_argument("--check-host", action="store_true")
    suite.add_argument("--playwright-image", default="mcp/playwright:latest")
    suite.add_argument("--out", type=Path, required=True)

    case = subparsers.add_parser("prepare-case")
    case.add_argument("--suite-manifest", type=Path, required=True)
    case.add_argument("--family", choices=sorted(FAMILIES), required=True)
    case.add_argument("--task-id", required=True)
    case.add_argument("--route-id", required=True)
    case.add_argument("--source-root", type=Path)
    case.add_argument("--target-root", type=Path)
    case.add_argument("--out", type=Path, required=True)

    wait = subparsers.add_parser("wait")
    wait.add_argument("--case-manifest", type=Path, required=True)
    wait.add_argument("--timeout", type=float, default=900)
    wait.add_argument("--poll", type=float, default=1)
    wait.add_argument("--evidence-dir", type=Path, required=True)
    wait.add_argument(
        "--screenshot-argv-json",
        default="[]",
        help="JSON array command executed once on terminal failure",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "suite-preflight":
            result = build_suite_preflight_manifest(
                project_root=args.project_root,
                state_dir=args.state_dir,
                config_path=args.config,
                implementation_root=args.implementation_root,
                require_backend=args.require_backend,
                check_host=args.check_host,
                playwright_image=args.playwright_image,
            )
            write_json(args.out, result)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result["status"] == "passed" else 20
        if args.command == "prepare-case":
            suite = read_json(args.suite_manifest)
            result = build_case_manifest(
                suite_manifest=suite,
                family=args.family,
                task_id=args.task_id,
                route_id=args.route_id,
                source_root=args.source_root,
                target_root=args.target_root,
            )
            write_json(args.out, result)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        manifest = read_json(args.case_manifest)
        screenshot = json.loads(args.screenshot_argv_json)
        if not isinstance(screenshot, list) or not all(
            isinstance(item, str) for item in screenshot
        ):
            raise RunnerPreflightError("screenshot argv must be a JSON string array")
        result = wait_for_case_terminal(
            manifest,
            timeout_seconds=args.timeout,
            poll_seconds=args.poll,
            evidence_dir=args.evidence_dir,
            screenshot_argv=screenshot,
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False))
        return 0 if result.status == "passed" else 20
    except (RunnerPreflightError, OSError, ValueError) as exc:
        print(f"five-workflow runner preflight failed: {exc}", file=sys.stderr)
        return 20


if __name__ == "__main__":
    raise SystemExit(main())
