"""Controlled actions for policy-authorized low-risk evolution assets."""

from __future__ import annotations

from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.evolution_contracts import stable_digest
from zf.runtime.evolution_coordinator import EvolutionCoordinator


_AUTO_STATES = frozenset({
    "validated",
    "approved",
    "canary_active",
    "active_retained",
    "revoked",
})


class EvolutionActionsMixin:
    def _evolution_asset_transition(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        error = self._evolution_policy_error(payload, transition=True)
        if error:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason=error,
                status_code=403,
                status="policy_rejected",
            )
        asset_id = str(payload.get("asset_id") or "").strip()
        target_state = str(payload.get("target_state") or "").strip()
        try:
            version = int(payload.get("version") or 0)
            revision = int(payload.get("expected_revision") or 0)
        except (TypeError, ValueError):
            version = revision = 0
        if not asset_id or version < 1 or revision < 1 or target_state not in _AUTO_STATES:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="invalid evolution asset transition payload",
                status_code=422,
                status="invalid_payload",
            )
        action_id = str(payload.get("action_id") or "").strip()
        if not action_id:
            action_id = "evoact-" + stable_digest(payload)[:20]
        receipt = write_immutable_json_sidecar(
            self.state_dir,
            {
                "schema_version": "controlled-action-receipt.v1",
                "action": action,
                "action_id": action_id,
                "asset_id": asset_id,
                "version": version,
                "target_state": target_state,
                "expected_revision": revision,
                "campaign_id": str(payload.get("campaign_id") or ""),
                "policy_digest": str(payload.get("policy_digest") or ""),
                "requested_event_id": requested.id,
                "actor": self.actor,
                "source": self.source,
            },
            root="evolution/receipts",
            kind="controlled_action_receipt",
            schema_version="controlled-action-receipt.v1",
            created_by=self.actor,
            source_event_id=requested.id,
        )
        result = EvolutionCoordinator(
            self.state_dir, writer=self.writer
        ).transition_asset(
            asset_id=asset_id,
            version=version,
            target_state=target_state,
            expected_revision=revision,
            action_id=action_id,
            receipt_ref=receipt,
            actor=self.actor,
        )
        event = self.writer.emit(
            "evolution.control_action.completed",
            actor=self.actor,
            causation_id=requested.id,
            correlation_id=str(payload.get("campaign_id") or asset_id),
            payload={
                "action": action,
                "action_id": action_id,
                "asset_id": asset_id,
                "version": version,
                "target_state": target_state,
                "applied": bool(result["applied"]),
                "receipt_ref": receipt,
            },
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="applied" if result["applied"] else "already_applied",
            task_id=None,
        )
        return {
            "_status_code": 200,
            "ok": True,
            "status": "applied" if result["applied"] else "already_applied",
            "action": action,
            "requested_action": requested_action,
            "event_id": event.id,
            "receipt_ref": receipt,
            **result,
        }

    def _evolution_asset_outcome(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        error = self._evolution_policy_error(payload, transition=False)
        if error:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason=error,
                status_code=403,
                status="policy_rejected",
            )
        asset_id = str(payload.get("asset_id") or "").strip()
        usage_ref = str(payload.get("usage_ref") or "").strip()
        outcome = str(payload.get("outcome") or "").strip()
        try:
            version = int(payload.get("version") or 0)
        except (TypeError, ValueError):
            version = 0
        if (
            not asset_id
            or version < 1
            or not usage_ref
            or outcome not in {"passed", "failed", "regressed", "neutral"}
        ):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="invalid evolution asset outcome payload",
                status_code=422,
                status="invalid_payload",
            )
        result = EvolutionCoordinator(
            self.state_dir, writer=self.writer
        ).record_asset_outcome(
            asset_id=asset_id,
            version=version,
            usage_ref=usage_ref,
            matched=True,
            outcome=outcome,
            cost=dict(payload.get("cost") or {}),
            cohort=dict(payload.get("cohort") or {}),
            evaluation=dict(payload.get("evaluation") or {}),
            actor=self.actor,
        )
        event = self.writer.emit(
            "evolution.control_action.completed",
            actor=self.actor,
            causation_id=requested.id,
            correlation_id=str(payload.get("campaign_id") or asset_id),
            payload={
                "action": action,
                "asset_id": asset_id,
                "version": version,
                "usage_ref": usage_ref,
                "outcome": outcome,
                "recorded": bool(result["recorded"]),
            },
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="recorded" if result["recorded"] else "already_recorded",
            task_id=None,
        )
        return {
            "_status_code": 200,
            "ok": True,
            "status": "recorded" if result["recorded"] else "already_recorded",
            "action": action,
            "requested_action": requested_action,
            "event_id": event.id,
            **result,
        }

    def _evolution_policy_error(
        self, payload: dict[str, Any], *, transition: bool
    ) -> str:
        policy = getattr(getattr(self.config, "runtime", None), "evolution", None)
        if self.actor != "run-manager" or self.source != "self-evolution":
            return "unattended evolution actions require the Run Manager authority"
        if policy is None or not bool(getattr(policy, "enabled", False)):
            return "runtime.evolution is disabled"
        if str(getattr(policy, "mode", "")) != "auto_low_risk":
            return "runtime.evolution.mode does not permit automatic adoption"
        registry = EvolutionCoordinator(self.state_dir).capabilities.load()
        key = f"{str(payload.get('asset_id') or '')}@{int(payload.get('version') or 0)}"
        asset = registry["assets"].get(key)
        if not isinstance(asset, dict):
            return "learning asset is unavailable"
        if str(asset.get("asset_kind") or "") not in set(
            getattr(policy, "auto_asset_kinds", []) or []
        ):
            return "learning asset kind is not authorized for unattended adoption"
        expected_policy = str(
            (asset.get("activation") or {}).get("automation_policy_digest") or ""
        )
        if not expected_policy or expected_policy != str(payload.get("policy_digest") or ""):
            return "evolution policy digest drift"
        if transition and str(payload.get("target_state") or "") not in _AUTO_STATES:
            return "evolution target state is not policy-authorized"
        return ""


__all__ = ["EvolutionActionsMixin"]
