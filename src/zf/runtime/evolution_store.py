"""Canonical current-state stores for evolution trials and capabilities."""

from __future__ import annotations

import json
import math
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.runtime.evolution_contracts import EvolutionContractError, stable_digest


EVOLUTION_TRIAL_STORE_SCHEMA = "evolution-trial-store.v1"
CAPABILITY_REGISTRY_SCHEMA = "capability-registry.v1"
TRIAL_STATUSES = frozenset({
    "prepared",
    "running",
    "failed",
    "settled",
    "dead_letter",
})
ASSET_STATES = frozenset({
    "candidate",
    "validated",
    "approved",
    "canary_active",
    "active_retained",
    "rejected",
    "expired",
    "revoked",
    "superseded",
})
_ASSET_TRANSITIONS = {
    "candidate": {"validated", "rejected", "expired"},
    "validated": {"approved", "rejected", "expired"},
    "approved": {"canary_active", "rejected", "expired"},
    "canary_active": {"active_retained", "revoked"},
    "active_retained": {"revoked", "superseded"},
    "rejected": set(),
    "expired": set(),
    "revoked": set(),
    "superseded": set(),
}


class EvolutionStoreError(RuntimeError):
    pass


class EvolutionConflictError(EvolutionStoreError):
    pass


class EvolutionTrialStore:
    """TaskAttempt-style lease and effectively-once trial settlement store."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        with locked_path(self.path):
            return self._load_unlocked()

    def register_attempt(
        self,
        *,
        attempt_id: str,
        artifact_ref: Mapping[str, Any],
        idempotency_key: str,
        max_trial_attempts: int,
        created_at: str,
    ) -> tuple[dict[str, Any], bool]:
        with locked_path(self.path):
            data = self._load_unlocked()
            existing = data["attempts"].get(attempt_id)
            identity = {
                "artifact_ref": dict(artifact_ref),
                "idempotency_key": idempotency_key,
                "max_trial_attempts": int(max_trial_attempts),
            }
            if isinstance(existing, dict):
                if stable_digest({key: existing.get(key) for key in identity}) != stable_digest(identity):
                    raise EvolutionConflictError(
                        f"evolution attempt identity conflict: {attempt_id}"
                    )
                return deepcopy(existing), False
            row = {
                "attempt_id": attempt_id,
                **identity,
                "status": "materialized",
                "created_at": created_at,
                "updated_at": created_at,
            }
            data["attempts"][attempt_id] = row
            self._save_unlocked(data)
            return deepcopy(row), True

    def ensure_trial(
        self,
        *,
        attempt_id: str,
        arm: str,
        replicate: int,
        created_at: str,
    ) -> tuple[dict[str, Any], bool]:
        if arm not in {"baseline", "candidate"}:
            raise EvolutionStoreError("trial arm must be baseline or candidate")
        if int(replicate) < 1:
            raise EvolutionStoreError("trial replicate must be positive")
        trial_id = "evotrial-" + stable_digest({
            "attempt_id": attempt_id,
            "arm": arm,
            "replicate": int(replicate),
        })[:20]
        with locked_path(self.path):
            data = self._load_unlocked()
            if attempt_id not in data["attempts"]:
                raise EvolutionStoreError(f"evolution attempt not found: {attempt_id}")
            existing = data["trials"].get(trial_id)
            if isinstance(existing, dict):
                return deepcopy(existing), False
            row = {
                "schema_version": "evolution-trial.v1",
                "trial_id": trial_id,
                "attempt_id": attempt_id,
                "arm": arm,
                "replicate": int(replicate),
                "attempt_number": 0,
                "status": "prepared",
                "lease_owner": "",
                "lease_expires_at": "",
                "started_at": "",
                "settled_at": "",
                "accepted_settlement_id": "",
                "settlement_ref": {},
                "archive_ref": "",
                "archive_digest": "",
                "cost_receipt_refs": [],
                "retryable": None,
                "failure_class": "",
                "created_at": created_at,
                "updated_at": created_at,
            }
            data["trials"][trial_id] = row
            self._save_unlocked(data)
            return deepcopy(row), True

    def claim_trial(
        self,
        trial_id: str,
        *,
        lease_owner: str,
        now: str,
        lease_expires_at: str,
    ) -> tuple[dict[str, Any], bool]:
        if not lease_owner:
            raise EvolutionStoreError("trial lease_owner is required")
        with locked_path(self.path):
            data = self._load_unlocked()
            row = data["trials"].get(trial_id)
            if not isinstance(row, dict):
                raise EvolutionStoreError(f"evolution trial not found: {trial_id}")
            if row["status"] in {"settled", "dead_letter"}:
                return deepcopy(row), False
            if row["status"] == "running" and not _is_expired(
                str(row.get("lease_expires_at") or ""), now
            ):
                return deepcopy(row), False
            parent = data["attempts"][row["attempt_id"]]
            next_attempt = int(row.get("attempt_number") or 0) + 1
            if next_attempt > int(parent["max_trial_attempts"]):
                row.update({
                    "status": "dead_letter",
                    "failure_class": "trial_attempt_budget_exhausted",
                    "retryable": False,
                    "updated_at": now,
                })
                self._save_unlocked(data)
                return deepcopy(row), False
            if row["status"] == "failed" and not bool(row.get("retryable")):
                return deepcopy(row), False
            row.update({
                "status": "running",
                "attempt_number": next_attempt,
                "lease_owner": lease_owner,
                "lease_expires_at": lease_expires_at,
                "started_at": now,
                "updated_at": now,
                "retryable": None,
                "failure_class": "",
            })
            self._save_unlocked(data)
            return deepcopy(row), True

    def settle_trial(
        self,
        trial_id: str,
        *,
        lease_owner: str,
        attempt_number: int,
        outcome: str,
        settlement_ref: Mapping[str, Any],
        archive_ref: str,
        archive_digest: str,
        cost_receipt_refs: list[str],
        failure_class: str,
        retryable: bool | None = None,
        settled_at: str,
    ) -> tuple[dict[str, Any], str]:
        if outcome not in {"passed", "semantic_failed", "infrastructure_failed"}:
            raise EvolutionStoreError(f"unsupported trial outcome: {outcome}")
        settlement_identity = {
            "trial_id": trial_id,
            "attempt_number": int(attempt_number),
            "outcome": outcome,
            "settlement_ref": dict(settlement_ref),
            "archive_ref": archive_ref,
            "archive_digest": archive_digest,
            "cost_receipt_refs": list(cost_receipt_refs),
            "failure_class": failure_class,
            "retryable": retryable,
        }
        settlement_id = "evoset-" + stable_digest(settlement_identity)[:20]
        with locked_path(self.path):
            data = self._load_unlocked()
            row = data["trials"].get(trial_id)
            if not isinstance(row, dict):
                raise EvolutionStoreError(f"evolution trial not found: {trial_id}")
            accepted = str(row.get("accepted_settlement_id") or "")
            if accepted:
                return deepcopy(row), "duplicate" if accepted == settlement_id else "stale"
            if (
                row.get("status") != "running"
                or str(row.get("lease_owner") or "") != lease_owner
                or int(row.get("attempt_number") or 0) != int(attempt_number)
            ):
                return deepcopy(row), "stale"
            if outcome == "infrastructure_failed":
                should_retry = True if retryable is None else bool(retryable)
                if not should_retry:
                    row.update({
                        "status": "dead_letter",
                        "outcome": outcome,
                        "accepted_settlement_id": settlement_id,
                        "settlement_ref": dict(settlement_ref),
                        "archive_ref": archive_ref,
                        "archive_digest": archive_digest,
                        "cost_receipt_refs": list(cost_receipt_refs),
                        "failure_class": failure_class or "infrastructure_failure",
                        "retryable": False,
                        "lease_owner": "",
                        "lease_expires_at": "",
                        "settled_at": settled_at,
                        "updated_at": settled_at,
                    })
                    self._save_unlocked(data)
                    return deepcopy(row), "dead_letter"
                row.update({
                    "status": "failed",
                    "retryable": True,
                    "failure_class": failure_class or "infrastructure_failure",
                    "lease_owner": "",
                    "lease_expires_at": "",
                    "updated_at": settled_at,
                })
                self._save_unlocked(data)
                return deepcopy(row), "retryable"
            row.update({
                "status": "settled",
                "outcome": outcome,
                "accepted_settlement_id": settlement_id,
                "settlement_ref": dict(settlement_ref),
                "archive_ref": archive_ref,
                "archive_digest": archive_digest,
                "cost_receipt_refs": list(cost_receipt_refs),
                "failure_class": failure_class,
                "retryable": False,
                "settled_at": settled_at,
                "updated_at": settled_at,
            })
            self._save_unlocked(data)
            return deepcopy(row), "accepted"

    def register_comparison(
        self,
        comparison: Mapping[str, Any],
        *,
        artifact_ref: Mapping[str, Any],
        created_at: str,
    ) -> tuple[dict[str, Any], bool]:
        comparison_id = str(comparison.get("comparison_id") or "")
        if not comparison_id:
            raise EvolutionStoreError("comparison_id is required")
        row = {
            "comparison_id": comparison_id,
            "attempt_id": str(comparison.get("attempt_id") or ""),
            "status": str(comparison.get("status") or ""),
            "adoption_eligible": bool(comparison.get("adoption_eligible")),
            "evaluator_generation_id": str(
                comparison.get("evaluator_generation_id") or ""
            ),
            "evaluator_generation_digest": str(
                comparison.get("evaluator_generation_digest") or ""
            ),
            "artifact_ref": dict(artifact_ref),
            "created_at": created_at,
        }
        with locked_path(self.path):
            data = self._load_unlocked()
            existing = data["comparisons"].get(comparison_id)
            if isinstance(existing, dict):
                if stable_digest(existing) != stable_digest(row):
                    raise EvolutionConflictError(
                        f"comparison identity conflict: {comparison_id}"
                    )
                return deepcopy(existing), False
            data["comparisons"][comparison_id] = row
            self._save_unlocked(data)
            return deepcopy(row), True

    def trials_for_attempt(self, attempt_id: str) -> list[dict[str, Any]]:
        data = self.load()
        return [
            deepcopy(row)
            for row in data["trials"].values()
            if row.get("attempt_id") == attempt_id
        ]

    def _load_unlocked(self) -> dict[str, Any]:
        raw = _read_json(self.path)
        if raw and raw.get("schema_version") != EVOLUTION_TRIAL_STORE_SCHEMA:
            raise EvolutionStoreError("unsupported evolution trial store schema")
        return {
            "schema_version": EVOLUTION_TRIAL_STORE_SCHEMA,
            "revision": int(raw.get("revision") or 0),
            "attempts": _object(raw.get("attempts"), "attempts"),
            "trials": _object(raw.get("trials"), "trials"),
            "comparisons": _object(raw.get("comparisons"), "comparisons"),
        }

    def _save_unlocked(self, data: Mapping[str, Any]) -> None:
        body = {
            **dict(data),
            "schema_version": EVOLUTION_TRIAL_STORE_SCHEMA,
            "revision": int(data.get("revision") or 0) + 1,
        }
        atomic_write_text(self.path, _json_text(body))


class CapabilityRegistry:
    """CAS-owned lifecycle for memory, skill, workflow, and route assets."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        with locked_path(self.path):
            return self._load_unlocked()

    def propose(
        self,
        asset: Mapping[str, Any],
        *,
        artifact_ref: Mapping[str, Any],
        created_at: str,
    ) -> tuple[dict[str, Any], bool]:
        asset_id = str(asset.get("asset_id") or "").strip()
        version = int(asset.get("version") or 0)
        if not asset_id or version < 1:
            raise EvolutionStoreError("learning asset requires asset_id and version")
        key = _asset_key(asset_id, version)
        fingerprint = str(asset.get("proposal_fingerprint") or stable_digest(asset))
        with locked_path(self.path):
            data = self._load_unlocked()
            rejected = data["rejected_fingerprints"].get(fingerprint)
            if rejected:
                raise EvolutionConflictError(
                    f"learning proposal fingerprint was already rejected: {fingerprint}"
                )
            existing = data["assets"].get(key)
            row = {
                "schema_version": "learning-asset.v1",
                "asset_id": asset_id,
                "asset_kind": str(asset.get("asset_kind") or ""),
                "version": version,
                "digest": str(asset.get("digest") or ""),
                "state": "candidate",
                "revision": 1,
                "proposal_fingerprint": fingerprint,
                "artifact_ref": dict(artifact_ref),
                "source_attempt_ids": list(asset.get("source_attempt_ids") or []),
                "applicability": deepcopy(dict(asset.get("applicability") or {})),
                "quality": deepcopy(dict(asset.get("quality") or {})),
                "activation": deepcopy(dict(asset.get("activation") or {})),
                "rollback": deepcopy(dict(asset.get("rollback") or {})),
                "dependencies": deepcopy(list(asset.get("dependencies") or [])),
                "provenance": deepcopy(dict(asset.get("provenance") or {})),
                "taint": deepcopy(dict(asset.get("taint") or {})),
                "outcomes": [],
                "created_at": created_at,
                "updated_at": created_at,
            }
            if isinstance(existing, dict):
                stable_fields = {
                    key: row[key]
                    for key in (
                        "asset_id", "asset_kind", "version", "digest",
                        "proposal_fingerprint", "artifact_ref",
                    )
                }
                if stable_digest({key: existing.get(key) for key in stable_fields}) != stable_digest(stable_fields):
                    raise EvolutionConflictError(f"learning asset conflict: {key}")
                return deepcopy(existing), False
            data["assets"][key] = row
            self._save_unlocked(data)
            return deepcopy(row), True

    def transition(
        self,
        *,
        asset_id: str,
        version: int,
        target_state: str,
        expected_revision: int,
        action_id: str,
        receipt_ref: Mapping[str, Any],
        updated_at: str,
    ) -> tuple[dict[str, Any], bool]:
        if target_state not in ASSET_STATES:
            raise EvolutionStoreError(f"unsupported asset state: {target_state}")
        key = _asset_key(asset_id, version)
        with locked_path(self.path):
            data = self._load_unlocked()
            prior_action = data["actions"].get(action_id)
            if isinstance(prior_action, dict):
                if prior_action.get("asset_key") != key or prior_action.get("target_state") != target_state:
                    raise EvolutionConflictError(f"asset action id conflict: {action_id}")
                return deepcopy(data["assets"][key]), False
            row = data["assets"].get(key)
            if not isinstance(row, dict):
                raise EvolutionStoreError(f"learning asset not found: {key}")
            if int(row.get("revision") or 0) != int(expected_revision):
                raise EvolutionConflictError(
                    f"stale learning asset revision: expected {expected_revision}, current {row.get('revision')}"
                )
            current = str(row.get("state") or "")
            if target_state not in _ASSET_TRANSITIONS.get(current, set()):
                raise EvolutionConflictError(
                    f"unsupported learning asset transition: {current} -> {target_state}"
                )
            if target_state in {"canary_active", "active_retained"}:
                _assert_activation_eligible(data, row, target_state=target_state)
            if target_state == "canary_active":
                active_key = str(data["active_versions"].get(asset_id) or "")
                current_canary = str(data["canary_versions"].get(asset_id) or "")
                expected_active = str(
                    (row.get("activation") or {}).get("expected_active_key") or ""
                )
                if active_key != expected_active:
                    raise EvolutionConflictError(
                        f"active-version CAS failed for {asset_id}"
                    )
                if current_canary and current_canary != key:
                    raise EvolutionConflictError(
                        f"another canary already owns {asset_id}"
                    )
            previous_key = str(data["active_versions"].get(asset_id) or "")
            row["state"] = target_state
            row["revision"] = int(row["revision"]) + 1
            row["updated_at"] = updated_at
            row["last_receipt_ref"] = dict(receipt_ref)
            if target_state == "active_retained":
                data["active_versions"][asset_id] = key
                data["canary_versions"].pop(asset_id, None)
                row["previous_active_key"] = previous_key
            elif target_state == "canary_active":
                data["canary_versions"][asset_id] = key
            elif target_state == "revoked":
                if data["canary_versions"].get(asset_id) == key:
                    data["canary_versions"].pop(asset_id, None)
                if data["active_versions"].get(asset_id) == key:
                    previous = str(row.get("previous_active_key") or "")
                    if previous:
                        data["active_versions"][asset_id] = previous
                    else:
                        data["active_versions"].pop(asset_id, None)
            elif target_state == "rejected":
                data["rejected_fingerprints"][row["proposal_fingerprint"]] = {
                    "asset_key": key,
                    "receipt_ref": dict(receipt_ref),
                }
            data["actions"][action_id] = {
                "asset_key": key,
                "target_state": target_state,
                "receipt_ref": dict(receipt_ref),
                "updated_at": updated_at,
            }
            self._save_unlocked(data)
            return deepcopy(row), True

    def record_outcome(
        self,
        *,
        asset_id: str,
        version: int,
        usage_ref: str,
        matched: bool,
        outcome: str,
        cost: Mapping[str, Any],
        cohort: Mapping[str, Any] | None = None,
        evaluation: Mapping[str, Any] | None = None,
        recorded_at: str,
    ) -> tuple[dict[str, Any], bool]:
        if not str(usage_ref or "").strip():
            raise EvolutionStoreError("learning asset outcome requires usage_ref")
        if outcome not in {"passed", "failed", "regressed", "neutral"}:
            raise EvolutionStoreError(f"unsupported learning asset outcome: {outcome}")
        key = _asset_key(asset_id, version)
        outcome_id = stable_digest({"asset_key": key, "usage_ref": usage_ref})
        with locked_path(self.path):
            data = self._load_unlocked()
            row = data["assets"].get(key)
            if not isinstance(row, dict):
                raise EvolutionStoreError(f"learning asset not found: {key}")
            if row.get("state") not in {"canary_active", "active_retained"}:
                raise EvolutionConflictError(
                    "learning asset outcome requires canary or retained activation"
                )
            if any(item.get("outcome_id") == outcome_id for item in row["outcomes"]):
                return deepcopy(row), False
            normalized_cohort = _normalized_cohort(cohort or {})
            normalized_evaluation = _normalized_outcome_evaluation(
                evaluation or {}, matched=bool(matched)
            )
            row["outcomes"].append({
                "outcome_id": outcome_id,
                "usage_ref": usage_ref,
                "matched": bool(matched),
                "outcome": outcome,
                "negative_transfer": bool(matched and outcome in {"failed", "regressed"}),
                "cost": deepcopy(dict(cost)),
                "cohort": normalized_cohort,
                "evaluation": normalized_evaluation,
                "recorded_at": recorded_at,
            })
            row["revision"] = int(row["revision"]) + 1
            row["updated_at"] = recorded_at
            self._save_unlocked(data)
            return deepcopy(row), True

    def record_target_validation(
        self,
        *,
        asset_id: str,
        version: int,
        expected_revision: int,
        action_id: str,
        passed: bool,
        receipt_ref: Mapping[str, Any],
        updated_at: str,
    ) -> tuple[dict[str, Any], bool]:
        key = _asset_key(asset_id, version)
        target_state = "target_validation_passed" if passed else "target_validation_failed"
        with locked_path(self.path):
            data = self._load_unlocked()
            prior_action = data["actions"].get(action_id)
            if isinstance(prior_action, dict):
                if (
                    prior_action.get("asset_key") != key
                    or prior_action.get("target_state") != target_state
                ):
                    raise EvolutionConflictError(
                        f"asset action id conflict: {action_id}"
                    )
                return deepcopy(data["assets"][key]), False
            row = data["assets"].get(key)
            if not isinstance(row, dict):
                raise EvolutionStoreError(f"learning asset not found: {key}")
            if int(row.get("revision") or 0) != int(expected_revision):
                raise EvolutionConflictError(
                    f"stale learning asset revision: expected {expected_revision}, "
                    f"current {row.get('revision')}"
                )
            provenance = (
                row.get("provenance")
                if isinstance(row.get("provenance"), Mapping)
                else {}
            )
            if not bool(provenance.get("imported")):
                raise EvolutionConflictError(
                    "target validation applies only to imported learning assets"
                )
            row["provenance"] = {
                **dict(provenance),
                "target_validation": "passed" if passed else "failed",
                "target_validation_receipt_ref": dict(receipt_ref),
            }
            row["revision"] = int(row["revision"]) + 1
            row["updated_at"] = updated_at
            data["actions"][action_id] = {
                "asset_key": key,
                "target_state": target_state,
                "receipt_ref": dict(receipt_ref),
                "updated_at": updated_at,
            }
            self._save_unlocked(data)
            return deepcopy(row), True

    def applicable_assets(self, context: Mapping[str, Any], *, now: str) -> list[dict[str, Any]]:
        return self.resolve_assets(context, now=now)["selected"]

    def resolve_assets(self, context: Mapping[str, Any], *, now: str) -> dict[str, Any]:
        data = self.load()
        selected: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        for row in data["assets"].values():
            reason = _applicability_reason(row, context, now=now)
            if not reason:
                selected.append(deepcopy(row))
            else:
                excluded.append({
                    "asset_id": row.get("asset_id"),
                    "version": row.get("version"),
                    "state": row.get("state"),
                    "reason": reason,
                })
        return {"selected": selected, "excluded": excluded}

    def _load_unlocked(self) -> dict[str, Any]:
        raw = _read_json(self.path)
        if raw and raw.get("schema_version") != CAPABILITY_REGISTRY_SCHEMA:
            raise EvolutionStoreError("unsupported capability registry schema")
        return {
            "schema_version": CAPABILITY_REGISTRY_SCHEMA,
            "revision": int(raw.get("revision") or 0),
            "assets": _object(raw.get("assets"), "assets"),
            "active_versions": _object(raw.get("active_versions"), "active_versions"),
            "canary_versions": _object(raw.get("canary_versions"), "canary_versions"),
            "rejected_fingerprints": _object(
                raw.get("rejected_fingerprints"), "rejected_fingerprints"
            ),
            "actions": _object(raw.get("actions"), "actions"),
        }

    def _save_unlocked(self, data: Mapping[str, Any]) -> None:
        body = {
            **dict(data),
            "schema_version": CAPABILITY_REGISTRY_SCHEMA,
            "revision": int(data.get("revision") or 0) + 1,
        }
        atomic_write_text(self.path, _json_text(body))


def _assert_activation_eligible(
    data: Mapping[str, Any],
    row: Mapping[str, Any],
    *,
    target_state: str,
) -> None:
    taint = row.get("taint") if isinstance(row.get("taint"), Mapping) else {}
    if any(
        bool(taint.get(flag))
        for flag in ("blocked", "secret", "pii", "license_unknown")
    ):
        raise EvolutionConflictError("tainted learning asset cannot be activated")
    provenance = row.get("provenance") if isinstance(row.get("provenance"), Mapping) else {}
    if bool(provenance.get("imported")) and provenance.get("target_validation") != "passed":
        raise EvolutionConflictError("imported learning asset requires target validation")
    if target_state == "canary_active":
        activation = row.get("activation") if isinstance(row.get("activation"), Mapping) else {}
        if not activation.get("canary_scope_ref"):
            raise EvolutionConflictError("canary activation requires canary_scope_ref")
    if target_state == "active_retained":
        activation = row.get("activation") if isinstance(row.get("activation"), Mapping) else {}
        retain_policy = (
            activation.get("retain_policy")
            if isinstance(activation.get("retain_policy"), Mapping)
            else {}
        )
        minimum = int(retain_policy.get("min_matched_outcomes") or 1)
        maximum_negative = int(retain_policy.get("max_negative_transfer") or 0)
        matched = [
            item for item in row.get("outcomes") or [] if bool(item.get("matched"))
        ]
        negative = [item for item in matched if bool(item.get("negative_transfer"))]
        if len(matched) < minimum:
            raise EvolutionConflictError(
                "canary retain policy lacks matched longitudinal outcomes"
            )
        if len(negative) > maximum_negative:
            raise EvolutionConflictError(
                "canary retain policy exceeded negative-transfer allowance"
            )
    for dependency in row.get("dependencies") or []:
        if not isinstance(dependency, Mapping):
            raise EvolutionConflictError("learning asset dependency is malformed")
        dependency_key = _asset_key(
            str(dependency.get("asset_id") or ""),
            int(dependency.get("version") or 0),
        )
        dependency_row = data["assets"].get(dependency_key)
        if not isinstance(dependency_row, dict) or dependency_row.get("state") != "active_retained":
            raise EvolutionConflictError(f"learning asset dependency is inactive: {dependency_key}")


def _applicability_reason(row: Mapping[str, Any], context: Mapping[str, Any], *, now: str) -> str:
    if row.get("state") not in {"canary_active", "active_retained"}:
        return "inactive"
    quality = row.get("quality") if isinstance(row.get("quality"), Mapping) else {}
    if quality.get("contradiction_refs"):
        return "contradicted"
    expires_at = str(quality.get("expires_at") or "")
    if expires_at and _is_expired(expires_at, now):
        return "expired"
    if row.get("state") == "canary_active":
        required_scope = str(
            (row.get("activation") or {}).get("canary_scope_ref") or ""
        )
        supplied_scopes = {
            str(value) for value in context.get("canary_scope_refs") or []
        }
        if required_scope and required_scope not in supplied_scopes:
            return "outside_canary_scope"
    taint = row.get("taint") if isinstance(row.get("taint"), Mapping) else {}
    if any(
        bool(taint.get(flag))
        for flag in ("blocked", "secret", "pii", "license_unknown")
    ):
        return "tainted"
    provenance = (
        row.get("provenance")
        if isinstance(row.get("provenance"), Mapping)
        else {}
    )
    if bool(provenance.get("imported")) and provenance.get("target_validation") != "passed":
        return "target_validation_pending"
    applicability = row.get("applicability") if isinstance(row.get("applicability"), Mapping) else {}
    for field in ("task_families", "providers", "models", "languages", "repositories"):
        allowed = applicability.get(field)
        if not isinstance(allowed, list) or not allowed:
            continue
        context_key = {
            "task_families": "task_family",
            "providers": "provider",
            "models": "model",
            "languages": "language",
            "repositories": "repository",
        }[field]
        if str(context.get(context_key) or "") not in {str(value) for value in allowed}:
            return f"outside_{field}"
    return ""


def _asset_key(asset_id: str, version: int) -> str:
    if not asset_id or int(version) < 1:
        raise EvolutionStoreError("invalid learning asset identity")
    return f"{asset_id}@{int(version)}"


def _normalized_cohort(raw: Mapping[str, Any]) -> dict[str, str]:
    allowed = ("task_family", "repository", "provider", "model", "language")
    return {
        key: str(raw.get(key) or "").strip()
        for key in allowed
        if str(raw.get(key) or "").strip()
    }


def _normalized_outcome_evaluation(
    raw: Mapping[str, Any],
    *,
    matched: bool,
) -> dict[str, Any]:
    baseline_ref = str(raw.get("baseline_ref") or "").strip()
    candidate_ref = str(raw.get("candidate_ref") or "").strip()
    if bool(baseline_ref) != bool(candidate_ref):
        raise EvolutionStoreError(
            "learning outcome evaluation requires both baseline_ref and candidate_ref"
        )
    holdout_matched = bool(raw.get("holdout_matched"))
    result: dict[str, Any] = {
        "baseline_ref": baseline_ref,
        "candidate_ref": candidate_ref,
        "holdout_matched": holdout_matched,
        "reuse_gain": None,
    }
    if "baseline_score" not in raw and "candidate_score" not in raw:
        return result
    if "baseline_score" not in raw or "candidate_score" not in raw:
        raise EvolutionStoreError(
            "learning outcome evaluation requires both baseline_score and candidate_score"
        )
    try:
        baseline_score = float(raw["baseline_score"])
        candidate_score = float(raw["candidate_score"])
    except (TypeError, ValueError) as exc:
        raise EvolutionStoreError("learning outcome scores must be numeric") from exc
    if not math.isfinite(baseline_score) or not math.isfinite(candidate_score):
        raise EvolutionStoreError("learning outcome scores must be finite")
    result.update({
        "baseline_score": baseline_score,
        "candidate_score": candidate_score,
    })
    if matched and holdout_matched:
        result["reuse_gain"] = candidate_score - baseline_score
    return result


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise EvolutionStoreError(f"invalid evolution store: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvolutionStoreError(f"evolution store must be an object: {path}")
    return value


def _object(value: object, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise EvolutionStoreError(f"evolution store {field} must be an object")
    return deepcopy(value)


def _json_text(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _is_expired(expires_at: str, now: str) -> bool:
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        current = datetime.fromisoformat(now.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return expiry <= current


__all__ = [
    "ASSET_STATES",
    "CAPABILITY_REGISTRY_SCHEMA",
    "CapabilityRegistry",
    "EvolutionConflictError",
    "EvolutionStoreError",
    "EvolutionTrialStore",
    "TRIAL_STATUSES",
]
