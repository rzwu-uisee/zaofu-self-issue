"""Controlled actions for the Self-Issue report-only lifecycle."""

from __future__ import annotations

from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.control_actions_helpers import _task_id_from_payload
from zf.runtime.self_issue_service import SelfIssueService

SELF_ISSUE_ACTIONS = frozenset({
    "self-issue-capture", "self-issue-update", "self-issue-preview",
    "self-issue-confirm", "self-issue-publish", "self-issue-recover",
    "self-issue-oauth-start", "self-issue-oauth-callback",
    "self-issue-oauth-disconnect", "self-issue-resolve-unknown",
    "self-issue-github-device-start", "self-issue-github-device-poll",
    "self-issue-get", "self-issue-dismiss",
    "self-issue-intake-get", "self-issue-intake-save",
    "self-issue-intake-submit", "self-issue-intake-dismiss",
    "self-issue-intake-attachment-add", "self-issue-intake-attachment-remove",
    "self-issue-evidence-start", "self-issue-evidence-interrupt",
    "self-issue-evidence-resume", "self-issue-evidence-apply",
    "self-issue-evidence-fail", "self-issue-runtime-check",
    "self-issue-limited-continue",
    "self-issue-attachment-preview", "self-issue-attachment-confirm",
    "self-issue-attachment-prepare", "self-issue-attachment-resolve-unknown",
})

class SelfIssueActionsMixin:
    def _self_issue_action(self, *, requested: ZfEvent, action: str,
                           requested_action: str, payload: dict[str, Any]) -> dict[str, Any]:
        service = SelfIssueService(
            self.state_dir, self.writer,
            project_root=self.project_root or self.state_dir.parent,
            policy=self.config.self_issue if self.config is not None else None,
        )
        operation = {
            "self-issue-capture": service.capture,
            "self-issue-update": service.update,
            "self-issue-preview": service.preview,
            "self-issue-confirm": service.confirm,
            "self-issue-publish": service.publish,
            "self-issue-recover": service.recover,
            "self-issue-oauth-start": service.oauth_start,
            "self-issue-oauth-callback": service.oauth_callback,
            "self-issue-oauth-disconnect": service.oauth_disconnect,
            "self-issue-github-device-start": service.github_device_start,
            "self-issue-github-device-poll": service.github_device_poll,
            "self-issue-resolve-unknown": service.resolve_unknown,
            "self-issue-get": service.get,
            "self-issue-dismiss": service.dismiss,
            "self-issue-intake-get": service.get_intake,
            "self-issue-intake-save": service.save_intake,
            "self-issue-intake-submit": service.submit_intake,
            "self-issue-intake-dismiss": service.dismiss_intake,
            "self-issue-intake-attachment-add": service.add_intake_attachment,
            "self-issue-intake-attachment-remove": service.remove_intake_attachment,
            "self-issue-evidence-start": service.start_evidence,
            "self-issue-evidence-interrupt": service.interrupt_evidence,
            "self-issue-evidence-resume": service.resume_evidence,
            "self-issue-evidence-apply": service.apply_evidence_assessment,
            "self-issue-evidence-fail": service.fail_evidence,
            "self-issue-runtime-check": service.check_runtime,
            "self-issue-limited-continue": service.continue_limited,
            "self-issue-attachment-preview": service.attachment_preview,
            "self-issue-attachment-confirm": service.attachment_confirm,
            "self-issue-attachment-prepare": service.attachment_prepare,
            "self-issue-attachment-resolve-unknown": service.resolve_attachment_unknown,
        }[action]
        try:
            result = operation(payload, causation_id=requested.id)
        except (ValueError, PermissionError) as exc:
            return self._failed(
                requested=requested, action=action, requested_action=requested_action,
                task_id=_task_id_from_payload(payload), reason=str(exc),
                status_code=422, status="invalid_self_issue_request",
            )
        should_cancel_evidence = (
            action == "self-issue-evidence-interrupt"
            and result.get("status") == "evidence_interrupted"
        ) or (
            action == "self-issue-dismiss" and result.get("cancel_evidence")
        )
        if should_cancel_evidence:
            from zf.web.agent_session_runtime import cancel_agent_session_run, run_key

            cancelled = cancel_agent_session_run(run_key(
                run_id=str(result.get("run_id") or ""),
                thread_id=str(result.get("thread_id") or ""),
            ))
            result.update({
                "interrupt_status": cancelled.status,
                "interrupt_supported": cancelled.interrupt_supported,
                "process_found": cancelled.process_found,
                "process_terminated": cancelled.process_terminated,
            })
        result.setdefault("action", action)
        result.setdefault("requested_action", requested_action)
        return result
