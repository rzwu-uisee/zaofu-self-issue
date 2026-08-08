"""Immutable output artifacts for Generic Workflow stage results."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import (
    call_result_envelope_ref,
    hydrate_call_result_envelope,
    write_immutable_json_sidecar,
)
from zf.runtime.sidecar_refs import (
    hydrate_sidecar_ref,
    sidecar_path,
    write_sidecar_text,
)


OUTPUT_ARTIFACT_SCHEMA = "generic-workflow-output.v1"
_PLACEHOLDER_BODIES = frozenset({
    "Short outcome summary.",
    "Short synthesis summary.",
    "Replace with the final report.",
})


def materialize_declared_workflow_outputs(
    state_dir: Path,
    event: ZfEvent,
    control_result: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Persist agent-authored declared outputs without inventing semantics."""

    result = dict(control_result)
    if (
        str(result.get("result_semantics") or "").strip().lower()
        != "artifact_production"
        or not str(result.get("generic_workflow_operation") or "").strip()
    ):
        return result, []
    ports = result.get("workflow_output_ports")
    if not isinstance(ports, list):
        return result, [{
            "field": "control_result.workflow_output_ports",
            "code": "missing_required",
            "message": "artifact production requires declared output ports",
        }]
    workflow_run_id = str(
        result.get("workflow_run_id")
        or event.correlation_id
        or ""
    ).strip()
    event_payload = event.payload if isinstance(event.payload, Mapping) else {}
    stage_id = str(
        result.get("stage_id") or event_payload.get("stage_id") or ""
    ).strip()
    issues: list[dict[str, str]] = []
    artifacts: list[dict[str, Any]] = []
    for index, raw_port in enumerate(ports):
        if not isinstance(raw_port, Mapping):
            issues.append({
                "field": f"control_result.workflow_output_ports[{index}]",
                "code": "schema_invalid",
                "message": "output port must be an object",
            })
            continue
        name = str(raw_port.get("name") or "").strip()
        kind = str(raw_port.get("kind") or "").strip()
        if not workflow_run_id or not stage_id or not name or not kind:
            issues.append({
                "field": f"control_result.workflow_output_ports[{index}]",
                "code": "missing_required",
                "message": "workflow_run_id, stage_id, name, and kind are required",
            })
            continue
        body = _output_body(result, name=name)
        if body in (None, "", [], {}) or (
            isinstance(body, str) and body.strip() in _PLACEHOLDER_BODIES
        ):
            issues.append({
                "field": f"control_result.workflow_output_ports[{index}]",
                "code": "workflow_output_missing",
                "message": f"{stage_id}.{name}",
            })
            continue
        descriptor = _write_output(
            Path(state_dir),
            workflow_run_id=workflow_run_id,
            stage_id=stage_id,
            name=name,
            kind=kind,
            body=body,
            source_event_id=event.id,
        )
        artifacts.append({
            **descriptor,
            "name": name,
            "kind": kind,
            "source_ref": f"{stage_id}.{name}",
            "producer_stage_id": stage_id,
        })
    if artifacts:
        result["output_artifacts"] = artifacts
    return result, issues


def resolve_declared_output_artifacts(
    state_dir: Path,
    *,
    input_result_refs: list[str],
    required_artifacts: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve required outputs only through admitted call-result envelopes."""

    available: dict[str, dict[str, Any]] = {}
    for value in input_result_refs:
        ref = call_result_envelope_ref(value)
        if not ref:
            continue
        try:
            envelope = hydrate_call_result_envelope(
                Path(state_dir),
                {"ref": ref, "sha256": PurePosixPath(ref).stem},
            )
        except (OSError, ValueError):
            continue
        control_descriptor = envelope.get("control_result")
        if not isinstance(control_descriptor, Mapping):
            continue
        try:
            control = hydrate_sidecar_ref(
                Path(state_dir),
                dict(control_descriptor),
            ).payload
        except (OSError, ValueError):
            continue
        if not isinstance(control, Mapping):
            continue
        raw_artifacts = control.get("output_artifacts") or []
        if not raw_artifacts:
            raw_artifacts = _legacy_output_artifacts(
                control,
                required_artifacts=required_artifacts,
            )
        for raw_artifact in raw_artifacts:
            if not isinstance(raw_artifact, Mapping):
                continue
            artifact = dict(raw_artifact)
            source_ref = str(artifact.get("source_ref") or "").strip()
            if source_ref:
                available[source_ref] = artifact
    resolved: list[dict[str, Any]] = []
    for expected in required_artifacts:
        source_ref = str(expected.get("source_ref") or "").strip()
        artifact = available.get(source_ref)
        if artifact is None:
            continue
        if any(
            str(expected.get(field) or "").strip()
            and str(artifact.get(field) or "").strip()
            != str(expected.get(field) or "").strip()
            for field in ("name", "kind")
        ):
            continue
        hydrate_sidecar_ref(Path(state_dir), artifact)
        resolved.append(artifact)
    return resolved


def resolve_declared_output_artifact_index(
    state_dir: Path,
    *,
    input_result_refs: list[str],
    required_artifacts: list[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Index resolved outputs and report missing required source refs."""

    artifacts = resolve_declared_output_artifacts(
        state_dir,
        input_result_refs=input_result_refs,
        required_artifacts=required_artifacts,
    )
    index = {
        str(item.get("source_ref") or ""): item for item in artifacts
    }
    missing = sorted(
        str(item.get("source_ref") or "")
        for item in required_artifacts
        if str(item.get("source_ref") or "") not in index
    )
    return index, missing


def _legacy_output_artifacts(
    control: Mapping[str, Any],
    *,
    required_artifacts: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Adapt the pre-output-artifacts single-output stage result."""

    if str(control.get("schema_version") or "") != "generic-stage-result.v1":
        return []
    stage_id = str(control.get("stage_id") or "").strip()
    output_ref = control.get("output_ref")
    if not stage_id or not isinstance(output_ref, Mapping):
        return []
    matching = [
        dict(expected)
        for expected in required_artifacts
        if str(expected.get("source_ref") or "").split(".", 1)[0] == stage_id
    ]
    if len(matching) != 1:
        return []
    expected = matching[0]
    return [{
        **dict(output_ref),
        "name": str(expected.get("name") or ""),
        "kind": str(expected.get("kind") or ""),
        "source_ref": str(expected.get("source_ref") or ""),
        "producer_stage_id": stage_id,
    }]


def _output_body(result: Mapping[str, Any], *, name: str) -> Any:
    outputs = result.get("outputs")
    if isinstance(outputs, Mapping) and outputs.get(name) not in (None, ""):
        return outputs[name]
    direct = result.get(name)
    if direct not in (None, "") and name not in {"report"}:
        return direct
    if name == "report":
        for key in ("report_md", "markdown", "content", "summary"):
            value = result.get(key)
            if value not in (None, ""):
                return value
    return direct


def _write_output(
    state_dir: Path,
    *,
    workflow_run_id: str,
    stage_id: str,
    name: str,
    kind: str,
    body: Any,
    source_event_id: str,
) -> dict[str, Any]:
    root = "/".join((
        "generic-workflow",
        "outputs",
        _safe(workflow_run_id),
        _safe(stage_id),
        _safe(name),
    ))
    if isinstance(body, str) and kind.endswith("/markdown"):
        text = body.rstrip() + "\n"
        encoded = text.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        ref = f"artifacts/{root}/{digest}.md"
        target = sidecar_path(state_dir, ref)
        if target.exists() and target.read_bytes() != encoded:
            raise ValueError(f"immutable workflow output collision at {ref}")
        return write_sidecar_text(
            state_dir,
            ref,
            text,
            kind=kind,
            schema_version=OUTPUT_ARTIFACT_SCHEMA,
            created_by="generic-workflow-output-materializer",
            source_event_id=source_event_id,
            required=True,
            content_type="text/markdown",
        )
    normalized = body if isinstance(body, Mapping) else {"body": body}
    return write_immutable_json_sidecar(
        state_dir,
        dict(normalized),
        root=root,
        kind=kind,
        schema_version=OUTPUT_ARTIFACT_SCHEMA,
        created_by="generic-workflow-output-materializer",
        source_event_id=source_event_id,
    )


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._") or "item"


__all__ = [
    "OUTPUT_ARTIFACT_SCHEMA",
    "materialize_declared_workflow_outputs",
    "resolve_declared_output_artifact_index",
    "resolve_declared_output_artifacts",
]
