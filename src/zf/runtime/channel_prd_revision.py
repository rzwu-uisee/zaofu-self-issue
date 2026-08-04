"""Canonical PRD revision persistence for Channel synthesis."""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from typing import Any

from zf.runtime.channel_contract_artifacts import (
    persist_channel_conclusion,
    persist_channel_prd,
    persist_channel_prd_readiness,
    typed_items,
)
from zf.runtime.channel_contracts import normalize_product_discussion_mode
from zf.runtime.channel_deliberation_contract import (
    active_discussion_roster,
)


def persist_synthesis_prd_revision(
    state_dir,
    *,
    channel: dict[str, Any],
    channel_id: str,
    thread_id: str,
    member_id: str,
    actor: str,
    reply_event_id: str,
    synthesis: dict[str, Any],
    typed_synthesis: dict[str, Any],
    summary: str,
    artifact_ref: PurePosixPath,
    artifact_body: str,
    source_refs: list[str],
    evidence_refs: list[str],
) -> dict[str, Any]:
    spec_digest = hashlib.sha256(
        artifact_body.encode("utf-8")
    ).hexdigest()
    prior_syntheses = [
        item
        for item in channel.get("syntheses") or []
        if isinstance(item, dict)
        and str(item.get("thread_id") or "main") == thread_id
        and int(item.get("prd_revision") or 0) > 0
    ]
    previous = max(
        prior_syntheses,
        key=lambda item: int(item.get("prd_revision") or 0),
        default={},
    )
    prd_revision = int(previous.get("prd_revision") or 0) + 1
    readiness_body = (
        synthesis.get("readiness")
        if isinstance(synthesis.get("readiness"), dict)
        else {
            "verdict": "unassessed",
            "gaps": [],
            "risks": typed_items(synthesis.get("risks")),
            "evidence_refs": evidence_refs,
            "reason": "synthesis did not emit semantic readiness",
        }
    )
    created_by = member_id or actor
    readiness = persist_channel_prd_readiness(
        state_dir,
        channel_id=channel_id,
        thread_id=thread_id,
        revision=prd_revision,
        body=readiness_body,
        created_by=created_by,
        source_event_id=reply_event_id,
    )
    prd = persist_channel_prd(
        state_dir,
        channel_id=channel_id,
        thread_id=thread_id,
        revision=prd_revision,
        previous_ref=str(previous.get("prd_ref") or ""),
        previous_digest=str(previous.get("prd_digest") or ""),
        body={
            "summary": summary,
            "title": str(
                synthesis.get("title")
                or channel.get("name")
                or channel_id
            ),
            "synthesis": typed_synthesis,
            "markdown": artifact_body,
            "spec_path": artifact_ref.as_posix(),
            "spec_digest": spec_digest,
            "source_refs": source_refs,
            "evidence_refs": evidence_refs,
        },
        readiness_descriptor=readiness,
        created_by=created_by,
        source_event_id=reply_event_id,
    )
    conclusion = persist_channel_conclusion(
        state_dir,
        channel_id=channel_id,
        thread_id=thread_id,
        revision=prd_revision,
        prd_descriptor=prd,
        readiness_descriptor=readiness,
        summary=summary,
        source_refs=source_refs,
        created_by=created_by,
        source_event_id=reply_event_id,
    )
    return {
        "artifact_ref": str(prd["ref"]),
        "artifact_digest": str(prd["sha256"]),
        "prd_ref": str(prd["ref"]),
        "prd_digest": str(prd["sha256"]),
        "prd_revision": prd_revision,
        "previous_prd_ref": str(previous.get("prd_ref") or ""),
        "previous_prd_digest": str(previous.get("prd_digest") or ""),
        "readiness_ref": str(readiness["ref"]),
        "readiness_digest": str(readiness["sha256"]),
        "readiness_verdict": str(
            readiness_body.get("verdict") or "unassessed"
        ),
        "implementation_start": (
            readiness_body.get("implementation_start") is True
        ),
        "conclusion_ref": str(conclusion["ref"]),
        "conclusion_digest": str(conclusion["sha256"]),
        "spec_path": artifact_ref.as_posix(),
        "spec_digest": spec_digest,
    }


def consensus_mode_and_required_signers(
    channel: dict[str, Any],
    *,
    thread_id: str,
) -> tuple[str, list[str]]:
    discussions = (
        channel.get("discussions")
        if isinstance(channel.get("discussions"), dict)
        else {}
    )
    active_discussion = (
        discussions.get(thread_id)
        if isinstance(discussions.get(thread_id), dict)
        else {}
    )
    product_mode = normalize_product_discussion_mode(
        active_discussion.get("product_mode")
        or (
            channel.get("discussion", {}).get("mode")
            if isinstance(channel.get("discussion"), dict)
            else ""
        )
    )
    if product_mode != "multi_lens":
        return product_mode, []
    return (
        product_mode,
        active_discussion_roster(channel, thread_id=thread_id),
    )
