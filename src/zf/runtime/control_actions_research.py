"""Controlled Research fanout start and result adoption actions."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from zf.core.events import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.backend import validate_provider_session_config
from zf.runtime.control_actions_helpers import _required_text, _workflow_stage
from zf.runtime.research_templates import (
    ADAPTIVE_RESEARCH_TEMPLATE,
    DEFAULT_RESEARCH_TEMPLATE,
    RESEARCH_TEMPLATES_BY_ID,
    ResearchTemplate,
    research_root_role,
    resolve_research_template,
)
from zf.runtime.workflow_requests import (
    WorkflowRequestError,
    adopt_workflow_research_result,
    require_current_workflow_request,
)
from zf.runtime.workflow_anchor import workflow_task_request_binding
from zf.runtime.workflow_origin import (
    WorkflowOriginError,
    assert_same_workflow_origin,
    workflow_origin_digest,
)
from zf.runtime.workflow_results import WORKFLOW_RESULT_AVAILABLE


RESEARCH_TEMPLATE_ID = DEFAULT_RESEARCH_TEMPLATE.template_id
RESEARCH_PATTERN_ID = DEFAULT_RESEARCH_TEMPLATE.pattern_id
RESEARCH_CHILD_ROLES = DEFAULT_RESEARCH_TEMPLATE.child_roles
RESEARCH_SYNTH_ROLE = DEFAULT_RESEARCH_TEMPLATE.synth_role


class ResearchActionsMixin:
    def _research_start(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        task_id = _required_text(payload, "task_id")
        topic = (
            _required_text(payload, "topic")
            or _required_text(payload, "objective")
            or _required_text(payload, "message")
        )
        template = resolve_research_template(
            _required_text(payload, "template_id")
        )
        if template is None:
            allowed = ", ".join(sorted(RESEARCH_TEMPLATES_BY_ID))
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id or None,
                reason=(
                    "unknown research template; registered templates: "
                    f"{allowed}"
                ),
                status_code=422,
                status="invalid_payload",
            )
        task = TaskStore(self.state_dir / "kanban.json").get(task_id)
        if task is None:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id or None,
                reason=f"research task {task_id!r} does not exist",
                status_code=404,
                status="preflight_blocked",
            )
        task_request = workflow_task_request_binding(task)
        request_id = _required_text(payload, "request_id")
        try:
            request_revision = int(payload.get("request_revision") or 0)
        except (TypeError, ValueError):
            request_revision = 0
        if task_request and not request_id:
            request_id = str(task_request["request_id"])
            request_revision = int(task_request["request_revision"])
        if _required_text(payload, "channel_id") and not request_id:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id,
                reason="channel-bound research requires request_id and request_revision",
                status_code=422,
                status="invalid_payload",
            )
        request_projection: dict = {}
        if request_id:
            try:
                request_projection = require_current_workflow_request(
                    self.state_dir,
                    request_id,
                    request_revision,
                )
            except WorkflowRequestError as exc:
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=task_id,
                    reason=str(exc),
                    status_code=409,
                    status="stale_or_missing_request",
                )
            if (
                not task_request
                or task_request["request_id"] != request_id
                or int(task_request["request_revision"])
                != request_revision
                or (
                    task_request.get("origin_binding_digest")
                    and task_request["origin_binding_digest"]
                    != workflow_origin_digest(
                        request_projection["origin_binding"]
                    )
                )
            ):
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=task_id,
                    reason="research Task is not bound to the current Workflow Request",
                    status_code=409,
                    status="workflow_task_stale",
                )
            origin = request_projection["origin_binding"]
            supplied_channel = _required_text(payload, "channel_id")
            supplied_thread = _required_text(payload, "thread_id")
            if (
                supplied_channel
                and supplied_channel != str(origin.get("channel_id") or "")
            ) or (
                supplied_thread
                and supplied_thread != str(origin.get("thread_id") or "")
            ):
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=task_id,
                    reason="research return target does not match Workflow Request origin",
                    status_code=409,
                    status="origin_binding_mismatch",
                )
        stage_error = _research_stage_error(self.config, template)
        if stage_error:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id,
                reason=stage_error,
                status_code=409,
                status="preflight_blocked",
            )
        invoke_payload = dict(payload)
        invoke_payload.update({
            "task_id": task_id,
            "pattern_id": template.pattern_id,
            "requested_by": _required_text(payload, "requested_by")
            or "skill:zf-research-fanout-trigger",
            "reason": _required_text(payload, "reason")
            or (
                "explicit adaptive Research pilot request"
                if template is ADAPTIVE_RESEARCH_TEMPLATE
                else "explicit fixed Research audit request"
            ),
            "expected_output": _required_text(payload, "expected_output")
            or "research synthesis plus PRD/refactor prompt inputs",
            "risk": _required_text(payload, "risk")
            or (
                "opt-in provider-native read-only pilot; child telemetry is "
                "self-reported and fixed audit remains the fallback"
                if template is ADAPTIVE_RESEARCH_TEMPLATE
                else "cost-bearing fixed multi-agent research audit"
            ),
        })
        if request_projection:
            origin = request_projection["origin_binding"]
            invoke_payload.update({
                "request_id": request_id,
                "request_revision": request_revision,
                "origin_binding": dict(origin),
                "channel_id": str(origin.get("channel_id") or ""),
                "thread_id": str(origin.get("thread_id") or ""),
            })
        source_refs = (
            dict(payload.get("source_refs"))
            if isinstance(payload.get("source_refs"), dict)
            else {}
        )
        source_refs.update({
            "template_id": template.template_id,
            "research_rollout": template.rollout,
            "topic": topic,
            "trigger_surface": self.surface,
        })
        invoke_payload["source_refs"] = source_refs
        result = self._workflow_invoke(
            requested=requested,
            action=action,
            requested_action=requested_action,
            payload=invoke_payload,
        )
        result.update({
            "template_id": template.template_id,
            "topic": topic,
        })
        return result

    def _research_adopt(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        result_event_id = _required_text(payload, "result_event_id")
        request_id = _required_text(payload, "request_id")
        artifact_ref = _required_text(payload, "artifact_ref")
        artifact_digest = _required_text(payload, "artifact_digest")
        summary = _required_text(payload, "summary")
        if not result_event_id:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_required_text(payload, "task_id") or None,
                reason="result_event_id is required",
                status_code=422,
                status="invalid_payload",
            )
        try:
            request_revision = int(payload.get("request_revision"))
        except (TypeError, ValueError):
            request_revision = 0
        path = _resolve_artifact_path(
            artifact_ref,
            state_dir=self.state_dir,
            project_root=self.project_root,
        )
        if path is None:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_required_text(payload, "task_id") or None,
                reason="research artifact_ref must resolve inside project_root or state_dir",
                status_code=422,
                status="invalid_artifact",
            )
        actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        expected_digest = artifact_digest.removeprefix("sha256:").lower()
        if actual_digest != expected_digest:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_required_text(payload, "task_id") or None,
                reason="research artifact digest mismatch",
                status_code=409,
                status="artifact_mismatch",
            )
        try:
            request_projection = require_current_workflow_request(
                self.state_dir,
                request_id,
                request_revision,
            )
        except WorkflowRequestError as exc:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_required_text(payload, "task_id") or None,
                reason=str(exc),
                status_code=409,
                status="stale_or_missing_request",
            )
        events = self.writer.event_log.read_all()
        candidates = [
            event
            for event in events
            if event.type == WORKFLOW_RESULT_AVAILABLE
            and (
                not result_event_id
                or event.id == result_event_id
            )
            and str((event.payload or {}).get("artifact_ref") or "")
            == artifact_ref
            and str((event.payload or {}).get("artifact_digest") or "")
            .removeprefix("sha256:")
            .lower()
            == actual_digest
        ]
        if len(candidates) != 1:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_required_text(payload, "task_id") or None,
                reason=(
                    "research adoption requires one matching "
                    "workflow.result.available event"
                ),
                status_code=409,
                status="invalid_result_lineage",
            )
        result_event = candidates[0]
        result_payload = (
            result_event.payload
            if isinstance(result_event.payload, dict)
            else {}
        )
        lineage_error = _research_result_lineage_error(
            events,
            result_event=result_event,
            result_payload=result_payload,
            artifact_ref=artifact_ref,
            artifact_digest=actual_digest,
        )
        if lineage_error:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_required_text(payload, "task_id") or None,
                reason=lineage_error,
                status_code=409,
                status="invalid_result_lineage",
            )
        if (
            str(result_payload.get("request_id") or "") != request_id
            or int(result_payload.get("request_revision") or 0)
            != request_revision
        ):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_required_text(payload, "task_id") or None,
                reason="research result does not match the Workflow Request revision",
                status_code=409,
                status="invalid_result_lineage",
            )
        canonical_task_id = str(result_payload.get("task_id") or "")
        canonical_run_id = str(
            result_payload.get("workflow_run_id") or ""
        )
        canonical_terminal_id = str(
            result_payload.get("terminal_event_id") or ""
        )
        if (
            not canonical_task_id
            or not canonical_run_id
            or not canonical_terminal_id
        ):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=canonical_task_id or None,
                reason=(
                    "research result is missing Task, Run, or terminal lineage"
                ),
                status_code=409,
                status="invalid_result_lineage",
            )
        task = TaskStore(self.state_dir / "kanban.json").get(
            canonical_task_id
        )
        task_request = (
            workflow_task_request_binding(task)
            if task is not None
            else {}
        )
        if (
            not task_request
            or task_request["request_id"] != request_id
            or int(task_request["request_revision"]) != request_revision
            or task_request.get("origin_binding_digest")
            != workflow_origin_digest(
                request_projection["origin_binding"]
            )
        ):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=canonical_task_id,
                reason=(
                    "research result Task is not bound to the current "
                    "Workflow Request"
                ),
                status_code=409,
                status="invalid_result_lineage",
            )
        for key, canonical in (
            ("task_id", canonical_task_id),
            ("workflow_run_id", canonical_run_id),
            ("terminal_event_id", canonical_terminal_id),
        ):
            supplied = _required_text(payload, key)
            if supplied and supplied != canonical:
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=canonical_task_id or None,
                    reason=f"research result {key} lineage mismatch",
                    status_code=409,
                    status="invalid_result_lineage",
                )
        try:
            assert_same_workflow_origin(
                request_projection["origin_binding"],
                result_payload.get("origin_binding") or {},
            )
        except WorkflowOriginError as exc:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=canonical_task_id or None,
                reason=str(exc),
                status_code=409,
                status="invalid_result_lineage",
            )
        canonical_origin = request_projection["origin_binding"]
        canonical_channel_id = (
            str(canonical_origin.get("channel_id") or "")
            if canonical_origin.get("surface") == "channel"
            else ""
        )
        canonical_thread_id = (
            str(canonical_origin.get("thread_id") or "main")
            if canonical_channel_id
            else ""
        )
        if (
            _required_text(payload, "channel_id")
            and _required_text(payload, "channel_id")
            != canonical_channel_id
        ) or (
            _required_text(payload, "thread_id")
            and _required_text(payload, "thread_id")
            != canonical_thread_id
        ):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=canonical_task_id or None,
                reason="research adoption destination does not match Request origin",
                status_code=409,
                status="origin_binding_mismatch",
            )
        try:
            projection, created = adopt_workflow_research_result(
                self.state_dir,
                request_id,
                expected_revision=request_revision,
                artifact_ref=artifact_ref,
                artifact_digest=actual_digest,
                summary=summary,
                actor=self.actor,
                source_event_id=result_event.id,
                result_event_id=result_event.id,
                task_id=canonical_task_id,
                workflow_run_id=canonical_run_id,
                terminal_event_id=canonical_terminal_id,
                writer=self.writer,
            )
        except WorkflowRequestError as exc:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_required_text(payload, "task_id") or None,
                reason=str(exc),
                status_code=409,
                status="stale_or_missing_request",
            )
        channel_id = canonical_channel_id
        thread_id = canonical_thread_id
        adopted_event = next(
            (
                event
                for event in reversed(self.writer.event_log.read_all())
                if event.type == "workflow.research.adopted"
                and str((event.payload or {}).get("request_id") or "") == request_id
                and str((event.payload or {}).get("artifact_digest") or "") == actual_digest
            ),
            requested,
        )
        if created and channel_id:
            self.writer.emit(
                "channel.artifact.attached",
                actor=self.actor,
                task_id=canonical_task_id or None,
                causation_id=adopted_event.id,
                correlation_id=channel_id,
                payload={
                    "channel_id": channel_id,
                    "thread_id": thread_id or "main",
                    "artifact_id": f"research-adoption-{actual_digest[:16]}",
                    "request_id": request_id,
                    "task_id": canonical_task_id,
                    "kind": "research_report",
                    "path": artifact_ref,
                    "sha256": actual_digest,
                    "hash": f"sha256:{actual_digest}",
                    "summary": summary,
                    "provenance": {
                        "source_event_id": result_event.id,
                        "terminal_event_id": canonical_terminal_id,
                        "request_revision": request_revision,
                    },
                    "source": self.surface,
                },
            )
            self.writer.emit(
                "channel.state_update.posted",
                actor=self.actor,
                task_id=canonical_task_id or None,
                causation_id=adopted_event.id,
                correlation_id=channel_id,
                payload={
                    "channel_id": channel_id,
                    "thread_id": thread_id or "main",
                    "status": "research_adopted",
                    "summary": summary,
                    "task_id": canonical_task_id,
                    "refs": {
                        "request_id": request_id,
                        "request_revision": request_revision,
                        "artifact_ref": artifact_ref,
                        "artifact_digest": actual_digest,
                        "workflow_research_event_id": adopted_event.id,
                        "workflow_result_event_id": result_event.id,
                        "workflow_run_id": canonical_run_id,
                        "terminal_event_id": canonical_terminal_id,
                    },
                    "source": self.surface,
                },
            )
        self._completed(
            requested=requested,
            event=adopted_event,
            action=action,
            requested_action=requested_action,
            status="adopted" if created else "already_adopted",
            task_id=canonical_task_id or None,
            extra={
                "request_id": request_id,
                "request_revision": request_revision,
                "artifact_ref": artifact_ref,
                "artifact_digest": actual_digest,
            },
        )
        return {
            "_status_code": 202 if created else 200,
            "ok": True,
            "status": "adopted" if created else "already_adopted",
            "action": action,
            "requested_action": requested_action,
            "request_id": request_id,
            "request_revision": request_revision,
            "artifact_ref": artifact_ref,
            "artifact_digest": actual_digest,
            "result_event_id": result_event.id,
            "task_id": canonical_task_id,
            "workflow_run_id": canonical_run_id,
            "terminal_event_id": canonical_terminal_id,
            "event_id": adopted_event.id,
        }


def _research_result_lineage_error(
    events: list[ZfEvent],
    *,
    result_event: ZfEvent,
    result_payload: dict[str, Any],
    artifact_ref: str,
    artifact_digest: str,
) -> str:
    if (
        str(result_payload.get("schema_version") or "")
        != "workflow-result.v1"
        or str(result_payload.get("result_kind") or "")
        != "research_report"
        or str(result_payload.get("status") or "") != "available"
    ):
        return "research result event has an invalid result contract"
    terminal_event_id = str(
        result_payload.get("terminal_event_id") or ""
    ).strip()
    terminal = next(
        (event for event in events if event.id == terminal_event_id),
        None,
    )
    if terminal is None:
        return "research result terminal event does not exist"
    if (
        terminal.type != "fanout.aggregate.completed"
        or result_event.causation_id != terminal.id
    ):
        return "research result is not caused by a terminal fanout aggregate"
    terminal_payload = (
        terminal.payload if isinstance(terminal.payload, dict) else {}
    )
    if str(terminal_payload.get("status") or "") != "completed":
        return "research result terminal aggregate is not completed"
    canonical_task_id = str(result_payload.get("task_id") or "").strip()
    if (
        not canonical_task_id
        or result_event.task_id != canonical_task_id
        or (
            terminal.task_id
            and terminal.task_id != canonical_task_id
        )
    ):
        return "research result terminal Task lineage mismatch"
    artifact_refs = terminal_payload.get("artifact_refs")
    if not isinstance(artifact_refs, list):
        artifact_refs = []
    descriptor = next(
        (
            item
            for item in artifact_refs
            if isinstance(item, dict)
            and str(item.get("kind") or "") == "research_report"
            and str(item.get("ref") or item.get("path") or "")
            == artifact_ref
            and str(
                item.get("sha256") or item.get("hash") or ""
            ).removeprefix("sha256:").lower()
            == artifact_digest
        ),
        None,
    )
    if descriptor is None:
        return "research result artifact is not bound to the terminal aggregate"
    checks = (
        ("task_id", canonical_task_id),
        (
            "workflow_run_id",
            str(result_payload.get("workflow_run_id") or "").strip(),
        ),
        (
            "request_id",
            str(result_payload.get("request_id") or "").strip(),
        ),
    )
    for key, expected in checks:
        actual = str(descriptor.get(key) or "").strip()
        if not expected or actual != expected:
            return f"research result terminal {key} lineage mismatch"
    try:
        descriptor_revision = int(
            descriptor.get("request_revision") or 0
        )
        result_revision = int(
            result_payload.get("request_revision") or 0
        )
    except (TypeError, ValueError):
        return "research result terminal request_revision is invalid"
    if (
        descriptor_revision < 1
        or result_revision < 1
        or descriptor_revision != result_revision
    ):
        return "research result terminal request_revision lineage mismatch"
    return ""


def _research_stage_error(
    config: Any,
    template: ResearchTemplate,
) -> str:
    stage = _workflow_stage(config, template.pattern_id)
    if stage is None:
        return f"{template.pattern_id} stage is not declared in zf.yaml"
    if str(getattr(stage, "topology", "") or "") != "fanout_reader":
        return f"{template.pattern_id} stage must use fanout_reader topology"
    children = tuple(
        str(getattr(item, "role_instance", "") or "")
        for item in getattr(stage, "children", []) or []
    )
    if children != template.child_roles:
        return (
            f"{template.pattern_id} stage must declare children "
            f"{list(template.child_roles)!r}"
        )
    synth_role = str(getattr(getattr(stage, "aggregate", None), "synth_role", "") or "")
    if synth_role != template.synth_role:
        if not template.synth_role:
            return (
                f"{template.pattern_id} stage must use direct root "
                "aggregation without aggregate.synth_role"
            )
        return (
            f"{template.pattern_id} stage must declare "
            f"{template.synth_role} as aggregate.synth_role"
        )
    if template is not ADAPTIVE_RESEARCH_TEMPLATE:
        return ""

    roles = list(getattr(config, "roles", []) or [])
    root = next(
        (
            role
            for role in roles
            if research_root_role(template)
            in {
                str(getattr(role, "name", "") or ""),
                str(getattr(role, "instance_id", "") or ""),
            }
        ),
        None,
    )
    if root is None:
        return "adaptive Research root role is not declared"
    if str(getattr(root, "role_kind", "") or "") != "reader":
        return "adaptive Research root must be a reader role"
    if str(getattr(root, "backend", "") or "") != "claude-code":
        return (
            "adaptive Research pilot currently requires claude-code; "
            "use research:fixed until another provider passes the "
            "root-result and read-only child pilot"
        )
    allowed_tools = {
        str(item or "").split("(", 1)[0].strip().lower()
        for item in getattr(root, "allowed_tools", []) or []
    }
    if (
        str(getattr(root, "permission_mode", "") or "") != "allowlist"
        or "agent" not in allowed_tools
        or allowed_tools & {"write", "edit", "notebookedit"}
    ):
        return (
            "adaptive Research root requires an allowlisted Agent tool and "
            "must not expose project write/edit tools"
        )
    try:
        validate_provider_session_config(root)
    except ValueError as exc:
        return str(exc)
    return ""


def _resolve_artifact_path(
    ref: str,
    *,
    state_dir: Path,
    project_root: Path | None,
) -> Path | None:
    if not ref:
        return None
    raw = Path(ref).expanduser()
    roots = [Path(state_dir).resolve()]
    if project_root is not None:
        roots.append(Path(project_root).resolve())
    candidates = [raw] if raw.is_absolute() else [root / raw for root in roots]
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file() and any(resolved.is_relative_to(root) for root in roots):
            return resolved
    return None
