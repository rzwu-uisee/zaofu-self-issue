"""Deterministic compile gate for refactor plan projections."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def compile_refactor_plan_projection(
    runtime: Any,
    projection: Any,
    *,
    pipeline_spec: Any,
    validate_pipeline: Any,
) -> Any:
    """Compile a refactor task map against the configured lane pipeline."""

    if projection is None or not getattr(projection, "ok", False):
        return projection
    payload = dict(getattr(projection, "payload", {}) or {})
    if payload.get("artifact_kind") != "refactor_plan":
        return projection
    task_map_ref = str(payload.get("task_map_ref") or "").strip()
    if not task_map_ref or pipeline_spec is None:
        if task_map_ref:
            payload["plan_compile_gate"] = "skipped"
            projection.payload.update(payload)
        return projection

    diagnostics: list[str] = []
    try:
        from zf.core.security.hash import sha256_file
        from zf.runtime.refactor_artifacts import RefactorArtifactProjection
        from zf.runtime.task_map import validate_task_map_payload
        from zf.runtime.writer_fanout_admission import (
            _resolve_artifact_ref,
            validate_writer_task_items,
            writer_task_items,
        )

        task_map_path = _resolve_artifact_ref(
            task_map_ref,
            state_dir=runtime.state_dir,
            project_root=runtime.project_root,
        )
        data = json.loads(task_map_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            task_map_validation = validate_task_map_payload(
                data,
                require_task_verification=False,
            )
            if not task_map_validation.passed:
                diagnostics.extend(
                    f"task_map: {error}"
                    for error in task_map_validation.errors
                )
        task_items = writer_task_items(data)
        validate_writer_task_items(task_items)
        problems = validate_pipeline(
            pipeline_spec,
            task_items,
            task_map_payload=data if isinstance(data, dict) else None,
        )
        diagnostics.extend(f"pipeline: {problem}" for problem in problems)
    except Exception as exc:
        diagnostics.append(f"plan compile failed: {exc}")

    if not diagnostics:
        payload["plan_compile_gate"] = "passed"
        projection.payload.update(payload)
        return projection

    artifact_dir = Path(
        str(payload.get("artifact_dir") or getattr(projection, "artifact_dir", ""))
    )
    if not artifact_dir:
        artifact_dir = runtime.state_dir / "artifacts" / "refactor-plan"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    diagnostics_path = artifact_dir / "artifact-gate-diagnostics.json"
    diagnostics_path.write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    artifact_refs = list(dict.fromkeys(
        [str(ref) for ref in payload.get("artifact_refs", []) or [] if str(ref)]
        + [str(diagnostics_path)]
    ))
    compile_reason = (
        "plan compile gate failed: " + "; ".join(diagnostics)
    )[:1000]
    findings = [
        dict(item)
        for item in payload.get("findings", []) or []
        if isinstance(item, dict)
    ]
    compile_finding = {
        "severity": "high",
        "category": "plan_compile_gate",
        "path": task_map_ref,
        "message": compile_reason,
    }
    if not any(
        str(item.get("message") or "") == compile_reason
        for item in findings
    ):
        findings.append(compile_finding)
    payload.update({
        "artifact_gate": "failed",
        "plan_compile_gate": "failed",
        "reason": compile_reason,
        "findings": findings,
        "diagnostics_ref": str(diagnostics_path),
        "artifact_refs": artifact_refs,
        "artifact_digests": {
            ref: sha256_file(Path(ref))
            for ref in artifact_refs
            if Path(ref).exists() and Path(ref).is_file()
        },
    })
    return RefactorArtifactProjection(
        status="failed",
        artifact_dir=str(artifact_dir),
        artifact_refs=artifact_refs,
        payload=payload,
        diagnostics=diagnostics,
    )


__all__ = ["compile_refactor_plan_projection"]
