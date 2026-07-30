"""Evidence authorization for artifact-delivery verification results."""

from __future__ import annotations

from typing import Any, Mapping


def artifact_delivery_evidence_binding_issues(
    result: Mapping[str, Any],
    *,
    admitted_envelopes: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, str]]:
    input_result_refs = _strings(result.get("input_result_refs"))
    authorized = {
        str(item.get("ref") or "")
        for item in result.get("artifacts") or []
        if isinstance(item, Mapping) and str(item.get("ref") or "").strip()
    }
    authorized.update(input_result_refs)
    authorized.update(_strings(result.get("open_gap_refs")))
    for ref in input_result_refs:
        envelope = admitted_envelopes.get(ref)
        if not isinstance(envelope, Mapping):
            continue
        authorized.update(_strings(envelope.get("evidence_refs")))
        for field in (
            "control_result",
            "artifact_manifest_ref",
            "provider_operation_summary_ref",
        ):
            descriptor = envelope.get(field)
            descriptor_ref = (
                str(descriptor.get("ref") or "").strip()
                if isinstance(descriptor, Mapping)
                else str(descriptor or "").strip()
            )
            if descriptor_ref:
                authorized.add(descriptor_ref)

    return [
        {
            "field": f"control_result.verification_evidence_refs[{index}]",
            "code": "verification_evidence_not_bound",
            "message": ref,
        }
        for index, ref in enumerate(
            _strings(result.get("verification_evidence_refs"))
        )
        if ref not in authorized
    ]


def _strings(value: Any) -> list[str]:
    values = (
        value
        if isinstance(value, (list, tuple, set))
        else [value] if value else []
    )
    return list(
        dict.fromkeys(
            str(item).strip() for item in values if str(item).strip()
        )
    )


__all__ = ["artifact_delivery_evidence_binding_issues"]
