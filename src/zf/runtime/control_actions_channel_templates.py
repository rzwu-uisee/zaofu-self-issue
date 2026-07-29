"""Controlled actions for versioned Channel templates."""

from __future__ import annotations

from zf.core.events import ZfEvent
from zf.runtime.channel_contracts import (
    normalize_channel_role,
    normalize_member_type,
    normalize_permission_profile,
    normalize_permissions,
    normalize_provider,
    normalize_visibility_profile,
    permission_profile_write_policy,
)
from zf.runtime.channel_discussion import discussion_roster
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_templates import materialize_channel_template
from zf.runtime.control_actions_channel_admin import _materialize_channel_skill_refs
from zf.runtime.control_actions_helpers import (
    _normal_channel_id,
    _required_text,
    _task_id_from_payload,
)


class ChannelTemplateActionsMixin:
    def _channel_create_from_template(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
        emit_completion: bool = True,
    ) -> dict:
        template_id = _required_text(payload, "template_id")
        materialized, error = materialize_channel_template(
            template_id,
            overrides=payload.get("overrides"),
        )
        if error or materialized is None:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason=error or "channel template preflight failed",
                status_code=422,
                status="invalid_template",
            )
        expected_digest = str(
            payload.get("expected_materialization_digest") or ""
        ).strip()
        if (
            expected_digest
            and expected_digest != materialized["materialization_digest"]
        ):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason=(
                    "channel template materialization changed after Plan "
                    "selection was presented"
                ),
                status_code=409,
                status="template_superseded",
            )
        name = str(payload.get("name") or materialized["name"])
        channel_id = _normal_channel_id(
            payload.get("channel_id")
            or f"{template_id}-{requested.id.removeprefix('evt-')[:10]}"
        )
        existing = project_channel(self.state_dir, channel_id) or {}
        if existing.get("created_by_event"):
            scope = existing.get("scope") if isinstance(existing.get("scope"), dict) else {}
            template = scope.get("template") if isinstance(scope.get("template"), dict) else {}
            if template.get("materialization_digest") == materialized[
                "materialization_digest"
            ]:
                return {
                    "_status_code": 200,
                    "ok": True,
                    "status": "existing",
                    "action": action,
                    "requested_action": requested_action,
                    "channel_id": channel_id,
                    "template": template,
                    "template_id": template_id,
                    "name": name,
                    "member_count": len(materialized["members"]),
                    "participants": list(
                        materialized["discussion"]["participants"]
                    ),
                    "max_rounds": int(
                        materialized["discussion"]["max_rounds"]
                    ),
                }
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="channel_id already exists with a different template",
                status_code=409,
                status="conflict",
            )

        created = self.writer.emit(
            "channel.created",
            actor=self.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=requested.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "name": name,
                "channel_name": name,
                "thread_id": str(payload.get("thread_id") or "main"),
                "task_id": str(payload.get("task_id") or ""),
                "created_by": str(payload.get("created_by") or self.actor),
                "source": self.surface,
                "scope": {
                    **(
                        payload.get("scope")
                        if isinstance(payload.get("scope"), dict)
                        else {}
                    ),
                    "template": {
                        "id": template_id,
                        "version": materialized["template_version"],
                        "digest": materialized["template_digest"],
                        "materialization_digest": materialized[
                            "materialization_digest"
                        ],
                        "writer_role": materialized["writer_role"],
                        "writer_scope": materialized["writer_scope"],
                    },
                },
            },
        )
        event = created
        for raw_member in materialized["members"]:
            member = dict(raw_member)
            role = normalize_channel_role(member.get("channel_role"))
            provider = normalize_provider(member.get("provider") or member.get("backend"))
            member_type = normalize_member_type(
                member.get("member_type"),
                backend=provider,
            )
            profile = normalize_permission_profile(member.get("permission_profile"))
            skill_refs = list(member.get("skill_refs") or [])
            _materialize_channel_skill_refs(
                skill_refs,
                project_root=self.project_root or self.state_dir.parent,
                state_dir=self.state_dir,
                config=self.config,
            )
            event = self.writer.emit(
                "channel.member.invited",
                actor=self.actor,
                task_id=_task_id_from_payload(payload),
                causation_id=created.id,
                correlation_id=channel_id,
                payload={
                    "channel_id": channel_id,
                    "thread_id": str(payload.get("thread_id") or "main"),
                    "member_id": str(member["member_id"]),
                    "persona": str(member["member_id"]),
                    "display_name": str(member["member_id"]).replace("_", " ").title(),
                    "role": role,
                    "channel_role": role,
                    "member_type": member_type,
                    "provider": provider,
                    "backend": str(member.get("backend") or provider),
                    "model": str(member.get("model") or ""),
                    "visibility_profile": normalize_visibility_profile(
                        "",
                        channel_role=role,
                        member_type=member_type,
                    ),
                    "permission_profile": profile,
                    "write_policy": permission_profile_write_policy(profile),
                    "role_context_ref": str(member.get("role_context_ref") or ""),
                    "skill_refs": skill_refs,
                    "scope": "channel-template",
                    "writer_scope": list(member.get("writer_scope") or []),
                    "permissions": normalize_permissions(
                        member.get("permissions"),
                        member_type=member_type,
                    ),
                    "source": self.surface,
                },
            )
            if profile != "read_only":
                self.writer.emit(
                    "channel.member.permission_profile.audit",
                    actor=self.actor,
                    causation_id=event.id,
                    correlation_id=channel_id,
                    payload={
                        "channel_id": channel_id,
                        "thread_id": str(payload.get("thread_id") or "main"),
                        "member_id": str(member["member_id"]),
                        "provider": provider,
                        "backend": str(member.get("backend") or provider),
                        "channel_role": role,
                        "permission_profile": profile,
                        "write_policy": permission_profile_write_policy(profile),
                        "dangerous_ack": False,
                        "reason": f"channel template {template_id}",
                        "source": self.surface,
                    },
                )
        discussion = dict(materialized["discussion"])
        event = self.writer.emit(
            "channel.discussion.mode.set",
            actor=self.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=created.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": str(payload.get("thread_id") or "main"),
                **discussion,
                "source": self.surface,
            },
        )
        if emit_completion:
            self._completed(
                requested=requested,
                event=event,
                action=action,
                requested_action=requested_action,
                status="created",
                task_id=_task_id_from_payload(payload),
                extra={
                    "channel_id": channel_id,
                    "template_id": template_id,
                    "template_version": materialized["template_version"],
                    "template_digest": materialized["template_digest"],
                    "materialization_digest": materialized[
                        "materialization_digest"
                    ],
                },
            )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "created",
            "action": action,
            "requested_action": requested_action,
            "channel_id": channel_id,
            "name": name,
            "template_id": template_id,
            "template_version": materialized["template_version"],
            "template_digest": materialized["template_digest"],
            "materialization_digest": materialized[
                "materialization_digest"
            ],
            "member_count": len(materialized["members"]),
            "participants": list(materialized["discussion"]["participants"]),
            "max_rounds": int(materialized["discussion"]["max_rounds"]),
            "event_id": event.id,
        }

    def _channel_create_and_start(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        created = self._channel_create_from_template(
            requested=requested,
            action=action,
            requested_action=requested_action,
            payload=payload,
            emit_completion=False,
        )
        if not created.get("ok"):
            return created

        channel_id = str(created["channel_id"])
        thread_id = str(payload.get("thread_id") or "main")
        message = str(
            payload.get("message")
            or payload.get("objective")
            or payload.get("text")
            or ""
        ).strip()
        message_id = str(
            payload.get("message_id")
            or f"msg-{requested.id.removeprefix('evt-')}"
        )
        events = self.writer.event_log.read_all()
        prior_message = next(
            (
                event
                for event in reversed(events)
                if event.type == "channel.message.posted"
                and str(event.payload.get("channel_id") or "") == channel_id
                and str(event.payload.get("thread_id") or "main") == thread_id
                and str(event.payload.get("message_id") or "") == message_id
            ),
            None,
        )
        prior_start = next(
            (
                event
                for event in reversed(events)
                if event.type == "channel.discussion.started"
                and str(event.payload.get("channel_id") or "") == channel_id
                and str(event.payload.get("thread_id") or "main") == thread_id
                and str(event.payload.get("requirement_message_id") or "")
                == message_id
            ),
            None,
        )
        if prior_message is not None and prior_start is not None:
            started = {
                "ok": True,
                "status": "existing",
                "event_id": prior_message.id,
                "message_id": message_id,
                "participants": list(created.get("participants") or []),
            }
            completion_event = prior_message
        else:
            started = self._channel_discussion_start(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload={
                    **payload,
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "message_id": message_id,
                    "message": message,
                },
                emit_completion=False,
            )
            if not started.get("ok"):
                return {
                    **started,
                    "channel_id": channel_id,
                    "creation_status": str(created.get("status") or ""),
                }
            completion_event = next(
                (
                    event
                    for event in reversed(self.writer.event_log.read_all())
                    if event.id == str(started.get("event_id") or "")
                ),
                None,
            )
        if completion_event is None:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="channel discussion start event is missing",
                status_code=500,
                status="event_missing",
            )

        participants = list(
            started.get("participants")
            or created.get("participants")
            or []
        )
        self._completed(
            requested=requested,
            event=completion_event,
            action=action,
            requested_action=requested_action,
            status="started",
            task_id=_task_id_from_payload(payload),
            extra={
                "channel_id": channel_id,
                "template_id": str(created.get("template_id") or ""),
                "member_count": int(created.get("member_count") or 0),
                "participants": participants,
                "max_rounds": int(created.get("max_rounds") or 0),
                "message_id": message_id,
                "thread_id": thread_id,
            },
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "started",
            "action": action,
            "requested_action": requested_action,
            "channel_id": channel_id,
            "name": str(created.get("name") or ""),
            "template_id": str(created.get("template_id") or ""),
            "creation_status": str(created.get("status") or ""),
            "member_count": int(created.get("member_count") or 0),
            "participants": participants,
            "max_rounds": int(created.get("max_rounds") or 0),
            "thread_id": thread_id,
            "message_id": message_id,
            "event_id": completion_event.id,
            "reply_request_count": int(
                started.get("reply_request_count") or 0
            ),
        }

    def _channel_discussion_start(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
        emit_completion: bool = True,
    ) -> dict:
        channel_id = _normal_channel_id(_required_text(payload, "channel_id"))
        channel = project_channel(self.state_dir, channel_id) or {}
        if not channel.get("created_by_event"):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="channel not found",
                status_code=404,
                status="not_found",
            )
        roster = discussion_roster(channel)
        if not roster:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="channel discussion has no routable participants",
                status_code=422,
                status="invalid_discussion",
            )
        result = self._channel_post_message(
            requested=requested,
            action=action,
            requested_action=requested_action,
            payload={
                **payload,
                "text": "@all " + str(
                    payload.get("message")
                    or payload.get("objective")
                    or payload.get("text")
                    or "Start the structured discussion."
                ).removeprefix("@all ").strip(),
                "mentions": ["all"],
            },
            emit_completion=emit_completion,
        )
        if result.get("ok"):
            result["status"] = "started"
            result["participants"] = roster
        return result


__all__ = ["ChannelTemplateActionsMixin"]
