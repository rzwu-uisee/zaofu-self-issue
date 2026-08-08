"""Canonical identity payloads for confirmed Channel PRDs."""

from __future__ import annotations

from typing import Any


def consensus_reached_payload(
    consensus: dict[str, Any],
    *,
    channel_id: str,
    thread_id: str,
    source: str,
    confirmed_by: str = "",
    risk_accepted: bool | None = None,
) -> dict[str, Any]:
    """Publish the complete immutable identity of a confirmed Channel PRD."""

    artifact_ref = str(consensus.get("artifact_ref") or "")
    artifact_digest = str(consensus.get("artifact_digest") or "")
    return {
        "channel_id": channel_id,
        "thread_id": thread_id,
        "artifact_ref": artifact_ref,
        "artifact_digest": artifact_digest,
        "prd_ref": str(consensus.get("prd_ref") or artifact_ref),
        "prd_digest": str(
            consensus.get("prd_digest") or artifact_digest
        ),
        "prd_revision": int(consensus.get("prd_revision") or 0),
        "readiness_ref": str(consensus.get("readiness_ref") or ""),
        "readiness_digest": str(
            consensus.get("readiness_digest") or ""
        ),
        "readiness_verdict": str(
            consensus.get("readiness_verdict") or "unassessed"
        ),
        "implementation_start": (
            consensus.get("implementation_start") is True
        ),
        "conclusion_ref": str(consensus.get("conclusion_ref") or ""),
        "conclusion_digest": str(
            consensus.get("conclusion_digest") or ""
        ),
        "confirmed_by": str(
            confirmed_by or consensus.get("human_confirmed_by") or ""
        ),
        "risk_accepted": (
            consensus.get("risk_accepted") is True
            if risk_accepted is None
            else risk_accepted
        ),
        "signed_by": sorted((consensus.get("signed") or {}).keys()),
        "product_mode": str(consensus.get("product_mode") or ""),
        "source_refs": [
            str(item)
            for item in consensus.get("source_refs") or []
            if str(item)
        ],
        "source": source,
    }


__all__ = ["consensus_reached_payload"]
