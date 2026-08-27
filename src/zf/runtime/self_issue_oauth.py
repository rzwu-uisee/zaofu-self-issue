"""GitLab.com OAuth transaction and confirmed-publication continuation."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from zf.core.security.secret_provider import SecretKey
from zf.core.self_issue.models import IssueDraft, stable_digest, utc_now
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.integrations.forge.oauth import pkce_pair


class SelfIssueOAuthMixin:
    """OAuth methods mixed into ``SelfIssueService`` without a second state path."""

    def oauth_start(self, payload: dict[str, Any], *, causation_id: str = "") -> dict[str, Any]:
        draft = self._required_draft(payload)
        self._validate_policy_target(draft.target_binding)
        authorization_domain = str(
            payload.get("authorization_domain")
            or getattr(self._target_config("gitlab"), "authorization_domain", "")
            or "gitlab.com"
        )
        if self._forge_for("gitlab").name != "gitlab" or authorization_domain != "gitlab.com":
            raise ValueError("GitLab OAuth supports GitLab.com only")
        target = self._target_config("gitlab")
        client_id = self._oauth_setting(
            payload, "client_id", getattr(target, "oauth_client_id", ""),
            "ZF_GITLAB_OAUTH_CLIENT_ID",
        )
        redirect_uri = self._oauth_setting(
            payload, "redirect_uri", getattr(target, "oauth_redirect_uri", ""),
            "ZF_GITLAB_OAUTH_REDIRECT_URI",
        )
        session_id = str(payload.get("session_id") or "").strip()
        if not client_id or not redirect_uri or not session_id:
            raise ValueError("client_id, redirect_uri, and session_id are required")
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(32)
        key = self._secret_key(payload, draft, provider="gitlab")
        continuation = self._oauth_publication_continuation(
            payload, draft=draft, credential_subject=key.subject,
        )
        tx = {
            "state": state,
            "verifier": verifier,
            "draft_id": draft.draft_id,
            "session_id": session_id,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "consumed": False,
            "user_id": key.user_id,
            "workspace_id": key.workspace_id,
            "provider": key.provider,
            "authorization_domain": key.authorization_domain,
            "publication_continuation": continuation,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        }
        self._save_oauth_transaction(tx)
        url = self.oauth.authorization_url(
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state,
            challenge=challenge,
        )
        self.writer.emit(
            "self_issue.oauth.started",
            actor="kernel",
            causation_id=causation_id or None,
            payload={
                "draft_id": draft.draft_id,
                "session_binding_digest": hashlib.sha256(session_id.encode()).hexdigest(),
                "authorization_domain": "gitlab.com",
                "publication_continuation": bool(continuation),
                "continuation_intent_id": continuation.get("intent_id", ""),
                "continuation_batch_id": continuation.get("batch_id", ""),
            },
        )
        return {
            "ok": True,
            "status": "authorization_required",
            "authorization_url": url,
            "draft_id": draft.draft_id,
            "scope": "api",
            "scope_notice": "GitLab api scope includes broader API access than issue creation alone.",
        }

    def oauth_callback(self, payload: dict[str, Any], *, causation_id: str = "") -> dict[str, Any]:
        tx = self._consume_oauth_transaction(
            str(payload.get("state") or ""),
            session_id=str(payload.get("session_id") or ""),
        )
        draft = self._required_draft({"draft_id": str(tx["draft_id"])})
        self._validate_policy_target(draft.target_binding)
        token = self.oauth.exchange(
            code=str(payload.get("code") or ""),
            verifier=str(tx["verifier"]),
            client_id=str(tx["client_id"]),
            redirect_uri=str(tx["redirect_uri"]),
        )
        key = SecretKey(
            user_id=str(tx["user_id"]),
            workspace_id=str(tx["workspace_id"]),
            provider=str(tx["provider"]),
            authorization_domain=str(tx["authorization_domain"]),
        )
        token["client_id"] = str(tx["client_id"])
        token["redirect_uri"] = str(tx["redirect_uri"])
        self.secrets.put(key, token)
        continuation = tx.get("publication_continuation")
        continuation_intent_id = (
            str(continuation.get("intent_id") or "")
            if isinstance(continuation, dict) else ""
        )
        continuation_preparation_id = (
            str(continuation.get("preparation_id") or "")
            if isinstance(continuation, dict) else ""
        )
        continuation_batch_id = (
            str(continuation.get("batch_id") or "")
            if isinstance(continuation, dict) else ""
        )
        preserved_batch_id = continuation_batch_id
        if not preserved_batch_id and continuation_intent_id:
            continuation_intent = self.intents.get(continuation_intent_id)
            preserved_batch_id = continuation_intent.batch_id if continuation_intent else ""
        preserved_intents = {
            continuation_intent_id,
            *(
                self.batches.get(continuation_batch_id).intent_ids.values()
                if continuation_batch_id and self.batches.get(continuation_batch_id)
                else ()
            ),
        } - {""}
        invalidated = self.intents.invalidate_unpublished(
            draft.draft_id,
            reason="authorization_changed",
            except_intent_ids=frozenset(preserved_intents),
        )
        self.batches.invalidate_unpublished(
            draft.draft_id,
            reason="authorization_changed",
            except_batch_ids=(
                frozenset({preserved_batch_id})
                if preserved_batch_id else frozenset()
            ),
        )
        if invalidated:
            draft.publication_state = "draft"
            draft.updated_at = utc_now()
            self.drafts.save(draft)
        self.writer.emit(
            "self_issue.oauth.connected",
            actor="kernel",
            causation_id=causation_id or None,
            payload={
                "draft_id": draft.draft_id,
                "credential_subject": key.subject,
                "authorization_domain": "gitlab.com",
                "invalidated_intent_ids": invalidated,
                "publication_continuation": bool(
                    continuation_intent_id or continuation_preparation_id
                    or continuation_batch_id
                ),
                "continuation_intent_id": continuation_intent_id,
                "continuation_preparation_id": continuation_preparation_id,
                "continuation_batch_id": continuation_batch_id,
            },
        )
        if isinstance(continuation, dict) and (
            continuation_intent_id or continuation_preparation_id or continuation_batch_id
        ):
            return self._resume_oauth_publication(
                draft=draft,
                key=key,
                tx=tx,
                continuation=continuation,
                causation_id=causation_id,
            )
        return {
            "ok": True,
            "status": "connected",
            "draft_id": draft.draft_id,
            "draft": self._draft_view(draft),
            "credential_subject": key.subject,
            "published": False,
        }

    def oauth_disconnect(self, payload: dict[str, Any], *, causation_id: str = "") -> dict[str, Any]:
        draft = self._required_draft(payload)
        provider = str(payload.get("provider") or "gitlab").strip().lower()
        self._target_for_provider(provider)
        key = self._secret_key(payload, draft, provider=provider)
        deleted = self.secrets.delete(key)
        event = self.writer.emit(
            "self_issue.oauth.disconnected",
            actor="kernel",
            causation_id=causation_id or None,
            payload={
                "draft_id": draft.draft_id,
                "credential_subject": key.subject,
                "authorization_domain": key.authorization_domain,
                "provider": provider,
                "deleted": deleted,
            },
        )
        return {
            "ok": True,
            "status": "disconnected",
            "deleted": deleted,
            "event_id": event.id,
        }

    def _resume_oauth_publication(
        self,
        *,
        draft: IssueDraft,
        key: SecretKey,
        tx: dict[str, Any],
        continuation: dict[str, Any],
        causation_id: str,
    ) -> dict[str, Any]:
        common = {
            "confirmation_id": str(continuation.get("confirmation_id") or ""),
            "user_id": str(tx["user_id"]),
            "workspace_id": str(tx["workspace_id"]),
            "authorization_domain": str(tx["authorization_domain"]),
        }
        if continuation.get("kind") == "attachment_preparation":
            publish_result = self.attachment_prepare({
                **common,
                "preparation_id": str(continuation.get("preparation_id") or ""),
            }, causation_id=causation_id)
        elif continuation.get("kind") == "publication_batch":
            publish_result = self.publish({
                "confirmation_id": common["confirmation_id"],
                "user_id": common["user_id"],
                "workspace_id": common["workspace_id"],
                "batch_id": str(continuation.get("batch_id") or ""),
            }, causation_id=causation_id)
        else:
            publish_result = self.publish({
                **common,
                "intent_id": str(continuation.get("intent_id") or ""),
            }, causation_id=causation_id)
        if publish_result.get("status") in {
            "confirmation_expired", "confirmation_required", "stale_confirmation",
        }:
            self.intents.invalidate_unpublished(
                draft.draft_id, reason="OAuth publication continuation became stale",
            )
            stale_draft = self._required_draft({"draft_id": draft.draft_id})
            stale_draft.publication_state = "draft"
            stale_draft.updated_at = utc_now()
            self.drafts.save(stale_draft)
        current = self._required_draft({"draft_id": draft.draft_id})
        return {
            **publish_result,
            "draft_id": draft.draft_id,
            "draft": self._draft_view(current),
            "credential_subject": key.subject,
            "resumed_publication": True,
        }

    def _oauth_publication_continuation(
        self,
        payload: dict[str, Any],
        *,
        draft: IssueDraft,
        credential_subject: str,
    ) -> dict[str, str]:
        preparation_id = str(payload.get("preparation_id") or "").strip()
        batch_id = str(payload.get("batch_id") or "").strip()
        intent_id = str(payload.get("intent_id") or "").strip()
        confirmation_id = str(payload.get("confirmation_id") or "").strip()
        if batch_id:
            if preparation_id or intent_id or not confirmation_id:
                raise ValueError(
                    "OAuth continuation must identify exactly one confirmed operation"
                )
            batch = self.batches.get(batch_id)
            if (
                batch is None
                or batch.draft_id != draft.draft_id
                or batch.status != "confirmed"
                or batch.confirmation_id != confirmation_id
                or "gitlab" not in batch.selected_providers
            ):
                raise ValueError("OAuth publication batch is not confirmed for this Draft")
            if datetime.now(timezone.utc) >= _parse_time(batch.confirmation_expires_at):
                raise ValueError("OAuth publication batch confirmation expired")
            intents = self._batch_intents(batch)
            gitlab_intent = next((
                item for item in intents
                if str(item.target_binding.get("provider") or "") == "gitlab"
            ), None)
            if gitlab_intent is None or gitlab_intent.credential_subject != credential_subject:
                raise ValueError("OAuth publication batch credential subject changed")
            self._validate_publication_snapshot(draft, gitlab_intent)
            return {
                "kind": "publication_batch",
                "batch_id": batch.batch_id,
                "confirmation_id": batch.confirmation_id,
                "payload_digest": batch.payload_digest,
            }
        if preparation_id:
            if intent_id or not confirmation_id:
                raise ValueError("OAuth continuation must identify exactly one confirmed operation")
            preparation = self._required_attachment_intent({"preparation_id": preparation_id})
            if preparation.draft_id != draft.draft_id or preparation.status != "confirmed":
                raise ValueError("OAuth attachment continuation is not confirmed for this Draft")
            if preparation.confirmation_id != confirmation_id:
                raise ValueError("OAuth attachment continuation confirmation does not match")
            if datetime.now(timezone.utc) >= _parse_time(preparation.confirmation_expires_at):
                raise ValueError("OAuth attachment continuation confirmation expired")
            if preparation.credential_subject != credential_subject:
                raise ValueError("OAuth attachment continuation credential subject changed")
            if (
                preparation.draft_revision != draft.revision
                or stable_digest(self._attachment_manifest(draft)) != preparation.manifest_digest
            ):
                raise ValueError("OAuth attachment continuation snapshot changed")
            return {
                "kind": "attachment_preparation",
                "preparation_id": preparation.preparation_id,
                "confirmation_id": preparation.confirmation_id,
                "manifest_digest": preparation.manifest_digest,
            }
        if not intent_id and not confirmation_id:
            return {}
        if not intent_id or not confirmation_id:
            raise ValueError("OAuth publication continuation requires intent_id and confirmation_id")
        intent = self._required_intent({"intent_id": intent_id})
        self._validate_publication_target(draft, intent)
        if intent.draft_id != draft.draft_id or intent.status != "confirmed":
            raise ValueError("OAuth publication continuation is not confirmed for this Draft")
        if intent.confirmation_id != confirmation_id:
            raise ValueError("OAuth publication continuation confirmation does not match")
        if datetime.now(timezone.utc) >= _parse_time(intent.confirmation_expires_at):
            raise ValueError("OAuth publication continuation confirmation expired")
        if intent.credential_subject != credential_subject:
            raise ValueError("OAuth publication continuation credential subject changed")
        current_payload, current_redaction, current_disclosure = self._publication_payload(
            draft, marker=intent.marker,
        )
        if (
            draft.revision != intent.draft_revision
            or stable_digest(current_payload) != intent.payload_digest
            or current_redaction != intent.redaction_digest
            or current_disclosure != intent.disclosure_digest
        ):
            raise ValueError("OAuth publication continuation snapshot changed")
        return {
            "kind": "publication",
            "intent_id": intent.intent_id,
            "confirmation_id": intent.confirmation_id,
            "payload_digest": intent.payload_digest,
        }

    def _oauth_setting(
        self,
        payload: dict[str, Any],
        field: str,
        configured: str,
        env_name: str,
    ) -> str:
        supplied = str(payload.get(field) or "").strip()
        configured = str(configured or "").strip()
        if self.policy.enabled and self.policy.target_locked and configured:
            if supplied and supplied != configured:
                raise ValueError(f"Self-Issue {field} is fixed by zf.yaml")
            return configured
        return supplied or configured or str(os.environ.get(env_name) or "").strip()

    @property
    def _oauth_path(self) -> Path:
        return self.state_dir / "self-issues" / "oauth-transactions.json"

    def _load_oauth(self) -> list[dict[str, Any]]:
        if not self._oauth_path.exists():
            return []
        value = json.loads(self._oauth_path.read_text(encoding="utf-8") or "[]")
        return value if isinstance(value, list) else []

    def _save_oauth_transaction(self, transaction: dict[str, Any]) -> None:
        with locked_path(self._oauth_path):
            rows = self._load_oauth()
            rows.append(transaction)
            atomic_write_text(
                self._oauth_path,
                json.dumps(rows, indent=2, sort_keys=True) + "\n",
            )
            os.chmod(self._oauth_path, 0o600)

    def _consume_oauth_transaction(self, state: str, *, session_id: str) -> dict[str, Any]:
        with locked_path(self._oauth_path):
            rows = self._load_oauth()
            match = next((row for row in rows if row.get("state") == state), None)
            if not match or match.get("consumed") or match.get("session_id") != session_id:
                raise ValueError("oauth state is invalid, consumed, or belongs to another session")
            if datetime.now(timezone.utc) >= _parse_time(str(match["expires_at"])):
                raise ValueError("oauth state expired")
            match["consumed"] = True
            atomic_write_text(
                self._oauth_path,
                json.dumps(rows, indent=2, sort_keys=True) + "\n",
            )
            os.chmod(self._oauth_path, 0o600)
            return dict(match)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
