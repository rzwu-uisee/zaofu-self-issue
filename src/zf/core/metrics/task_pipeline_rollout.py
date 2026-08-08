"""Fair, fail-closed rollout evidence for Task Pipeline v4."""

from __future__ import annotations

import hashlib
import json
import statistics
from pathlib import Path
from typing import Any, Iterable, Mapping


TASK_PIPELINE_AB_REGISTRATION_SCHEMA = "task-pipeline-ab-registration.v1"
TASK_PIPELINE_AB_REPORT_SCHEMA = "task-pipeline-ab-report.v1"
TASK_PIPELINE_CANARY_MANIFEST_SCHEMA = "task-pipeline-canary-manifest.v1"

_ARMS = ("v3", "v4")
_EXPECTED_PROFILES = {
    "v3": "stage_barrier",
    "v4": "task_pipeline_pool",
}
_IDENTITY_FIELDS = (
    "source_commit",
    "plan_package_digest",
    "task_map_digest",
    "task_contract_digest",
    "prompt_digest",
    "normalized_config_digest",
    "provider_identity",
    "budget",
)
_LOWER_IS_BETTER = (
    "latency_seconds",
    "rework_count",
    "conflict_count",
    "cost_usd",
    "false_completion_count",
    "terminal_residual_count",
)
_HIGHER_IS_BETTER = (
    "utilization",
    "intervention_quality",
)


class TaskPipelineRolloutError(ValueError):
    """The preregistered experiment or its result is malformed."""


def build_task_pipeline_canary_manifest(
    registration: Mapping[str, Any],
    *,
    repo_root: Path,
    output_root: Path,
) -> dict[str, Any]:
    """Freeze serial v3/v4 executions against one exact source."""

    normalized = _validate_registration(registration)
    runs = []
    for sample in normalized["samples"]:
        sample_id = str(sample["sample_id"])
        for arm in _ARMS:
            run_id = f"{sample_id}-{arm}"
            run_root = Path(output_root) / run_id
            runs.append({
                "run_id": run_id,
                "sample_id": sample_id,
                "arm": arm,
                "execution_profile": _EXPECTED_PROFILES[arm],
                "source_commit": normalized["source_commit"],
                "repo_root": str(Path(repo_root).resolve()),
                "worktree": str((run_root / "worktree").resolve()),
                "state_dir": str((run_root / "state").resolve()),
                "result_path": str((run_root / "result.json").resolve()),
                "expected_conditional_roles": list(
                    sample["expected_conditional_roles"]
                ),
                "expected_recovery_turns": int(
                    sample["expected_recovery_turns"]
                ),
            })
    manifest = {
        "schema_version": TASK_PIPELINE_CANARY_MANIFEST_SCHEMA,
        "experiment_id": normalized["experiment_id"],
        "registration_digest": _digest(normalized),
        "serial_execution": True,
        "only_preregistered_variable": "execution_profile",
        "runs": runs,
    }
    manifest["manifest_digest"] = _digest(manifest)
    return manifest


def build_task_pipeline_ab_report(
    registration: Mapping[str, Any],
    arm_reports: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare preregistered arms and HOLD on any identity drift."""

    normalized = _validate_registration(registration)
    samples_by_arm = {
        arm: _samples_by_id(arm_reports.get(arm, {}))
        for arm in _ARMS
    }
    expected_ids = {
        str(sample["sample_id"])
        for sample in normalized["samples"]
    }
    fairness: dict[str, bool] = {
        "sample_set": all(
            set(samples_by_arm[arm]) == expected_ids
            for arm in _ARMS
        ),
        "execution_profile_only_variable": all(
            str(arm_reports.get(arm, {}).get("execution_profile") or "")
            == _EXPECTED_PROFILES[arm]
            for arm in _ARMS
        ),
    }
    for field in _IDENTITY_FIELDS:
        fairness[field] = _identity_field_matches(
            normalized,
            samples_by_arm,
            field,
            expected_ids,
        )
    fairness["actual_provider_identity"] = _sample_field_matches(
        normalized,
        samples_by_arm,
        field="actual_provider_identity",
        expected_field="provider_identity",
        expected_ids=expected_ids,
    )
    fairness["conditional_roles"] = _sample_expectation_matches(
        normalized,
        samples_by_arm,
        field="conditional_roles",
        expected_field="expected_conditional_roles",
        expected_ids=expected_ids,
        normalize=_sorted_strings,
    )
    fairness["recovery_turns"] = _sample_expectation_matches(
        normalized,
        samples_by_arm,
        field="recovery_turns",
        expected_field="expected_recovery_turns",
        expected_ids=expected_ids,
        normalize=lambda value: int(value or 0),
    )

    terminal_closed = fairness["sample_set"] and all(
        bool(samples_by_arm[arm][sample_id].get("terminal_closed"))
        and str(samples_by_arm[arm][sample_id].get("status") or "")
        == "passed"
        for arm in _ARMS
        for sample_id in sorted(expected_ids)
    )
    metrics = {
        arm: _aggregate_metrics(samples_by_arm[arm].values())
        for arm in _ARMS
    }
    fairness_passed = all(fairness.values())
    metric_gate = _metric_gate(normalized, metrics)
    eligible = fairness_passed and terminal_closed and metric_gate["passed"]
    hold_reasons = []
    if not fairness_passed:
        hold_reasons.append("fairness_mismatch")
    if not terminal_closed:
        hold_reasons.append("terminal_not_closed")
    if not metric_gate["passed"]:
        hold_reasons.extend(metric_gate["reasons"])
    report = {
        "schema_version": TASK_PIPELINE_AB_REPORT_SCHEMA,
        "experiment_id": normalized["experiment_id"],
        "registration_digest": _digest(normalized),
        "status": "passed" if eligible else "hold",
        "rollout_decision": "CANARY_EXPAND" if eligible else "HOLD",
        "winner": "v4" if eligible else None,
        "v4_default_enabled": False,
        "fairness": fairness,
        "terminal_closed_both": terminal_closed,
        "metrics": metrics,
        "metric_gate": metric_gate,
        "hold_reasons": sorted(set(hold_reasons)),
        "arms": {
            arm: {
                "execution_profile": _EXPECTED_PROFILES[arm],
                "sample_ids": sorted(samples_by_arm[arm]),
            }
            for arm in _ARMS
        },
    }
    report["report_digest"] = _digest(report)
    return report


def _validate_registration(
    registration: Mapping[str, Any],
) -> dict[str, Any]:
    value = dict(registration)
    if value.get("schema_version") != TASK_PIPELINE_AB_REGISTRATION_SCHEMA:
        raise TaskPipelineRolloutError("invalid A/B registration schema")
    if not str(value.get("experiment_id") or "").strip():
        raise TaskPipelineRolloutError("experiment_id is required")
    for field in _IDENTITY_FIELDS:
        if value.get(field) in (None, "", {}):
            raise TaskPipelineRolloutError(f"registration requires {field}")
    arms = value.get("arms")
    if not isinstance(arms, Mapping) or any(
        str((arms.get(arm) or {}).get("execution_profile") or "")
        != _EXPECTED_PROFILES[arm]
        for arm in _ARMS
    ):
        raise TaskPipelineRolloutError(
            "arms must preregister stage_barrier vs task_pipeline_pool"
        )
    samples = value.get("samples")
    if not isinstance(samples, list) or len(samples) < 2:
        raise TaskPipelineRolloutError("at least two preregistered samples required")
    normalized_samples = []
    sample_ids = set()
    for raw in samples:
        if not isinstance(raw, Mapping):
            raise TaskPipelineRolloutError("sample registration must be an object")
        sample_id = str(raw.get("sample_id") or "").strip()
        if not sample_id or sample_id in sample_ids:
            raise TaskPipelineRolloutError("sample_id must be unique and non-empty")
        sample_ids.add(sample_id)
        normalized_samples.append({
            **dict(raw),
            "sample_id": sample_id,
            "expected_conditional_roles": _sorted_strings(
                raw.get("expected_conditional_roles")
            ),
            "expected_recovery_turns": int(
                raw.get("expected_recovery_turns") or 0
            ),
        })
    return {
        **value,
        "samples": sorted(normalized_samples, key=lambda row: row["sample_id"]),
    }


def _samples_by_id(report: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    samples = report.get("samples")
    if not isinstance(samples, list):
        return {}
    result = {}
    for raw in samples:
        if not isinstance(raw, Mapping):
            continue
        sample_id = str(raw.get("sample_id") or "").strip()
        if sample_id and sample_id not in result:
            result[sample_id] = dict(raw)
    return result


def _identity_field_matches(
    registration: Mapping[str, Any],
    samples_by_arm: Mapping[str, Mapping[str, Mapping[str, Any]]],
    field: str,
    expected_ids: set[str],
) -> bool:
    expected = registration[field]
    return all(
        sample_id in samples_by_arm[arm]
        and _same(samples_by_arm[arm][sample_id].get(field), expected)
        for arm in _ARMS
        for sample_id in expected_ids
    )


def _sample_field_matches(
    registration: Mapping[str, Any],
    samples_by_arm: Mapping[str, Mapping[str, Mapping[str, Any]]],
    *,
    field: str,
    expected_field: str,
    expected_ids: set[str],
) -> bool:
    expected = registration[expected_field]
    return all(
        sample_id in samples_by_arm[arm]
        and _same(samples_by_arm[arm][sample_id].get(field), expected)
        for arm in _ARMS
        for sample_id in expected_ids
    )


def _sample_expectation_matches(
    registration: Mapping[str, Any],
    samples_by_arm: Mapping[str, Mapping[str, Mapping[str, Any]]],
    *,
    field: str,
    expected_field: str,
    expected_ids: set[str],
    normalize: Any,
) -> bool:
    expected = {
        str(sample["sample_id"]): normalize(sample[expected_field])
        for sample in registration["samples"]
    }
    return all(
        sample_id in samples_by_arm[arm]
        and normalize(samples_by_arm[arm][sample_id].get(field))
        == expected[sample_id]
        for arm in _ARMS
        for sample_id in expected_ids
    )


def _aggregate_metrics(
    samples: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    rows = list(samples)
    result: dict[str, Any] = {"sample_count": len(rows)}
    for field in (*_LOWER_IS_BETTER, *_HIGHER_IS_BETTER):
        values = [float((row.get("metrics") or {}).get(field) or 0.0) for row in rows]
        result[field] = {
            "mean": statistics.fmean(values) if values else 0.0,
            "variance": statistics.pvariance(values) if len(values) > 1 else 0.0,
        }
    return result


def _metric_gate(
    registration: Mapping[str, Any],
    metrics: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    thresholds = registration.get("thresholds")
    thresholds = thresholds if isinstance(thresholds, Mapping) else {}
    v3 = metrics["v3"]
    v4 = metrics["v4"]
    reasons = []
    latency_gain = (
        float(v3["latency_seconds"]["mean"])
        - float(v4["latency_seconds"]["mean"])
    )
    utilization_gain = (
        float(v4["utilization"]["mean"])
        - float(v3["utilization"]["mean"])
    )
    if latency_gain <= float(thresholds.get("min_latency_gain_seconds") or 0):
        reasons.append("latency_not_improved")
    if utilization_gain <= float(
        thresholds.get("min_utilization_gain") or 0
    ):
        reasons.append("utilization_not_improved")
    for field in _LOWER_IS_BETTER[1:]:
        allowed = float(
            (thresholds.get("max_regression") or {}).get(field) or 0
        )
        if (
            float(v4[field]["mean"])
            - float(v3[field]["mean"])
        ) > allowed:
            reasons.append(f"{field}_regressed")
    allowed_quality = float(
        (thresholds.get("max_regression") or {}).get(
            "intervention_quality",
            0,
        )
        or 0
    )
    if (
        float(v3["intervention_quality"]["mean"])
        - float(v4["intervention_quality"]["mean"])
    ) > allowed_quality:
        reasons.append("intervention_quality_regressed")
    return {
        "passed": not reasons,
        "latency_gain_seconds": latency_gain,
        "utilization_gain": utilization_gain,
        "reasons": reasons,
    }


def _sorted_strings(value: Any) -> list[str]:
    return sorted(str(item) for item in (value or []) if str(item).strip())


def _same(left: Any, right: Any) -> bool:
    return _digest(left) == _digest(right)


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "TASK_PIPELINE_AB_REGISTRATION_SCHEMA",
    "TASK_PIPELINE_AB_REPORT_SCHEMA",
    "TASK_PIPELINE_CANARY_MANIFEST_SCHEMA",
    "TaskPipelineRolloutError",
    "build_task_pipeline_ab_report",
    "build_task_pipeline_canary_manifest",
]
