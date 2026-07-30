"""Replay-stable dependency joins for canonical Generic Workflow stages."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.events.model import ZfEvent
from zf.runtime.run_scope import event_run_id, run_aliases


SATISFIED_EVENT = "workflow.dependency_barrier.satisfied"
BLOCKED_EVENT = "workflow.dependency_barrier.blocked"

_GENERATION_FIELDS = (
    "workflow_generation",
    "task_map_generation",
    "request_revision",
    "run_contract_digest",
    "generic_workflow_contract_digest",
)
_PROPAGATED_FIELDS = (
    "workflow_run_id",
    "run_id",
    "request_id",
    "flow_kind",
    "request_kind",
    "workflow_generation",
    "task_map_generation",
    "request_revision",
    "workflow_template",
    "completion_profile",
    "generic_workflow_contract_digest",
    "goal_id",
    "workflow_intent",
    "required_delivery_artifacts",
    "goal_claim_set_ref",
    "goal_claim_set_digest",
    "run_contract_ref",
    "run_contract_digest",
    "workflow_proposal_ref",
    "workflow_proposal_digest",
    "effective_config_ref",
    "effective_config_digest",
    "requirement_spec_ref",
    "requirement_spec_digest",
)
_PROPAGATED_REF_FIELDS = (
    "artifact_refs",
    "evidence_refs",
    "input_refs",
    "input_result_refs",
)


@dataclass(frozen=True)
class DependencyBarrierDecision:
    event_type: str
    payload: dict[str, Any]
    causation_id: str
    correlation_id: str

    def to_event(self) -> ZfEvent:
        return ZfEvent(
            type=self.event_type,
            actor="orchestrator",
            payload=dict(self.payload),
            causation_id=self.causation_id or None,
            correlation_id=self.correlation_id or None,
        )


def reconcile_dependency_barriers(
    config: ZfConfig,
    events: Iterable[ZfEvent],
) -> list[DependencyBarrierDecision]:
    """Derive new barrier verdicts without mutating canonical state."""

    rows = list(events)
    aliases = run_aliases(rows)
    known_runs = set(aliases.values())
    singleton_run = next(iter(known_runs)) if len(known_runs) == 1 else ""
    decisions: list[DependencyBarrierDecision] = []
    for stage in getattr(getattr(config, "workflow", None), "stages", []) or []:
        barrier_id = str(
            getattr(stage, "dependency_barrier_id", "") or ""
        ).strip()
        barrier_digest = str(
            getattr(stage, "dependency_barrier_digest", "") or ""
        ).strip()
        required_events = tuple(
            str(item)
            for item in (
                getattr(stage, "dependency_events", []) or []
            )
            if str(item).strip()
        )
        failure_events = tuple(
            str(item)
            for item in (
                getattr(stage, "dependency_failure_events", []) or []
            )
            if str(item).strip()
        )
        if (
            not barrier_id
            or not barrier_digest
            or len(required_events) < 2
            or len(required_events) != len(failure_events)
        ):
            continue
        decisions.extend(_reconcile_stage(
            rows,
            aliases=aliases,
            singleton_run=singleton_run,
            stage_id=str(getattr(stage, "id", "") or ""),
            barrier_id=barrier_id,
            barrier_digest=barrier_digest,
            dependencies=tuple(
                str(item)
                for item in (
                    getattr(stage, "dependencies", []) or []
                )
            ),
            required_events=required_events,
            failure_events=failure_events,
        ))
    return decisions


def _reconcile_stage(
    events: list[ZfEvent],
    *,
    aliases: dict[str, str],
    singleton_run: str,
    stage_id: str,
    barrier_id: str,
    barrier_digest: str,
    dependencies: tuple[str, ...],
    required_events: tuple[str, ...],
    failure_events: tuple[str, ...],
) -> list[DependencyBarrierDecision]:
    relevant_types = set(required_events) | set(failure_events)
    grouped: dict[tuple[str, str], list[tuple[int, ZfEvent]]] = {}
    settled: set[tuple[str, str, str, str, str]] = set()
    settled_refs: dict[
        tuple[str, str, str],
        list[dict[str, list[Any]]],
    ] = {}
    blocked_fingerprints: set[tuple[str, str, str, str]] = set()
    for index, event in enumerate(events):
        payload = _payload(event)
        if event.type in {SATISFIED_EVENT, BLOCKED_EVENT}:
            if (
                str(payload.get("barrier_id") or "") != barrier_id
                or str(payload.get("barrier_digest") or "") != barrier_digest
            ):
                continue
            run_id = str(payload.get("workflow_run_id") or "").strip()
            generation = str(payload.get("generation_key") or "").strip()
            if not run_id or not generation:
                continue
            if event.type == SATISFIED_EVENT:
                source_fingerprint = str(
                    payload.get("source_fingerprint") or ""
                ).strip() or _stable_digest(
                    list(payload.get("source_event_ids") or [])
                )
                propagated_refs = _propagated_refs((event,))
                propagation_digest = str(
                    payload.get("propagation_digest") or ""
                ).strip() or _stable_digest(propagated_refs)
                settled.add((
                    run_id,
                    generation,
                    barrier_digest,
                    source_fingerprint,
                    propagation_digest,
                ))
                settled_refs.setdefault(
                    (run_id, generation, barrier_digest),
                    [],
                ).append(propagated_refs)
            else:
                blocked_fingerprints.add((
                    run_id,
                    generation,
                    barrier_digest,
                    str(payload.get("source_fingerprint") or ""),
                ))
            continue
        if event.type not in relevant_types:
            continue
        flow_kind = str(payload.get("flow_kind") or "").strip().lower()
        if flow_kind and flow_kind != "workflow":
            continue
        run_id = event_run_id(event, aliases=aliases) or singleton_run
        if not run_id:
            continue
        generation = _generation_key(payload)
        grouped.setdefault((run_id, generation), []).append((index, event))

    decisions: list[DependencyBarrierDecision] = []
    for (run_id, generation), scoped in sorted(grouped.items()):
        latest_by_type: dict[str, tuple[int, ZfEvent]] = {}
        for index, event in scoped:
            latest_by_type[event.type] = (index, event)
        selected: list[ZfEvent] = []
        failed: list[ZfEvent] = []
        pending = False
        for success_type, failure_type in zip(
            required_events,
            failure_events,
            strict=True,
        ):
            success = latest_by_type.get(success_type)
            failure = latest_by_type.get(failure_type)
            if failure is not None and (
                success is None or failure[0] > success[0]
            ):
                failed.append(failure[1])
                continue
            if success is None:
                pending = True
                continue
            selected.append(success[1])
        if failed:
            source_ids = [event.id for event in failed]
            fingerprint = _stable_digest(source_ids)
            if (
                run_id,
                generation,
                barrier_digest,
                fingerprint,
            ) in blocked_fingerprints:
                continue
            source = failed[-1]
            decisions.append(DependencyBarrierDecision(
                event_type=BLOCKED_EVENT,
                payload={
                    **_identity_payload(source),
                    "flow_kind": "workflow",
                    "workflow_run_id": run_id,
                    "stage_id": stage_id,
                    "barrier_id": barrier_id,
                    "barrier_digest": barrier_digest,
                    "generation_key": generation,
                    "dependencies": list(dependencies),
                    "required_events": list(required_events),
                    "failure_events": list(failure_events),
                    "failed_event_ids": source_ids,
                    "source_fingerprint": fingerprint,
                    "reason": "dependency_failed",
                },
                causation_id=source.id,
                correlation_id=run_id,
            ))
            continue
        if pending or len(selected) != len(required_events):
            continue
        source_ids = [event.id for event in selected]
        source_fingerprint = _stable_digest(source_ids)
        propagated_refs = _propagated_refs(selected)
        propagation_digest = _stable_digest(propagated_refs)
        prior_settlements = {
            item[4]
            for item in settled
            if item[:3] == (run_id, generation, barrier_digest)
        }
        if (
            propagation_digest in prior_settlements
            or any(
                _propagated_refs_are_subset(propagated_refs, prior)
                for prior in settled_refs.get(
                    (run_id, generation, barrier_digest),
                    [],
                )
            )
        ):
            continue
        source = selected[-1]
        decisions.append(DependencyBarrierDecision(
            event_type=SATISFIED_EVENT,
            payload={
                **_identity_payload(source),
                **propagated_refs,
                "flow_kind": "workflow",
                "workflow_run_id": run_id,
                "stage_id": stage_id,
                "barrier_id": barrier_id,
                "barrier_digest": barrier_digest,
                "generation_key": generation,
                "dependencies": list(dependencies),
                "required_events": list(required_events),
                "source_event_ids": source_ids,
                "source_fingerprint": source_fingerprint,
                "propagation_digest": propagation_digest,
                "dependency_sources": _dependency_sources(
                    dependencies,
                    required_events,
                    selected,
                ),
            },
            causation_id=source.id,
            correlation_id=run_id,
        ))
    return decisions


def _generation_key(payload: dict[str, Any]) -> str:
    identity = {
        key: str(payload.get(key) or "").strip()
        for key in _GENERATION_FIELDS
        if str(payload.get(key) or "").strip()
    }
    return _stable_digest(identity or {"generation": "legacy"})


def _identity_payload(event: ZfEvent) -> dict[str, Any]:
    payload = _payload(event)
    return {
        key: payload[key]
        for key in _PROPAGATED_FIELDS
        if payload.get(key) not in (None, "", [], {})
    }


def _propagated_refs(events: Iterable[ZfEvent]) -> dict[str, list[Any]]:
    propagated: dict[str, list[Any]] = {}
    for field in _PROPAGATED_REF_FIELDS:
        values: list[Any] = []
        seen: set[str] = set()
        for event in events:
            raw = _payload(event).get(field)
            if not isinstance(raw, list):
                continue
            for item in raw:
                normalized = _normalized_ref(item)
                if normalized is None:
                    continue
                key = _stable_json(normalized)
                if key in seen:
                    continue
                seen.add(key)
                values.append(normalized)
        if values:
            propagated[field] = values
    return propagated


def _dependency_sources(
    dependencies: tuple[str, ...],
    required_events: tuple[str, ...],
    selected: list[ZfEvent],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, event in enumerate(selected):
        row: dict[str, Any] = {
            "dependency": (
                dependencies[index]
                if index < len(dependencies)
                else required_events[index]
            ),
            "event_type": required_events[index],
            "event_id": event.id,
        }
        row.update(_propagated_refs((event,)))
        rows.append(row)
    return rows


def _normalized_ref(value: Any) -> Any | None:
    if isinstance(value, Mapping):
        ref = str(value.get("path") or value.get("ref") or "").strip()
        if not ref:
            return None
        return {
            str(key): item
            for key, item in value.items()
            if item not in (None, "", [], {})
        }
    ref = str(value or "").strip()
    return ref or None


def _propagated_refs_are_subset(
    candidate: Mapping[str, list[Any]],
    existing: Mapping[str, list[Any]],
) -> bool:
    for field, values in candidate.items():
        candidate_keys = {_stable_json(item) for item in values}
        existing_keys = {
            _stable_json(item)
            for item in existing.get(field, [])
        }
        if not candidate_keys.issubset(existing_keys):
            return False
    return True


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _stable_digest(value: Any) -> str:
    body = _stable_json(value).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


__all__ = [
    "BLOCKED_EVENT",
    "SATISFIED_EVENT",
    "DependencyBarrierDecision",
    "reconcile_dependency_barriers",
]
