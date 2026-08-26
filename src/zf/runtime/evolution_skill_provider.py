"""Provider-facing prompt and evidence helpers for Skill treatment trials."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from zf.runtime.evolution_contracts import EvolutionContractError, stable_digest


def assert_skill_case_identity(
    spec: Mapping[str, Any],
    cases: list[dict[str, Any]],
) -> None:
    suite = spec.get("eval_suite")
    expected_rows = suite.get("cases") if isinstance(suite, Mapping) else None
    if not isinstance(expected_rows, list):
        raise EvolutionContractError("Skill trial eval suite has no cases")
    expected = {
        str(item.get("case_id") or ""): (
            str(item.get("case_kind") or ""),
            str(item.get("treatment") or ""),
        )
        for item in expected_rows
        if isinstance(item, Mapping)
    }
    observed = {
        str(item.get("case_id") or ""): (
            str(item.get("case_kind") or ""),
            str(item.get("treatment") or ""),
        )
        for item in cases
        if isinstance(item, Mapping)
    }
    if observed != expected or "" in observed:
        raise EvolutionContractError(
            "sealed Skill cases differ from the frozen public suite identity"
        )


def skill_trial_prompt(
    spec: Mapping[str, Any],
    case: Mapping[str, Any],
) -> str:
    purpose = str(spec.get("evaluation_purpose") or "")
    skill_name = str(spec.get("skill_name") or "")
    instruction = ""
    if purpose in {"treatment_smoke", "content_lift"}:
        instruction = (
            f"Use the available ${skill_name} Skill when it is present. "
            "Read its SKILL.md before solving. "
        )
    return (
        instruction
        + "Solve the task in the current isolated workspace. "
        + "Return only the requested answer; do not discuss the evaluation.\n\n"
        + f"Task:\n{str(case.get('prompt') or '')}"
    )


def skill_load_evidence(
    *,
    stdout: str,
    stderr: str,
    skill_name: str,
    target_path: str,
) -> list[dict[str, str]]:
    if not skill_name or not target_path:
        return []
    targets = {
        str(Path(target_path).resolve(strict=False)),
        str(Path(target_path).resolve(strict=False) / "SKILL.md"),
    }
    evidence: list[dict[str, str]] = []
    for line_number, line in enumerate((stdout + "\n" + stderr).splitlines(), start=1):
        text = line.strip()
        if not text or not any(target in text for target in targets):
            continue
        lowered = text.lower()
        if not any(
            token in lowered for token in ("read", "cat ", "sed ", "head ", "skill")
        ):
            continue
        evidence.append({
            "kind": "provider_skill_read",
            "skill": skill_name,
            "line": str(line_number),
            "digest": stable_digest(text),
        })
    return evidence


def codex_skill_isolation_args(
    *,
    environment: Mapping[str, str],
) -> list[str]:
    """Keep auth in the operator home while excluding non-trial Skill treatments."""

    codex_home = Path(
        environment.get("CODEX_HOME") or Path.home() / ".codex"
    ).resolve(strict=False)
    skill_paths = sorted(
        path.resolve(strict=False)
        for path in (codex_home / "skills").rglob("SKILL.md")
        if path.is_file()
    )
    entries = ",".join(
        "{path=" + json.dumps(str(path)) + ",enabled=false}"
        for path in skill_paths
    )
    args = ["--config", "skills.bundled.enabled=false"]
    if entries:
        args.extend(["--config", f"skills.config=[{entries}]"])
    return args


__all__ = [
    "assert_skill_case_identity",
    "codex_skill_isolation_args",
    "skill_load_evidence",
    "skill_trial_prompt",
]
