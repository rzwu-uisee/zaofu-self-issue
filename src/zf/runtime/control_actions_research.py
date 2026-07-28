"""Controlled Research fanout start and result adoption actions."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from zf.core.events import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.control_actions_helpers import _required_text, _workflow_stage
from zf.runtime.workflow_requests import (
    WorkflowRequestError,
    adopt_workflow_research_result,
)


RESEARCH_TEMPLATE_ID = "research-fanout.fixed.v1"
RESEARCH_PATTERN_ID = "research-fanout"
RESEARCH_CHILD_ROLES = (
    "source_researcher",
    "product_analyst",
    "technical_analyst",
    "risk_critic",
)
RESEARCH_SYNTH_ROLE = "synthesizer"


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
        if TaskStore(self.state_dir / "kanban.json").get(task_id) is None:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id or None,
                reason=f"research task {task_id!r} does not exist",
                status_code=404,
                status="preflight_blocked",
            )
        stage_error = _research_stage_error(self.config)
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
            "pattern_id": RESEARCH_PATTERN_ID,
            "requested_by": _required_text(payload, "requested_by")
            or "skill:zf-research-fanout-trigger",
            "reason": _required_text(payload, "reason")
            or "explicit research fanout request from channel/Kanban Agent",
            "expected_output": _required_text(payload, "expected_output")
            or "research synthesis plus PRD/refactor prompt inputs",
            "risk": _required_text(payload, "risk")
            or "cost-bearing multi-agent research; evidence and open questions required",
        })
        source_refs = (
            dict(payload.get("source_refs"))
            if isinstance(payload.get("source_refs"), dict)
            else {}
        )
        source_refs.update({
            "template_id": RESEARCH_TEMPLATE_ID,
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
            "template_id": RESEARCH_TEMPLATE_ID,
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
        request_id = _required_text(payload, "request_id")
        artifact_ref = _required_text(payload, "artifact_ref")
        artifact_digest = _required_text(payload, "artifact_digest")
        summary = _required_text(payload, "summary")
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
            projection, created = adopt_workflow_research_result(
                self.state_dir,
                request_id,
                expected_revision=request_revision,
                artifact_ref=artifact_ref,
                artifact_digest=actual_digest,
                summary=summary,
                actor=self.actor,
                source_event_id=requested.id,
                channel_id=_required_text(payload, "channel_id"),
                thread_id=_required_text(payload, "thread_id"),
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
        channel_id = _required_text(payload, "channel_id") or str(
            projection.get("channel_id") or ""
        )
        thread_id = _required_text(payload, "thread_id") or str(
            projection.get("thread_id") or "main"
        )
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
                task_id=_required_text(payload, "task_id") or None,
                causation_id=adopted_event.id,
                correlation_id=channel_id,
                payload={
                    "channel_id": channel_id,
                    "thread_id": thread_id or "main",
                    "artifact_id": f"research-adoption-{actual_digest[:16]}",
                    "request_id": request_id,
                    "task_id": _required_text(payload, "task_id"),
                    "kind": "research_report",
                    "path": artifact_ref,
                    "sha256": actual_digest,
                    "hash": f"sha256:{actual_digest}",
                    "summary": summary,
                    "provenance": {
                        "source_event_id": requested.id,
                        "request_revision": request_revision,
                    },
                    "source": self.surface,
                },
            )
            self.writer.emit(
                "channel.state_update.posted",
                actor=self.actor,
                task_id=_required_text(payload, "task_id") or None,
                causation_id=adopted_event.id,
                correlation_id=channel_id,
                payload={
                    "channel_id": channel_id,
                    "thread_id": thread_id or "main",
                    "status": "research_adopted",
                    "summary": summary,
                    "task_id": _required_text(payload, "task_id"),
                    "refs": {
                        "request_id": request_id,
                        "request_revision": request_revision,
                        "artifact_ref": artifact_ref,
                        "artifact_digest": actual_digest,
                        "workflow_research_event_id": adopted_event.id,
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
            task_id=_required_text(payload, "task_id") or None,
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
            "event_id": adopted_event.id,
        }


def _research_stage_error(config: Any) -> str:
    stage = _workflow_stage(config, RESEARCH_PATTERN_ID)
    if stage is None:
        return "research-fanout stage is not declared in zf.yaml"
    if str(getattr(stage, "topology", "") or "") != "fanout_reader":
        return "research-fanout stage must use fanout_reader topology"
    children = tuple(
        str(getattr(item, "role_instance", "") or "")
        for item in getattr(stage, "children", []) or []
    )
    if children != RESEARCH_CHILD_ROLES:
        return "research-fanout stage must declare the fixed four research children"
    synth_role = str(getattr(getattr(stage, "aggregate", None), "synth_role", "") or "")
    if synth_role != RESEARCH_SYNTH_ROLE:
        return "research-fanout stage must declare synthesizer as aggregate.synth_role"
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
