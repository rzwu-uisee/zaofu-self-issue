"""Production campaign contracts for Skill treatment evaluation."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from zf.core.config.schema import RoleConfig, ZfConfig
from zf.core.skills.provenance import resolve_skill_source
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.evolution_contracts import (
    EvolutionContractError,
    normalize_digest,
    stable_digest,
)
from zf.runtime.evolution_skill import (
    build_skill_trial_spec,
    counterbalanced_arms,
)
from zf.runtime.evolution_skill_eval import (
    validate_case_results,
    validate_skill_treatment_observation,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


ROUTING_STRESS_REPORT_SCHEMA = "skill-routing-stress-report.v1"
ROUTING_STRESS_OBSERVATION_SCHEMA = "skill-routing-observation.v1"


def validate_skill_routing_stress_report(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an immutable routing result without trusting its pass boolean."""

    body = deepcopy(dict(raw))
    if body.get("schema_version") != ROUTING_STRESS_REPORT_SCHEMA:
        raise EvolutionContractError(
            f"schema_version must be {ROUTING_STRESS_REPORT_SCHEMA}"
        )
    for key in ("skill_name", "candidate_digest", "eval_suite_digest"):
        if not str(body.get(key) or "").strip():
            raise EvolutionContractError(f"routing stress report requires {key}")
    required = _positive_ints(body.get("required_pool_sizes"), "required_pool_sizes")
    executed = _positive_ints(body.get("executed_pool_sizes"), "executed_pool_sizes")
    if not required or not set(required).issubset(executed):
        raise EvolutionContractError(
            "routing stress report did not execute every required pool size"
        )
    negative = _strings(body.get("negative_case_ids"))
    confusable = _strings(body.get("confusable_case_ids"))
    if not negative and not confusable:
        raise EvolutionContractError(
            "routing stress report requires negative or confusable cases"
        )
    observations = body.get("observation_refs")
    if not isinstance(observations, list) or not observations:
        raise EvolutionContractError("routing stress report requires observation_refs")
    normalized_refs: list[dict[str, Any]] = []
    for descriptor in observations:
        if not isinstance(descriptor, Mapping):
            raise EvolutionContractError(
                "routing stress observation refs must be sidecar descriptors"
            )
        ref = str(descriptor.get("ref") or "").strip()
        digest = str(descriptor.get("sha256") or "").strip()
        if not ref or not digest:
            raise EvolutionContractError(
                "routing stress observation refs require ref and sha256"
            )
        normalized_refs.append(dict(descriptor))
    try:
        overtrigger_count = int(body.get("overtrigger_count") or 0)
    except (TypeError, ValueError) as exc:
        raise EvolutionContractError(
            "routing stress overtrigger_count must be an integer"
        ) from exc
    if overtrigger_count < 0:
        raise EvolutionContractError(
            "routing stress overtrigger_count must be non-negative"
        )
    derived_status = "passed" if overtrigger_count == 0 else "failed"
    if str(body.get("status") or "") != derived_status:
        raise EvolutionContractError(
            "routing stress status is inconsistent with observations"
        )
    body.update({
        "required_pool_sizes": required,
        "executed_pool_sizes": executed,
        "negative_case_ids": negative,
        "confusable_case_ids": confusable,
        "observation_refs": normalized_refs,
        "overtrigger_count": overtrigger_count,
        "status": derived_status,
    })
    supplied = str(body.pop("report_digest", "") or "")
    body["report_digest"] = stable_digest(body)
    if supplied and supplied.removeprefix("sha256:") != body["report_digest"]:
        raise EvolutionContractError("routing stress report digest drift")
    return body


def verify_skill_routing_stress_ref(
    state_dir: Path,
    descriptor: Mapping[str, Any],
    *,
    expected_skill: str = "",
    expected_candidate_digest: str = "",
    expected_eval_suite_digest: str = "",
) -> dict[str, Any]:
    hydrated = hydrate_sidecar_ref(
        Path(state_dir),
        dict(descriptor),
        purpose="skill-routing-stress-admission",
        actor="evolution-coordinator",
    )
    if not isinstance(hydrated.payload, Mapping):
        raise EvolutionContractError("routing stress sidecar must contain an object")
    report = validate_skill_routing_stress_report(hydrated.payload)
    expected = {
        "skill_name": expected_skill,
        "candidate_digest": expected_candidate_digest.removeprefix("sha256:"),
        "eval_suite_digest": expected_eval_suite_digest.removeprefix("sha256:"),
    }
    for key, value in expected.items():
        if value and str(report.get(key) or "").removeprefix("sha256:") != value:
            raise EvolutionContractError(f"routing stress {key} is stale")
    facts = _routing_observation_facts(
        Path(state_dir),
        report["observation_refs"],
        expected_skill=str(report["skill_name"]),
        expected_candidate_digest=str(report["candidate_digest"]),
        expected_eval_suite_digest=str(report["eval_suite_digest"]),
    )
    if facts["executed_pool_sizes"] != report["executed_pool_sizes"]:
        raise EvolutionContractError("routing stress executed pool sizes are stale")
    if facts["overtrigger_count"] != report["overtrigger_count"]:
        raise EvolutionContractError("routing stress overtrigger count is stale")
    if not set(report["negative_case_ids"]).issubset(facts["negative_case_ids"]):
        raise EvolutionContractError("routing stress negative case evidence is incomplete")
    if not set(report["confusable_case_ids"]).issubset(facts["confusable_case_ids"]):
        raise EvolutionContractError("routing stress confusable case evidence is incomplete")
    if report["status"] != "passed":
        raise EvolutionContractError("routing stress report did not pass")
    return report


def build_skill_routing_stress_report(
    state_dir: Path,
    *,
    skill_name: str,
    candidate_digest: str,
    eval_suite_digest: str,
    required_pool_sizes: Sequence[int],
    observation_refs: Sequence[Mapping[str, Any]],
    created_by: str = "autoresearch",
) -> dict[str, Any]:
    """Build a report only from hydrated, mechanically recomputed observations."""

    refs = [dict(item) for item in observation_refs]
    facts = _routing_observation_facts(
        Path(state_dir),
        refs,
        expected_skill=skill_name,
        expected_candidate_digest=candidate_digest,
        expected_eval_suite_digest=eval_suite_digest,
    )
    report = validate_skill_routing_stress_report({
        "schema_version": ROUTING_STRESS_REPORT_SCHEMA,
        "skill_name": skill_name,
        "candidate_digest": candidate_digest.removeprefix("sha256:"),
        "eval_suite_digest": eval_suite_digest.removeprefix("sha256:"),
        "required_pool_sizes": list(required_pool_sizes),
        "executed_pool_sizes": facts["executed_pool_sizes"],
        "negative_case_ids": sorted(facts["negative_case_ids"]),
        "confusable_case_ids": sorted(facts["confusable_case_ids"]),
        "observation_refs": refs,
        "overtrigger_count": facts["overtrigger_count"],
        "status": "passed" if facts["overtrigger_count"] == 0 else "failed",
    })
    descriptor = write_immutable_json_sidecar(
        Path(state_dir),
        report,
        root="evolution/routing-stress",
        kind="skill_routing_stress_report",
        schema_version=ROUTING_STRESS_REPORT_SCHEMA,
        created_by=created_by,
    )
    return {"report": report, "report_ref": descriptor}


def _routing_observation_facts(
    state_dir: Path,
    refs: Sequence[Mapping[str, Any]],
    *,
    expected_skill: str,
    expected_candidate_digest: str,
    expected_eval_suite_digest: str,
) -> dict[str, Any]:
    pool_sizes: set[int] = set()
    negative: set[str] = set()
    confusable: set[str] = set()
    overtrigger_count = 0
    for descriptor in refs:
        hydrated = hydrate_sidecar_ref(
            state_dir,
            dict(descriptor),
            purpose="skill-routing-stress-observation",
            actor="evolution-coordinator",
        )
        payload = hydrated.payload
        if not isinstance(payload, Mapping) or payload.get("schema_version") != ROUTING_STRESS_OBSERVATION_SCHEMA:
            raise EvolutionContractError("routing stress observation schema is invalid")
        expected = {
            "skill_name": expected_skill,
            "candidate_digest": expected_candidate_digest.removeprefix("sha256:"),
            "eval_suite_digest": expected_eval_suite_digest.removeprefix("sha256:"),
        }
        for key, value in expected.items():
            if str(payload.get(key) or "").removeprefix("sha256:") != value:
                raise EvolutionContractError(f"routing observation {key} is stale")
        try:
            pool_size = int(payload.get("pool_size") or 0)
        except (TypeError, ValueError) as exc:
            raise EvolutionContractError("routing observation pool_size is invalid") from exc
        if pool_size < 1:
            raise EvolutionContractError("routing observation pool_size must be positive")
        treatment = payload.get("treatment")
        if not isinstance(treatment, Mapping):
            raise EvolutionContractError("routing observation treatment is missing")
        observed = validate_skill_treatment_observation(treatment)
        target = observed["identity"]["target_skill"]
        if (
            str(target.get("name") or "") != expected_skill
            or str(target.get("digest") or "") != expected["candidate_digest"]
        ):
            raise EvolutionContractError("routing observation target Skill is stale")
        pool_sizes.add(pool_size)
        overtrigger_count += int(observed["overtrigger_count"])
        for case in observed["cases"]:
            if case["case_kind"] == "negative":
                negative.add(str(case["case_id"]))
            elif case["case_kind"] == "confusable":
                confusable.add(str(case["case_id"]))
    return {
        "executed_pool_sizes": sorted(pool_sizes),
        "negative_case_ids": negative,
        "confusable_case_ids": confusable,
        "overtrigger_count": overtrigger_count,
    }


def specialize_skill_campaign(
    campaign: Mapping[str, Any],
    *,
    state_dir: Path,
    project_root: Path,
    config: ZfConfig,
    candidate: Mapping[str, Any],
    source_event_id: str,
    deposition_ref: Mapping[str, Any],
) -> dict[str, Any]:
    """Turn the generic campaign envelope into a frozen Skill treatment run."""

    body = deepcopy(dict(campaign))
    skill_candidate = candidate.get("skill_candidate")
    suite = candidate.get("skill_eval_suite")
    if not isinstance(skill_candidate, Mapping) or not isinstance(suite, Mapping):
        raise EvolutionContractError(
            "skill_prompt campaign requires validated candidate and eval suite"
        )
    role = _role(config, str(candidate.get("role_instance") or ""))
    routing_pool = suite.get("routing_pool")
    routing_names: list[str] = []
    if isinstance(routing_pool, Mapping):
        for category in ("support_skills", "decoy_skills", "confusable_skills"):
            routing_names.extend(
                str(item.get("name") or "")
                for item in routing_pool.get(category) or []
                if isinstance(item, Mapping)
            )
    support_names = _strings([
        *_strings(candidate.get("support_skill_names")),
        *routing_names,
    ])
    support_names = [
        name for name in support_names
        if name != str(skill_candidate["skill_name"])
    ]
    supports = _support_skills(
        project_root=Path(project_root),
        state_dir=Path(state_dir),
        config=config,
        names=support_names,
    )
    current = _current_skill(
        project_root=Path(project_root),
        state_dir=Path(state_dir),
        config=config,
        name=str(skill_candidate["skill_name"]),
    )
    support_identity = sorted(
        [{"name": item["name"], "digest": item["digest"]} for item in supports],
        key=lambda item: item["name"],
    )
    attempt = body.get("attempt")
    if not isinstance(attempt, dict):
        raise EvolutionContractError("evolution campaign attempt is missing")
    frozen = attempt.get("frozen_inputs")
    if not isinstance(frozen, dict):
        raise EvolutionContractError("evolution campaign frozen inputs are missing")
    evaluator = body.get("evaluator")
    if not isinstance(evaluator, Mapping):
        raise EvolutionContractError("evolution campaign evaluator is missing")
    identity_fields = set(evaluator.get("comparison_identity_fields") or [])
    if "skill_treatment_spec_digest" not in identity_fields:
        raise EvolutionContractError(
            "Skill evaluator must freeze skill_treatment_spec_digest"
        )
    common_identity = {
        "support_skill_inventory_digest": stable_digest(support_identity),
        "role_profile_digest": stable_digest(asdict(role)),
        "briefing_digest": str(deposition_ref.get("sha256") or ""),
        "prompt_digest": stable_digest({
            "scenario_set_digest": evaluator.get("scenario_set_digest"),
            "case_ids": [row.get("case_id") for row in suite.get("cases") or []],
        }),
        "workspace_fixture_digest": stable_digest([
            {
                "case_id": row.get("case_id"),
                "fixture_digest": row.get("fixture_digest") or "",
            }
            for row in suite.get("cases") or []
            if isinstance(row, Mapping)
        ]),
        "tool_policy_digest": str(frozen.get("sandbox_policy_digest") or ""),
        "eval_suite_generation_digest": str(suite.get("suite_digest") or ""),
    }
    spec = build_skill_trial_spec(
        skill_candidate,
        common_identity=common_identity,
        eval_suite=suite,
        current=current,
        support_skills=supports,
    )
    spec_descriptor = write_immutable_json_sidecar(
        Path(state_dir),
        spec,
        root="evolution/skill-trial-specs",
        kind="evolution_skill_trial_spec",
        schema_version=str(spec["schema_version"]),
        created_by="run-manager",
        source_event_id=source_event_id,
    )
    spec_digest = str(spec_descriptor["sha256"])
    comparison_identity = dict(body.get("comparison_identity") or {})
    comparison_identity["skill_treatment_spec_digest"] = spec_digest
    body["comparison_identity"] = comparison_identity
    frozen.update({
        "skill_treatment_spec_ref": str(spec_descriptor["ref"]),
        "skill_treatment_spec_digest": spec_digest,
    })
    mutation = attempt.get("mutation")
    if not isinstance(mutation, dict):
        raise EvolutionContractError("evolution campaign mutation is missing")
    mutation.update({
        "object_kind": "skill_prompt",
        "skill_name": str(spec["skill_name"]),
        "base_version": str((current or {}).get("digest") or stable_digest({
            "skill_name": spec["skill_name"],
            "base": "absent",
        })),
        "candidate_version": str(spec["candidate_digest"]),
    })
    purpose = str(spec["evaluation_purpose"])
    attempt["adoption_claim"] = (
        "persistent_capability" if purpose == "adoption_lift" else "experiment_only"
    )
    attempt["evidence_kinds"] = list(dict.fromkeys([
        *list(attempt.get("evidence_kinds") or []),
        "skill_treatment",
        "routing",
    ]))
    source_identity = attempt.get("source_identity")
    if isinstance(source_identity, dict):
        source_identity["skill_lock_ref"] = str(spec_descriptor["ref"])
        source_identity["skill_lock_digest"] = spec_digest
    evaluation = attempt.get("evaluation_policy")
    if not isinstance(evaluation, dict):
        raise EvolutionContractError("evolution campaign evaluation policy is missing")
    evaluation.update({
        "pairing_key": stable_digest(comparison_identity),
        "evaluation_purpose": purpose,
    })
    if purpose == "adoption_lift":
        routing_descriptor = candidate.get("routing_stress_ref")
        if not isinstance(routing_descriptor, Mapping):
            raise EvolutionContractError(
                "adoption_lift requires an immutable routing_stress_ref"
            )
        routing = verify_skill_routing_stress_ref(
            Path(state_dir),
            routing_descriptor,
            expected_skill=str(spec["skill_name"]),
            expected_candidate_digest=str(spec["candidate_digest"]),
            expected_eval_suite_digest=str(spec["eval_suite_digest"]),
        )
        evaluation["skill_adoption"] = {
            "min_distinct_cases": max(
                3, int(candidate.get("min_distinct_cases") or 3)
            ),
            "min_replicates_per_case": max(
                2, int(candidate.get("min_replicates_per_case") or 2)
            ),
            "required_arms": sorted(set(spec["arm_map"].values())),
            "routing_stress_ref": str(routing_descriptor["ref"]),
            "routing_stress_digest": str(routing_descriptor["sha256"]),
            "routing_stress_status": str(routing["status"]),
        }
    repetitions = max(
        int(body.get("trial_repetitions") or 1),
        int(evaluation.get("min_trials") or 1),
        int(candidate.get("min_replicates_per_case") or 1),
    )
    body["trial_repetitions"] = repetitions
    body["trial_arms"] = list(spec["trial_arms"])
    body["skill_trial_spec"] = spec
    body["skill_trial_spec_ref"] = spec_descriptor
    body["role_instance"] = role.instance_id
    asset = body.get("asset")
    if not isinstance(asset, dict):
        raise EvolutionContractError("evolution campaign asset is missing")
    asset.update({
        "asset_kind": "skill_prompt",
        "skill_name": str(spec["skill_name"]),
        "digest": str(spec["candidate_digest"]),
        "content": str(skill_candidate["content"]),
    })
    activation = asset.get("activation")
    if not isinstance(activation, dict):
        raise EvolutionContractError("Skill asset activation is missing")
    activation.update({
        "mode": "proposal_only",
        "overlay_mode": "scoped_overlay",
        "skill_name": str(spec["skill_name"]),
        "owner_approval_required": True,
        "auto_inject": bool(candidate.get("auto_inject", False)),
        "scope": _scope(candidate, role=role, task_family=str(candidate["task_family"])),
        "previous_digest": str((current or {}).get("digest") or ""),
        "expires_at": str(
            candidate.get("canary_expires_at")
            or asset.get("quality", {}).get("expires_at")
            or "2099-01-01T00:00:00+00:00"
        ),
        "budget": deepcopy(dict(attempt.get("budget") or {})),
    })
    rollback = asset.get("rollback")
    if isinstance(rollback, dict):
        rollback["previous_digest"] = str((current or {}).get("digest") or "")
    return body


def skill_trial_arm_order(campaign: Mapping[str, Any], replicate: int) -> list[str]:
    arms = campaign.get("trial_arms")
    if not isinstance(arms, list) or not arms:
        return ["baseline", "candidate"]
    return counterbalanced_arms([str(item) for item in arms], replicate)


def verify_skill_attempt_evidence(
    state_dir: Path,
    attempt: Mapping[str, Any],
) -> dict[str, Any]:
    mutation = attempt.get("mutation")
    if not isinstance(mutation, Mapping) or mutation.get("object_kind") != "skill_prompt":
        return {}
    frozen = attempt.get("frozen_inputs")
    if not isinstance(frozen, Mapping):
        raise EvolutionContractError("Skill attempt frozen inputs are missing")
    spec_ref = str(frozen.get("skill_treatment_spec_ref") or "")
    spec_digest = str(frozen.get("skill_treatment_spec_digest") or "")
    hydrated = hydrate_sidecar_ref(
        Path(state_dir),
        {"ref": spec_ref, "sha256": spec_digest},
        purpose="skill-attempt-currentness",
        actor="evolution-coordinator",
    )
    spec = hydrated.payload
    if not isinstance(spec, Mapping) or spec.get("schema_version") != "evolution-skill-trial-spec.v1":
        raise EvolutionContractError("Skill treatment spec sidecar is invalid")
    if str(spec.get("skill_name") or "") != str(mutation.get("skill_name") or ""):
        raise EvolutionContractError("Skill treatment spec name differs from attempt")
    if str(spec.get("candidate_digest") or "") != str(
        mutation.get("candidate_version") or ""
    ):
        raise EvolutionContractError("Skill treatment spec candidate is stale")
    evaluation = attempt.get("evaluation_policy")
    if not isinstance(evaluation, Mapping):
        raise EvolutionContractError("Skill attempt evaluation policy is missing")
    if str(evaluation.get("evaluation_purpose") or "") == "adoption_lift":
        policy = evaluation.get("skill_adoption")
        if not isinstance(policy, Mapping):
            raise EvolutionContractError("Skill adoption policy is missing")
        verify_skill_routing_stress_ref(
            Path(state_dir),
            {
                "ref": str(policy.get("routing_stress_ref") or ""),
                "sha256": str(policy.get("routing_stress_digest") or ""),
            },
            expected_skill=str(spec.get("skill_name") or ""),
            expected_candidate_digest=str(spec.get("candidate_digest") or ""),
            expected_eval_suite_digest=str(spec.get("eval_suite_digest") or ""),
        )
    return dict(spec)


def verify_skill_trial_settlement(
    state_dir: Path,
    *,
    attempt: Mapping[str, Any],
    trial: Mapping[str, Any],
    measurement: Mapping[str, Any],
    archive_path: Path,
    archive_digest: str,
) -> None:
    """Bind Skill measurement facts to its trial identity and Run Archive."""

    spec = verify_skill_attempt_evidence(state_dir, attempt)
    if not spec:
        return
    arm = str(trial.get("arm") or "")
    arm_map = spec.get("arm_map")
    identities = spec.get("treatment_identities")
    if not isinstance(arm_map, Mapping) or not isinstance(identities, Mapping):
        raise EvolutionContractError("Skill trial treatment map is invalid")
    semantic_arm = str(arm_map.get(arm) or "")
    expected_identity = identities.get(semantic_arm)
    treatment = measurement.get("treatment")
    if not isinstance(expected_identity, Mapping) or not isinstance(treatment, Mapping):
        raise EvolutionContractError("Skill settlement lacks treatment evidence")
    observed = validate_skill_treatment_observation(treatment)
    if stable_digest(observed["identity"]) != stable_digest(expected_identity):
        raise EvolutionContractError("Skill settlement treatment identity drift")
    pairing = measurement.get("pairing")
    if not isinstance(pairing, Mapping) or int(pairing.get("replicate") or 0) != int(
        trial.get("replicate") or 0
    ):
        raise EvolutionContractError("Skill settlement replicate identity drift")
    normalized_archive_digest = normalize_digest(
        archive_digest, field="Skill settlement archive_digest"
    )
    if str(measurement.get("evidence_archive_ref") or "") != str(archive_path):
        raise EvolutionContractError("Skill measurement archive ref drift")
    if normalize_digest(
        measurement.get("evidence_archive_digest"),
        field="Skill measurement evidence_archive_digest",
    ) != normalized_archive_digest:
        raise EvolutionContractError("Skill measurement archive digest drift")
    evidence_path = archive_path.parent / "supplemental" / "provider-outputs.json"
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvolutionContractError(
            "Skill trial archive lacks valid provider-outputs evidence"
        ) from exc
    if not isinstance(evidence, Mapping):
        raise EvolutionContractError("Skill provider evidence must be an object")
    archived_treatment = evidence.get("treatment")
    if not isinstance(archived_treatment, Mapping) or stable_digest(
        archived_treatment
    ) != stable_digest(observed):
        raise EvolutionContractError("Skill treatment differs from archived evidence")
    archived_evaluation = evidence.get("evaluation")
    if not isinstance(archived_evaluation, Mapping) or stable_digest(
        validate_case_results(archived_evaluation.get("case_results"))
    ) != stable_digest(validate_case_results(measurement.get("case_results"))):
        raise EvolutionContractError("Skill case results differ from archived evidence")
    _verify_archived_materializations(
        spec=spec,
        semantic_arm=semantic_arm,
        evidence=evidence,
        treatment=observed,
    )


def _verify_archived_materializations(
    *,
    spec: Mapping[str, Any],
    semantic_arm: str,
    evidence: Mapping[str, Any],
    treatment: Mapping[str, Any],
) -> None:
    outputs = evidence.get("outputs")
    materials = evidence.get("materializations")
    cases = treatment.get("cases")
    if not isinstance(outputs, list) or not isinstance(materials, list):
        raise EvolutionContractError("Skill archive lacks materialization evidence")
    if not isinstance(cases, list) or len(outputs) != len(materials) or len(outputs) != len(cases):
        raise EvolutionContractError("Skill materialization/case evidence count differs")
    expected_identity = (spec.get("treatment_identities") or {}).get(semantic_arm)
    target_digest = str(
        ((expected_identity or {}).get("target_skill") or {}).get("digest") or ""
    )
    loaded_ids: set[str] = set()
    for output, material in zip(outputs, materials, strict=True):
        if not isinstance(output, Mapping) or not isinstance(material, Mapping):
            raise EvolutionContractError("Skill materialization evidence is malformed")
        if stable_digest(material.get("treatment_identity") or {}) != stable_digest(
            expected_identity or {}
        ):
            raise EvolutionContractError("archived Skill treatment identity drift")
        manifest = material.get("manifest")
        if not isinstance(manifest, Mapping):
            raise EvolutionContractError("Skill archive lacks resolver manifest")
        target_rows = [
            item for item in manifest.get("skills") or []
            if isinstance(item, Mapping)
            and str(item.get("name") or "") == str(spec.get("skill_name") or "")
        ]
        available = bool((expected_identity or {}).get("target_skill", {}).get("available"))
        if available:
            if len(target_rows) != 1 or str(target_rows[0].get("sha256") or "") != target_digest:
                raise EvolutionContractError("resolver manifest target Skill drift")
        elif target_rows:
            raise EvolutionContractError("raw arm resolver manifest contains target Skill")
        if bool(output.get("target_skill_loaded")):
            if not output.get("skill_load_evidence"):
                raise EvolutionContractError("Skill loaded claim lacks provider trace")
            loaded_ids.add(str(output.get("case_id") or ""))
    observed_loaded = {
        str(item.get("case_id") or "")
        for item in cases
        if isinstance(item, Mapping) and bool(item.get("loaded"))
    }
    if loaded_ids != observed_loaded:
        raise EvolutionContractError("Skill loaded observations differ from provider trace")


def _role(config: ZfConfig, instance_id: str) -> RoleConfig:
    for role in config.roles:
        if role.instance_id == instance_id:
            return role
    raise EvolutionContractError(
        f"Skill evolution role_instance is not configured: {instance_id or '<empty>'}"
    )


def _support_skills(
    *,
    project_root: Path,
    state_dir: Path,
    config: ZfConfig,
    names: Sequence[str],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for name in names:
        path = resolve_skill_source(
            project_root=project_root,
            state_dir=state_dir,
            name=name,
            config=config,
        )
        if path is None:
            raise EvolutionContractError(f"support Skill is unavailable: {name}")
        content = path.read_text(encoding="utf-8")
        rows.append({
            "name": name,
            "content": content,
            "digest": _content_digest(content),
        })
    return rows


def _current_skill(
    *,
    project_root: Path,
    state_dir: Path,
    config: ZfConfig,
    name: str,
) -> dict[str, str] | None:
    path = resolve_skill_source(
        project_root=project_root,
        state_dir=state_dir,
        name=name,
        config=config,
    )
    if path is None:
        return None
    content = path.read_text(encoding="utf-8")
    digest = _content_digest(content)
    return {
        "skill_name": name,
        "content": content,
        "digest": digest,
        "version": digest,
    }


def _scope(
    candidate: Mapping[str, Any],
    *,
    role: RoleConfig,
    task_family: str,
) -> dict[str, Any]:
    supplied = candidate.get("canary_scope")
    scope = deepcopy(dict(supplied)) if isinstance(supplied, Mapping) else {}
    scope["roles"] = _strings(scope.get("roles")) or [role.instance_id]
    scope["task_families"] = _strings(scope.get("task_families")) or [task_family]
    scope["cohorts"] = _strings(scope.get("cohorts"))
    return scope


def _positive_ints(value: object, field: str) -> list[int]:
    if not isinstance(value, list):
        raise EvolutionContractError(f"{field} must be a list")
    rows: list[int] = []
    for item in value:
        try:
            number = int(item)
        except (TypeError, ValueError) as exc:
            raise EvolutionContractError(f"{field} must contain integers") from exc
        if number < 1:
            raise EvolutionContractError(f"{field} must contain positive integers")
        if number not in rows:
            rows.append(number)
    return sorted(rows)


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _content_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


__all__ = [
    "ROUTING_STRESS_REPORT_SCHEMA",
    "ROUTING_STRESS_OBSERVATION_SCHEMA",
    "build_skill_routing_stress_report",
    "skill_trial_arm_order",
    "specialize_skill_campaign",
    "validate_skill_routing_stress_report",
    "verify_skill_attempt_evidence",
    "verify_skill_trial_settlement",
    "verify_skill_routing_stress_ref",
]
