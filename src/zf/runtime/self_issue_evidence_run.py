"""Kernel transitions for reporter evidence and Orchestrator assessment runs."""

from __future__ import annotations

import os
import uuid
from dataclasses import replace
from typing import Any

from zf.core.security.redaction import redact_obj
from zf.core.self_issue.safe_export import safe_export_obj
from zf.core.self_issue.models import (
    CLASSIFICATIONS,
    REPRODUCTION_STATUSES,
    SEVERITIES,
    IssueDraft,
    utc_now,
)
from zf.core.state.locks import locked_path
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_json
from zf.runtime.self_issue_evidence_activity import EvidenceActivityStore
from zf.runtime.self_issue_log_evidence import (
    normalize_log_findings,
    verified_log_candidate_map,
)
from zf.runtime.self_issue_liveness import self_issue_runtime_status
from zf.runtime.self_issue_public_evidence import prepare_public_evidence_attachments
from zf.runtime.self_issue_reproduction_ledger import (
    finalize_incomplete_reproductions,
    initialize_reproduction_ledger,
    read_reproduction_ledger,
    reproduction_ledger_path,
)


ASSESSMENT_FIELDS = frozenset({
    "schema_version", "classification", "severity", "reproduction_status",
    "component", "impact_scope", "confidence", "analysis",
    "recommended_next_action",
})


class SelfIssueEvidenceRunMixin:
    def start_evidence(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        with locked_path(self.state_dir / "self-issues" / "evidence-run.lock"):
            draft = self._required_draft(payload)
            if draft.publication_state == "published":
                return {
                    "ok": False,
                    "status": "published_immutable",
                    "reason": "Published Issue evidence is immutable; create a new Self-Issue report.",
                    "draft": self._draft_view(draft),
                }
            expected = int(payload.get("revision") or draft.revision)
            if expected != draft.revision:
                return {
                    "ok": False, "status": "revision_conflict",
                    "reason": "Draft changed before evidence collection",
                    "draft": self._draft_view(draft),
                }
            force = bool(payload.get("force"))
            if draft.evidence_status in {
                "collecting_static", "collecting_live", "waiting_for_runtime",
            } and not force:
                return {
                    "ok": True, "status": f"evidence_{draft.evidence_status}",
                    "started": False,
                    "draft": self._draft_view(draft),
                }
            if draft.evidence_status == "interrupted" and not force:
                return {
                    "ok": True, "status": "evidence_interrupted", "started": False,
                    "draft": self._draft_view(draft),
                }
            if draft.evidence_status == "completed" and not force:
                return {
                    "ok": True, "status": "evidence_completed", "started": False,
                    "draft": self._draft_view(draft),
                }
            run_id = f"sie-{uuid.uuid4().hex[:12]}"
            initialize_reproduction_ledger(
                self.state_dir, draft_id=draft.draft_id, run_id=run_id,
            )
            draft = replace(
                draft,
                revision=draft.revision + 1,
                evidence_status="collecting_static",
                runtime_status=self_issue_runtime_status(self.state_dir),
                evidence_collection_mode="pending",
                evidence_limit_reason="",
                evidence_run_id=run_id,
                evidence_checkpoint_ref={},
                evidence_result_ref={},
                evidence_error="",
                assessment_status="waiting_for_evidence",
                assessment_claim_id="",
                assessment_claimed_at="",
                assessment_owner_pid=0,
                attachment_refs=[
                    item for item in draft.attachment_refs
                    if not str(item.get("kind") or "").startswith(
                        "self_issue_public_evidence_"
                    )
                ],
                published_attachments=[],
                publication_state="draft",
                updated_at=utc_now(),
            )
            draft.evidence_base_revision = draft.revision
            self.drafts.save(draft)
            evidence_ref = self._collect_local_evidence(
                draft.draft_id,
                revision=draft.revision,
                run_id=run_id,
                reporter_context=draft.reporter_context,
                runtime_status=draft.runtime_status,
            )
            mechanical_evidence = safe_export_obj(hydrate_sidecar_ref(
                self.state_dir,
                evidence_ref,
                purpose="self_issue_evidence",
                actor="kernel",
                max_bytes=2 * 1024 * 1024,
            ).payload)
            draft.evidence_refs = [*draft.evidence_refs, evidence_ref]
            draft.evidence_input_ref = write_sidecar_json(
                self.state_dir,
                f"artifacts/self-issues/{draft.draft_id}/evidence-input-{run_id}.json",
                {
                    "schema_version": "self-issue-evidence-input.v1",
                    "draft_id": draft.draft_id,
                    "run_id": run_id,
                    "reporter_context": draft.reporter_context,
                    "report": {
                        "title": draft.title,
                        "bug_description": draft.bug_description,
                        "reproduction_steps": draft.reproduction_steps,
                        "expected_behavior": draft.expected_behavior,
                        "environment": draft.environment,
                        "zaofu_version": draft.zaofu_version,
                        "additional_context": draft.additional_context,
                        "attachment_context": draft.attachment_context,
                    },
                    "evidence_refs": draft.evidence_refs,
                    "mechanical_evidence": mechanical_evidence,
                    "attachment_refs": draft.attachment_refs,
                },
                kind="self_issue_evidence_input",
                schema_version="self-issue-evidence-input.v1",
                created_by="kernel",
                access_scope={"external_disclosure": False},
                required=True,
                preview="Local-only bounded evidence input",
            )
            self.intents.invalidate_unpublished(
                draft.draft_id, reason="evidence assessment restarted",
            )
            self.attachments.invalidate_unprepared(
                draft.draft_id, reason="evidence assessment restarted",
            )
            runtime_live = draft.runtime_status == "live"
            draft = replace(
                draft,
                evidence_status=("collecting_live" if runtime_live else "waiting_for_runtime"),
                evidence_collection_mode=("full" if runtime_live else "static_only"),
                assessment_status=("not_started" if runtime_live else "waiting_for_runtime"),
                updated_at=utc_now(),
            )
            self.drafts.save(draft)
            activity = EvidenceActivityStore(
                self.state_dir, draft_id=draft.draft_id, run_id=run_id,
            )
            activity.start(actor="kernel")
            reporter = str(draft.reporter_context.get("reported_by") or "user")
            if draft.reporter_context.get("discovered_by") == "kernel":
                activity.phase(
                    "kernel", "reporter_context",
                    "Kernel supplied a verified incident seed; Orchestrator reporting is pending",
                )
            else:
                activity.phase(
                    reporter, "reporter_context",
                    f"{reporter} supplied the incident report and local context",
                )
            activity.phase(
                "kernel collector", "mechanical_snapshot",
                "Collected redacted events, Trace refs, Git state, logs, and runtime timing",
            )
            browser = mechanical_evidence.get("browser_capture")
            browser = browser if isinstance(browser, dict) else {}
            browser_status = str(browser.get("status") or "not_requested")
            if browser_status != "not_requested":
                activity.phase(
                    "kernel collector",
                    f"browser_capture_{browser_status}",
                    str(browser.get("reason") or "Passive browser capture produced no result")[:300],
                )
            if runtime_live:
                activity.phase(
                    "kernel", "assessment_requested",
                    "Live evidence is ready; waiting for the runtime Orchestrator to claim assessment",
                )
            else:
                activity.phase(
                    "kernel", "waiting_for_runtime",
                    "Static evidence was saved; semantic assessment is waiting for the project runtime",
                )
            static_event = self.writer.emit(
                "self_issue.evidence.static_collected", actor="kernel",
                causation_id=causation_id or None,
                payload={
                    "draft_id": draft.draft_id,
                    "revision": draft.revision,
                    "run_id": run_id,
                    "runtime_status": draft.runtime_status,
                },
            )
            event = self.writer.emit(
                "self_issue.assessment.requested", actor="kernel",
                causation_id=static_event.id,
                payload={
                    "draft_id": draft.draft_id, "revision": draft.revision,
                    "run_id": run_id,
                    "runtime_status": draft.runtime_status,
                    "input_ref": draft.evidence_input_ref.get("ref", ""),
                },
            )
            return {
                "ok": True,
                "status": (
                    "assessment_requested" if runtime_live else "evidence_waiting_for_runtime"
                ),
                "started": False,
                "scheduled": runtime_live,
                "run_id": run_id, "expected_revision": draft.revision,
                "input_ref": dict(draft.evidence_input_ref),
                "draft": self._draft_view(draft), "event_id": event.id,
            }

    def interrupt_evidence(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        with locked_path(self.state_dir / "self-issues" / "evidence-run.lock"):
            draft = self._required_draft(payload)
            if draft.evidence_status == "interrupted":
                return {
                    "ok": True, "status": "evidence_interrupted", "interrupted": False,
                    "run_id": draft.evidence_run_id, "draft": self._draft_view(draft),
                }
            if draft.evidence_status not in {"collecting_static", "collecting_live"}:
                return {
                    "ok": False, "status": "evidence_not_running",
                    "reason": "only a running evidence assessment can be interrupted",
                    "draft": self._draft_view(draft),
                }
            ledger_path = reproduction_ledger_path(
                self.state_dir,
                draft_id=draft.draft_id,
                run_id=draft.evidence_run_id,
            )
            reproduction_ledger = finalize_incomplete_reproductions(ledger_path)
            checkpoint = write_sidecar_json(
                self.state_dir,
                f"artifacts/self-issues/{draft.draft_id}/evidence-checkpoint-{draft.evidence_run_id}.json",
                {
                    "schema_version": "self-issue-evidence-checkpoint.v1",
                    "draft_id": draft.draft_id,
                    "run_id": draft.evidence_run_id,
                    "input_ref": draft.evidence_input_ref,
                    "evidence_refs": draft.evidence_refs,
                    "reproduction_ledger": reproduction_ledger,
                },
                kind="self_issue_evidence_checkpoint",
                schema_version="self-issue-evidence-checkpoint.v1",
                created_by="kernel",
                access_scope={"external_disclosure": False},
                required=True,
                preview="Local-only evidence checkpoint",
            )
            draft = replace(
                draft,
                revision=draft.revision + 1,
                evidence_status="interrupted",
                evidence_collection_mode="limited",
                evidence_limit_reason="Not collected because the user interrupted evidence collection.",
                assessment_status="skipped",
                assessment_claim_id="",
                assessment_claimed_at="",
                assessment_owner_pid=0,
                evidence_checkpoint_ref=checkpoint,
                updated_at=utc_now(),
            )
            self.drafts.save(draft)
            EvidenceActivityStore(
                self.state_dir, draft_id=draft.draft_id, run_id=draft.evidence_run_id,
            ).interrupt(actor="kernel")
            event = self.writer.emit(
                "self_issue.evidence.interrupted", actor="kernel",
                causation_id=causation_id or None,
                payload={"draft_id": draft.draft_id, "run_id": draft.evidence_run_id},
            )
            return {
                "ok": True, "status": "evidence_interrupted", "interrupted": True,
                "run_id": draft.evidence_run_id,
                "thread_id": f"self-issue-assessment:{draft.draft_id}:{draft.evidence_run_id}",
                "draft": self._draft_view(draft), "event_id": event.id,
            }

    def resume_evidence(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        with locked_path(self.state_dir / "self-issues" / "evidence-run.lock"):
            draft = self._required_draft(payload)
            if draft.evidence_status != "interrupted":
                return {
                    "ok": False, "status": "evidence_not_interrupted",
                    "reason": "only an interrupted evidence run can be resumed",
                    "draft": self._draft_view(draft),
                }
            ledger_path = reproduction_ledger_path(
                self.state_dir,
                draft_id=draft.draft_id,
                run_id=draft.evidence_run_id,
            )
            finalize_incomplete_reproductions(ledger_path)
            read_reproduction_ledger(ledger_path)
            runtime_status = self_issue_runtime_status(self.state_dir)
            runtime_live = runtime_status == "live"
            draft = replace(
                draft,
                revision=draft.revision + 1,
                runtime_status=runtime_status,
                evidence_status=("collecting_live" if runtime_live else "waiting_for_runtime"),
                evidence_collection_mode=("full" if runtime_live else "static_only"),
                evidence_limit_reason="",
                evidence_error="",
                assessment_status=("not_started" if runtime_live else "waiting_for_runtime"),
                assessment_claim_id="",
                assessment_claimed_at="",
                assessment_owner_pid=0,
                updated_at=utc_now(),
            )
            draft.evidence_base_revision = draft.revision
            self.drafts.save(draft)
            EvidenceActivityStore(
                self.state_dir, draft_id=draft.draft_id, run_id=draft.evidence_run_id,
            ).resume(actor="kernel")
            resumed_event = self.writer.emit(
                "self_issue.evidence.resumed", actor="kernel",
                causation_id=causation_id or None,
                payload={"draft_id": draft.draft_id, "run_id": draft.evidence_run_id},
            )
            event = self.writer.emit(
                "self_issue.assessment.requested", actor="kernel",
                causation_id=resumed_event.id,
                payload={"draft_id": draft.draft_id, "run_id": draft.evidence_run_id},
            )
            return {
                "ok": True,
                "status": (
                    "assessment_requested" if runtime_live else "evidence_waiting_for_runtime"
                ),
                "started": False, "scheduled": runtime_live, "resumed": True,
                "run_id": draft.evidence_run_id, "expected_revision": draft.revision,
                "input_ref": dict(draft.evidence_input_ref),
                "checkpoint_ref": dict(draft.evidence_checkpoint_ref),
                "draft": self._draft_view(draft), "event_id": event.id,
            }

    def check_runtime(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        with locked_path(self.state_dir / "self-issues" / "evidence-run.lock"):
            draft = self._required_draft(payload)
            runtime_status = self_issue_runtime_status(self.state_dir)
            updates: dict[str, Any] = {"runtime_status": runtime_status, "updated_at": utc_now()}
            requested = False
            if runtime_status == "live" and draft.evidence_status == "waiting_for_runtime":
                updates.update({
                    "evidence_status": "collecting_live",
                    "evidence_collection_mode": "full",
                    "assessment_status": "not_started",
                })
                requested = True
            updated = replace(draft, **updates)
            self.drafts.save(updated)
            if requested:
                EvidenceActivityStore(
                    self.state_dir,
                    draft_id=updated.draft_id,
                    run_id=updated.evidence_run_id,
                ).phase(
                    "kernel", "assessment_requested",
                    "Project runtime is live; assessment is waiting for the runtime Orchestrator",
                )
                event = self.writer.emit(
                    "self_issue.assessment.requested",
                    actor="kernel",
                    causation_id=causation_id or None,
                    payload={
                        "draft_id": updated.draft_id,
                        "run_id": updated.evidence_run_id,
                        "revision": updated.revision,
                        "runtime_status": "live",
                    },
                )
                return {
                    "ok": True, "status": "assessment_requested",
                    "draft": self._draft_view(updated), "event_id": event.id,
                }
            return {
                "ok": True, "status": f"runtime_{runtime_status}",
                "draft": self._draft_view(updated),
            }

    def continue_limited(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        with locked_path(self.state_dir / "self-issues" / "evidence-run.lock"):
            draft = self._required_draft(payload)
            if draft.evidence_status not in {"waiting_for_runtime", "interrupted", "failed"}:
                return {
                    "ok": False, "status": "limited_report_not_available",
                    "reason": "limited continuation requires stopped, interrupted, or failed evidence",
                    "draft": self._draft_view(draft),
                }
            reason = (
                draft.evidence_limit_reason
                if draft.evidence_status == "interrupted" and draft.evidence_limit_reason
                else "Project runtime was stopped and the user chose to continue."
            )
            updated = replace(
                draft,
                revision=draft.revision + 1,
                runtime_status=self_issue_runtime_status(self.state_dir),
                evidence_status="completed",
                evidence_collection_mode="limited",
                evidence_limit_reason=reason,
                assessment_status="skipped",
                assessment_confidence="low",
                recommended_next_action=(
                    "Assessment was not performed. Start the project runtime and create a new "
                    "evidence run if a complete assessment is required."
                ),
                suggested_fix=(
                    "Assessment was not performed. Start the project runtime and create a new "
                    "evidence run if a complete assessment is required."
                ),
                updated_at=utc_now(),
            )
            self.intents.invalidate_unpublished(
                draft.draft_id, reason="limited evidence continuation selected",
            )
            self.batches.invalidate_unpublished(
                draft.draft_id, reason="limited evidence continuation selected",
            )
            self.drafts.save(updated)
            EvidenceActivityStore(
                self.state_dir,
                draft_id=updated.draft_id,
                run_id=updated.evidence_run_id,
            ).limited(actor="kernel", reason=reason)
            event = self.writer.emit(
                "self_issue.evidence.limited", actor="kernel",
                causation_id=causation_id or None,
                payload={"draft_id": updated.draft_id, "run_id": updated.evidence_run_id},
            )
            return {
                "ok": True, "status": "evidence_limited",
                "draft": self._draft_view(updated), "event_id": event.id,
            }

    def claim_pending_assessment(self, *, owner_pid: int) -> dict[str, Any] | None:
        with locked_path(self.state_dir / "self-issues" / "evidence-run.lock"):
            candidates = sorted(self.drafts.list(), key=lambda item: item.updated_at)
            draft = next((
                item for item in candidates
                if item.evidence_status in {"waiting_for_runtime", "collecting_live"}
                and (
                    item.assessment_status in {"not_started", "waiting_for_runtime"}
                    or (
                        item.assessment_status == "running"
                        and not _pid_alive(item.assessment_owner_pid)
                    )
                )
                and item.evidence_input_ref
            ), None)
            if draft is None:
                return None
            claim_id = f"siac-{uuid.uuid4().hex[:12]}"
            updated = replace(
                draft,
                runtime_status="live",
                evidence_status="collecting_live",
                evidence_collection_mode="full",
                assessment_status="running",
                assessment_claim_id=claim_id,
                assessment_claimed_at=utc_now(),
                assessment_owner_pid=max(0, int(owner_pid)),
                evidence_base_revision=draft.revision,
                updated_at=utc_now(),
            )
            self.drafts.save(updated)
            EvidenceActivityStore(
                self.state_dir,
                draft_id=updated.draft_id,
                run_id=updated.evidence_run_id,
            ).phase(
                "orchestrator", "assessment_claimed",
                "Runtime Orchestrator claimed the pending semantic assessment",
            )
            event = self.writer.emit(
                "self_issue.assessment.claimed", actor="kernel",
                payload={
                    "draft_id": updated.draft_id,
                    "run_id": updated.evidence_run_id,
                    "claim_id": claim_id,
                },
            )
            return {
                "ok": True, "status": "assessment_claimed", "started": True,
                "run_id": updated.evidence_run_id,
                "expected_revision": updated.revision,
                "input_ref": dict(updated.evidence_input_ref),
                "draft": self._draft_view(updated), "event_id": event.id,
            }

    def fail_pending_assessment(self, *, reason: str) -> bool:
        with locked_path(self.state_dir / "self-issues" / "evidence-run.lock"):
            draft = next((
                item for item in sorted(self.drafts.list(), key=lambda value: value.updated_at)
                if item.assessment_status in {"not_started", "waiting_for_runtime"}
                and item.evidence_status in {"waiting_for_runtime", "collecting_live"}
            ), None)
            if draft is None:
                return False
            updated = replace(
                draft,
                runtime_status="live",
                evidence_status="failed",
                assessment_status="failed",
                evidence_error=str(reason)[:300],
                updated_at=utc_now(),
            )
            self.drafts.save(updated)
            EvidenceActivityStore(
                self.state_dir, draft_id=updated.draft_id, run_id=updated.evidence_run_id,
            ).fail(actor="kernel")
            self.writer.emit(
                "self_issue.assessment.failed", actor="kernel",
                payload={"draft_id": updated.draft_id, "run_id": updated.evidence_run_id},
            )
            return True

    def apply_evidence_assessment(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        with locked_path(self.state_dir / "self-issues" / "evidence-run.lock"):
            draft = self._required_draft(payload)
            run_id = str(payload.get("run_id") or "")
            expected = int(payload.get("expected_revision") or 0)
            if (
                draft.evidence_status != "collecting_live"
                or draft.assessment_status != "running"
                or draft.evidence_run_id != run_id
                or draft.evidence_base_revision != expected
                or draft.revision != expected
            ):
                return {
                    "ok": False, "status": "evidence_conflict",
                    "reason": "Draft or evidence run changed before assessment was applied",
                    "draft": self._draft_view(draft),
                }
            report = normalize_assessment(payload.get("report"))
            evidence_input = hydrate_sidecar_ref(
                self.state_dir,
                draft.evidence_input_ref,
                purpose="self_issue_public_evidence",
                actor="kernel",
                max_bytes=2 * 1024 * 1024,
            ).payload
            mechanical_evidence = (
                evidence_input.get("mechanical_evidence", {})
                if isinstance(evidence_input, dict)
                else {}
            )
            if not isinstance(mechanical_evidence, dict):
                mechanical_evidence = {}
            mechanical_evidence, browser_result = self._capture_assessed_browser_evidence(
                draft=draft,
                report=report,
                mechanical_evidence=mechanical_evidence,
            )
            if browser_result.status not in {"not_requested", "deferred"}:
                EvidenceActivityStore(
                    self.state_dir, draft_id=draft.draft_id, run_id=run_id,
                ).phase(
                    "kernel collector",
                    f"browser_capture_{browser_result.status}",
                    browser_result.reason[:300],
                )
            candidate_map = verified_log_candidate_map(
                mechanical_evidence.get("log_error_candidates"),
            )
            report["analysis"]["log_findings"] = normalize_log_findings(
                report["analysis"].get("log_findings"),
                allowed_candidate_ids=set(candidate_map),
            )
            public_evidence = prepare_public_evidence_attachments(
                self.state_dir,
                draft_id=draft.draft_id,
                run_id=run_id,
                mechanical_evidence=mechanical_evidence,
                semantic_log_findings=report["analysis"]["log_findings"],
            )
            result_ref = write_sidecar_json(
                self.state_dir,
                f"artifacts/self-issues/{draft.draft_id}/assessment-{run_id}.json",
                {"schema_version": "self-issue-assessment-result.v1", "report": report},
                kind="self_issue_assessment",
                schema_version="self-issue-assessment-result.v1",
                created_by="orchestrator",
                access_scope={"external_disclosure": False},
                required=True,
                preview="Local-only Orchestrator assessment",
            )
            updated = replace(
                draft,
                revision=draft.revision + 1,
                classification=report["classification"],
                severity=report["severity"],
                reproduction_status=report["reproduction_status"],
                component=report["component"],
                impact_scope=report["impact_scope"],
                assessment_confidence=report["confidence"],
                analysis=report["analysis"],
                suggested_fix=report["recommended_next_action"],
                recommended_next_action=report["recommended_next_action"],
                evidence_status="completed",
                evidence_collection_mode="full",
                evidence_limit_reason="",
                assessment_status="completed",
                assessment_claim_id="",
                assessment_claimed_at="",
                assessment_owner_pid=0,
                attachment_refs=[
                    *[
                        item for item in draft.attachment_refs
                        if not str(item.get("kind") or "").startswith(
                            "self_issue_public_evidence_"
                        )
                    ],
                    *public_evidence,
                ],
                published_attachments=[],
                evidence_result_ref=result_ref,
                evidence_checkpoint_ref={},
                evidence_error="",
                updated_at=utc_now(),
            )
            self.drafts.save(updated)
            EvidenceActivityStore(
                self.state_dir, draft_id=updated.draft_id, run_id=run_id,
            ).phase(
                "kernel collector",
                "disclosure_candidates",
                (
                    f"Prepared {len(public_evidence)} sanitized evidence file(s) "
                    "for explicit disclosure confirmation"
                    if public_evidence
                    else "No safe external evidence file was available"
                ),
            )
            EvidenceActivityStore(
                self.state_dir, draft_id=updated.draft_id, run_id=run_id,
            ).complete(actor="kernel")
            event = self.writer.emit(
                "self_issue.assessment.completed", actor="kernel",
                causation_id=causation_id or None,
                payload={
                    "draft_id": updated.draft_id, "run_id": run_id,
                    "classification": updated.classification, "severity": updated.severity,
                    "component": updated.component, "result_ref": result_ref["ref"],
                },
            )
            return {
                "ok": True, "status": "evidence_completed",
                "draft": self._draft_view(updated), "event_id": event.id,
            }

    def fail_evidence(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        with locked_path(self.state_dir / "self-issues" / "evidence-run.lock"):
            draft = self._required_draft(payload)
            run_id = str(payload.get("run_id") or "")
            if draft.evidence_run_id != run_id:
                return {"ok": False, "status": "evidence_superseded"}
            draft = replace(
                draft,
                revision=draft.revision + 1,
                evidence_status="failed",
                assessment_status="failed",
                assessment_claim_id="",
                assessment_claimed_at="",
                assessment_owner_pid=0,
                evidence_error=str(payload.get("reason") or "assessment failed")[:300],
                updated_at=utc_now(),
            )
            self.drafts.save(draft)
            EvidenceActivityStore(
                self.state_dir, draft_id=draft.draft_id, run_id=run_id,
            ).fail(actor="kernel")
            self.writer.emit(
                "self_issue.assessment.failed", actor="kernel",
                causation_id=causation_id or None,
                payload={"draft_id": draft.draft_id, "run_id": run_id},
            )
            return {"ok": False, "status": "evidence_failed", "draft": self._draft_view(draft)}


def normalize_assessment(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != ASSESSMENT_FIELDS:
        raise ValueError("Orchestrator assessment does not match the canonical schema")
    if value.get("schema_version") != "self-issue-assessment.v1":
        raise ValueError("unsupported Orchestrator assessment schema")
    classification = str(value.get("classification") or "")
    severity = str(value.get("severity") or "")
    reproduction = str(value.get("reproduction_status") or "")
    confidence = str(value.get("confidence") or "")
    if classification not in CLASSIFICATIONS:
        raise ValueError("unsupported assessment classification")
    if severity not in SEVERITIES:
        raise ValueError("unsupported assessment severity")
    if reproduction not in REPRODUCTION_STATUSES:
        raise ValueError("unsupported assessment reproduction status")
    if confidence not in {"low", "medium", "high"}:
        raise ValueError("unsupported assessment confidence")
    analysis = value.get("analysis")
    if not isinstance(analysis, dict):
        raise ValueError("assessment analysis must be an object")
    analysis = dict(analysis)
    analysis["log_findings"] = normalize_log_findings(analysis.get("log_findings"))
    return redact_obj({
        "schema_version": "self-issue-assessment.v1",
        "classification": classification,
        "severity": severity,
        "reproduction_status": reproduction,
        "component": str(value.get("component") or "unknown")[:160],
        "impact_scope": str(value.get("impact_scope") or "unknown")[:500],
        "confidence": confidence,
        "analysis": analysis,
        "recommended_next_action": str(value.get("recommended_next_action") or "")[:4000],
    })


def _pid_alive(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
