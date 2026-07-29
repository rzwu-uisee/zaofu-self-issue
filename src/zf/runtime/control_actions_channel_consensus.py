"""Controlled Channel question and consensus decisions."""

from __future__ import annotations

from zf.core.events import ZfEvent
from zf.runtime.channel_projection import project_channel
from zf.runtime.control_actions_helpers import (
    _normal_channel_id,
    _optional_str,
    _required_text,
    _stable_control_id,
    _task_id_from_payload,
)


class ChannelConsensusActionsMixin:
    def _channel_question_resolve(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        channel_id = _normal_channel_id(
            _required_text(payload, "channel_id")
        )
        thread_id = _optional_str(payload.get("thread_id")) or "main"
        question_id = _required_text(payload, "question_id")
        resolution = _required_text(payload, "resolution")
        channel = project_channel(self.state_dir, channel_id) or {}
        question = next(
            (
                item
                for item in channel.get("open_questions") or []
                if isinstance(item, dict)
                and str(item.get("question_id") or "") == question_id
                and str(item.get("thread_id") or "main") == thread_id
            ),
            None,
        )
        if question is None or str(question.get("status") or "") != "open":
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="channel question is missing or already resolved",
                status_code=409,
                status="question_not_open",
            )
        answer = str(payload.get("answer") or "").strip()
        if resolution in {"answered", "assumption"} and not answer:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="answer is required for answered or assumption resolution",
                status="invalid_payload",
            )
        event = self.writer.emit(
            "channel.question.resolved",
            actor=self.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=requested.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "question_id": question_id,
                "resolution": resolution,
                "resolved_by": str(
                    payload.get("resolved_by") or "owner:operator"
                ),
                "answer": answer,
                "risk_note": str(payload.get("risk_note") or ""),
                "source": self.surface,
            },
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="resolved",
            task_id=_task_id_from_payload(payload),
            extra={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "question_id": question_id,
                "resolution": resolution,
            },
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "resolved",
            "action": action,
            "requested_action": requested_action,
            "channel_id": channel_id,
            "thread_id": thread_id,
            "question_id": question_id,
            "event_id": event.id,
        }

    def _channel_consensus_decision(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
        decision: str,
    ) -> dict:
        channel_id = _normal_channel_id(
            _required_text(payload, "channel_id")
        )
        thread_id = _optional_str(payload.get("thread_id")) or "main"
        channel = project_channel(self.state_dir, channel_id) or {}
        consensus = (
            channel.get("consensus", {}).get(thread_id)
            if isinstance(channel.get("consensus"), dict)
            else {}
        )
        if not isinstance(consensus, dict) or not str(
            consensus.get("artifact_ref") or ""
        ):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="channel consensus proposal is missing",
                status_code=409,
                status="consensus_not_proposed",
            )
        artifact_ref = str(consensus.get("artifact_ref") or "")
        artifact_digest = str(consensus.get("artifact_digest") or "")
        expected_ref = str(payload.get("artifact_ref") or "")
        expected_digest = str(payload.get("artifact_digest") or "")
        if (
            (expected_ref and expected_ref != artifact_ref)
            or (
                expected_digest
                and expected_digest.removeprefix("sha256:")
                != artifact_digest.removeprefix("sha256:")
            )
        ):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="channel consensus artifact binding is stale",
                status_code=409,
                status="consensus_stale",
            )
        member_id = str(
            payload.get("member_id") or "owner:operator"
        )
        if decision == "confirm":
            event_type = "channel.consensus.signed"
            event_payload = {
                "channel_id": channel_id,
                "thread_id": thread_id,
                "member_id": member_id,
                "artifact_ref": artifact_ref,
                "artifact_digest": artifact_digest,
                "source": self.surface,
            }
            status = "confirmed"
        else:
            blocker = str(
                payload.get("blocker_question")
                or payload.get("reason")
                or ""
            ).strip()
            if not blocker:
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=_task_id_from_payload(payload),
                    reason="blocker_question is required",
                    status="invalid_payload",
                )
            event_type = "channel.consensus.blocked"
            event_payload = {
                "channel_id": channel_id,
                "thread_id": thread_id,
                "member_id": member_id,
                "blocker_question_id": str(
                    payload.get("blocker_question_id")
                    or _stable_control_id(
                        "q-blocker",
                        channel_id,
                        thread_id,
                        artifact_digest,
                        blocker,
                    )
                ),
                "blocker_question": blocker,
                "artifact_ref": artifact_ref,
                "artifact_digest": artifact_digest,
                "source": self.surface,
            }
            status = "blocked"
        event = self.writer.emit(
            event_type,
            actor=self.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=requested.id,
            correlation_id=channel_id,
            payload=event_payload,
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status=status,
            task_id=_task_id_from_payload(payload),
            extra={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "artifact_ref": artifact_ref,
                "artifact_digest": artifact_digest,
            },
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": status,
            "action": action,
            "requested_action": requested_action,
            "channel_id": channel_id,
            "thread_id": thread_id,
            "artifact_ref": artifact_ref,
            "artifact_digest": artifact_digest,
            "event_id": event.id,
        }
