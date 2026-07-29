"""Apply an exact action bound to a durable Kanban Agent Plan option."""

from __future__ import annotations

import hashlib
from typing import Any

from zf.core.events import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.state.locks import locked_path
from zf.core.task.store import TaskStore
from zf.runtime.channel_sidecar import (
    SidecarRefError,
    hydrate_channel_message_text,
)
from zf.runtime.control_actions_helpers import (
    _normal_channel_id,
    _task_id_from_payload,
    validate_shared_action_payload,
)
from zf.runtime.kanban_plan_requests import (
    PLAN_ANSWERED_EVENT,
    PLAN_APPLY_ALLOWED_ACTIONS,
    PLAN_DIRECT_APPLY_ACTIONS,
    PLAN_PROPOSAL_ACTIONS,
    PLAN_REQUESTED_EVENT,
    PLAN_RESPONSE_SCHEMA_VERSION,
    normalize_plan_request_revision,
    plan_requirement_digest,
    plan_response_gate,
)
from zf.runtime.kanban_proposals import (
    PROPOSAL_EVENT,
    PROPOSAL_EVENT_TYPES,
    canonical_proposal_action,
    proposal_payload_digest,
)
from zf.runtime.task_workflow_plans import (
    build_task_workflow_plan_request,
    task_workflow_binding_digest,
)
from zf.runtime.workflow_route_catalog import (
    resolve_workflow_route,
    workflow_route_catalog,
)
from zf.runtime.workflow_start import is_workflow_start_action


class PlanApplyActionsMixin:
    def _kanban_plan_apply(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        response = payload.get("plan_response")
        if not isinstance(response, dict):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="plan_response is required",
                status="invalid_payload",
            )
        request_event_id = str(response.get("request_event_id") or "").strip()
        lock_id = hashlib.sha256(request_event_id.encode("utf-8")).hexdigest()[:24]
        with locked_path(
            self.state_dir / "locks" / f"kanban-plan-apply-{lock_id}"
        ):
            return self._apply_bound_plan_option(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
                response=response,
            )

    def _apply_bound_plan_option(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
        response: dict[str, Any],
    ) -> dict:
        events = self.writer.event_log.read_all()
        request_event_id = str(response.get("request_event_id") or "")
        request_event = next(
            (
                event
                for event in events
                if event.id == request_event_id
                and event.type == PLAN_REQUESTED_EVENT
            ),
            None,
        )
        gate = plan_response_gate(
            events,
            request_event_id=request_event_id,
            request_id=str(response.get("request_id") or ""),
            revision=response.get("revision"),
            question_id=str(response.get("question_id") or ""),
            option_id=str(response.get("option_id") or ""),
            answer=str(response.get("answer") or ""),
            answers=response.get("answers"),
        )
        if not gate.get("ok"):
            gate_status = str(
                gate.get("status") or "plan_response_rejected"
            )
            extra: dict[str, Any] = {}
            if gate_status == "plan_request_superseded":
                replacement = _latest_plan_revision(
                    events,
                    request_id=str(response.get("request_id") or ""),
                )
                extra = {
                    "actionability": "observation",
                    "replacement_plan_event_id": str(
                        replacement.get("request_event_id") or ""
                    ),
                    "replacement_revision": int(
                        replacement.get("revision") or 0
                    ),
                }
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason=gate_status,
                status_code=409,
                status=gate_status,
                extra=extra,
            )
        if gate.get("status") == "already_answered":
            answer_event = next(
                (
                    event
                    for event in events
                    if event.id == str(gate.get("answer_event_id") or "")
                ),
                None,
            )
            answer_payload = (
                answer_event.payload
                if answer_event is not None
                and isinstance(answer_event.payload, dict)
                else {}
            )
            applied_result = (
                answer_payload.get("applied_result")
                if isinstance(answer_payload.get("applied_result"), dict)
                else {}
            )
            return {
                "_status_code": 200,
                "ok": True,
                "status": str(
                    answer_payload.get("status") or "already_applied"
                ),
                "action": action,
                "requested_action": requested_action,
                "answer_event_id": str(gate.get("answer_event_id") or ""),
                "applied_action": str(
                    answer_payload.get("applied_action") or ""
                ),
                **applied_result,
            }
        if request_event is None:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="plan_request_not_found",
                status_code=404,
                status="plan_request_not_found",
            )

        submit_action = str(gate.get("submit_action") or "")
        submit_mode = str(gate.get("submit_mode") or "")
        if submit_action not in PLAN_APPLY_ALLOWED_ACTIONS:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=request_event.task_id,
                reason="plan request is not bound to an applicable action",
                status_code=409,
                status="plan_action_not_bound",
            )
        submit_payload = (
            dict(gate.get("submit_payload"))
            if isinstance(gate.get("submit_payload"), dict)
            else {}
        )
        submit_details = (
            dict(gate.get("submit_details"))
            if isinstance(gate.get("submit_details"), dict)
            else {}
        )
        request = (
            gate.get("request")
            if isinstance(gate.get("request"), dict)
            else {}
        )
        if submit_mode == "propose":
            if submit_action not in PLAN_PROPOSAL_ACTIONS:
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=request_event.task_id,
                    reason="plan action is not allowed to create a proposal",
                    status_code=409,
                    status="plan_action_not_bound",
                )
            return self._propose_bound_plan_action(
                requested=requested,
                action=action,
                requested_action=requested_action,
                request_event=request_event,
                request=request,
                gate=gate,
                submit_action=submit_action,
                submit_payload=submit_payload,
            )
        if (
            submit_mode != "apply"
            or submit_action not in PLAN_DIRECT_APPLY_ACTIONS
        ):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=request_event.task_id,
                reason="plan option must continue through the Agent",
                status_code=409,
                status="plan_action_not_bound",
            )
        origin_message = _originating_plan_message(
            self.state_dir,
            events,
            request,
        )
        if not origin_message:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=request_event.task_id,
                reason="originating Plan requirement is missing",
                status_code=409,
                status="plan_origin_missing",
            )

        request_id = str(gate.get("request_id") or "")
        template_id = str(submit_payload.get("template_id") or "")
        child_payload = {
            **submit_payload,
            "channel_id": str(
                submit_payload.get("channel_id")
                or _normal_channel_id(
                    f"{template_id}-{request_id.removeprefix('plan-')[-10:]}"
                )
            ),
            "thread_id": str(submit_payload.get("thread_id") or "main"),
            "message_id": f"msg-{request_id}",
            "message": origin_message,
            "expected_materialization_digest": str(
                submit_details.get("materialization_digest") or ""
            ),
            "task_id": str(
                request.get("task_id")
                or submit_payload.get("task_id")
                or request_event.task_id
                or ""
            ),
            "created_by": str(payload.get("created_by") or self.actor),
            "refs": {
                **(
                    submit_payload.get("refs")
                    if isinstance(submit_payload.get("refs"), dict)
                    else {}
                ),
                "plan_requirement_event_ids": [
                    str(item)
                    for item in request.get(
                        "originating_message_event_ids", []
                    )
                    if str(item)
                ],
                "plan_requirement_digest": str(
                    request.get("requirement_digest") or ""
                ),
            },
        }
        validation_error = validate_shared_action_payload(
            submit_action,
            child_payload,
            config=self.config,
        )
        if validation_error:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(child_payload),
                reason=validation_error,
                status="invalid_plan_action",
            )

        child_result = self._run_controlled_action(
            action=submit_action,
            requested_action=submit_action,
            payload=child_payload,
            requested=request_event,
            proposal_event_id="",
            proposal_gate={},
        )
        if not child_result.get("ok"):
            failed = self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(child_payload),
                reason=str(
                    child_result.get("reason")
                    or child_result.get("status")
                    or "bound Plan action failed"
                ),
                status_code=int(child_result.get("_status_code") or 422),
                status="plan_action_failed",
            )
            failed["applied_action"] = submit_action
            failed["child_result"] = redact_obj(child_result)
            return failed

        applied_result = {
            key: child_result[key]
            for key in (
                "channel_id",
                "event_id",
                "max_rounds",
                "member_count",
                "message_id",
                "participants",
                "template_id",
                "thread_id",
            )
            if key in child_result
        }
        answer_payload = {
            "schema_version": PLAN_RESPONSE_SCHEMA_VERSION,
            "request_event_id": request_event_id,
            "request_id": request_id,
            "request_digest": str(gate.get("request_digest") or ""),
            "revision": int(gate.get("revision") or 1),
            "question_id": str(gate.get("question_id") or ""),
            "option_id": str(gate.get("option_id") or ""),
            "answer": str(gate.get("answer") or ""),
            "answers": redact_obj(gate.get("answers") or []),
            "source": self.surface,
            "project_id": str(request.get("project_id") or ""),
            "conversation_id": str(request.get("conversation_id") or ""),
            "thread_key": str(request.get("thread_key") or ""),
            "applied_action": submit_action,
            "applied_result": redact_obj(applied_result),
        }
        answered = self.writer.emit(
            PLAN_ANSWERED_EVENT,
            actor=self.actor,
            task_id=_task_id_from_payload(child_payload),
            causation_id=requested.id,
            correlation_id=(
                request_event.correlation_id
                or requested.correlation_id
            ),
            payload=answer_payload,
        )
        self._completed(
            requested=requested,
            event=answered,
            action=action,
            requested_action=requested_action,
            status="applied",
            task_id=_task_id_from_payload(child_payload),
            extra={
                "request_event_id": request_event_id,
                "request_id": request_id,
                "applied_action": submit_action,
                **applied_result,
            },
        )
        return {
            "_status_code": int(child_result.get("_status_code") or 202),
            "ok": True,
            "status": "applied",
            "action": action,
            "requested_action": requested_action,
            "applied_action": submit_action,
            "answer_event_id": answered.id,
            "child_result": redact_obj(child_result),
            **applied_result,
        }

    def _propose_bound_plan_action(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        request_event: ZfEvent,
        request: dict[str, Any],
        gate: dict[str, Any],
        submit_action: str,
        submit_payload: dict[str, Any],
    ) -> dict:
        validation_error = validate_shared_action_payload(
            submit_action,
            submit_payload,
            config=self.config,
        )
        task_id = _task_id_from_payload(submit_payload)
        task = (
            TaskStore(self.state_dir / "kanban.json").get(task_id)
            if task_id
            else None
        )
        current_task_digest = (
            task_workflow_binding_digest(task)
            if task is not None
            else ""
        )
        expected_task_digest = str(
            request.get("task_contract_digest") or ""
        )
        submitted_task_digest = str(
            submit_payload.get("task_contract_digest") or ""
        )
        if (
            not validation_error
            and is_workflow_start_action(submit_action)
            and (
                task is None
                or not expected_task_digest
                or expected_task_digest != current_task_digest
                or submitted_task_digest != current_task_digest
            )
        ):
            replacement = self._remint_workflow_plan(
                requested=requested,
                request_event=request_event,
                request=request,
                task=task,
            )
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id,
                reason="workflow Task binding is stale or missing",
                status_code=409,
                status="workflow_task_stale",
                extra={
                    "actionability": "observation",
                    "replacement_plan_event_id": (
                        replacement.id if replacement is not None else ""
                    ),
                    "replacement_revision": int(
                        (
                            replacement.payload.get("request", {})
                            if replacement is not None
                            and isinstance(replacement.payload, dict)
                            and isinstance(
                                replacement.payload.get("request"), dict
                            )
                            else {}
                        ).get("revision") or 0
                    ),
                },
            )
        if (
            not validation_error
            and is_workflow_start_action(submit_action)
            and resolve_workflow_route(
                self.config,
                str(submit_payload.get("route_id") or ""),
                expected_config_digest=str(
                    submit_payload.get("config_digest") or ""
                ),
            )
            is None
        ):
            replacement = self._remint_workflow_plan(
                requested=requested,
                request_event=request_event,
                request=request,
                task=task,
            )
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id,
                reason="workflow route is stale or unavailable",
                status_code=409,
                status="workflow_route_stale",
                extra={
                    "actionability": "observation",
                    "replacement_plan_event_id": (
                        replacement.id if replacement is not None else ""
                    ),
                    "replacement_revision": int(
                        (
                            replacement.payload.get("request", {})
                            if replacement is not None
                            and isinstance(replacement.payload, dict)
                            and isinstance(
                                replacement.payload.get("request"), dict
                            )
                            else {}
                        ).get("revision") or 0
                    ),
                },
            )
        if validation_error:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(submit_payload),
                reason=validation_error,
                status="invalid_plan_action",
            )

        proposal_action = canonical_proposal_action(submit_action)
        proposal_digest = proposal_payload_digest(
            proposal_action,
            submit_payload,
        )
        proposal_id = f"proposal-{proposal_digest[:24]}"
        prior = next(
            (
                event
                for event in self.writer.event_log.read_all()
                if event.type in PROPOSAL_EVENT_TYPES
                and isinstance(event.payload, dict)
                and isinstance(event.payload.get("proposal"), dict)
                and str(
                    event.payload["proposal"].get("proposal_id") or ""
                )
                == proposal_id
            ),
            None,
        )
        proposal = {
            "proposal_id": proposal_id,
            "proposal_digest": proposal_digest,
            "revision": int(gate.get("revision") or 1),
            "expires_at": str(request.get("expires_at") or ""),
            "supersedes": "",
            "action": proposal_action,
            "requested_action": submit_action,
            "payload": redact_obj(submit_payload),
            "reason": str(
                request.get("reason")
                or f"selected Plan option: {gate.get('answer') or ''}"
            ),
            "confidence": "",
            "valid": True,
            "validation_error": "",
            "mutates_task_state": False,
        }
        if prior is None:
            proposal_event = ZfEvent(
                type=PROPOSAL_EVENT,
                actor=self.actor,
                task_id=_task_id_from_payload(submit_payload),
                causation_id=request_event.id,
                correlation_id=(
                    request_event.correlation_id
                    or requested.correlation_id
                ),
            )
            proposal["proposal_event_id"] = proposal_event.id
            proposal_event.payload = {
                "turn_id": str(request.get("turn_id") or ""),
                "thread_key": str(request.get("thread_key") or ""),
                "project_id": str(request.get("project_id") or ""),
                "conversation_id": str(
                    request.get("conversation_id") or ""
                ),
                "reply_event_id": "",
                "plan_request_event_id": request_event.id,
                "proposal": redact_obj(proposal),
                "source": self.surface,
            }
            self.writer.append(proposal_event)
        else:
            proposal_event = prior
            prior_proposal = (
                prior.payload.get("proposal")
                if isinstance(prior.payload, dict)
                and isinstance(prior.payload.get("proposal"), dict)
                else {}
            )
            proposal["proposal_event_id"] = prior.id
            if prior_proposal:
                proposal = dict(prior_proposal)

        request_id = str(gate.get("request_id") or "")
        proposed_result = {
            "proposal_event_id": proposal_event.id,
            "proposal_id": str(proposal.get("proposal_id") or proposal_id),
            "proposal_digest": str(
                proposal.get("proposal_digest") or proposal_digest
            ),
            "proposed_action": proposal_action,
        }
        answer_payload = {
            "schema_version": PLAN_RESPONSE_SCHEMA_VERSION,
            "request_event_id": request_event.id,
            "request_id": request_id,
            "request_digest": str(gate.get("request_digest") or ""),
            "revision": int(gate.get("revision") or 1),
            "question_id": str(gate.get("question_id") or ""),
            "option_id": str(gate.get("option_id") or ""),
            "answer": str(gate.get("answer") or ""),
            "answers": redact_obj(gate.get("answers") or []),
            "source": self.surface,
            "project_id": str(request.get("project_id") or ""),
            "conversation_id": str(request.get("conversation_id") or ""),
            "thread_key": str(request.get("thread_key") or ""),
            "status": "proposal_ready",
            "applied_action": proposal_action,
            "applied_result": redact_obj(proposed_result),
        }
        answered = self.writer.emit(
            PLAN_ANSWERED_EVENT,
            actor=self.actor,
            task_id=_task_id_from_payload(submit_payload),
            causation_id=requested.id,
            correlation_id=(
                request_event.correlation_id
                or requested.correlation_id
            ),
            payload=answer_payload,
        )
        self._completed(
            requested=requested,
            event=answered,
            action=action,
            requested_action=requested_action,
            status="proposal_ready",
            task_id=_task_id_from_payload(submit_payload),
            extra={
                "request_event_id": request_event.id,
                "request_id": request_id,
                **proposed_result,
            },
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "proposal_ready",
            "action": action,
            "requested_action": requested_action,
            "answer_event_id": answered.id,
            **proposed_result,
        }

    def _remint_workflow_plan(
        self,
        *,
        requested: ZfEvent,
        request_event: ZfEvent,
        request: dict[str, Any],
        task,
    ) -> ZfEvent | None:
        if task is None:
            return None
        catalog = workflow_route_catalog(self.config)
        routes = {
            str(route.get("route_id") or ""): route
            for route in catalog.get("routes") or []
            if isinstance(route, dict)
            and bool(route.get("available"))
            and str(route.get("route_id") or "")
        }
        shared_parameters = _shared_workflow_parameters(
            request.get("options")
        )
        options: list[dict[str, Any]] = []
        used_routes: set[str] = set()
        for option in request.get("options") or []:
            if not isinstance(option, dict):
                continue
            submit_payload = (
                option.get("submit_payload")
                if isinstance(option.get("submit_payload"), dict)
                else {}
            )
            route_id = str(submit_payload.get("route_id") or "")
            if route_id and route_id in routes:
                used_routes.add(route_id)
                options.append({
                    "id": str(option.get("id") or ""),
                    "label": str(option.get("label") or route_id),
                    "description": str(
                        option.get("description") or ""
                    ),
                    "recommended": bool(option.get("recommended")),
                    "route_id": route_id,
                    "objective": str(
                        submit_payload.get("objective") or task.title
                    ),
                    "parameters": (
                        dict(submit_payload.get("parameters"))
                        if isinstance(
                            submit_payload.get("parameters"), dict
                        )
                        else {}
                    ),
                })
            elif str(option.get("submit_mode") or "") == "continue":
                options.append({
                    "id": str(option.get("id") or "defer"),
                    "label": str(
                        option.get("label") or "No workflow yet"
                    ),
                    "description": str(
                        option.get("description") or ""
                    ),
                    "mode": "defer",
                })
        for route_id, route in routes.items():
            if len(options) >= 3 or route_id in used_routes:
                continue
            options.append({
                "id": route_id.replace(":", "-"),
                "label": (
                    f"{route.get('family') or 'workflow'} "
                    f"{route.get('kind') or route_id}"
                ).strip(),
                "description": (
                    f"{route.get('topology') or 'configured'} route"
                ),
                "route_id": route_id,
                "objective": task.title,
                "parameters": shared_parameters,
            })
        if not any(option.get("route_id") for option in options):
            return None
        if len(options) < 2:
            options.append({
                "id": "defer",
                "label": "No workflow yet",
                "description": "Keep the Task tracked without ignition.",
                "mode": "defer",
            })
        options = options[:3]
        if not any(option.get("recommended") for option in options):
            next(
                option for option in options if option.get("route_id")
            )["recommended"] = True
        replacement, error = build_task_workflow_plan_request(
            {
                "header": str(
                    request.get("header") or "Workflow plan"
                ),
                "question_id": str(
                    request.get("question_id") or "workflow-route"
                ),
                "question": str(
                    request.get("question")
                    or f"How should {task.id} run?"
                ),
                "options": options,
                "allow_other": bool(
                    request.get("allow_other", True)
                ),
                "reason": str(request.get("reason") or ""),
                "expires_at": str(request.get("expires_at") or ""),
            },
            task=task,
            task_event_id=_latest_task_binding_event_id(
                self.writer.event_log.read_all(),
                task_id=task.id,
                fallback=str(request.get("task_event_id") or ""),
            ),
            config=self.config,
            context={
                key: request.get(key)
                for key in (
                    "backend",
                    "conversation_id",
                    "originating_message_event_id",
                    "originating_message_event_ids",
                    "project_id",
                    "provider_session_id",
                    "requirement_digest",
                    "thread_key",
                    "turn_id",
                )
            },
        )
        if error or replacement is None:
            return None
        replacement = normalize_plan_request_revision(
            self.writer.event_log.read_all(),
            replacement,
        )
        event = ZfEvent(
            type=PLAN_REQUESTED_EVENT,
            actor=self.actor,
            task_id=task.id,
            causation_id=requested.id,
            correlation_id=(
                request_event.correlation_id
                or requested.correlation_id
            ),
        )
        replacement["request_event_id"] = event.id
        source_payload = (
            request_event.payload
            if isinstance(request_event.payload, dict)
            else {}
        )
        event.payload = {
            **{
                key: source_payload[key]
                for key in (
                    "backend",
                    "conversation_id",
                    "project_id",
                    "source",
                    "thread_id",
                    "thread_key",
                    "turn_id",
                )
                if key in source_payload
            },
            "source": str(
                source_payload.get("source")
                or "kanban-agent.plan-remint"
            ),
            "supersedes_request_event_id": request_event.id,
            "remint_reason": "workflow_binding_stale",
            "plan_request": redact_obj(replacement),
            "request": redact_obj(replacement),
        }
        return self.writer.append(event)


def _originating_plan_message(
    state_dir,
    events: list[ZfEvent],
    request: dict[str, Any],
) -> str:
    event_ids = [
        str(item)
        for item in request.get("originating_message_event_ids", [])
        if str(item)
    ]
    legacy_id = str(
        request.get("originating_message_event_id") or ""
    )
    if not event_ids and legacy_id:
        event_ids = [legacy_id]
    by_id = {event.id: event for event in events}
    rows: list[tuple[str, str]] = []
    for event_id in event_ids:
        origin = by_id.get(event_id)
        if origin is None or not isinstance(origin.payload, dict):
            return ""
        if origin.type == "user.message":
            message = str(
                origin.payload.get("message")
                or origin.payload.get("text")
                or ""
            ).strip()
        elif origin.type == "channel.message.posted":
            try:
                message = hydrate_channel_message_text(
                    state_dir,
                    origin.payload,
                    strict=True,
                ).strip()
            except SidecarRefError:
                return ""
        else:
            return ""
        if message:
            rows.append((event_id, message))
    expected_digest = str(request.get("requirement_digest") or "")
    if (
        expected_digest
        and expected_digest != plan_requirement_digest(rows)
    ):
        return ""
    return "\n\n".join(message for _event_id, message in rows)


def _latest_plan_revision(
    events: list[ZfEvent],
    *,
    request_id: str,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for event in events:
        if event.type != PLAN_REQUESTED_EVENT:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        request = (
            payload.get("request")
            if isinstance(payload.get("request"), dict)
            else payload.get("plan_request")
            if isinstance(payload.get("plan_request"), dict)
            else {}
        )
        if str(request.get("request_id") or event.id) != request_id:
            continue
        candidates.append({
            **request,
            "request_event_id": event.id,
        })
    return max(
        candidates,
        key=lambda item: int(item.get("revision") or 1),
        default={},
    )


def _latest_task_binding_event_id(
    events: list[ZfEvent],
    *,
    task_id: str,
    fallback: str,
) -> str:
    for event in reversed(events):
        if (
            event.task_id == task_id
            and event.type in {
                "task.created",
                "task.contract.update",
                "task.updated",
            }
        ):
            return event.id
    return fallback


def _shared_workflow_parameters(value: object) -> dict[str, Any]:
    parameter_sets: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return {}
    for option in value:
        if not isinstance(option, dict):
            continue
        submit_payload = option.get("submit_payload")
        if not isinstance(submit_payload, dict):
            continue
        parameters = submit_payload.get("parameters")
        if isinstance(parameters, dict):
            parameter_sets.append(parameters)
    if not parameter_sets:
        return {}
    shared = dict(parameter_sets[0])
    for key in list(shared):
        if any(
            key not in parameters
            or parameters[key] != shared[key]
            for parameters in parameter_sets[1:]
        ):
            shared.pop(key)
    return shared


__all__ = ["PlanApplyActionsMixin"]
