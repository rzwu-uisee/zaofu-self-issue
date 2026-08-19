"""Structured contribution coverage for Channel synthesis."""

from __future__ import annotations

from typing import Any

from zf.runtime.channel_context import channel_contribution_index


def synthesis_contract_sources(
    channel: dict[str, Any],
    *,
    thread_id: str,
) -> tuple[list[str], list[str]]:
    refs: list[str] = []
    digests: list[str] = []
    for contribution in channel_contribution_index(
        channel,
        thread_id=thread_id,
    ):
        if str(contribution.get("contract_status") or "") != "structured":
            continue
        _append_artifact_identity(contribution, refs=refs, digests=digests)
    raw_cross_reviews = channel.get("cross_reviews") or []
    cross_reviews = (
        raw_cross_reviews.values()
        if isinstance(raw_cross_reviews, dict)
        else raw_cross_reviews
    )
    for review in cross_reviews:
        if not isinstance(review, dict):
            continue
        if str(review.get("thread_id") or "main") != thread_id:
            continue
        if str(review.get("status") or "") != "completed":
            continue
        _append_artifact_identity(review, refs=refs, digests=digests)
    return list(dict.fromkeys(refs)), list(dict.fromkeys(digests))


def contract_coverage_error(
    *,
    required_refs: list[str],
    required_digests: list[str],
    consumed_refs: list[str],
    consumed_digests: list[str],
) -> str:
    checks = (
        (
            "contribution_coverage_missing_refs:",
            set(required_refs) - set(consumed_refs),
        ),
        (
            "contribution_coverage_missing_digests:",
            set(required_digests) - set(consumed_digests),
        ),
        (
            "contribution_coverage_unknown_refs:",
            set(consumed_refs) - set(required_refs),
        ),
        (
            "contribution_coverage_unknown_digests:",
            set(consumed_digests) - set(required_digests),
        ),
    )
    for prefix, values in checks:
        if values:
            return prefix + ",".join(sorted(values))
    return ""


def _append_artifact_identity(
    item: dict[str, Any],
    *,
    refs: list[str],
    digests: list[str],
) -> None:
    artifact_ref = str(item.get("artifact_ref") or "").strip()
    artifact_digest = str(item.get("artifact_digest") or "").strip()
    if artifact_ref:
        refs.append(artifact_ref)
    if artifact_digest:
        digests.append(artifact_digest)


__all__ = [
    "contract_coverage_error",
    "synthesis_contract_sources",
]
