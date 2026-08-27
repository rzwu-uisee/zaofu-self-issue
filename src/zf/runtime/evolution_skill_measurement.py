"""Independent outcome and Skill-behavior measurement helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.evolution_contracts import EvolutionContractError
from zf.runtime.evolution_skill_trajectory import (
    BEHAVIOR_VERDICT_SCHEMA,
    evaluate_trajectory_behavior,
)


def score_provider_outputs(
    outputs: list[Mapping[str, Any]],
    cases: list[Mapping[str, Any]],
    evaluator: Mapping[str, Any],
) -> dict[str, Any]:
    """Score product output without deriving behavior from its result."""

    scores: list[float] = []
    secrets_clear = True
    case_results: list[dict[str, Any]] = []
    for index, (output, case) in enumerate(zip(outputs, cases, strict=True), start=1):
        text = str(output.get("final") or "").lower()
        required = case.get("required_concepts") or []
        matched = sum(
            1
            for alternatives in required
            if any(str(term).lower() in text for term in alternatives)
        )
        if required:
            score = matched / len(required) * 100.0
        else:
            expected = str(case.get("expected_text") or "").strip().lower()
            score = 100.0 if expected and expected in text else 0.0
        scores.append(score)
        case_secrets_clear = not any(
            str(term).lower() in text for term in case.get("forbidden_terms") or []
        )
        secrets_clear = secrets_clear and case_secrets_clear
        minimum_score = float(case.get("minimum_score") or 60.0)
        trajectory = output.get("trajectory")
        if not isinstance(trajectory, Mapping):
            raise EvolutionContractError("provider output lacks normalized trajectory")
        case_id = str(case.get("case_id") or f"case-{index}")
        behavior = evaluate_trajectory_behavior(
            {**dict(case), "case_id": case_id},
            trajectory,
        )
        case_results.append(
            {
                "case_id": case_id,
                "case_kind": str(case.get("case_kind") or "explicit"),
                "score": score,
                "gate_passed": score >= minimum_score and case_secrets_clear,
                "behavior_followed": behavior["behavior_followed"],
                "behavior_verdict": behavior,
            }
        )
    correctness = sum(scores) / len(scores) if scores else 0.0
    minimum = min(
        [float(case.get("minimum_score") or 60.0) for case in cases],
        default=60.0,
    )
    gate_passed = correctness >= minimum and secrets_clear
    gates: dict[str, str] = {}
    for gate in evaluator["required_gates"]:
        gate_id = str(gate["id"])
        if "secret" in gate_id.lower():
            gates[gate_id] = "passed" if secrets_clear else "failed"
        else:
            gates[gate_id] = "passed" if gate_passed else "failed"
    dimensions = {
        str(item["id"]): (
            max(0.0, min(100.0, correctness))
            if "correct" in str(item["id"]).lower()
            else 100.0
            if gate_passed
            else 0.0
        )
        for item in evaluator["required_score_dimensions"]
    }
    total_score = sum(dimensions.values()) / len(dimensions) if dimensions else 0.0
    return {
        "gates": gates,
        "scores": dimensions,
        "gate_passed": gate_passed,
        "total_score": total_score,
        "case_results": case_results,
    }


def persist_behavior_verdicts(
    *,
    state_dir: Path,
    request: ZfEvent,
    outputs: list[Mapping[str, Any]],
    evaluation: Mapping[str, Any],
) -> None:
    """Persist complete behavior bodies and leave only refs in case results."""

    case_results = evaluation.get("case_results")
    if not isinstance(case_results, list):
        return
    output_by_case = {
        str(item.get("case_id") or ""): item
        for item in outputs
        if isinstance(item, Mapping)
    }
    for item in case_results:
        if not isinstance(item, dict):
            continue
        verdict = item.pop("behavior_verdict", None)
        if not isinstance(verdict, Mapping):
            continue
        output = output_by_case.get(str(item.get("case_id") or ""), {})
        trajectory_ref = output.get("trajectory_ref")
        body = {**dict(verdict), "trajectory_ref": dict(trajectory_ref or {})}
        descriptor = write_immutable_json_sidecar(
            state_dir,
            body,
            root="evolution/skill-behavior-verdicts",
            kind="skill_behavior_verdict",
            schema_version=BEHAVIOR_VERDICT_SCHEMA,
            created_by="autoresearch-evolution-runner",
            source_event_id=request.id,
        )
        item["behavior_verdict_ref"] = descriptor
        item["behavior_evidence_step_refs"] = [
            step_ref
            for check in body.get("checks") or []
            if isinstance(check, Mapping)
            for step_ref in check.get("trajectory_step_refs") or []
        ]


__all__ = ["persist_behavior_verdicts", "score_provider_outputs"]
