"""Sealed evaluator metadata and strict repeated A/B comparison."""

from __future__ import annotations

import hmac
import json
import math
import os
import statistics
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.evolution_contracts import (
    EVOLUTION_COMPARISON_SCHEMA,
    EvolutionContractError,
    FAILING_GATE_STATES,
    PASSING_GATE_STATES,
    evaluator_public_projection,
    stable_digest,
    validate_evaluator_generation,
)


MEASUREMENT_SCHEMA = "evolution-measurement.v1"
COMPARISON_STATUSES = frozenset({
    "candidate_better",
    "baseline_better",
    "tie",
    "inconclusive",
    "incomparable",
})


class SealedEvaluatorAuthority:
    """Keep hidden cases outside project state and expose opaque handles only.

    This is a process capability boundary: the authority root and access token
    belong to the evaluator service, not to candidate workers or Web routes.
    """

    def __init__(self, root: Path, *, access_token: str) -> None:
        token = str(access_token or "")
        if len(token) < 16:
            raise EvolutionContractError("sealed evaluator token is too short")
        self._root = Path(root).expanduser().resolve(strict=False)
        self._token = token

    def register_generation(
        self,
        *,
        state_dir: Path,
        public_spec: Mapping[str, Any],
        sealed_cases: Sequence[Mapping[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        state_dir = Path(state_dir).expanduser().resolve(strict=False)
        if _is_relative_to(self._root, state_dir):
            raise EvolutionContractError(
                "sealed evaluator root must be outside the project state directory"
            )
        cases = [deepcopy(dict(item)) for item in sealed_cases]
        if not cases:
            raise EvolutionContractError("sealed evaluator requires at least one case")
        generation_id = str(public_spec.get("generation_id") or "").strip()
        if not generation_id:
            raise EvolutionContractError("generation_id is required")
        private_body = {
            "schema_version": "sealed-evaluator-generation.v1",
            "generation_id": generation_id,
            "cases": cases,
        }
        holdout_digest = stable_digest(private_body)
        generation = validate_evaluator_generation({
            **dict(public_spec),
            "holdout_authority_ref": (
                f"sealed-evaluator://generation/{generation_id}"
            ),
            "holdout_generation_digest": holdout_digest,
        })
        target_dir = self._root / "generations" / _safe_component(generation_id)
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(target_dir, 0o700)
        target = target_dir / f"{holdout_digest}.json"
        encoded = json.dumps(
            private_body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
        if target.exists() and target.read_text(encoding="utf-8") != encoded:
            raise EvolutionContractError("sealed evaluator digest collision")
        if not target.exists():
            atomic_write_text(target, encoded)
        os.chmod(target, 0o600)
        public = evaluator_public_projection(generation)
        descriptor = write_immutable_json_sidecar(
            state_dir,
            public,
            root="evolution/evaluators",
            kind="evaluator_generation",
            schema_version="evaluator-generation.v1",
            created_by="evolution-coordinator",
        )
        return public, descriptor

    def evaluate(
        self,
        handle: str,
        *,
        generation_digest: str,
        access_token: str,
        trusted_runner: Callable[[list[dict[str, Any]]], Mapping[str, Any]],
    ) -> dict[str, Any]:
        self._authorize(access_token)
        generation_id = _generation_id_from_handle(handle)
        candidates = sorted(
            (self._root / "generations" / _safe_component(generation_id)).glob("*.json")
        )
        if len(candidates) != 1:
            raise EvolutionContractError("sealed evaluator generation is unavailable")
        body = json.loads(candidates[0].read_text(encoding="utf-8"))
        cases = body.get("cases")
        if not isinstance(cases, list):
            raise EvolutionContractError("sealed evaluator body is invalid")
        result = dict(trusted_runner(deepcopy(cases)))
        result["evaluator_generation_digest"] = generation_digest
        return result

    def _authorize(self, value: str) -> None:
        if not hmac.compare_digest(self._token, str(value or "")):
            raise PermissionError("sealed evaluator access denied")


def validate_measurement(
    generation: Mapping[str, Any],
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    """Require exact gate/dimension membership before any score is computed."""

    evaluator = validate_evaluator_generation(generation)
    body = deepcopy(dict(raw))
    if str(body.get("schema_version") or "") != MEASUREMENT_SCHEMA:
        raise EvolutionContractError(f"schema_version must be {MEASUREMENT_SCHEMA}")
    for key in ("trial_id", "arm"):
        if not str(body.get(key) or "").strip():
            raise EvolutionContractError(f"measurement {key} is required")
    if str(body.get("arm")) not in {"baseline", "candidate"}:
        raise EvolutionContractError("measurement arm must be baseline or candidate")
    if str(body.get("evaluator_generation_digest") or "") != str(
        evaluator["generation_digest"]
    ):
        raise EvolutionContractError("evaluator generation identity drift")

    identity = body.get("comparison_identity")
    if not isinstance(identity, Mapping):
        raise EvolutionContractError("comparison_identity must be an object")
    required_identity = set(evaluator["comparison_identity_fields"])
    if set(identity) != required_identity or any(
        not str(identity.get(key) or "").strip() for key in required_identity
    ):
        raise EvolutionContractError(
            "comparison_identity must contain the exact frozen field set"
        )
    body["comparison_fingerprint"] = stable_digest(dict(identity))

    gates = body.get("gates")
    if not isinstance(gates, Mapping):
        raise EvolutionContractError("measurement gates must be an object")
    gate_specs = {str(item["id"]): item for item in evaluator["required_gates"]}
    if set(gates) != set(gate_specs):
        raise EvolutionContractError("measurement gates must match the exact required set")
    normalized_gates: dict[str, str] = {}
    for gate_id, value in gates.items():
        status = str(value or "").strip().lower()
        if status not in PASSING_GATE_STATES | FAILING_GATE_STATES:
            raise EvolutionContractError(f"gate {gate_id} has unsupported status")
        normalized_gates[str(gate_id)] = status
    body["gates"] = normalized_gates
    body["gate_passed"] = all(
        normalized_gates[gate_id] in PASSING_GATE_STATES
        for gate_id, spec in gate_specs.items()
        if bool(spec.get("blocking"))
    )

    scores = body.get("scores")
    if not isinstance(scores, Mapping):
        raise EvolutionContractError("measurement scores must be an object")
    dimensions = {
        str(item["id"]): item for item in evaluator["required_score_dimensions"]
    }
    if set(scores) != set(dimensions):
        raise EvolutionContractError(
            "measurement scores must match the exact required dimension set"
        )
    normalized_scores: dict[str, float] = {}
    weighted = 0.0
    weight_sum = 0.0
    for dimension_id, spec in dimensions.items():
        value = _finite_number(scores[dimension_id], f"score {dimension_id}")
        lower = float(spec["min"])
        upper = float(spec["max"])
        if value < lower or value > upper:
            raise EvolutionContractError(
                f"score {dimension_id} is outside [{lower}, {upper}]"
            )
        normalized_scores[dimension_id] = value
        normalized = (value - lower) / (upper - lower) * 100.0
        if not bool(spec.get("higher_is_better", True)):
            normalized = 100.0 - normalized
        weighted += normalized * float(spec["weight"])
        weight_sum += float(spec["weight"])
    body["scores"] = normalized_scores
    body["total_score"] = round(weighted / weight_sum, 6)
    return body


def compare_repeated_trials(
    generation: Mapping[str, Any],
    *,
    attempt_id: str,
    baseline: Sequence[Mapping[str, Any]],
    candidate: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    evaluator = validate_evaluator_generation(generation)
    try:
        baseline_rows = [validate_measurement(evaluator, row) for row in baseline]
        candidate_rows = [validate_measurement(evaluator, row) for row in candidate]
    except EvolutionContractError as exc:
        return _comparison(
            attempt_id,
            evaluator,
            status="incomparable",
            reason=str(exc),
            baseline=[],
            candidate=[],
        )

    all_fingerprints = {
        str(row["comparison_fingerprint"])
        for row in [*baseline_rows, *candidate_rows]
    }
    if len(all_fingerprints) != 1:
        return _comparison(
            attempt_id,
            evaluator,
            status="incomparable",
            reason="baseline and candidate comparison identity differs",
            baseline=baseline_rows,
            candidate=candidate_rows,
        )
    minimum = int(evaluator["min_trials"])
    if len(baseline_rows) < minimum or len(candidate_rows) < minimum:
        return _comparison(
            attempt_id,
            evaluator,
            status="inconclusive",
            reason=f"minimum repeated trials not met ({minimum})",
            baseline=baseline_rows,
            candidate=candidate_rows,
        )

    baseline_gate = all(bool(row["gate_passed"]) for row in baseline_rows)
    candidate_gate = all(bool(row["gate_passed"]) for row in candidate_rows)
    if baseline_gate and not candidate_gate:
        status = "baseline_better"
        reason = "candidate failed at least one blocking gate"
    elif candidate_gate and not baseline_gate:
        status = "candidate_better"
        reason = "candidate passed blocking gates while baseline did not"
    elif not baseline_gate and not candidate_gate:
        status = "inconclusive"
        reason = "both arms failed blocking gates"
    else:
        baseline_median = statistics.median(row["total_score"] for row in baseline_rows)
        candidate_median = statistics.median(row["total_score"] for row in candidate_rows)
        delta = candidate_median - baseline_median
        spread = max(
            _spread([row["total_score"] for row in baseline_rows]),
            _spread([row["total_score"] for row in candidate_rows]),
        )
        blocking_regression = _blocking_dimension_regression(
            evaluator,
            baseline_rows,
            candidate_rows,
        )
        if blocking_regression:
            status = "baseline_better"
            reason = f"blocking dimension regressed: {blocking_regression}"
        elif spread > float(evaluator["max_spread"]):
            status = "inconclusive"
            reason = "trial spread exceeds evaluator policy"
        elif delta > float(evaluator["min_delta"]):
            status = "candidate_better"
            reason = "candidate median exceeds min_delta"
        elif delta < -float(evaluator["min_delta"]):
            status = "baseline_better"
            reason = "candidate median regressed beyond min_delta"
        else:
            status = "tie"
            reason = "median delta is within min_delta"
    return _comparison(
        attempt_id,
        evaluator,
        status=status,
        reason=reason,
        baseline=baseline_rows,
        candidate=candidate_rows,
    )


def incomparable_comparison(
    generation: Mapping[str, Any],
    *,
    attempt_id: str,
    reason: str,
) -> dict[str, Any]:
    """Build a typed non-adoptable verdict before any candidate is scored."""

    evaluator = validate_evaluator_generation(generation)
    return _comparison(
        attempt_id,
        evaluator,
        status="incomparable",
        reason=str(reason or "comparison identity is not admissible"),
        baseline=[],
        candidate=[],
    )


def pareto_frontier(
    candidates: Sequence[Mapping[str, Any]],
    *,
    dimensions: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Return stable non-dominated candidates for maximize/minimize dimensions."""

    rows = [deepcopy(dict(item)) for item in candidates]
    for dimension, direction in dimensions.items():
        if direction not in {"maximize", "minimize"}:
            raise EvolutionContractError(f"unsupported Pareto direction for {dimension}")
        for row in rows:
            metrics = row.get("metrics")
            if not isinstance(metrics, Mapping) or dimension not in metrics:
                raise EvolutionContractError(f"candidate is missing Pareto metric {dimension}")
            _finite_number(metrics[dimension], f"Pareto metric {dimension}")
    frontier: list[dict[str, Any]] = []
    for row in rows:
        if not any(
            other is not row and _dominates(other, row, dimensions)
            for other in rows
        ):
            frontier.append(row)
    return sorted(
        frontier,
        key=lambda item: str(
            item.get("candidate_id") or item.get("variant_id") or ""
        ),
    )


def _comparison(
    attempt_id: str,
    evaluator: Mapping[str, Any],
    *,
    status: str,
    reason: str,
    baseline: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
) -> dict[str, Any]:
    if status not in COMPARISON_STATUSES:
        raise EvolutionContractError(f"unsupported comparison status {status}")
    summaries = {
        "baseline": _arm_summary(evaluator, baseline),
        "candidate": _arm_summary(evaluator, candidate),
    }
    baseline_score = summaries["baseline"].get("median_total")
    candidate_score = summaries["candidate"].get("median_total")
    delta = None
    if isinstance(baseline_score, float) and isinstance(candidate_score, float):
        delta = round(candidate_score - baseline_score, 6)
    body = {
        "schema_version": EVOLUTION_COMPARISON_SCHEMA,
        "comparison_id": "evocmp-" + stable_digest({
            "attempt_id": attempt_id,
            "generation": evaluator.get("generation_digest"),
            "baseline": [row.get("trial_id") for row in baseline],
            "candidate": [row.get("trial_id") for row in candidate],
        })[:20],
        "attempt_id": attempt_id,
        "status": status,
        "reason": reason,
        "adoption_eligible": status == "candidate_better",
        "evaluator_generation_id": evaluator.get("generation_id"),
        "evaluator_generation_digest": evaluator.get("generation_digest"),
        "comparison_fingerprint": (
            baseline[0].get("comparison_fingerprint")
            if baseline else candidate[0].get("comparison_fingerprint") if candidate else ""
        ),
        "arms": summaries,
        "score_delta": delta,
    }
    body["comparison_digest"] = stable_digest(body)
    return body


def _arm_summary(
    evaluator: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not rows:
        return {"trial_count": 0, "gate_passed": False, "dimensions": {}}
    dimensions: dict[str, Any] = {}
    for spec in evaluator["required_score_dimensions"]:
        key = str(spec["id"])
        values = [float(row["scores"][key]) for row in rows]
        dimensions[key] = {
            "values": values,
            "median": statistics.median(values),
            "spread": _spread(values),
        }
    totals = [float(row["total_score"]) for row in rows]
    return {
        "trial_count": len(rows),
        "gate_passed": all(bool(row["gate_passed"]) for row in rows),
        "trial_ids": [str(row["trial_id"]) for row in rows],
        "median_total": float(statistics.median(totals)),
        "spread_total": _spread(totals),
        "dimensions": dimensions,
    }


def _blocking_dimension_regression(
    evaluator: Mapping[str, Any],
    baseline: Sequence[Mapping[str, Any]],
    candidate: Sequence[Mapping[str, Any]],
) -> str:
    for spec in evaluator["required_score_dimensions"]:
        if not bool(spec.get("blocking_regression")):
            continue
        key = str(spec["id"])
        base = statistics.median(float(row["scores"][key]) for row in baseline)
        cand = statistics.median(float(row["scores"][key]) for row in candidate)
        regressed = cand < base if bool(spec.get("higher_is_better", True)) else cand > base
        if regressed:
            return key
    return ""


def _spread(values: Sequence[float]) -> float:
    return round(max(values) - min(values), 6) if values else 0.0


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _dominates(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    dimensions: Mapping[str, str],
) -> bool:
    left_metrics = left["metrics"]
    right_metrics = right["metrics"]
    no_worse = True
    strictly_better = False
    for key, direction in dimensions.items():
        left_value = float(left_metrics[key])
        right_value = float(right_metrics[key])
        if direction == "maximize":
            no_worse = no_worse and left_value >= right_value
            strictly_better = strictly_better or left_value > right_value
        else:
            no_worse = no_worse and left_value <= right_value
            strictly_better = strictly_better or left_value < right_value
    return no_worse and strictly_better


def _generation_id_from_handle(handle: str) -> str:
    prefix = "sealed-evaluator://generation/"
    if not str(handle or "").startswith(prefix):
        raise EvolutionContractError("invalid sealed evaluator handle")
    value = str(handle)[len(prefix):].strip()
    if not value:
        raise EvolutionContractError("invalid sealed evaluator handle")
    return value


def _safe_component(value: str) -> str:
    out = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in value)
    if not out or out in {".", ".."}:
        raise EvolutionContractError("unsafe evaluator generation id")
    return out


def _finite_number(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise EvolutionContractError(f"{field} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise EvolutionContractError(f"{field} must be a finite number") from exc
    if not math.isfinite(number):
        raise EvolutionContractError(f"{field} must be a finite number")
    return number


__all__ = [
    "COMPARISON_STATUSES",
    "MEASUREMENT_SCHEMA",
    "SealedEvaluatorAuthority",
    "compare_repeated_trials",
    "incomparable_comparison",
    "pareto_frontier",
    "validate_measurement",
]
