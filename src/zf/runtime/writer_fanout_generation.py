"""Verified writer-generation lookup used by fanout redrive fences."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from zf.core.events.model import ZfEvent
from zf.runtime.candidate_result_binding import same_task_map_generation
from zf.runtime.run_scope import event_run_id, resolve_run_id, run_aliases


@dataclass(frozen=True)
class CompletedWriterGeneration:
    candidate_event_id: str
    candidate_head_commit: str
    candidate_ref: str
    verification_event_id: str
    task_ids: list[str] = field(default_factory=list)


def completed_writer_generation(
    events: Sequence[ZfEvent],
    *,
    trigger_event: ZfEvent,
    task_ids: Sequence[str],
    task_map_generation: str = "",
    workflow_run_id: str = "",
) -> CompletedWriterGeneration | None:
    """Find a verified candidate that makes a recovery writer trigger stale."""

    trigger_payload = _payload(trigger_event)
    if not (
        str(trigger_payload.get("redrive_of") or "").strip()
        or str(trigger_payload.get("redispatch_fingerprint") or "").strip()
    ):
        return None
    trigger_index = next(
        (
            index
            for index, event in enumerate(events)
            if event.id and event.id == trigger_event.id
        ),
        -1,
    )
    if trigger_index < 0:
        return None
    requested = {
        str(task_id).strip() for task_id in task_ids if str(task_id).strip()
    }
    generation = str(
        task_map_generation or trigger_payload.get("task_map_generation") or ""
    ).strip()
    raw_run_id = str(
        workflow_run_id
        or trigger_payload.get("workflow_run_id")
        or trigger_payload.get("run_id")
        or trigger_event.correlation_id
        or ""
    ).strip()
    if not requested or not generation or not raw_run_id:
        return None

    aliases = run_aliases(events)
    canonical_run_id = resolve_run_id(events, raw_run_id)
    prefix = list(events[:trigger_index])
    for candidate_index in range(len(prefix) - 1, -1, -1):
        candidate = prefix[candidate_index]
        if candidate.type != "candidate.ready":
            continue
        body = _payload(candidate)
        if event_run_id(candidate, aliases=aliases) != canonical_run_id:
            continue
        if not same_task_map_generation(
            generation,
            str(body.get("task_map_generation") or ""),
        ):
            continue
        completed = {
            str(task_id).strip()
            for task_id in body.get("completed_task_ids") or []
            if str(task_id).strip()
        }
        if not requested.issubset(completed):
            continue
        if str(body.get("quality_status") or "passed").lower() in {
            "failed",
            "rejected",
        }:
            continue
        candidate_head = str(body.get("candidate_head_commit") or "").strip()
        candidate_ref = str(body.get("candidate_ref") or "").strip()
        if not candidate_head or not candidate_ref:
            continue

        latest_outcome: tuple[str, ZfEvent] | None = None
        for verification in prefix[candidate_index + 1:]:
            outcome = _writer_verification_outcome(verification)
            if not outcome:
                continue
            verification_body = _payload(verification)
            if event_run_id(verification, aliases=aliases) != canonical_run_id:
                continue
            if not same_task_map_generation(
                generation,
                str(verification_body.get("task_map_generation") or ""),
            ):
                continue
            verification_tasks = {
                str(task_id).strip()
                for task_id in verification_body.get("task_ids") or []
                if str(task_id).strip()
            }
            if verification.task_id:
                verification_tasks.add(str(verification.task_id).strip())
            target_ref = str(
                verification_body.get("target_ref")
                or verification_body.get("candidate_ref")
                or ""
            ).strip()
            target_commit = _verification_target_commit(verification_body)
            if target_ref and target_ref != candidate_ref:
                continue
            if target_commit and target_commit != candidate_head:
                continue
            if not target_ref and not target_commit and not requested.issubset(
                verification_tasks
            ):
                continue
            if verification_tasks and not requested.issubset(verification_tasks):
                continue
            latest_outcome = (outcome, verification)
        if latest_outcome is None or latest_outcome[0] != "passed":
            continue
        return CompletedWriterGeneration(
            candidate_event_id=candidate.id,
            candidate_head_commit=candidate_head,
            candidate_ref=candidate_ref,
            verification_event_id=latest_outcome[1].id,
            task_ids=sorted(requested),
        )
    return None


def _writer_verification_outcome(event: ZfEvent) -> str:
    success_types = {"verify.passed", "test.passed", "review.approved"}
    failure_types = {"verify.failed", "test.failed", "review.rejected"}
    if event.type not in success_types | failure_types:
        return ""
    payload = _payload(event)
    result = payload.get("verification_result")
    if isinstance(result, Mapping):
        verdict = str(result.get("verdict") or "").strip().lower()
        if verdict in {"rejected", "failed"}:
            return "failed"
        if verdict and verdict != "passed":
            return ""
    return "passed" if event.type in success_types else "failed"


def _verification_target_commit(payload: Mapping[str, Any]) -> str:
    result = payload.get("verification_result")
    result = result if isinstance(result, Mapping) else {}
    target = payload.get("target_snapshot")
    target = target if isinstance(target, Mapping) else {}
    return str(
        payload.get("target_commit")
        or payload.get("candidate_head_commit")
        or result.get("target_commit")
        or target.get("target_commit")
        or ""
    ).strip()


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}
