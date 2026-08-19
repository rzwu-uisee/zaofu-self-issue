"""Typed contracts for trustworthy harness-level evolution.

The contracts in this module are intentionally mechanical. Project-specific
quality criteria still come from run artifacts; this layer only freezes the
identity, evaluator, evidence, and proposal boundaries needed for replay.
"""

from __future__ import annotations

import math
import re
from copy import deepcopy
from typing import Any, Mapping

from zf.runtime.call_result_envelope import canonical_json_sha256


EVOLUTION_ATTEMPT_SCHEMA = "evolution-attempt.v1"
EVOLUTION_TRIAL_SCHEMA = "evolution-trial.v1"
EVOLUTION_COMPARISON_SCHEMA = "evolution-comparison.v1"
EVALUATOR_GENERATION_SCHEMA = "evaluator-generation.v1"
LEARNING_ASSET_SCHEMA = "learning-asset.v1"
DEFAULT_COMPARISON_IDENTITY_FIELDS = (
    "scenario_set_digest",
    "config_generation",
    "provider_capability_digest",
    "toolchain_digest",
    "environment_digest",
    "sandbox_policy_digest",
    "network_policy_digest",
    "credential_policy_digest",
    "budget_digest",
    "seed_policy_digest",
    "task_family",
)

EVOLUTION_TIMES = frozenset({"task_time", "post_task", "stage_wise"})
PERSISTENCE_SCOPES = frozenset({"run", "project", "workspace", "portable"})
ADOPTION_CLAIMS = frozenset({"experiment_only", "canary", "persistent_capability"})
MUTATION_OBJECT_KINDS = frozenset({
    "framework_code",
    "memory_entry",
    "skill_prompt",
    "workflow_config",
    "provider_route",
    "tool_capability",
    "evaluator_challenge",
})
IDENTITY_KINDS = frozenset({
    "git_commit",
    "artifact_digest",
    "config_generation",
    "route_policy_digest",
})
PASSING_GATE_STATES = frozenset({"passed", "skipped", "not_applicable"})
FAILING_GATE_STATES = frozenset({"failed", "blocked", "rejected", "error"})

_DIGEST_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_REF_DIGEST_PAIRS = (
    ("briefing_ref", "briefing_digest"),
    ("context_read_set_ref", "context_read_set_digest"),
    ("skill_lock_ref", "skill_lock_digest"),
    ("memory_snapshot_ref", "memory_snapshot_digest"),
    ("tool_policy_ref", "tool_policy_digest"),
)
_FROZEN_REF_DIGEST_PAIRS = (
    ("config_ref", "config_digest"),
    ("evaluator_ref", "evaluator_digest"),
    ("scenario_set_ref", "scenario_set_digest"),
    ("provider_capability_ref", "provider_capability_digest"),
    ("toolchain_ref", "toolchain_digest"),
    ("environment_ref", "environment_digest"),
    ("sandbox_policy_ref", "sandbox_policy_digest"),
    ("network_policy_ref", "network_policy_digest"),
    ("credential_policy_ref", "credential_policy_digest"),
    ("run_archive_manifest_ref", "run_archive_manifest_digest"),
)


class EvolutionContractError(ValueError):
    """One evolution artifact is incomplete, malformed, or not comparable."""


def normalize_digest(value: object, *, field: str) -> str:
    text = str(value or "").strip().lower()
    if not _DIGEST_RE.fullmatch(text):
        raise EvolutionContractError(f"{field} must be a sha256 digest")
    return text.removeprefix("sha256:")


def stable_digest(value: object) -> str:
    return canonical_json_sha256(value)


def validate_evolution_attempt(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize one immutable evolution-attempt manifest."""

    body = deepcopy(dict(raw))
    _expect_schema(body, EVOLUTION_ATTEMPT_SCHEMA)
    _required_text(body, "attempt_id")
    _required_text(body, "campaign_id")
    _enum(body, "evolution_time", EVOLUTION_TIMES)
    _enum(body, "persistence_scope", PERSISTENCE_SCOPES)
    _enum(body, "adoption_claim", ADOPTION_CLAIMS)

    evidence_kinds = _unique_strings(body.get("evidence_kinds"), "evidence_kinds")
    if not {"outcome", "environmental", "trajectory"}.issubset(evidence_kinds):
        raise EvolutionContractError(
            "evidence_kinds must include outcome, environmental, and trajectory"
        )
    body["evidence_kinds"] = evidence_kinds

    objective = _mapping(body, "objective")
    _required_text(objective, "kind", prefix="objective")
    _required_text(objective, "summary", prefix="objective")
    _required_text(objective, "task_family", prefix="objective")

    mutation = _mapping(body, "mutation")
    object_kind = _enum(mutation, "object_kind", MUTATION_OBJECT_KINDS, prefix="mutation")
    identity_kind = _enum(mutation, "identity_kind", IDENTITY_KINDS, prefix="mutation")
    _required_text(mutation, "object_ref", prefix="mutation")
    _required_text(mutation, "base_version", prefix="mutation")
    _required_text(mutation, "candidate_version", prefix="mutation")
    _ref_digest_pair(mutation, "diff_ref", "diff_digest", prefix="mutation")
    mutation["tcb_affected"] = bool(
        mutation.get("tcb_affected") or object_kind == "evaluator_challenge"
    )
    if mutation["tcb_affected"]:
        _ref_digest_pair(
            mutation,
            "proposed_evaluator_generation_ref",
            "proposed_evaluator_generation_digest",
            prefix="mutation",
        )
        if body["adoption_claim"] != "experiment_only":
            raise EvolutionContractError(
                "TCB/evaluator mutations must remain experiment_only until an "
                "independent evaluator generation N+1 approves them"
            )

    source = _mapping(body, "source_identity")
    _required_text(source, "workflow_run_id", prefix="source_identity")
    source["source_task_ids"] = _unique_strings(
        source.get("source_task_ids"), "source_identity.source_task_ids"
    )
    if not source["source_task_ids"]:
        raise EvolutionContractError("source_identity.source_task_ids is required")
    if object_kind == "framework_code" or identity_kind == "git_commit":
        for key in (
            "base_commit",
            "candidate_commit",
            "candidate_ref",
            "candidate_verification_authority_ref",
        ):
            _required_text(source, key, prefix="source_identity")
    elif identity_kind == "artifact_digest":
        normalize_digest(mutation["base_version"], field="mutation.base_version")
        normalize_digest(mutation["candidate_version"], field="mutation.candidate_version")
    for ref_key, digest_key in _REF_DIGEST_PAIRS:
        _ref_digest_pair(source, ref_key, digest_key, prefix="source_identity")

    frozen = _mapping(body, "frozen_inputs")
    for key in (
        "workflow_generation",
        "provider",
        "model",
        "holdout_authority_ref",
    ):
        _required_text(frozen, key, prefix="frozen_inputs")
    for key in (
        "evaluation_harness_digest",
        "comparison_parser_digest",
        "holdout_generation_digest",
        "credential_policy_digest",
    ):
        frozen[key] = normalize_digest(frozen.get(key), field=f"frozen_inputs.{key}")
    for ref_key, digest_key in _FROZEN_REF_DIGEST_PAIRS:
        _ref_digest_pair(frozen, ref_key, digest_key, prefix="frozen_inputs")

    evaluation = _mapping(body, "evaluation_policy")
    evaluation["pairing_key"] = normalize_digest(
        evaluation.get("pairing_key"), field="evaluation_policy.pairing_key"
    )
    evaluation["required_gates"] = _unique_strings(
        evaluation.get("required_gates"), "evaluation_policy.required_gates"
    )
    evaluation["required_score_dimensions"] = _unique_strings(
        evaluation.get("required_score_dimensions"),
        "evaluation_policy.required_score_dimensions",
    )
    if not evaluation["required_gates"] or not evaluation["required_score_dimensions"]:
        raise EvolutionContractError("evaluation policy requires gates and score dimensions")
    evaluation["score_weights_digest"] = normalize_digest(
        evaluation.get("score_weights_digest"),
        field="evaluation_policy.score_weights_digest",
    )
    if str(evaluation.get("numeric_policy") or "") != "finite_bounded":
        raise EvolutionContractError("evaluation_policy.numeric_policy must be finite_bounded")
    _positive_int(evaluation, "min_trials", prefix="evaluation_policy")
    _finite_number(evaluation, "min_delta", prefix="evaluation_policy", minimum=0.0)

    execution = _mapping(body, "execution_policy")
    execution["attempt_idempotency_key"] = normalize_digest(
        execution.get("attempt_idempotency_key"),
        field="execution_policy.attempt_idempotency_key",
    )
    _positive_int(execution, "lease_seconds", prefix="execution_policy")
    _positive_int(execution, "max_trial_attempts", prefix="execution_policy")
    if str(execution.get("retry_policy") or "") != "infrastructure_only":
        raise EvolutionContractError(
            "execution_policy.retry_policy must be infrastructure_only"
        )

    budget = _mapping(body, "budget")
    for key in ("max_cost_usd", "max_wall_seconds", "max_tokens"):
        _finite_number(budget, key, prefix="budget", minimum=0.0)

    policy = _mapping(body, "policy")
    if str(policy.get("apply_mode") or "") != "proposal_only":
        raise EvolutionContractError("evolution attempts must be proposal_only")
    if not bool(policy.get("owner_approval_required")):
        raise EvolutionContractError("owner_approval_required must be true")
    if not bool(policy.get("canary_required")):
        raise EvolutionContractError("canary_required must be true")
    return body


def validate_evaluator_generation(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Freeze exact evaluator membership, ranges, parser, and TCB identity."""

    body = deepcopy(dict(raw))
    _expect_schema(body, EVALUATOR_GENERATION_SCHEMA)
    _required_text(body, "generation_id")
    for key in (
        "parser_digest",
        "tcb_digest",
        "scenario_set_digest",
        "holdout_generation_digest",
    ):
        body[key] = normalize_digest(body.get(key), field=key)
    authority = _required_text(body, "holdout_authority_ref")
    if not authority.startswith("sealed-evaluator://generation/"):
        raise EvolutionContractError(
            "holdout_authority_ref must be an opaque sealed-evaluator handle"
        )

    gates = body.get("required_gates")
    if not isinstance(gates, list) or not gates:
        raise EvolutionContractError("required_gates must be a non-empty list")
    normalized_gates: list[dict[str, Any]] = []
    gate_ids: set[str] = set()
    for item in gates:
        if not isinstance(item, Mapping):
            raise EvolutionContractError("required_gates entries must be objects")
        gate_id = str(item.get("id") or "").strip()
        if not gate_id or gate_id in gate_ids:
            raise EvolutionContractError("required_gates ids must be non-empty and unique")
        gate_ids.add(gate_id)
        normalized_gates.append({"id": gate_id, "blocking": bool(item.get("blocking", True))})
    body["required_gates"] = normalized_gates

    dimensions = body.get("required_score_dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        raise EvolutionContractError(
            "required_score_dimensions must be a non-empty list"
        )
    normalized_dimensions: list[dict[str, Any]] = []
    dimension_ids: set[str] = set()
    for item in dimensions:
        if not isinstance(item, Mapping):
            raise EvolutionContractError(
                "required_score_dimensions entries must be objects"
            )
        dimension_id = str(item.get("id") or "").strip()
        if not dimension_id or dimension_id in dimension_ids:
            raise EvolutionContractError(
                "required_score_dimensions ids must be non-empty and unique"
            )
        dimension_ids.add(dimension_id)
        weight = _finite_value(item.get("weight"), f"dimension {dimension_id} weight")
        lower = _finite_value(item.get("min"), f"dimension {dimension_id} min")
        upper = _finite_value(item.get("max"), f"dimension {dimension_id} max")
        if weight <= 0 or lower >= upper:
            raise EvolutionContractError(
                f"dimension {dimension_id} requires weight > 0 and min < max"
            )
        normalized_dimensions.append({
            "id": dimension_id,
            "weight": weight,
            "min": lower,
            "max": upper,
            "higher_is_better": bool(item.get("higher_is_better", True)),
            "blocking_regression": bool(item.get("blocking_regression", False)),
        })
    body["required_score_dimensions"] = normalized_dimensions
    identity_fields = body.get("comparison_identity_fields")
    if identity_fields is None:
        identity_fields = list(DEFAULT_COMPARISON_IDENTITY_FIELDS)
    body["comparison_identity_fields"] = _unique_strings(
        identity_fields,
        "comparison_identity_fields",
    )
    min_trials = body.get("min_trials", 1)
    try:
        min_trials = int(min_trials)
    except (TypeError, ValueError) as exc:
        raise EvolutionContractError("min_trials must be an integer") from exc
    if min_trials < 1:
        raise EvolutionContractError("min_trials must be positive")
    body["min_trials"] = min_trials
    body["min_delta"] = _finite_value(body.get("min_delta", 0), "min_delta")
    if body["min_delta"] < 0:
        raise EvolutionContractError("min_delta must be >= 0")
    body["max_spread"] = _finite_value(
        body.get("max_spread", 100), "max_spread"
    )
    if body["max_spread"] < 0:
        raise EvolutionContractError("max_spread must be >= 0")
    body["weights_digest"] = stable_digest({
        item["id"]: item["weight"] for item in normalized_dimensions
    })
    body["generation_digest"] = stable_digest({
        key: value for key, value in body.items() if key != "generation_digest"
    })
    return body


def evaluator_public_projection(generation: Mapping[str, Any]) -> dict[str, Any]:
    """Return evaluator metadata safe for candidates and Web projections."""

    normalized = validate_evaluator_generation(generation)
    allowed = {
        "schema_version",
        "generation_id",
        "generation_digest",
        "required_gates",
        "required_score_dimensions",
        "comparison_identity_fields",
        "min_trials",
        "min_delta",
        "max_spread",
        "weights_digest",
        "parser_digest",
        "tcb_digest",
        "scenario_set_digest",
        "holdout_generation_digest",
        "holdout_authority_ref",
    }
    return {key: deepcopy(value) for key, value in normalized.items() if key in allowed}


def _expect_schema(body: Mapping[str, Any], expected: str) -> None:
    if str(body.get("schema_version") or "") != expected:
        raise EvolutionContractError(f"schema_version must be {expected}")


def _mapping(body: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = body.get(key)
    if not isinstance(value, Mapping):
        raise EvolutionContractError(f"{key} must be an object")
    return value if isinstance(value, dict) else dict(value)


def _required_text(body: Mapping[str, Any], key: str, *, prefix: str = "") -> str:
    value = str(body.get(key) or "").strip()
    if not value:
        label = f"{prefix}.{key}" if prefix else key
        raise EvolutionContractError(f"{label} is required")
    if isinstance(body, dict):
        body[key] = value
    return value


def _enum(
    body: Mapping[str, Any],
    key: str,
    allowed: frozenset[str],
    *,
    prefix: str = "",
) -> str:
    value = _required_text(body, key, prefix=prefix)
    if value not in allowed:
        label = f"{prefix}.{key}" if prefix else key
        raise EvolutionContractError(f"{label} has unsupported value {value!r}")
    return value


def _unique_strings(value: object, field: str) -> list[str]:
    if not isinstance(value, list):
        raise EvolutionContractError(f"{field} must be a list")
    out = [str(item or "").strip() for item in value]
    if any(not item for item in out) or len(set(out)) != len(out):
        raise EvolutionContractError(f"{field} must contain unique non-empty strings")
    return out


def _ref_digest_pair(
    body: Mapping[str, Any],
    ref_key: str,
    digest_key: str,
    *,
    prefix: str,
) -> None:
    _required_text(body, ref_key, prefix=prefix)
    normalized = normalize_digest(
        body.get(digest_key), field=f"{prefix}.{digest_key}"
    )
    if isinstance(body, dict):
        body[digest_key] = normalized


def _positive_int(body: Mapping[str, Any], key: str, *, prefix: str) -> int:
    try:
        value = int(body.get(key))
    except (TypeError, ValueError) as exc:
        raise EvolutionContractError(f"{prefix}.{key} must be an integer") from exc
    if value <= 0:
        raise EvolutionContractError(f"{prefix}.{key} must be positive")
    if isinstance(body, dict):
        body[key] = value
    return value


def _finite_value(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise EvolutionContractError(f"{field} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise EvolutionContractError(f"{field} must be a finite number") from exc
    if not math.isfinite(number):
        raise EvolutionContractError(f"{field} must be a finite number")
    return number


def _finite_number(
    body: Mapping[str, Any],
    key: str,
    *,
    prefix: str,
    minimum: float,
) -> float:
    value = _finite_value(body.get(key), f"{prefix}.{key}")
    if value < minimum:
        raise EvolutionContractError(f"{prefix}.{key} must be >= {minimum}")
    if isinstance(body, dict):
        body[key] = value
    return value


__all__ = [
    "ADOPTION_CLAIMS",
    "EVALUATOR_GENERATION_SCHEMA",
    "EVOLUTION_ATTEMPT_SCHEMA",
    "EVOLUTION_COMPARISON_SCHEMA",
    "EVOLUTION_TRIAL_SCHEMA",
    "EvolutionContractError",
    "FAILING_GATE_STATES",
    "LEARNING_ASSET_SCHEMA",
    "PASSING_GATE_STATES",
    "evaluator_public_projection",
    "normalize_digest",
    "stable_digest",
    "validate_evaluator_generation",
    "validate_evolution_attempt",
]
