"""Rework briefing context and payload normalization for dispatch."""

from __future__ import annotations

import json
from pathlib import Path

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.rework_feedback import (
    descriptor_from_payload as feedback_descriptor_from_payload,
    feedback_briefing_lines,
    hydrate_rework_feedback,
)


TASK_REF_SCOPE_REJECTION_REASON = "source_commit changes outside task contract scope"


def _payload_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _rework_required_actions(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    actions: list[str] = []

    def add_action(value: object) -> None:
        text = _payload_text(value)
        if text and text not in actions:
            actions.append(text)

    for key in ("required_action", "action", "next_step", "fix", "fix_hint"):
        add_action(payload.get(key))

    for key in ("must_fix", "required_actions", "actions", "fixes", "next_steps"):
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    add_action(
                        item.get("required_action")
                        or item.get("action")
                        or item.get("fix")
                        or item.get("summary")
                        or item.get("reason")
                    )
                else:
                    add_action(item)
        else:
            add_action(value)

    findings = payload.get("findings")
    if isinstance(findings, list):
        for item in findings:
            if not isinstance(item, dict):
                text = _payload_text(item)
                if text:
                    actions.append(text)
                continue
            parts: list[str] = []
            severity = _payload_text(item.get("severity"))
            evidence = _payload_text(item.get("evidence"))
            required = _payload_text(item.get("required_action"))
            summary = _payload_text(item.get("summary") or item.get("reason"))
            if severity:
                parts.append(f"{severity}:")
            if summary:
                parts.append(summary)
            if required:
                parts.append(f"required action: {required}")
            if evidence:
                parts.append(f"evidence: {evidence}")
            if parts:
                actions.append(" ".join(parts))

    blockers = payload.get("blockers")
    if isinstance(blockers, list):
        for item in blockers:
            text = _payload_text(item)
            if text:
                actions.append(f"blocker: {text}")

    if _task_ref_scope_repair_payload(payload):
        add_action(
            "Produce a new source_commit whose diff contains only this "
            "task's allowed contract scope; do not reuse the rejected "
            "source_commit or emit a metadata-only repair."
        )

    # Preserve order while deduping repeated gate payload fields.
    out: list[str] = []
    seen: set[str] = set()
    for action in actions:
        if action in seen:
            continue
        seen.add(action)
        out.append(action)
    return out


def _task_ref_scope_repair_payload(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    reason = str(payload.get("reason") or "").strip()
    expected_action = str(payload.get("expected_action") or "").strip()
    return (
        reason == TASK_REF_SCOPE_REJECTION_REASON
        or expected_action == "split_or_rebase_source_commit_and_reemit_handoff"
        or bool(payload.get("out_of_scope_files"))
    )


def _payload_excerpt(payload: object, *, limit: int = 3000) -> str:
    if not isinstance(payload, dict) or not payload:
        return ""
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n... <truncated>"


class ReworkDispatchContextMixin:
    @staticmethod
    def _task_ref_repair_identity_instruction(
        trigger_event: ZfEvent,
    ) -> str:
        payload = (
            trigger_event.payload
            if isinstance(trigger_event.payload, dict)
            else {}
        )
        if "stale task contract" in str(payload.get("reason") or "").lower():
            return (
                "Preserve its logical `fanout_id`, `stage_id`, `child_id`, and "
                "source baseline, but replace contract revision and snapshot "
                "fields from the current kernel task capsule. When the Kernel "
                "renders a `Runtime Durable Repair Identity`, use its new "
                "`run_id`, operation, request, and attempt identity; never copy "
                "the rejected call identity. Never copy the rejected stale "
                "contract snapshot. "
                "The nested `impl_self_check.attempt_id` is scheduler TaskAttempt "
                "identity, not fanout/run identity: set it to the current "
                "`Runtime TaskAttempt Identity` rendered below and never copy the "
                "source completion's old scheduler attempt.\n"
            )
        return (
            "Preserve its logical `fanout_id`, `stage_id`, `child_id`, source "
            "baseline, and current contract fields. When the Kernel renders a "
            "`Runtime Durable Repair Identity`, use its new `run_id`, operation, "
            "request, and attempt identity instead of copying the source "
            "completion's call identity. The nested `impl_self_check.attempt_id` "
            "is scheduler "
            "TaskAttempt identity, not fanout/run identity: set it to the current "
            "`Runtime TaskAttempt Identity` rendered below and never copy the "
            "source completion's old scheduler attempt.\n"
        )

    def _task_ref_repair_git_baseline(
        self,
        task: Task,
        role: RoleConfig,
        trigger_event: ZfEvent,
    ) -> tuple[str, Path] | None:
        """Recover the immutable writer baseline for task-ref repair.

        A repair dispatch happens after the original writer result, so the
        project checkout may already point at an unrelated candidate commit.
        Reusing that checkout HEAD breaks the original fanout lineage.  The
        rejected completion and its kernel-owned child retain the correct
        dispatch base and writer workdir.
        """

        if (
            getattr(trigger_event, "type", "") != "task.ref.repair.requested"
            or str(getattr(role, "role_kind", "") or "") != "writer"
        ):
            return None
        payload = (
            trigger_event.payload
            if isinstance(getattr(trigger_event, "payload", None), dict)
            else {}
        )
        source_event_id = str(payload.get("source_event_id") or "").strip()
        if not source_event_id:
            return None
        try:
            source_event = next(
                event
                for event in reversed(self.event_log.read_all())
                if event.id == source_event_id
            )
        except (OSError, StopIteration):
            return None
        source_payload = (
            source_event.payload
            if isinstance(source_event.payload, dict)
            else {}
        )
        child = self._writer_source_fanout_child(
            source_payload,
            task_id=task.id,
        )
        child_payload = (
            child.get("payload")
            if isinstance(child.get("payload"), dict)
            else {}
        )
        base_git_head = (
            self._writer_child_base_commit(child)
            or str(source_payload.get("base_git_head") or "").strip()
            or str(source_payload.get("base_commit") or "").strip()
        )
        if not base_git_head:
            return None
        workdir = str(
            child.get("workdir")
            or child_payload.get("workdir")
            or source_payload.get("workdir")
            or ""
        ).strip()
        evidence_root = Path(workdir) if workdir else Path(self.project_root)
        return base_git_head, evidence_root

    def _rework_context_for_dispatch(self, task: Task, role: RoleConfig) -> str:
        if getattr(task, "retry_count", 0) <= 0:
            return ""
        event = self._latest_rework_trigger_event(task.id)
        if event is None:
            return ""
        actions = _rework_required_actions(event.payload)
        action_section = ""
        if actions:
            action_section = (
                "\n### Required Rework Items\n"
                + "\n".join(f"- {item}" for item in actions)
                + "\n"
            )
        payload = event.payload if isinstance(event.payload, dict) else {}
        trigger_summary = _payload_text(
            payload.get("summary")
            or payload.get("verdict")
            or payload.get("reason")
        )
        summary_section = ""
        if trigger_summary:
            summary_section = f"\n### Trigger Summary\n{trigger_summary}\n"
        payload_excerpt = _payload_excerpt(event.payload, limit=2400)
        payload_section = ""
        if payload_excerpt:
            payload_section = (
                "\n### Trigger Payload Evidence\n"
                "```json\n"
                f"{payload_excerpt}\n"
                "```\n"
            )
        feedback_artifact_ref = str(payload.get("feedback_artifact_ref") or "").strip()
        rework_request = self._latest_rework_request_event(task.id)
        rework_payload = (
            rework_request.payload
            if rework_request is not None and isinstance(rework_request.payload, dict)
            else {}
        )
        if not feedback_artifact_ref:
            if str(rework_payload.get("trigger_event_id") or "") == event.id:
                feedback_artifact_ref = str(
                    rework_payload.get("feedback_artifact_ref") or ""
                ).strip()
        feedback_artifact_section = ""
        if rework_payload.get("rework_feedback_ref") or rework_payload.get("rework_feedback_digest"):
            body = hydrate_rework_feedback(
                self.state_dir,
                feedback_descriptor_from_payload(rework_payload),
                expected_task_id=task.id,
                expected_fingerprint=str(rework_payload.get("failure_fingerprint") or ""),
                expected_attempt_identity={
                    "attempt_domain": "task",
                    "task_id": task.id,
                    "task_map_generation": str(
                        rework_payload.get("task_map_generation") or ""
                    ),
                    "plan_artifact_package_id": str(
                        rework_payload.get("plan_artifact_package_id") or ""
                    ),
                    "plan_artifact_package_digest": str(
                        rework_payload.get("plan_artifact_package_digest") or ""
                    ),
                },
            )
            feedback_artifact_section = (
                "\n### Verified Rework Feedback\n"
                f"- rework_feedback_ref: `{rework_payload.get('rework_feedback_ref', '')}`\n"
                f"- rework_feedback_digest: `{rework_payload.get('rework_feedback_digest', '')}`\n"
                + (
                    f"- legacy_feedback_artifact_ref: `{feedback_artifact_ref}`\n"
                    if feedback_artifact_ref
                    else ""
                )
                + "".join(
                    f"- {line}\n" for line in feedback_briefing_lines(body)
                )
            )
        elif feedback_artifact_ref:
            feedback_artifact_section = (
                "\n### Feedback Artifact\n"
                f"- feedback_artifact_ref: `{feedback_artifact_ref}`\n"
                "Load this file before editing; it is the durable rejection "
                "summary for this rework attempt.\n"
            )
        return (
            "\n\n## Rework Context\n"
            f"- trigger_event: `{event.type}`\n"
            f"- trigger_event_id: `{event.id}`\n"
            f"- trigger_actor: `{event.actor or ''}`\n"
            f"{summary_section}"
            f"{action_section}"
            f"{payload_section}"
            f"{feedback_artifact_section}"
            "Address the rework evidence above before emitting the success event.\n"
        )
