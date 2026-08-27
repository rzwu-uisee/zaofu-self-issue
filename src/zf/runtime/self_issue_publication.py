"""Kernel-owned multi-provider Self-Issue publication coordination."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from zf.core.self_issue.models import (
    PublicationBatch,
    PublicationIntent,
    stable_digest,
    utc_now,
)
from zf.integrations.forge.base import ForgeResult, IssuePublishRequest


class SelfIssuePublicationMixin:
    """One immutable batch with independently recoverable provider intents."""

    def preview(self, payload: dict[str, Any], *, causation_id: str = "") -> dict[str, Any]:
        with self._publication_lock("preview.lock"):
            draft = self._required_draft(payload)
            if draft.evidence_status not in self._publication_ready_evidence_statuses:
                return {
                    "ok": False,
                    "status": f"evidence_{draft.evidence_status}",
                    "reason": "evidence assessment must settle before publication preview",
                    "draft": self._draft_view(draft),
                }
            if draft.publication_state == "published":
                published_batch = self.batches.latest_for_draft(draft.draft_id)
                if published_batch is not None and published_batch.status == "published":
                    return self._batch_response(published_batch)
            mode, providers = self._publication_selection(payload, draft=draft)
            if (
                "gitlab" in providers
                and draft.attachment_refs
                and len(draft.published_attachments) != len(draft.attachment_refs)
            ):
                return {
                    "ok": False,
                    "status": "attachment_preparation_required",
                    "reason": "confirmed GitLab attachment preparation is required first",
                    "draft": self._draft_view(draft),
                    "publication_mode": mode,
                }
            locked = self.intents.locked_for_draft(draft.draft_id)
            if locked is not None:
                return {
                    "ok": False,
                    "status": "outcome_unknown",
                    "provider": str(locked.target_binding.get("provider") or ""),
                    "intent_id": locked.intent_id,
                    "reason": "publication recovery is locked",
                }
            intent_ids: dict[str, str] = {}
            previews: dict[str, dict[str, Any]] = {}
            child_digests: dict[str, str] = {}
            for provider in providers:
                target = self._target_for_provider(provider, draft=draft)
                key = self._secret_key(payload, draft, provider=provider)
                marker_seed = stable_digest({
                    "draft_id": draft.draft_id,
                    "draft_revision": draft.revision,
                    "provider": provider,
                    "target": target,
                    "title": draft.title,
                    "disclosure_fields": draft.disclosure_fields,
                })
                marker = hashlib.sha256(marker_seed.encode()).hexdigest()[:24]
                publication_payload, redaction_digest, disclosure_digest = (
                    self._publication_payload(draft, marker=marker, provider=provider)
                )
                payload_digest = stable_digest(publication_payload)
                reusable = next((
                    candidate
                    for candidate in reversed(self.intents.for_draft(draft.draft_id))
                    if candidate.draft_revision == draft.revision
                    and candidate.target_binding == target
                    and candidate.payload_digest == payload_digest
                    and candidate.status in {"previewed", "confirmed"}
                ), None)
                if reusable is None:
                    reusable = PublicationIntent(
                        intent_id=f"pub-{uuid.uuid4().hex[:12]}",
                        draft_id=draft.draft_id,
                        draft_revision=draft.revision,
                        payload=publication_payload,
                        payload_digest=payload_digest,
                        redaction_digest=redaction_digest,
                        disclosure_digest=disclosure_digest,
                        target_binding=target,
                        credential_subject=key.subject,
                        permission_snapshot={
                            "scope": self._permission_scope(provider),
                            "provider": provider,
                            "authorization_domain": key.authorization_domain,
                        },
                        marker=marker,
                    )
                    self.intents.save(reusable)
                intent_ids[provider] = reusable.intent_id
                previews[provider] = dict(reusable.payload)
                child_digests[provider] = reusable.payload_digest

            digest = (
                next(iter(child_digests.values()))
                if len(child_digests) == 1 else stable_digest(child_digests)
            )
            reusable_batch = next((
                item for item in reversed(self.batches.for_draft(draft.draft_id))
                if item.draft_revision == draft.revision
                and item.publication_mode == mode
                and item.intent_ids == intent_ids
                and item.payload_digest == digest
                and item.status in {"previewed", "confirmed"}
            ), None)
            batch = reusable_batch or PublicationBatch(
                batch_id=f"pubb-{uuid.uuid4().hex[:12]}",
                draft_id=draft.draft_id,
                draft_revision=draft.revision,
                publication_mode=mode,
                selected_providers=list(providers),
                intent_ids=intent_ids,
                payload_digest=digest,
            )
            for intent_id in batch.intent_ids.values():
                intent = self.intents.get(intent_id)
                if intent is not None and intent.batch_id != batch.batch_id:
                    intent.batch_id = batch.batch_id
                    intent.updated_at = utc_now()
                    self.intents.save(intent)
            self.batches.save(batch)
            draft.publication_state = batch.status
            draft.updated_at = utc_now()
            self.drafts.save(draft)
            if reusable_batch is None:
                self.writer.emit(
                    "self_issue.publication.previewed",
                    actor="kernel",
                    causation_id=causation_id or None,
                    payload={
                        "draft_id": draft.draft_id,
                        "batch_id": batch.batch_id,
                        "intent_ids": dict(batch.intent_ids),
                        "providers": list(batch.selected_providers),
                        "payload_digest": batch.payload_digest,
                    },
                )
            return self._batch_response(batch, previews=previews)

    def confirm(self, payload: dict[str, Any], *, causation_id: str = "") -> dict[str, Any]:
        batch = self._required_batch(payload)
        draft = self._required_draft({"draft_id": batch.draft_id})
        intents = self._batch_intents(batch)
        if (
            batch.status != "previewed"
            or draft.revision != batch.draft_revision
            or any(intent.status != "previewed" for intent in intents)
        ):
            return {"ok": False, "status": "stale_preview", "reason": "preview no longer matches draft"}
        if str(payload.get("payload_digest") or "") != batch.payload_digest:
            return {"ok": False, "status": "confirmation_mismatch", "reason": "payload digest mismatch"}
        confirmation_id = secrets.token_urlsafe(24)
        expires = datetime.now(timezone.utc) + self._confirmation_ttl
        for intent in intents:
            self._validate_publication_target(draft, intent)
            intent.status = "confirmed"
            intent.confirmation_id = confirmation_id
            intent.confirmation_expires_at = expires.isoformat()
            intent.confirmed_at = utc_now()
            intent.updated_at = utc_now()
            self.intents.save(intent)
        batch.status = "confirmed"
        batch.confirmation_id = confirmation_id
        batch.confirmation_expires_at = expires.isoformat()
        batch.confirmed_at = utc_now()
        batch.updated_at = utc_now()
        self.batches.save(batch)
        draft.publication_state = "confirmed"
        draft.updated_at = utc_now()
        self.drafts.save(draft)
        self.writer.emit(
            "self_issue.publication.confirmed",
            actor="kernel",
            causation_id=causation_id or None,
            payload={
                "draft_id": draft.draft_id,
                "batch_id": batch.batch_id,
                "intent_ids": dict(batch.intent_ids),
                "payload_digest": batch.payload_digest,
                "expires_at": batch.confirmation_expires_at,
            },
        )
        return {
            "ok": True,
            "status": "confirmed",
            "batch_id": batch.batch_id,
            "intent_id": intents[0].intent_id if len(intents) == 1 else "",
            "confirmation_id": confirmation_id,
            "expires_at": batch.confirmation_expires_at,
        }

    def publish(self, payload: dict[str, Any], *, causation_id: str = "") -> dict[str, Any]:
        batch = self._required_batch(payload)
        draft = self._required_draft({"draft_id": batch.draft_id})
        if batch.status == "published":
            return self._batch_response(batch)
        if batch.status == "outcome_unknown":
            return {
                "ok": False,
                "status": "outcome_unknown",
                "batch_id": batch.batch_id,
                "providers": self._provider_statuses(batch),
            }
        if batch.status == "invalidated":
            return {
                "ok": False,
                "status": "stale_confirmation",
                "reason": batch.failure_reason or "publication snapshot changed",
            }
        confirmation_id = str(payload.get("confirmation_id") or "")
        if batch.status != "confirmed" or confirmation_id != batch.confirmation_id:
            return {"ok": False, "status": "confirmation_required", "reason": "valid one-time confirmation required"}
        if datetime.now(timezone.utc) >= self._parse_time(batch.confirmation_expires_at):
            return {"ok": False, "status": "confirmation_expired", "reason": "confirmation expired"}

        provider_secrets: dict[str, tuple[Any, dict[str, str]]] = {}
        for intent in self._batch_intents(batch):
            provider = str(intent.target_binding.get("provider") or "")
            self._validate_publication_snapshot(draft, intent)
            key = self._secret_key(payload, draft, provider=provider)
            if key.subject != intent.credential_subject:
                return {"ok": False, "status": "stale_confirmation", "reason": "credential subject changed"}
            secret = self._provider_secret(key, causation_id=causation_id)
            if not secret:
                return {
                    "ok": False,
                    "status": "authorization_required",
                    "provider": provider,
                    "authorization_domain": key.authorization_domain,
                    "draft_id": draft.draft_id,
                    "batch_id": batch.batch_id,
                    "confirmation_id": batch.confirmation_id,
                }
            if str(secret.get("scope") or self._permission_scope(provider)) != self._permission_scope(provider):
                return {"ok": False, "status": "stale_confirmation", "reason": "permission snapshot changed"}
            provider_secrets[provider] = (key, secret)

        batch.status = "publishing"
        batch.confirmation_id = ""
        batch.updated_at = utc_now()
        self.batches.save(batch)
        for intent in self._batch_intents(batch):
            provider = str(intent.target_binding.get("provider") or "")
            key, secret = provider_secrets[provider]
            self._publish_intent(
                draft=draft,
                intent=intent,
                confirmation_id=confirmation_id,
                secret=secret,
                secret_key=key,
                causation_id=causation_id,
            )
        return self._finish_batch(batch, draft=draft)

    def recover(self, payload: dict[str, Any], *, causation_id: str = "") -> dict[str, Any]:
        intent = self._required_intent(payload)
        if intent.status not in {"publishing", "outcome_unknown"}:
            return {"ok": False, "status": "not_recoverable", "reason": "intent is not outcome_unknown"}
        draft = self._required_draft({"draft_id": intent.draft_id})
        self._validate_publication_target(draft, intent)
        if intent.status == "publishing":
            intent.status = "outcome_unknown"
            intent.updated_at = utc_now()
            self.intents.save(intent)
        provider = str(intent.target_binding.get("provider") or "")
        key = self._secret_key(payload, draft, provider=provider)
        if key.subject != intent.credential_subject:
            return {"ok": False, "status": "stale_confirmation", "reason": "credential subject changed"}
        secret = self._provider_secret(key, causation_id=causation_id)
        if not secret:
            return {"ok": False, "status": "authorization_required", "provider": provider}
        try:
            matches = self._forge_for(provider).find_by_marker(
                str(intent.target_binding.get("project") or ""),
                intent.marker,
                access_token=str(secret["access_token"]),
            )
        except Exception:
            matches = []
        if len(matches) == 1:
            intent.status = "published"
            intent.published_issue_ref = matches[0].__dict__
            intent.updated_at = utc_now()
            self.intents.save(intent)
            self.writer.emit(
                "self_issue.published",
                actor="kernel",
                causation_id=causation_id or None,
                payload={
                    "draft_id": draft.draft_id,
                    "intent_id": intent.intent_id,
                    "provider": provider,
                    "issue_ref": intent.published_issue_ref,
                    "recovered": True,
                },
            )
            batch = self.batches.get(intent.batch_id) if intent.batch_id else None
            return self._finish_batch(batch, draft=draft) if batch else {
                "ok": True, "status": "published", "issue": intent.published_issue_ref,
            }
        return {
            "ok": False,
            "status": "outcome_unknown",
            "provider": provider,
            "intent_id": intent.intent_id,
            "matches": len(matches),
        }

    def resolve_unknown(self, payload: dict[str, Any], *, causation_id: str = "") -> dict[str, Any]:
        intent = self._required_intent(payload)
        if intent.status not in {"publishing", "outcome_unknown"}:
            return {"ok": False, "status": "not_recoverable"}
        evidence_refs = [str(item) for item in payload.get("evidence_refs") or [] if str(item)]
        decision = str(payload.get("decision") or "")
        if not evidence_refs or decision not in {"published", "not_published"}:
            raise ValueError("manual outcome decision and evidence_refs are required")
        draft = self._required_draft({"draft_id": intent.draft_id})
        intent.outcome_evidence_refs = evidence_refs
        if decision == "published":
            issue_ref = dict(payload.get("issue_ref") or {})
            if not str(issue_ref.get("url") or ""):
                raise ValueError("published manual outcome requires issue_ref.url")
            intent.status = "published"
            intent.published_issue_ref = {str(key): str(value) for key, value in issue_ref.items()}
        else:
            intent.status = "publish_failed"
            intent.failure_reason = "manual_not_published"
        intent.updated_at = utc_now()
        self.intents.save(intent)
        event = self.writer.emit(
            "self_issue.publication.resolved",
            actor="kernel",
            causation_id=causation_id or None,
            payload={
                "draft_id": draft.draft_id,
                "intent_id": intent.intent_id,
                "decision": decision,
                "evidence_refs": evidence_refs,
                "issue_ref": intent.published_issue_ref,
            },
        )
        batch = self.batches.get(intent.batch_id) if intent.batch_id else None
        response = self._finish_batch(batch, draft=draft) if batch else {
            "ok": True, "status": intent.status, "issue": intent.published_issue_ref,
        }
        response["event_id"] = event.id
        return response

    def _publish_intent(
        self,
        *,
        draft: Any,
        intent: PublicationIntent,
        confirmation_id: str,
        secret: dict[str, str],
        secret_key: Any,
        causation_id: str,
    ) -> None:
        claimed_intent, claimed = self.intents.claim_publish(intent.intent_id, confirmation_id)
        if not claimed or claimed_intent is None:
            return
        intent = claimed_intent
        provider_name = str(intent.target_binding.get("provider") or "")
        provider = self._forge_for(provider_name)
        try:
            result = provider.publish(IssuePublishRequest(
                project=str(intent.target_binding.get("project") or ""),
                title=str(intent.payload["title"]),
                body=str(intent.payload["body"]),
                labels=tuple(intent.payload.get("labels") or ()),
                marker=intent.marker,
            ), access_token=str(secret["access_token"]))
        except Exception as exc:
            result = ForgeResult(
                status="outcome_unknown", reason=f"provider_exception:{type(exc).__name__}",
            )
        if result.status == "published" and result.issue:
            intent.status = "published"
            intent.published_issue_ref = result.issue.__dict__
            event_type = "self_issue.published"
        elif result.status == "outcome_unknown":
            intent.status = "outcome_unknown"
            event_type = "self_issue.publication.outcome_unknown"
        else:
            intent.status = "publish_failed"
            intent.failure_reason = result.reason
            if result.status == "authorization_required":
                self.secrets.delete(secret_key)
            event_type = "self_issue.publish.failed"
        intent.updated_at = utc_now()
        self.intents.save(intent)
        self.writer.emit(
            event_type,
            actor="kernel",
            causation_id=causation_id or None,
            payload={
                "draft_id": draft.draft_id,
                "batch_id": intent.batch_id,
                "intent_id": intent.intent_id,
                "provider": provider_name,
                "status": intent.status,
                "issue_ref": intent.published_issue_ref,
                "reason": result.reason,
            },
        )

    def _finish_batch(self, batch: PublicationBatch | None, *, draft: Any) -> dict[str, Any]:
        if batch is None:
            raise ValueError("publication batch not found")
        statuses = self._provider_statuses(batch)
        values = set(statuses.values())
        if values == {"published"}:
            status = "published"
        elif values.intersection({"outcome_unknown", "publishing"}):
            status = "outcome_unknown"
        elif "published" in values:
            status = "partially_published"
        elif values == {"publish_failed"}:
            status = "publish_failed"
        else:
            status = "publishing"
        batch.status = status
        batch.updated_at = utc_now()
        self.batches.save(batch)
        refs = self._published_refs(batch)
        draft.publication_state = status
        if refs:
            draft.published_issue_ref = dict(next(iter(refs.values())))
        draft.updated_at = utc_now()
        self.drafts.save(draft)
        response = self._batch_response(batch)
        response.update({
            "ok": status == "published",
            "status": status,
            "providers": statuses,
            "issues": refs,
            "issue": dict(next(iter(refs.values()))) if len(refs) == 1 else {},
            "draft": self._draft_view(draft),
        })
        return response

    def _batch_response(
        self, batch: PublicationBatch, *, previews: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        intents = self._batch_intents(batch)
        previews = previews or {str(item.target_binding["provider"]): dict(item.payload) for item in intents}
        refs = self._published_refs(batch)
        response: dict[str, Any] = {
            "ok": batch.status in {"previewed", "confirmed", "published"},
            "status": batch.status,
            "batch_id": batch.batch_id,
            "draft_revision": batch.draft_revision,
            "publication_mode": batch.publication_mode,
            "intent_ids": dict(batch.intent_ids),
            "payload_digest": batch.payload_digest,
            "previews": previews,
            "providers": self._provider_statuses(batch),
            "issues": refs,
            "confirmation_id": batch.confirmation_id,
        }
        if len(intents) == 1:
            response["intent_id"] = intents[0].intent_id
            response["preview"] = dict(intents[0].payload)
            if refs:
                response["issue"] = dict(next(iter(refs.values())))
        return response

    def _required_batch(self, payload: dict[str, Any]) -> PublicationBatch:
        batch_id = str(payload.get("batch_id") or "").strip()
        if not batch_id:
            intent_id = str(payload.get("intent_id") or "").strip()
            intent = self.intents.get(intent_id) if intent_id else None
            batch_id = intent.batch_id if intent is not None else ""
        batch = self.batches.get(batch_id) if batch_id else None
        if batch is None:
            raise ValueError("publication batch not found")
        return batch

    def _batch_intents(self, batch: PublicationBatch) -> list[PublicationIntent]:
        intents = [self.intents.get(batch.intent_ids[provider]) for provider in batch.selected_providers]
        if any(item is None for item in intents):
            raise ValueError("publication batch intent is missing")
        return [item for item in intents if item is not None]

    def _provider_statuses(self, batch: PublicationBatch) -> dict[str, str]:
        return {
            provider: str(self.intents.get(intent_id).status)
            for provider, intent_id in batch.intent_ids.items()
            if self.intents.get(intent_id) is not None
        }

    def _published_refs(self, batch: PublicationBatch) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        for provider, intent_id in batch.intent_ids.items():
            intent = self.intents.get(intent_id)
            if intent is not None and intent.status == "published" and intent.published_issue_ref:
                result[provider] = dict(intent.published_issue_ref)
        return result

    def _validate_publication_snapshot(self, draft: Any, intent: PublicationIntent) -> None:
        self._validate_publication_target(draft, intent)
        provider = str(intent.target_binding.get("provider") or "")
        current_payload, redaction, disclosure = self._publication_payload(
            draft, marker=intent.marker, provider=provider,
        )
        if (
            draft.revision != intent.draft_revision
            or stable_digest(intent.payload) != intent.payload_digest
            or stable_digest(current_payload) != intent.payload_digest
            or redaction != intent.redaction_digest
            or disclosure != intent.disclosure_digest
        ):
            raise ValueError("publication snapshot changed")
