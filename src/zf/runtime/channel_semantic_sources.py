"""Complete source bundles and coverage gates for Channel deliberation turns."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from zf.core.events import EventWriter
from zf.runtime.channel_sidecar import hydrate_channel_message_text
from zf.runtime.sidecar_refs import SidecarRefError


SEMANTIC_SOURCE_SCHEMA_VERSION = "channel.semantic_sources.v1"
DEFAULT_MAX_SEMANTIC_SOURCE_CHARS = 120_000


def reject_incomplete_semantic_source_bundle(
    *,
    writer: EventWriter,
    context_pack: dict[str, Any],
    actor: str,
    task_id: str | None,
    causation_id: str,
    channel_id: str,
    thread_id: str,
    target_member_id: str,
    trigger_message_id: str,
    routing_reason: str,
    source: str,
) -> str:
    if (
        context_pack.get("semantic_source_required") is not True
        or context_pack.get("semantic_source_complete") is True
    ):
        return ""
    reason = str(
        context_pack.get("semantic_source_reason")
        or "semantic_source_bundle_incomplete"
    )
    writer.emit(
        "channel.context_pack.rejected",
        actor=actor,
        task_id=task_id,
        causation_id=causation_id,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": thread_id,
            "context_pack_id": context_pack["context_pack_id"],
            "target_member_id": target_member_id,
            "trigger_message_id": trigger_message_id,
            "reason": reason,
            "routing_reason": routing_reason,
            "limits": context_pack.get("limits") or {},
            "source": source,
        },
    )
    return reason


def build_semantic_source_bundle(
    channel: dict[str, Any] | None,
    *,
    thread_id: str,
    trigger_message_id: str,
    state_dir: Path | None,
    max_source_chars: int = DEFAULT_MAX_SEMANTIC_SOURCE_CHARS,
) -> dict[str, Any]:
    """Build a fail-closed source bundle for cross-review and synthesis.

    Ordinary conversation deliberately returns ``required=False`` and keeps the
    existing excerpt-only context path. Deliberation turns carry every relevant
    prior message ref/digest plus its complete, sidecar-verified body.
    """

    channel = channel or {}
    messages = _dict_rows(channel.get("messages") or channel.get("recent_messages"))
    trigger = next(
        (
            item
            for item in messages
            if str(item.get("message_id") or "") == trigger_message_id
        ),
        {},
    )
    stage = semantic_stage(trigger.get("refs"))
    session = _discussion_session(channel, thread_id)
    requirement_message_id = str(session.get("requirement_message_id") or "")
    required = bool(stage and requirement_message_id)
    empty = {
        "schema_version": SEMANTIC_SOURCE_SCHEMA_VERSION,
        "required": required,
        "stage": stage,
        "complete": True,
        "reason": "",
        "manifest": [],
        "manifest_digest": _stable_digest([]),
        "required_message_digests": [],
        "documents": [],
        "source_chars": 0,
        "max_source_chars": max_source_chars,
    }
    if not required:
        return empty

    thread_messages = [
        item
        for item in messages
        if str(item.get("thread_id") or "main") == thread_id
    ]
    message_positions = {
        str(item.get("message_id") or ""): index
        for index, item in enumerate(thread_messages)
    }
    start = message_positions.get(requirement_message_id, 0)
    end = message_positions.get(trigger_message_id, len(thread_messages))
    if end < start:
        start = 0
    reply_requests = {
        str(item.get("request_id") or ""): item
        for item in _dict_rows(channel.get("reply_requests"))
        if str(item.get("request_id") or "")
    }
    messages_by_id = {
        str(item.get("message_id") or ""): item
        for item in thread_messages
        if str(item.get("message_id") or "")
    }
    counters: dict[str, int] = {}
    manifest: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    errors: list[str] = []
    source_chars = 0

    for message in thread_messages[start:end]:
        if not _is_semantic_source_message(
            message,
            requirement_message_id=requirement_message_id,
        ):
            continue
        message_id = str(message.get("message_id") or "")
        kind = _message_kind(
            message,
            requirement_message_id=requirement_message_id,
            reply_requests=reply_requests,
            messages_by_id=messages_by_id,
        )
        counters[kind] = counters.get(kind, 0) + 1
        round_id = (
            "requirement"
            if kind == "requirement"
            else f"{kind}:{counters[kind]}"
        )
        descriptor = _message_descriptor(message)
        body_ref = str(descriptor.get("ref") or message.get("body_ref") or "")
        body_digest = str(
            descriptor.get("sha256") or message.get("body_sha256") or ""
        )
        try:
            text = (
                hydrate_channel_message_text(Path(state_dir), message, strict=True)
                if state_dir is not None and body_ref
                else str(message.get("text") or message.get("message") or "")
            )
        except SidecarRefError as exc:
            errors.append(f"{message_id}:{exc.code}")
            continue
        if not body_ref:
            event_id = str(message.get("event_id") or message_id)
            body_ref = f"event:{event_id}#inline-message"
        if not body_digest:
            body_digest = hashlib.sha256(
                text.encode("utf-8", errors="replace")
            ).hexdigest()
        source_chars += len(text)
        entry = {
            "message_id": message_id,
            "event_id": str(message.get("event_id") or ""),
            "member_id": str(message.get("member_id") or message.get("actor") or ""),
            "role": str(message.get("role") or ""),
            "round": round_id,
            "kind": kind,
            "message_body_ref": body_ref,
            "message_body_digest": body_digest,
            "message_body_byte_count": int(
                descriptor.get("byte_count")
                or message.get("body_byte_count")
                or len(text.encode("utf-8", errors="replace"))
            ),
        }
        manifest.append(entry)
        documents.append({**entry, "content": text})

    if source_chars > max_source_chars:
        errors.append(
            "semantic_source_budget_exceeded:"
            f"{source_chars}>{max_source_chars}"
        )
    manifest_digest = _stable_digest(manifest)
    return {
        **empty,
        "complete": not errors,
        "reason": ";".join(errors),
        "manifest": manifest,
        "manifest_digest": manifest_digest,
        "required_message_digests": [
            str(item["message_body_digest"]) for item in manifest
        ],
        "documents": documents if not errors else [],
        "source_chars": source_chars,
    }


def validate_semantic_source_coverage(
    channel: dict[str, Any],
    request: dict[str, Any],
    declared_digests: object,
) -> tuple[dict[str, Any], str]:
    """Validate provider-declared consumption against the bound context pack."""

    context_pack_id = str(request.get("context_pack_id") or "")
    context_pack = _context_pack(channel, context_pack_id)
    required = bool(context_pack.get("semantic_source_required"))
    result = {
        "required": required,
        "status": "not_required",
        "manifest_digest": str(
            context_pack.get("semantic_source_manifest_digest") or ""
        ),
        "required_message_digests": _string_list(
            context_pack.get("semantic_source_required_digests")
        ),
        "consumed_message_digests": _string_list(declared_digests),
        "sources": context_pack.get("semantic_source_manifest")
        if isinstance(context_pack.get("semantic_source_manifest"), list)
        else [],
    }
    if not required:
        return result, ""
    if context_pack.get("semantic_source_complete") is not True:
        return result, (
            "semantic_source_bundle_incomplete:"
            + str(context_pack.get("semantic_source_reason") or "unknown")
        )
    expected_manifest_digest = _stable_digest(result["sources"])
    if not result["manifest_digest"] or (
        result["manifest_digest"] != expected_manifest_digest
    ):
        return result, "semantic_source_manifest_digest_mismatch"
    required_digests = set(result["required_message_digests"])
    consumed_digests = set(result["consumed_message_digests"])
    missing = sorted(required_digests - consumed_digests)
    unknown = sorted(consumed_digests - required_digests)
    if missing:
        return result, "semantic_source_coverage_missing:" + ",".join(missing)
    if unknown:
        return result, "semantic_source_coverage_unknown:" + ",".join(unknown)
    result["status"] = "complete"
    return result, ""


def semantic_stage(refs: object) -> str:
    refs = refs if isinstance(refs, dict) else {}
    if refs.get("cross_review_request_id"):
        return "cross_review"
    if refs.get("synthesis_request_id"):
        return "synthesis"
    return ""


def _discussion_session(
    channel: dict[str, Any],
    thread_id: str,
) -> dict[str, Any]:
    sessions = channel.get("discussions")
    if not isinstance(sessions, dict):
        return {}
    session = sessions.get(thread_id)
    return session if isinstance(session, dict) else {}


def _is_semantic_source_message(
    message: dict[str, Any],
    *,
    requirement_message_id: str,
) -> bool:
    message_id = str(message.get("message_id") or "")
    if message_id == requirement_message_id:
        return True
    role = str(message.get("role") or "").lower()
    if role not in {"assistant", "user"}:
        return False
    refs = message.get("refs") if isinstance(message.get("refs"), dict) else {}
    if str(message.get("source") or "") == "runtime" and semantic_stage(refs):
        return False
    if str(message.get("source") or "") == "runtime" and (
        refs.get("question_dedup_request_id")
        or refs.get("consensus_review_id")
    ):
        return False
    return True


def _message_kind(
    message: dict[str, Any],
    *,
    requirement_message_id: str,
    reply_requests: dict[str, dict[str, Any]],
    messages_by_id: dict[str, dict[str, Any]],
) -> str:
    if str(message.get("message_id") or "") == requirement_message_id:
        return "requirement"
    if str(message.get("role") or "").lower() == "user":
        return "owner_input"
    refs = message.get("refs") if isinstance(message.get("refs"), dict) else {}
    request_id = str(refs.get("request_id") or "")
    request = reply_requests.get(request_id, {})
    origin = messages_by_id.get(str(request.get("message_id") or ""), {})
    origin_refs = origin.get("refs") if isinstance(origin.get("refs"), dict) else {}
    if origin_refs.get("cross_review_request_id"):
        return "cross_review"
    if origin_refs.get("question_dedup_request_id"):
        return "question_dedup"
    if origin_refs.get("synthesis_request_id"):
        return "synthesis_attempt"
    if origin_refs.get("consensus_review_id"):
        return "consensus_review"
    return "contribution"


def _message_descriptor(message: dict[str, Any]) -> dict[str, Any]:
    refs = message.get("refs") if isinstance(message.get("refs"), dict) else {}
    descriptor = refs.get("message_body")
    return descriptor if isinstance(descriptor, dict) else {}


def _context_pack(
    channel: dict[str, Any],
    context_pack_id: str,
) -> dict[str, Any]:
    packs = channel.get("context_packs")
    if isinstance(packs, dict):
        item = packs.get(context_pack_id)
        return item if isinstance(item, dict) else {}
    for item in packs or []:
        if (
            isinstance(item, dict)
            and str(item.get("context_pack_id") or "") == context_pack_id
        ):
            return item
    return {}


def _dict_rows(value: object) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        rows = value.values()
    elif isinstance(value, list):
        rows = value
    else:
        rows = []
    return [item for item in rows if isinstance(item, dict)]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(
        str(item).strip()
        for item in value
        if isinstance(item, str) and str(item).strip()
    ))


def _stable_digest(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "DEFAULT_MAX_SEMANTIC_SOURCE_CHARS",
    "SEMANTIC_SOURCE_SCHEMA_VERSION",
    "build_semantic_source_bundle",
    "reject_incomplete_semantic_source_bundle",
    "semantic_stage",
    "validate_semantic_source_coverage",
]
