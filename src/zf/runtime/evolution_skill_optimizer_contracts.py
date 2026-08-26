"""Mechanical contracts and patch operations for Skill optimization."""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from typing import Any, Mapping, Sequence

from zf.runtime.evolution_contracts import (
    EvolutionContractError,
    normalize_digest,
    stable_digest,
)
from zf.runtime.evolution_skill import validate_skill_candidate


CAMPAIGN_SCHEMA = "skill-optimization-campaign.v2"
LEGACY_CAMPAIGN_SCHEMA = "skill-optimization-campaign.v1"
DATASET_SPLIT_SCHEMA = "skill-eval-split.v1"
EVALUATION_SCHEMA = "skill-optimization-evaluation.v1"
MATERIAL_SCHEMA = "skill-optimization-material.v1"
PROPOSAL_SCHEMA = "skill-edit-proposal.v1"
STATE_SCHEMA = "skill-optimizer-state.v1"
STEP_SCHEMA = "skill-optimization-step.v1"

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")
_LEGACY_FROZEN_IDENTITY_KEYS = frozenset({
    "eval_suite_digest",
    "grader_digest",
    "model_digest",
    "prompt_digest",
    "provider_digest",
    "support_skill_inventory_digest",
    "workspace_fixture_digest",
})
_FROZEN_IDENTITY_KEYS = frozenset({
    *_LEGACY_FROZEN_IDENTITY_KEYS,
    "runtime_commit_digest",
    "role_profile_digest",
    "tool_policy_digest",
    "sandbox_policy_digest",
    "network_policy_digest",
    "evaluator_generation_digest",
    "budget_digest",
})


class SkillOptimizerError(EvolutionContractError):
    """Raised when optimizer identity, budget, or patch protocol is invalid."""


def normalize_campaign(raw: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    body = deepcopy(dict(raw))
    schema_version = str(body.get("schema_version") or "")
    if schema_version not in {LEGACY_CAMPAIGN_SCHEMA, CAMPAIGN_SCHEMA}:
        raise SkillOptimizerError("Skill optimizer campaign schema_version is unsupported")
    campaign_id = str(body.get("campaign_id") or "").strip()
    if not _ID_RE.fullmatch(campaign_id):
        raise SkillOptimizerError("Skill optimizer campaign_id is invalid")
    skill_name = str(body.get("skill_name") or "").strip()
    base_content = str(body.pop("base_content", "") or "")
    metadata = body.get("candidate_metadata")
    if not isinstance(metadata, Mapping):
        raise SkillOptimizerError("Skill optimizer candidate_metadata is required")
    candidate_metadata = deepcopy(dict(metadata))
    for key in (
        "candidate_version",
        "content",
        "content_digest",
        "schema_version",
        "skill_name",
    ):
        candidate_metadata.pop(key, None)
    candidate = validate_skill_candidate({
        **candidate_metadata,
        "schema_version": "skill-candidate.v1",
        "skill_name": skill_name,
        "content": base_content,
    })
    frozen = body.get("frozen_identity")
    expected_frozen_keys = (
        _FROZEN_IDENTITY_KEYS
        if schema_version == CAMPAIGN_SCHEMA
        else _LEGACY_FROZEN_IDENTITY_KEYS
    )
    if not isinstance(frozen, Mapping) or set(frozen) != expected_frozen_keys:
        raise SkillOptimizerError(
            "Skill optimizer frozen_identity must contain the exact required keys"
        )
    normalized_frozen = {
        key: normalize_digest(value, field=f"frozen_identity.{key}")
        for key, value in frozen.items()
    }
    suite_digest = normalize_digest(
        body.get("eval_suite_digest"),
        field="eval_suite_digest",
    )
    if normalized_frozen["eval_suite_digest"] != suite_digest:
        raise SkillOptimizerError("frozen eval suite digest drift")
    if schema_version == CAMPAIGN_SCHEMA:
        splits = normalize_dataset_splits(body.get("dataset_splits"))
        selection = splits["selection"]
        test = splits["test"]
        if selection["digest"] != suite_digest:
            raise SkillOptimizerError("selection split and eval suite digest drift")
        if candidate["public_eval_suite_digest"] != test["digest"]:
            raise SkillOptimizerError("candidate metadata must bind the Test split digest")
        sealed_test_generation_ref = str(
            body.get("sealed_test_generation_ref") or ""
        ).strip()
        if not sealed_test_generation_ref.startswith(
            "sealed-evaluator://generation/"
        ):
            raise SkillOptimizerError("sealed_test_generation_ref must be opaque")
        if candidate["sealed_eval_generation_ref"] != sealed_test_generation_ref:
            raise SkillOptimizerError("candidate metadata Test generation drift")
        body.update({
            "dataset_splits": splits,
            "dataset_split_digest": stable_digest(splits),
            "split_seed": _bounded_int(body.get("split_seed"), "split_seed", 0, 2**31 - 1),
            "sealed_test_generation_ref": sealed_test_generation_ref,
            "optimizer_protocol": "train-selection-test-isolated",
        })
    else:
        if candidate["public_eval_suite_digest"] != suite_digest:
            raise SkillOptimizerError("candidate metadata eval suite digest drift")
        body.update({
            "dataset_splits": {},
            "dataset_split_digest": "",
            "split_seed": 0,
            "sealed_test_generation_ref": candidate["sealed_eval_generation_ref"],
            "optimizer_protocol": "legacy-single-suite",
        })
    body.update({
        "schema_version": schema_version,
        "campaign_id": campaign_id,
        "skill_name": skill_name,
        "candidate_metadata": candidate_metadata,
        "frozen_identity": normalized_frozen,
        "frozen_identity_digest": stable_digest(normalized_frozen),
        "eval_suite_digest": suite_digest,
        "score_dimensions": _score_dimensions(body.get("score_dimensions")),
        "max_epochs": _bounded_int(body.get("max_epochs", 4), "max_epochs", 1, 16),
        "max_edits_per_step": _bounded_int(
            body.get("max_edits_per_step", 4),
            "max_edits_per_step",
            1,
            4,
        ),
        "rejection_buffer_size": _bounded_int(
            body.get("rejection_buffer_size", 20),
            "rejection_buffer_size",
            1,
            100,
        ),
        "max_consecutive_no_improvement": _bounded_int(
            body.get("max_consecutive_no_improvement", 4),
            "max_consecutive_no_improvement",
            1,
            16,
        ),
        "slow_meta_cadence": _bounded_int(
            body.get("slow_meta_cadence", 2),
            "slow_meta_cadence",
            1,
            16,
        ),
    })
    return body, base_content


def normalize_proposal(
    raw: Mapping[str, Any],
    *,
    campaign: Mapping[str, Any],
    state: Mapping[str, Any],
    next_epoch: int,
) -> dict[str, Any]:
    body = deepcopy(dict(raw))
    if body.get("schema_version") != PROPOSAL_SCHEMA:
        raise SkillOptimizerError(f"schema_version must be {PROPOSAL_SCHEMA}")
    if str(body.get("campaign_id") or "") != str(campaign["campaign_id"]):
        raise SkillOptimizerError("Skill edit proposal campaign_id drift")
    base_digest = normalize_digest(body.get("base_digest"), field="base_digest")
    if base_digest != str(state["best_content_digest"]):
        raise SkillOptimizerError("Skill edit proposal base digest is stale")
    edits = body.get("edits")
    if not isinstance(edits, list) or not edits:
        raise SkillOptimizerError("Skill edit proposal requires edits")
    if len(edits) > int(campaign["max_edits_per_step"]):
        raise SkillOptimizerError("Skill edit proposal exceeds edit budget")
    normalized_edits = [_normalize_edit(item, index) for index, item in enumerate(edits)]
    update = body.get("slow_meta_update")
    cadence = int(campaign["slow_meta_cadence"])
    if update is not None:
        if next_epoch % cadence:
            raise SkillOptimizerError("slow/meta update is outside the configured cadence")
        if not isinstance(update, Mapping):
            raise SkillOptimizerError("slow_meta_update must be an object")
        encoded = json.dumps(update, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(encoded) > 32768:
            raise SkillOptimizerError("slow_meta_update exceeds 32 KiB")
        body["slow_meta_update"] = deepcopy(dict(update))
    body.update({"base_digest": base_digest, "edits": normalized_edits})
    return body


def apply_edits(content: str, edits: Sequence[Mapping[str, str]]) -> str:
    result = content
    for edit in edits:
        operation = edit["operation"]
        if operation == "add":
            anchor = edit["anchor"]
            if result.count(anchor) != 1:
                raise SkillOptimizerError("add edit anchor must occur exactly once")
            replacement = (
                edit["text"] + anchor
                if edit["position"] == "before"
                else anchor + edit["text"]
            )
            result = result.replace(anchor, replacement, 1)
            continue
        old_text = edit["old_text"]
        if result.count(old_text) != 1:
            raise SkillOptimizerError(f"{operation} edit target must occur exactly once")
        result = result.replace(old_text, edit.get("new_text", ""), 1)
    if result == content:
        raise SkillOptimizerError("Skill edit proposal is a no-op")
    if len(result.encode("utf-8")) > 262144:
        raise SkillOptimizerError("optimized Skill exceeds 256 KiB")
    return result


def normalize_evaluation(
    raw: Mapping[str, Any],
    *,
    campaign: Mapping[str, Any],
    expected_candidate_digest: str,
) -> dict[str, Any]:
    body = deepcopy(dict(raw))
    if body.get("schema_version") != EVALUATION_SCHEMA:
        raise SkillOptimizerError(f"schema_version must be {EVALUATION_SCHEMA}")
    expected = {
        "campaign_id": str(campaign["campaign_id"]),
        "candidate_digest": expected_candidate_digest,
        "eval_suite_digest": str(campaign["eval_suite_digest"]),
        "frozen_identity_digest": str(campaign["frozen_identity_digest"]),
    }
    if campaign.get("schema_version") == CAMPAIGN_SCHEMA:
        selection = campaign["dataset_splits"]["selection"]
        expected.update({
            "split": "selection",
            "split_ref": str(selection["ref"]),
            "split_digest": str(selection["digest"]),
            "split_descriptor_digest": str(selection["descriptor_digest"]),
            "evaluator_generation_ref": str(selection["generation_ref"]),
        })
    for key, value in expected.items():
        actual = str(body.get(key) or "")
        normalized_actual = (
            actual.removeprefix("sha256:") if key.endswith("digest") else actual
        )
        normalized_expected = (
            value.removeprefix("sha256:") if key.endswith("digest") else value
        )
        if normalized_actual != normalized_expected:
            raise SkillOptimizerError(f"Skill optimization evaluation {key} drift")
        body[key] = normalized_expected
    score_input = body.get("scores")
    if not isinstance(score_input, Mapping):
        raise SkillOptimizerError("Skill optimization evaluation scores are required")
    dimension_ids = [str(item["id"]) for item in campaign["score_dimensions"]]
    if set(score_input) != set(dimension_ids):
        raise SkillOptimizerError("evaluation scores must match exact campaign dimensions")
    scores: dict[str, float] = {}
    weighted = 0.0
    total_weight = 0.0
    for dimension in campaign["score_dimensions"]:
        dimension_id = str(dimension["id"])
        value = _finite_float(score_input[dimension_id], f"score {dimension_id}")
        scores[dimension_id] = value
        weight = float(dimension["weight"])
        weighted += value * weight
        total_weight += weight
    evidence = body.get("case_result_refs")
    if campaign.get("schema_version") == CAMPAIGN_SCHEMA:
        evidence = _selection_case_result_refs(
            evidence,
            campaign["dataset_splits"]["selection"],
        )
    elif not isinstance(evidence, list) or not evidence:
        raise SkillOptimizerError("held-out evaluation requires case_result_refs")
    body.update({
        "scores": scores,
        "total_score": round(weighted / total_weight, 6),
        "case_result_refs": deepcopy(evidence),
    })
    return body


def normalize_dataset_splits(value: object) -> dict[str, dict[str, Any]]:
    """Validate immutable Train/Selection/Test descriptors and lineage isolation."""

    if not isinstance(value, Mapping) or set(value) != {"train", "selection", "test"}:
        raise SkillOptimizerError(
            "dataset_splits must contain exact train/selection/test descriptors"
        )
    result = {
        name: _normalize_split_descriptor(raw, expected_split=name)
        for name, raw in value.items()
    }
    for field in ("case_ids", "fixture_digests", "lineage_refs"):
        owners: dict[str, str] = {}
        for split_name, descriptor in result.items():
            for item in descriptor[field]:
                prior = owners.setdefault(str(item), split_name)
                if prior != split_name:
                    raise SkillOptimizerError(
                        f"dataset split {field} overlap: {item} in {prior}/{split_name}"
                    )
    return result


def _normalize_split_descriptor(
    raw: object,
    *,
    expected_split: str,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise SkillOptimizerError(f"{expected_split} split descriptor is required")
    body = deepcopy(dict(raw))
    if body.get("schema_version") != DATASET_SPLIT_SCHEMA:
        raise SkillOptimizerError(f"{expected_split} split schema is invalid")
    if str(body.get("split") or "") != expected_split:
        raise SkillOptimizerError(f"{expected_split} split identity drift")
    ref = str(body.get("ref") or "").strip()
    generation_ref = str(body.get("generation_ref") or "").strip()
    if not ref or not generation_ref:
        raise SkillOptimizerError(f"{expected_split} split ref/generation_ref are required")
    digest = normalize_digest(body.get("digest"), field=f"{expected_split}.digest")
    case_ids = _unique_strings(body.get("case_ids"), f"{expected_split}.case_ids")
    fixture_digests = [
        normalize_digest(item, field=f"{expected_split}.fixture_digests")
        for item in _unique_strings(
            body.get("fixture_digests"), f"{expected_split}.fixture_digests"
        )
    ]
    lineage_refs = _unique_strings(
        body.get("lineage_refs"), f"{expected_split}.lineage_refs"
    )
    normalized = {
        "schema_version": DATASET_SPLIT_SCHEMA,
        "split": expected_split,
        "ref": ref,
        "digest": digest,
        "generation_ref": generation_ref,
        "case_ids": case_ids,
        "fixture_digests": fixture_digests,
        "lineage_refs": lineage_refs,
    }
    supplied = str(body.get("descriptor_digest") or "").strip()
    normalized["descriptor_digest"] = stable_digest(normalized)
    if supplied and normalize_digest(
        supplied, field=f"{expected_split}.descriptor_digest"
    ) != normalized["descriptor_digest"]:
        raise SkillOptimizerError(f"{expected_split} split descriptor digest drift")
    return normalized


def _selection_case_result_refs(
    value: object,
    selection: Mapping[str, Any],
) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise SkillOptimizerError("Selection evaluation requires case_result_refs")
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise SkillOptimizerError("Selection case_result_refs must be typed objects")
        case_id = str(item.get("case_id") or "").strip()
        ref = str(item.get("ref") or "").strip()
        if not case_id or case_id in seen or not ref:
            raise SkillOptimizerError("Selection case result identity is invalid")
        seen.add(case_id)
        rows.append({
            "case_id": case_id,
            "ref": ref,
            "digest": normalize_digest(
                item.get("digest"), field=f"Selection case {case_id} digest"
            ),
        })
    if seen != set(selection["case_ids"]):
        raise SkillOptimizerError("Selection case_result_refs must cover the exact split")
    return rows


def _unique_strings(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise SkillOptimizerError(f"{field} must be a non-empty list")
    rows = [str(item or "").strip() for item in value]
    if any(not item for item in rows) or len(set(rows)) != len(rows):
        raise SkillOptimizerError(f"{field} must contain unique non-empty values")
    return rows


def _normalize_edit(raw: object, index: int) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        raise SkillOptimizerError("Skill edit entries must be objects")
    operation = str(raw.get("operation") or "").strip()
    edit_id = str(raw.get("edit_id") or f"edit-{index + 1}").strip()
    if operation not in {"add", "delete", "replace"} or not _ID_RE.fullmatch(edit_id):
        raise SkillOptimizerError("Skill edit operation or edit_id is invalid")
    edit = {"edit_id": edit_id, "operation": operation}
    if operation == "add":
        anchor = str(raw.get("anchor") or "")
        text = str(raw.get("text") or "")
        position = str(raw.get("position") or "after")
        if not anchor or not text or position not in {"before", "after"}:
            raise SkillOptimizerError("add edit requires anchor, text, and before/after")
        edit.update({"anchor": anchor, "text": text, "position": position})
    else:
        old_text = str(raw.get("old_text") or "")
        new_text = str(raw.get("new_text") or "")
        if not old_text or (operation == "replace" and not new_text):
            raise SkillOptimizerError(f"{operation} edit text is incomplete")
        edit["old_text"] = old_text
        if operation == "replace":
            edit["new_text"] = new_text
    return edit


def _score_dimensions(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise SkillOptimizerError("Skill optimizer score_dimensions are required")
    rows: list[dict[str, Any]] = []
    ids: set[str] = set()
    for raw in value:
        if not isinstance(raw, Mapping):
            raise SkillOptimizerError("score_dimensions entries must be objects")
        dimension_id = str(raw.get("id") or "").strip()
        if not _ID_RE.fullmatch(dimension_id) or dimension_id in ids:
            raise SkillOptimizerError("score dimension ids must be unique and valid")
        weight = _finite_float(raw.get("weight", 1), f"weight {dimension_id}")
        if weight <= 0:
            raise SkillOptimizerError("score dimension weights must be positive")
        if str(raw.get("direction") or "maximize") != "maximize":
            raise SkillOptimizerError("initial Skill optimizer supports maximize dimensions only")
        ids.add(dimension_id)
        rows.append({
            "id": dimension_id,
            "weight": weight,
            "direction": "maximize",
            "blocking": bool(raw.get("blocking", False)),
        })
    if not any(row["blocking"] for row in rows):
        raise SkillOptimizerError("at least one blocking score dimension is required")
    return rows


def _bounded_int(value: object, name: str, lower: int, upper: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise SkillOptimizerError(f"{name} must be an integer") from exc
    if not lower <= result <= upper:
        raise SkillOptimizerError(f"{name} must be in [{lower}, {upper}]")
    return result


def _finite_float(value: object, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SkillOptimizerError(f"{name} must be numeric") from exc
    if not math.isfinite(result):
        raise SkillOptimizerError(f"{name} must be finite")
    return result


__all__ = [
    "CAMPAIGN_SCHEMA",
    "DATASET_SPLIT_SCHEMA",
    "EVALUATION_SCHEMA",
    "LEGACY_CAMPAIGN_SCHEMA",
    "MATERIAL_SCHEMA",
    "PROPOSAL_SCHEMA",
    "STATE_SCHEMA",
    "STEP_SCHEMA",
    "SkillOptimizerError",
    "apply_edits",
    "normalize_campaign",
    "normalize_dataset_splits",
    "normalize_evaluation",
    "normalize_proposal",
]
