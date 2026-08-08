"""Typed result contract for optional Task Pipeline risk acceptance review."""

from __future__ import annotations

from typing import Any, Mapping


SCHEMA_VERSION = "task-integration-acceptance-result.v1"
PROFILE_ID = "integration-acceptance-review"
PROFILE_REVISION = "1"
COMPLETED_VERDICTS = frozenset({"admit", "revise", "replan", "block"})
EXECUTION_STATUSES = frozenset({"completed", "failed"})


class IntegrationAcceptanceResultError(ValueError):
    """A risk-review result cannot authorize an integration transition."""


def normalize_integration_acceptance_result(
    payload: Mapping[str, Any],
    *,
    require_read_ledger: bool = False,
) -> dict[str, Any]:
    raw = payload.get("integration_acceptance_result")
    result = dict(raw) if isinstance(raw, Mapping) else dict(payload)
    result.setdefault("schema_version", SCHEMA_VERSION)
    result.setdefault("execution_status", "completed")
    result.setdefault("verdict", "admit")
    result["finding_refs"] = _strings(result.get("finding_refs"))
    result["feedback_refs"] = _strings(result.get("feedback_refs"))
    result["evidence_refs"] = _strings(result.get("evidence_refs"))
    result["residual_risks"] = _list(result.get("residual_risks"))
    result["feedback"] = _objects(result.get("feedback"))
    blocker = result.get("blocker")
    result["blocker"] = dict(blocker) if isinstance(blocker, Mapping) else {}
    delta = result.get("delta_intent")
    result["delta_intent"] = dict(delta) if isinstance(delta, Mapping) else {}
    validate_integration_acceptance_result(
        result,
        require_read_ledger=require_read_ledger,
    )
    return result


def bind_required_read_ledger(
    result: Mapping[str, Any],
    descriptor: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind the Kernel-sealed Required Read ledger, ignoring worker claims."""

    ref = str(descriptor.get("ref") or "").strip()
    digest = str(descriptor.get("sha256") or "").strip()
    if not ref or not digest:
        raise IntegrationAcceptanceResultError(
            "risk acceptance result requires a sealed Required Read ledger"
        )
    bound = {
        **dict(result),
        "required_read_ledger_ref": ref,
        "required_read_ledger_digest": digest,
    }
    validate_integration_acceptance_result(bound, require_read_ledger=True)
    return bound


def validate_integration_acceptance_result(
    result: Mapping[str, Any],
    *,
    require_read_ledger: bool = False,
) -> None:
    if str(result.get("schema_version") or "") != SCHEMA_VERSION:
        raise IntegrationAcceptanceResultError(
            "unsupported integration acceptance result schema"
        )
    required = (
        "workflow_run_id",
        "task_id",
        "task_map_generation",
        "contract_revision",
        "risk_class",
        "integration_admission_profile",
        "operation_id",
        "operation_generation",
        "attempt_id",
        "exact_task_target_commit",
        "verification_result_ref",
        "verification_result_digest",
        "contract_snapshot_ref",
        "contract_snapshot_digest",
        "target_snapshot_ref",
        "target_snapshot_digest",
        "execution_profile_id",
        "execution_profile_digest",
        "risk_review_timeout_seconds",
        "risk_review_max_turns",
        "risk_review_budget_usd",
    )
    if require_read_ledger:
        required = (
            *required,
            "required_read_ledger_ref",
            "required_read_ledger_digest",
        )
    missing = [
        key for key in required
        if not str(result.get(key) or "").strip()
    ]
    if missing:
        raise IntegrationAcceptanceResultError(
            "integration acceptance result missing: " + ", ".join(missing)
        )
    if str(result.get("integration_admission_profile") or "") != "risk_review":
        raise IntegrationAcceptanceResultError(
            "integration acceptance result requires risk_review profile"
        )
    if str(result.get("risk_class") or "") not in {"high", "critical"}:
        raise IntegrationAcceptanceResultError(
            "integration acceptance result requires high or critical risk"
        )
    if str(result.get("exact_task_target_commit") or "") != str(
        result.get("target_commit") or ""
    ):
        raise IntegrationAcceptanceResultError(
            "exact task target commit does not match target_commit"
        )
    execution_status = str(result.get("execution_status") or "")
    verdict = str(result.get("verdict") or "")
    if execution_status not in EXECUTION_STATUSES:
        raise IntegrationAcceptanceResultError(
            f"invalid execution_status {execution_status!r}"
        )
    if execution_status == "failed":
        if verdict != "abstained":
            raise IntegrationAcceptanceResultError(
                "failed risk reviewer execution must abstain"
            )
        return
    if verdict not in COMPLETED_VERDICTS:
        raise IntegrationAcceptanceResultError(
            f"invalid integration acceptance verdict {verdict!r}"
        )
    if not _strings(result.get("evidence_refs")):
        raise IntegrationAcceptanceResultError(
            "completed integration acceptance result requires evidence_refs"
        )
    if verdict == "revise" and not _objects(result.get("feedback")):
        raise IntegrationAcceptanceResultError(
            "revise verdict requires task-local typed feedback"
        )
    if verdict == "replan" and not isinstance(
        result.get("delta_intent"), Mapping
    ):
        raise IntegrationAcceptanceResultError(
            "replan verdict requires a typed delta_intent"
        )
    if verdict == "replan" and not dict(result.get("delta_intent") or {}):
        raise IntegrationAcceptanceResultError(
            "replan verdict requires a non-empty delta_intent"
        )
    if verdict == "block" and not dict(result.get("blocker") or {}):
        raise IntegrationAcceptanceResultError(
            "block verdict requires a typed blocker"
        )


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(
        str(item).strip() for item in value if str(item).strip()
    ))


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _objects(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


__all__ = [
    "COMPLETED_VERDICTS",
    "IntegrationAcceptanceResultError",
    "PROFILE_ID",
    "PROFILE_REVISION",
    "SCHEMA_VERSION",
    "bind_required_read_ledger",
    "normalize_integration_acceptance_result",
    "validate_integration_acceptance_result",
]
