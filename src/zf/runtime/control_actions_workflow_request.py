"""Controlled Project Request creation and explicit workflow ignition."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from pathlib import Path

from zf.core.events import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.control_actions_helpers import _required_text, _string_list
from zf.runtime.workflow_delivery import (
    apply_flow_submit,
    build_flow_submit_preview,
)
from zf.runtime.workflow_intake import build_flow_intake
from zf.runtime.workflow_anchor import (
    is_workflow_managed_task,
    mark_workflow_managed_task,
)
from zf.runtime.workflow_start import WorkflowStartService


class WorkflowRequestActionsMixin:
    def _workflow_start(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        preview = WorkflowStartService(
            self.state_dir,
            self.config,
        ).preview(
            payload,
            require_bindings=True,
            origin=self.surface,
        )
        if not preview.get("ok"):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=str(preview.get("task_id") or "") or None,
                reason=str(preview.get("reason") or "workflow preflight blocked"),
                status_code=int(preview.get("_status_code") or 409),
                status=str(preview.get("status") or "preflight_blocked"),
            )
        normalized = dict(preview.get("payload") or {})
        task_id = str(normalized.get("task_id") or "")
        route_id = str(normalized.get("route_id") or "")
        objective = str(normalized.get("objective") or "")
        route = dict(preview.get("route") or {})
        parameters = (
            dict(normalized.get("parameters"))
            if isinstance(normalized.get("parameters"), dict)
            else {}
        )
        task_store = TaskStore(self.state_dir / "kanban.json")
        workflow_task = task_store.get(task_id)
        if (
            workflow_task is not None
            and not is_workflow_managed_task(workflow_task)
        ):
            mark_workflow_managed_task(workflow_task)
            task_store.update(task_id, contract=workflow_task.contract)
            self.writer.emit(
                "task.contract.update",
                actor=self.actor,
                task_id=task_id,
                causation_id=requested.id,
                correlation_id=requested.correlation_id,
                payload={
                    "source": "workflow_start",
                    "contract": asdict(workflow_task.contract),
                    "execution_owner": "workflow",
                },
            )
        common_payload = {
            **parameters,
            "task_id": task_id,
            "objective": objective,
            "route_id": route_id,
            "pattern_id": str(route.get("entry_pattern_id") or ""),
            "requested_by": self.actor,
            "reason": _required_text(payload, "reason")
            or f"approved task workflow route {route_id}",
        }
        for key in (
            "artifact_refs",
            "channel_id",
            "source_refs",
            "thread_id",
        ):
            value = normalized.get(key)
            if value not in (None, "", [], {}):
                common_payload[key] = value
        adapter = str(route.get("start_adapter") or "")
        if adapter == "fixed_research":
            result = self._research_start(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload={
                    **common_payload,
                    "topic": str(
                        parameters.get("topic")
                        or objective
                    ),
                },
            )
        elif adapter == "registered_general":
            result = self._workflow_invoke(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=common_payload,
            )
        elif adapter == "delivery_request_submit":
            request_result = self._workflow_request(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload={
                    **common_payload,
                    "kind": str(route.get("kind") or ""),
                },
            )
            if not request_result.get("ok"):
                return {
                    **request_result,
                    "route_id": route_id,
                    "route": route,
                }
            result = self._workflow_submit(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload={
                    **common_payload,
                    "kind": str(route.get("kind") or ""),
                    "intake_ref": str(
                        request_result.get("intake_ref") or ""
                    ),
                },
            )
            result["workflow_request"] = {
                key: request_result.get(key)
                for key in (
                    "request_id",
                    "intake_ref",
                    "workflow_input_manifest_ref",
                    "submit_preview_ref",
                )
            }
        else:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id,
                reason=f"workflow route {route_id!r} has no start adapter",
                status_code=409,
                status="workflow_route_unavailable",
            )
        result.update({
            "route_id": route_id,
            "route": route,
        })
        return result

    def _workflow_request(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        if self.project_root is None or self.config is None:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="initialized project context is required",
                status_code=409,
                status="project_initialization_required",
            )
        config_ref = Path(
            _required_text(payload, "config_ref") or self.project_root / "zf.yaml"
        ).expanduser()
        if not config_ref.is_absolute():
            config_ref = self.project_root / config_ref
        if not config_ref.exists():
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason=f"workflow config does not exist: {config_ref}",
                status_code=409,
                status="project_initialization_required",
            )

        objective = (
            _required_text(payload, "objective")
            or _required_text(payload, "message")
            or _required_text(payload, "title")
        )
        request_id = _required_text(payload, "request_id") or _stable_request_id(
            self.project_root,
            requested.id,
            objective,
        )
        source_ref = _project_ref(
            self.project_root,
            self.state_dir,
            _required_text(payload, "source_ref") or _required_text(payload, "artifact_ref"),
        )
        intake = build_flow_intake(
            kind=_required_text(payload, "kind") or "auto",
            source_ref=source_ref,
            objective=objective,
            source_root=_required_text(payload, "source_root"),
            target_root=_required_text(payload, "target_root") or _required_text(payload, "target"),
            backend=_required_text(payload, "backend"),
            lanes=int(payload.get("lanes") or payload.get("requested_lanes") or 0),
            project_id=_required_text(payload, "project_id") or self.config.project.name,
            project_name=self.config.project.name,
            strictness=_required_text(payload, "strictness") or "standard",
            acceptance=tuple(_string_list(payload.get("acceptance"))),
            constraints=tuple(_string_list(payload.get("constraints"))),
            open_questions=tuple(_string_list(payload.get("open_questions"))),
            request_id=request_id,
            source=self.surface,
            created_by=self.actor,
            channel_id=_required_text(payload, "channel_id"),
            thread_id=_required_text(payload, "thread_id"),
            source_refs=(
                {
                    str(key): str(value)
                    for key, value in payload.get("source_refs", {}).items()
                    if str(key).strip() and str(value).strip()
                }
                if isinstance(payload.get("source_refs"), dict)
                else {}
            ),
            output=self.project_root / "docs" / "intake" / f"{request_id}.md",
        )
        preview = build_flow_submit_preview(
            config_path=config_ref,
            intake_path=Path(str(intake["intake_ref"])),
            flow_kind=_required_text(payload, "kind"),
            task_id=_required_text(payload, "task_id"),
            pattern_id=_required_text(payload, "pattern_id"),
            requested_by=self.actor,
            reason=_required_text(payload, "reason") or "workflow request proposal",
            allow_missing_env=bool(payload.get("allow_missing_env")),
        )
        ready = preview.get("status") != "STOP"
        return {
            "_status_code": 202 if ready else 409,
            "ok": ready,
            "status": "proposal_ready" if ready else "clarification_required",
            "action": action,
            "requested_action": requested_action,
            "request_id": request_id,
            "intake_ref": str(intake["intake_ref"]),
            "workflow_input_manifest_ref": str(intake["workflow_input_manifest_ref"]),
            "request_projection_ref": str(intake.get("request_projection_ref") or ""),
            "submit_preview_ref": str(preview.get("submit_preview_ref") or ""),
            "blockers": list(preview.get("blockers") or []),
        }

    def _workflow_submit(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        if self.project_root is None or self.config is None:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="initialized project context is required",
                status_code=409,
                status="project_initialization_required",
            )
        intake_ref = _required_text(payload, "intake_ref") or _required_text(payload, "intake")
        if not intake_ref or intake_ref == "<created-intake-ref>":
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="intake_ref is required",
                status_code=422,
                status="invalid_payload",
            )
        intake_path = Path(intake_ref).expanduser()
        if not intake_path.is_absolute():
            intake_path = self.project_root / intake_path
        config_ref = Path(
            _required_text(payload, "config_ref") or self.project_root / "zf.yaml"
        ).expanduser()
        if not config_ref.is_absolute():
            config_ref = self.project_root / config_ref
        result = apply_flow_submit(
            config_path=config_ref,
            intake_path=intake_path,
            flow_kind=_required_text(payload, "kind"),
            task_id=_required_text(payload, "task_id"),
            pattern_id=_required_text(payload, "pattern_id"),
            requested_by=self.actor,
            reason=_required_text(payload, "reason") or "approved workflow request",
            allow_missing_env=bool(payload.get("allow_missing_env")),
        )
        accepted = result.get("status") != "STOP"
        return {
            "_status_code": 202 if accepted else 409,
            "ok": accepted,
            "status": str(result.get("status") or "STOP"),
            "action": action,
            "requested_action": requested_action,
            "request_id": str((result.get("payload") or {}).get("request_id") or ""),
            "workflow_invoke_event_id": str(result.get("workflow_invoke_event_id") or ""),
            "event_ids": list(result.get("event_ids") or []),
            "blockers": list(result.get("blockers") or []),
        }


def _stable_request_id(project_root: Path, requested_event_id: str, objective: str) -> str:
    digest = hashlib.sha256(
        f"{project_root.resolve()}\0{requested_event_id}\0{objective}".encode("utf-8")
    ).hexdigest()[:16]
    return f"workflow-{digest}"


def _project_ref(project_root: Path, state_dir: Path, raw: str) -> str:
    if not raw:
        return ""
    path = Path(raw).expanduser()
    if path.is_absolute():
        return str(path)
    for candidate in (project_root / path, state_dir / path):
        if candidate.exists():
            return str(candidate)
    return raw
