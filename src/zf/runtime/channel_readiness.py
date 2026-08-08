"""Mechanical authorization helpers for Channel PRD readiness."""

from __future__ import annotations

from typing import Any


def owner_readiness_risk_accepted(
    consensus: dict[str, Any],
    *,
    readiness_ref: object,
    readiness_digest: object,
) -> bool:
    """Return whether the Owner accepted this exact readiness artifact."""

    expected_ref = str(readiness_ref or "").strip()
    expected_digest = _bare_digest(readiness_digest)
    return bool(
        consensus.get("human_confirmed")
        and consensus.get("risk_accepted") is True
        and expected_ref
        and expected_digest
        and str(consensus.get("confirmed_readiness_ref") or "").strip()
        == expected_ref
        and _bare_digest(consensus.get("confirmed_readiness_digest"))
        == expected_digest
    )


def _bare_digest(value: object) -> str:
    return str(value or "").strip().removeprefix("sha256:")


__all__ = ["owner_readiness_risk_accepted"]
