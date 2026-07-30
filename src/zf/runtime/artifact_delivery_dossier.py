"""Goal Dossier projection for admitted artifact-only delivery results."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from zf.core.security.redaction import redact_obj
from zf.runtime.call_result_envelope import call_result_envelope_ref


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def project_artifact_delivery_dossier(
    events: Iterable[Any],
    *,
    result_schema_version: str,
) -> dict[str, Any]:
    """Project only an admitted artifact delivery into a Goal Dossier."""

    saw_unverified_result = False
    for event in reversed(list(events)):
        payload = getattr(event, "payload", None)
        payload = payload if isinstance(payload, Mapping) else {}
        result = payload.get("artifact_delivery_result")
        if not isinstance(result, Mapping):
            continue
        if str(result.get("schema_version") or "") != result_schema_version:
            continue
        saw_unverified_result = True
        admitted_ref = payload.get("admitted_call_result_ref")
        control_ref = payload.get("control_result_ref")
        if (
            str(getattr(event, "type", "") or "")
            != "artifact.delivery.verified"
            or not call_result_envelope_ref(admitted_ref)
            or not isinstance(control_ref, Mapping)
            or not str(control_ref.get("ref") or "").strip()
            or not _SHA256.fullmatch(str(control_ref.get("sha256") or ""))
        ):
            continue
        return redact_obj({
            "schema_version": "goal-dossier-artifact-delivery.v1",
            "status": (
                "ready"
                if str(result.get("verdict") or "") == "passed"
                and not list(result.get("open_gap_refs") or [])
                else "incomplete"
            ),
            "completion_profile": str(
                result.get("completion_profile") or ""
            ),
            "workflow_generation": str(
                result.get("workflow_generation") or ""
            ),
            "generic_workflow_contract_digest": str(
                result.get("generic_workflow_contract_digest") or ""
            ),
            "run_contract_ref": str(result.get("run_contract_ref") or ""),
            "run_contract_digest": str(
                result.get("run_contract_digest") or ""
            ),
            "verifier_stage_id": str(
                result.get("verifier_stage_id") or ""
            ),
            "verifier_role": str(result.get("verifier_role") or ""),
            "required_artifacts": [
                dict(item)
                for item in result.get("artifacts") or []
                if isinstance(item, Mapping)
            ],
            "goal_coverage": [
                dict(item)
                for item in result.get("goal_coverage") or []
                if isinstance(item, Mapping)
            ],
            "verification_evidence_refs": [
                str(item)
                for item in result.get("verification_evidence_refs") or []
                if str(item).strip()
            ],
            "admitted_call_result_ref": dict(admitted_ref),
            "control_result_ref": dict(control_ref),
            "source_event_id": str(getattr(event, "id", "") or ""),
        })
    if saw_unverified_result:
        return {
            "schema_version": "goal-dossier-artifact-delivery.v1",
            "status": "incomplete",
            "required_artifacts": [],
        }
    return {
        "schema_version": "goal-dossier-artifact-delivery.v1",
        "status": "not_applicable",
        "required_artifacts": [],
    }


__all__ = ["project_artifact_delivery_dossier"]
