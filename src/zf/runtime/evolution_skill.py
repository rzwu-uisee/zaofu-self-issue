"""Skill candidate and treatment materialization for evolution trials."""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.skills.materialize import materialize_role_skills
from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.evolution_contracts import (
    EvolutionContractError,
    normalize_digest,
    stable_digest,
)
from zf.runtime.evolution_skill_eval import (
    build_skill_treatment_identity,
    skill_evaluation_policy,
    validate_skill_eval_suite,
)


SKILL_CANDIDATE_SCHEMA = "skill-candidate.v1"
SKILL_TRIAL_SPEC_SCHEMA = "evolution-skill-trial-spec.v1"
_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,127}$")
_OUTCOMES = frozenset({"passed", "failed", "regressed", "neutral"})


def validate_skill_candidate(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate mechanical identity without judging the candidate method."""

    body = deepcopy(dict(raw))
    if body.get("schema_version") != SKILL_CANDIDATE_SCHEMA:
        raise EvolutionContractError(f"schema_version must be {SKILL_CANDIDATE_SCHEMA}")
    name = str(body.get("skill_name") or "").strip()
    if not _SKILL_NAME_RE.fullmatch(name):
        raise EvolutionContractError("Skill candidate name must be lowercase kebab-case")
    content = str(body.get("content") or "")
    _validate_skill_source(content, expected_name=name)
    digest = _content_digest(content)
    supplied = str(body.get("content_digest") or "").strip()
    if supplied and normalize_digest(
        supplied, field="Skill candidate content_digest"
    ) != digest:
        raise EvolutionContractError("Skill candidate content digest drift")
    task_families = _string_list(body.get("task_families"))
    if not task_families:
        raise EvolutionContractError("Skill candidate task_families are required")
    trajectories = _trajectory_rows(body.get("source_trajectories"))
    for ref_key, digest_key in (
        ("applicability_ref", "applicability_digest"),
        ("public_eval_suite_ref", "public_eval_suite_digest"),
    ):
        if not str(body.get(ref_key) or "").strip():
            raise EvolutionContractError(f"Skill candidate {ref_key} is required")
        body[digest_key] = normalize_digest(body.get(digest_key), field=digest_key)
    sealed = str(body.get("sealed_eval_generation_ref") or "").strip()
    if not sealed.startswith("sealed-evaluator://generation/"):
        raise EvolutionContractError(
            "Skill candidate sealed_eval_generation_ref must be opaque"
        )
    policy = skill_evaluation_policy(body)
    body.update(policy)
    body["skill_name"] = name
    body["content"] = content
    body["content_digest"] = digest
    body["task_families"] = task_families
    body["source_trajectories"] = trajectories
    body["candidate_version"] = str(body.get("candidate_version") or digest)
    return body


def build_skill_trial_spec(
    candidate: Mapping[str, Any],
    *,
    common_identity: Mapping[str, Any],
    eval_suite: Mapping[str, Any],
    current: Mapping[str, Any] | None = None,
    support_skills: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build raw/current/candidate arms without selecting a winner."""

    candidate_body = validate_skill_candidate(candidate)
    suite = validate_skill_eval_suite(eval_suite)
    if suite["evaluation_purpose"] != candidate_body["evaluation_purpose"]:
        raise EvolutionContractError("Skill candidate and eval suite purpose differ")
    if candidate_body["public_eval_suite_digest"] != suite["suite_digest"]:
        raise EvolutionContractError("Skill candidate public eval suite digest drift")
    supports = [_validate_support_skill(item) for item in support_skills]
    support_identity = sorted(
        [{"name": item["name"], "digest": item["digest"]} for item in supports],
        key=lambda item: item["name"],
    )
    expected_support_digest = normalize_digest(
        common_identity.get("support_skill_inventory_digest"),
        field="support_skill_inventory_digest",
    )
    if stable_digest(support_identity) != expected_support_digest:
        raise EvolutionContractError("support Skill inventory digest drift")
    target_name = str(candidate_body["skill_name"])
    semantic_material: dict[str, dict[str, Any]] = {
        "raw": {"kind": "raw", "skill_name": target_name},
        "candidate": {
            "kind": "skill",
            "skill_name": target_name,
            "content": candidate_body["content"],
            "digest": candidate_body["content_digest"],
            "version": candidate_body["candidate_version"],
        },
    }
    if current is not None:
        current_body = _validate_current_skill(current, expected_name=target_name)
        semantic_material["current"] = current_body
        arm_map = {
            "control": "raw",
            "baseline": "current",
            "candidate": "candidate",
        }
    else:
        arm_map = {"baseline": "raw", "candidate": "candidate"}
    treatment_identities: dict[str, dict[str, Any]] = {}
    logical_path_digest = stable_digest({
        "provider_target": "role-scoped-skill-root",
        "skill_name": target_name,
    })
    for semantic_arm, material in semantic_material.items():
        available = material["kind"] == "skill"
        treatment_identities[semantic_arm] = build_skill_treatment_identity(
            arm=semantic_arm,
            target_skill={
                "name": target_name,
                "available": available,
                "version": str(material.get("version") or ""),
                "digest": str(material.get("digest") or ""),
                "materialized_path_digest": logical_path_digest if available else "",
            },
            common_identity=common_identity,
            evaluation_purpose=str(candidate_body["evaluation_purpose"]),
        )
    return {
        "schema_version": SKILL_TRIAL_SPEC_SCHEMA,
        "skill_name": target_name,
        "evaluation_purpose": candidate_body["evaluation_purpose"],
        "routing_mode": candidate_body["routing_mode"],
        "task_families": list(candidate_body["task_families"]),
        "candidate_digest": candidate_body["content_digest"],
        "eval_suite": suite,
        "eval_suite_digest": suite["suite_digest"],
        "support_skills": supports,
        "support_skill_inventory_digest": expected_support_digest,
        "trial_arms": list(arm_map),
        "arm_map": arm_map,
        "arm_material": semantic_material,
        "treatment_identities": treatment_identities,
    }


def materialize_skill_trial_arm(
    *,
    workdir: Path,
    backend: str,
    spec: Mapping[str, Any],
    arm: str,
) -> dict[str, Any]:
    """Materialize one arm through the normal role Skill resolver."""

    if spec.get("schema_version") != SKILL_TRIAL_SPEC_SCHEMA:
        raise EvolutionContractError("Skill trial spec is invalid")
    arm_map = spec.get("arm_map")
    materials = spec.get("arm_material")
    if not isinstance(arm_map, Mapping) or not isinstance(materials, Mapping):
        raise EvolutionContractError("Skill trial spec arms are invalid")
    semantic_arm = str(arm_map.get(arm) or "")
    material = materials.get(semantic_arm)
    if not semantic_arm or not isinstance(material, Mapping):
        raise EvolutionContractError(f"Skill trial has no material for arm {arm}")
    if backend not in {"codex", "claude-code"}:
        raise EvolutionContractError("Skill trial backend must be codex or claude-code")
    root = Path(workdir).resolve(strict=False)
    source_root = root / "skills"
    support_skills = spec.get("support_skills")
    if not isinstance(support_skills, list):
        raise EvolutionContractError("Skill trial support_skills are invalid")
    role_skills: list[str] = []
    for item in support_skills:
        support = _validate_support_skill(item)
        _write_skill_source(source_root, support["name"], support["content"])
        role_skills.append(support["name"])
    target_name = str(spec.get("skill_name") or "")
    if str(material.get("kind") or "") == "skill":
        content = str(material.get("content") or "")
        _validate_skill_source(content, expected_name=target_name)
        if _content_digest(content) != str(material.get("digest") or ""):
            raise EvolutionContractError("Skill trial target content digest drift")
        _write_skill_source(source_root, target_name, content)
        role_skills.append(target_name)
    state_dir = root / ".zf-evolution-skill-trial"
    state_dir.mkdir(parents=True, exist_ok=True)
    role = RoleConfig(
        name="evolution-skill-evaluator",
        instance_id="evolution-skill-evaluator",
        backend=backend,
        skills=role_skills,
    )
    config = ZfConfig(
        project=ProjectConfig(
            name="evolution-skill-trial",
            state_dir=state_dir.name,
        ),
        roles=[role],
    )
    result = materialize_role_skills(
        config=config,
        project_root=root,
        state_dir=state_dir,
        role=role,
        task_id="evolution-skill-trial",
        execution_project_root=root,
    )
    entries = list(result.skills) if result is not None else []
    observed_support = sorted(
        [
            {"name": item.name, "digest": str(item.sha256 or "")}
            for item in entries
            if item.name != target_name
        ],
        key=lambda item: item["name"],
    )
    if stable_digest(observed_support) != str(
        spec.get("support_skill_inventory_digest") or ""
    ):
        raise EvolutionContractError("materialized support Skill inventory drift")
    target_entry = next((item for item in entries if item.name == target_name), None)
    available = str(material.get("kind") or "") == "skill"
    if available and (
        target_entry is None
        or target_entry.status != "resolved"
        or str(target_entry.sha256 or "") != str(material.get("digest") or "")
    ):
        raise EvolutionContractError("materialized target Skill identity drift")
    if not available and target_entry is not None:
        raise EvolutionContractError("raw arm unexpectedly materialized target Skill")
    identities = spec.get("treatment_identities")
    if not isinstance(identities, Mapping) or not isinstance(
        identities.get(semantic_arm), Mapping
    ):
        raise EvolutionContractError("Skill trial treatment identity is missing")
    manifest_path = Path(result.manifest_path) if result is not None else None
    if manifest_path is not None and not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    return {
        "schema_version": "skill-trial-materialization.v1",
        "trial_arm": arm,
        "semantic_arm": semantic_arm,
        "backend": backend,
        "manifest_path": str(manifest_path) if manifest_path is not None else "",
        "target_materialized": available,
        "target_path": str(target_entry.materialized_to or "") if target_entry else "",
        "support_skill_count": len(observed_support),
        "treatment_identity": deepcopy(dict(identities[semantic_arm])),
    }


def counterbalanced_arms(arms: Sequence[str], replicate: int) -> list[str]:
    values = [str(item) for item in arms if str(item)]
    if not values:
        raise EvolutionContractError("Skill trial requires arms")
    if int(replicate) < 1:
        raise EvolutionContractError("Skill trial replicate must be positive")
    offset = (int(replicate) - 1) % len(values)
    ordered = values[offset:] + values[:offset]
    if ((int(replicate) - 1) // len(values)) % 2:
        ordered.reverse()
    return ordered


def _validate_current_skill(
    raw: Mapping[str, Any],
    *,
    expected_name: str,
) -> dict[str, Any]:
    name = str(raw.get("skill_name") or expected_name).strip()
    if name != expected_name:
        raise EvolutionContractError("current Skill name differs from candidate")
    content = str(raw.get("content") or "")
    _validate_skill_source(content, expected_name=name)
    digest = _content_digest(content)
    supplied = str(raw.get("digest") or "").strip()
    if supplied and normalize_digest(supplied, field="current Skill digest") != digest:
        raise EvolutionContractError("current Skill digest drift")
    return {
        "kind": "skill",
        "skill_name": name,
        "content": content,
        "digest": digest,
        "version": str(raw.get("version") or digest),
    }


def _validate_support_skill(raw: Mapping[str, Any]) -> dict[str, str]:
    name = str(raw.get("name") or "").strip()
    if not _SKILL_NAME_RE.fullmatch(name):
        raise EvolutionContractError("support Skill name is invalid")
    content = str(raw.get("content") or "")
    _validate_skill_source(content, expected_name=name)
    digest = _content_digest(content)
    supplied = str(raw.get("digest") or "").strip()
    if supplied and normalize_digest(supplied, field=f"support Skill {name} digest") != digest:
        raise EvolutionContractError(f"support Skill {name} digest drift")
    return {"name": name, "content": content, "digest": digest}


def _validate_skill_source(content: str, *, expected_name: str) -> None:
    if not content.startswith("---\n"):
        raise EvolutionContractError("Skill source requires YAML frontmatter")
    lines = content.splitlines()
    try:
        end = lines[1:].index("---") + 1
    except ValueError as exc:
        raise EvolutionContractError("Skill source frontmatter is not closed") from exc
    try:
        frontmatter = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError as exc:
        raise EvolutionContractError("Skill source frontmatter is invalid") from exc
    if not isinstance(frontmatter, Mapping):
        raise EvolutionContractError("Skill source frontmatter must be an object")
    if str(frontmatter.get("name") or "") != expected_name:
        raise EvolutionContractError("Skill source frontmatter.name mismatch")
    if not str(frontmatter.get("description") or "").strip():
        raise EvolutionContractError("Skill source frontmatter.description is required")


def _trajectory_rows(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise EvolutionContractError("Skill candidate source_trajectories are required")
    rows: list[dict[str, str]] = []
    refs: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise EvolutionContractError("source_trajectories entries must be objects")
        ref = str(item.get("ref") or "").strip()
        outcome = str(item.get("outcome") or "").strip()
        if not ref or outcome not in _OUTCOMES or ref in refs:
            raise EvolutionContractError(
                "source_trajectories require unique refs and labelled outcomes"
            )
        digest = normalize_digest(
            item.get("digest"),
            field=f"source trajectory {ref} digest",
        )
        refs.add(ref)
        rows.append({"ref": ref, "digest": digest, "outcome": outcome})
    return rows


def _string_list(value: object) -> list[str]:
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


def _write_skill_source(root: Path, name: str, content: str) -> None:
    atomic_write_text(root / name / "SKILL.md", content)


def _content_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


__all__ = [
    "SKILL_CANDIDATE_SCHEMA",
    "SKILL_TRIAL_SPEC_SCHEMA",
    "build_skill_trial_spec",
    "counterbalanced_arms",
    "materialize_skill_trial_arm",
    "validate_skill_candidate",
]
