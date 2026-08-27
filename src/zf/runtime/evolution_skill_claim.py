"""Paired lift and fail-closed adoption claims for Skill evolution."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from zf.runtime.evolution_contracts import EvolutionContractError
from zf.runtime.evolution_skill_eval import compare_skill_treatment_identities


def paired_skill_lifts(
    *,
    baseline: Sequence[Mapping[str, Any]],
    candidate: Sequence[Mapping[str, Any]],
    control: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    try:
        arms = {
            "raw": _case_score_index(control if control else baseline),
            "candidate": _case_score_index(candidate),
        }
        if control:
            arms["current"] = _case_score_index(baseline)
    except EvolutionContractError as exc:
        return {
            "pairing_key": "case_id+replicate",
            "status": "invalid",
            "reason": str(exc),
            "comparisons": {},
        }
    comparisons: dict[str, Any] = {}
    if control:
        comparisons["current_vs_raw"] = _paired_delta(arms["current"], arms["raw"])
        comparisons["candidate_vs_current"] = _paired_delta(
            arms["candidate"], arms["current"]
        )
    comparisons["candidate_vs_raw"] = _paired_delta(
        arms["candidate"], arms["raw"]
    )
    return {
        "pairing_key": "case_id+replicate",
        "status": (
            "available"
            if any(row["matched_pair_count"] for row in comparisons.values())
            else "unavailable"
        ),
        "comparisons": comparisons,
    }


def skill_comparison_claim(
    rows: Sequence[Mapping[str, Any]],
    *,
    status: str,
    attempt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a fail-closed claim scope for generic or Skill comparisons."""

    attempt_body = dict(attempt or {})
    object_kind = str((attempt_body.get("mutation") or {}).get("object_kind") or "")
    treatments = [
        row.get("treatment")
        for row in rows
        if isinstance(row.get("treatment"), Mapping)
    ]
    is_skill = object_kind == "skill_prompt" or bool(treatments)
    if not is_skill:
        reasons = [] if status == "candidate_better" else [f"comparison_{status}"]
        return {
            "object_kind": object_kind or "generic",
            "evaluation_purpose": "legacy_outcome",
            "claim_scope": "product_outcome",
            "adoption_eligible": status == "candidate_better",
            "blocking_reasons": reasons,
        }
    reasons: list[str] = []
    if status != "candidate_better":
        reasons.append(f"comparison_{status}")
    if object_kind != "skill_prompt":
        reasons.append("skill_mutation_identity_missing")
    if len(treatments) != len(rows) or not treatments:
        reasons.append("legacy_skill_measurement")
        purposes: set[str] = set()
        identities: list[Mapping[str, Any]] = []
    else:
        purposes = {str(item.get("evaluation_purpose") or "") for item in treatments}
        identities = [
            item["identity"]
            for item in treatments
            if isinstance(item.get("identity"), Mapping)
        ]
        if len(identities) != len(treatments):
            reasons.append("skill_treatment_identity_missing")
        else:
            identity_comparison = compare_skill_treatment_identities(identities)
            if not identity_comparison["comparable"]:
                reasons.append("skill_treatment_incomparable")
    purpose = next(iter(purposes)) if len(purposes) == 1 else "mixed"
    if purpose != "adoption_lift":
        reasons.append("non_adoption_evaluation_purpose")
    policy = (attempt_body.get("evaluation_policy") or {}).get("skill_adoption")
    if not isinstance(policy, Mapping):
        reasons.append("skill_adoption_policy_missing")
        policy = {}
    if str(attempt_body.get("adoption_claim") or "") != "persistent_capability":
        reasons.append("persistent_adoption_claim_missing")
    if str(policy.get("routing_stress_status") or "") != "passed":
        reasons.append("routing_stress_not_passed")
    if not str(policy.get("routing_stress_ref") or "").strip() or not str(
        policy.get("routing_stress_digest") or ""
    ).strip():
        reasons.append("routing_stress_evidence_missing")
    min_cases = max(3, _positive_int(policy.get("min_distinct_cases"), default=0))
    min_replicates = max(
        2, _positive_int(policy.get("min_replicates_per_case"), default=0)
    )
    required_arms = {
        str(item) for item in policy.get("required_arms") or [] if str(item)
    }
    if required_arms not in ({"raw", "candidate"}, {"raw", "current", "candidate"}):
        reasons.append("skill_required_arms_invalid")
    coverage = _case_replicate_coverage(rows)
    for key in _duplicate_case_replicate_keys(rows):
        reasons.append(f"duplicate_case_replicate:{key}")
    candidate_cases = set(coverage.get("candidate", {}))
    if len(candidate_cases) < min_cases:
        reasons.append("insufficient_distinct_cases")
    for arm in sorted(required_arms):
        arm_cases = coverage.get(arm, {})
        if set(arm_cases) != candidate_cases:
            reasons.append(f"unmatched_case_set:{arm}")
            continue
        for case_id in sorted(candidate_cases):
            replicates = arm_cases[case_id]
            if replicates != coverage["candidate"][case_id]:
                reasons.append(f"unmatched_pair_set:{arm}:{case_id}")
            if len(replicates) < min_replicates:
                reasons.append(f"insufficient_replicates:{arm}")
    if any(int(item.get("overtrigger_count") or 0) > 0 for item in treatments):
        reasons.append("routing_overtrigger_observed")
    reasons = list(dict.fromkeys(reasons))
    scopes = {
        "treatment_smoke": "mechanism_only",
        "content_lift": "forced_content_lift",
        "natural_lift": "natural_routing_lift",
        "routing_stress": "routing_stress",
        "adoption_lift": "product_adoption_lift",
    }
    return {
        "object_kind": "skill_prompt",
        "evaluation_purpose": purpose,
        "claim_scope": scopes.get(purpose, "incomparable_claim_scope"),
        "adoption_eligible": status == "candidate_better" and not reasons,
        "blocking_reasons": reasons,
        "evidence_summary": {
            "distinct_candidate_cases": len(candidate_cases),
            "required_min_cases": min_cases,
            "required_min_replicates_per_case": min_replicates,
            "coverage": {
                arm: {case_id: sorted(values) for case_id, values in cases.items()}
                for arm, cases in coverage.items()
            },
        },
    }


def _case_score_index(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    index: dict[str, float] = {}
    for position, row in enumerate(rows, start=1):
        pairing = row.get("pairing") if isinstance(row.get("pairing"), Mapping) else {}
        replicate = int(pairing.get("replicate") or position)
        for case in row.get("case_results") or []:
            if not isinstance(case, Mapping):
                continue
            key = f"{str(case.get('case_id') or '')}::{replicate}"
            if key in index:
                raise EvolutionContractError(f"duplicate paired Skill observation {key}")
            index[key] = float(case.get("score") or 0.0)
    return index


def _paired_delta(left: Mapping[str, float], right: Mapping[str, float]) -> dict[str, Any]:
    keys = sorted(set(left).intersection(right))
    deltas = [left[key] - right[key] for key in keys]
    return {
        "matched_pair_count": len(keys),
        "pair_keys": keys,
        "deltas": deltas,
        "median_delta": _median(deltas),
    }


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(float(item) for item in values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _case_replicate_coverage(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, set[int]]]:
    coverage: dict[str, dict[str, set[int]]] = {}
    for position, row in enumerate(rows, start=1):
        treatment = row.get("treatment")
        if not isinstance(treatment, Mapping):
            continue
        identity = treatment.get("identity")
        if not isinstance(identity, Mapping):
            continue
        arm = str(identity.get("arm") or "")
        pairing = row.get("pairing") if isinstance(row.get("pairing"), Mapping) else {}
        replicate = int(pairing.get("replicate") or position)
        for case in row.get("case_results") or []:
            if not isinstance(case, Mapping):
                continue
            case_id = str(case.get("case_id") or "")
            if case_id:
                coverage.setdefault(arm, {}).setdefault(case_id, set()).add(replicate)
    return coverage


def _duplicate_case_replicate_keys(
    rows: Sequence[Mapping[str, Any]],
) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for position, row in enumerate(rows, start=1):
        treatment = row.get("treatment")
        if not isinstance(treatment, Mapping):
            continue
        identity = treatment.get("identity")
        if not isinstance(identity, Mapping):
            continue
        arm = str(identity.get("arm") or "")
        pairing = row.get("pairing") if isinstance(row.get("pairing"), Mapping) else {}
        replicate = int(pairing.get("replicate") or position)
        for case in row.get("case_results") or []:
            if not isinstance(case, Mapping):
                continue
            case_id = str(case.get("case_id") or "")
            key = f"{arm}:{case_id}:{replicate}"
            if key in seen:
                duplicates.add(key)
            seen.add(key)
    return sorted(duplicates)


def _positive_int(value: object, *, default: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return result if result > 0 else default


__all__ = ["paired_skill_lifts", "skill_comparison_claim"]
