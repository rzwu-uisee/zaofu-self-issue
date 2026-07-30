"""Build deterministic synth-retry events for candidate-level replans."""

from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace

from zf.core.events.model import ZfEvent
from zf.runtime.candidate_rework_identity import (
    _CANDIDATE_REWORK_IDENTITY_KEYS,
)


def build_replan_resynth_event(
    *,
    plan: object,
    events: Sequence[ZfEvent],
    config: object,
) -> ZfEvent | None:
    """Return the plan-synth trigger for a candidate-level replan."""

    workflow = getattr(config, "workflow", None)
    replan_cfg = getattr(workflow, "admission_replan", None)
    trigger = _resynth_trigger(
        config,
        flow_kind=str(getattr(plan, "flow_kind", "") or ""),
    )
    if not getattr(replan_cfg, "enabled", False) or not trigger:
        return None

    base_payload = _latest_trigger_payload(
        events,
        trigger=trigger,
        trace_id=str(getattr(plan, "trace_id", "") or ""),
    )
    payload = dict(base_payload)
    payload.update({
        "pdd_id": getattr(plan, "pdd_id", ""),
        "trace_id": getattr(plan, "trace_id", ""),
        "target_ref": getattr(plan, "target_ref", "") or payload.get("target_ref", ""),
        "rework_of": getattr(plan, "source_event_id", ""),
        "rework_attempt": getattr(plan, "attempt", 0),
        "rework_source": getattr(plan, "source_event_type", ""),
        "rework_feedback": list(getattr(plan, "feedback", ()) or ()),
        "rework_categories": list(getattr(plan, "failure_categories", ()) or ()),
        "rework_summary": dict(getattr(plan, "rework_summary", {}) or {}),
        "replan_classification": getattr(plan, "classification", ""),
    })
    for key in _CANDIDATE_REWORK_IDENTITY_KEYS:
        value = getattr(plan, key, None)
        if value not in (None, "", [], {}):
            payload[key] = value
    for key in (
        "failed_task_ids",
        "task_ids",
        "downstream_task_ids",
        "resume_scope",
    ):
        value = getattr(plan, key, None)
        if value not in (None, "", (), []):
            payload[key] = list(value) if isinstance(value, tuple) else value
    return ZfEvent(
        type=trigger,
        actor="zf-cli",
        payload=payload,
        correlation_id=str(getattr(plan, "trace_id", "") or ""),
    )


def _resynth_trigger(config: object, *, flow_kind: str) -> str:
    workflow = getattr(config, "workflow", None)
    replan_cfg = getattr(workflow, "admission_replan", None)
    fallback = str(getattr(replan_cfg, "resynth_trigger", "") or "").strip()
    scope = str(flow_kind or "").strip().lower()
    if not scope:
        return fallback
    for stage in list(getattr(workflow, "stages", []) or []):
        if str(getattr(stage, "flow_kind", "") or "").strip().lower() != scope:
            continue
        if str(getattr(stage, "topology", "") or "") != "fanout_reader":
            continue
        aggregate = getattr(stage, "aggregate", None)
        success_event = str(
            getattr(stage, "success_event", "")
            or getattr(aggregate, "success_event", "")
            or ""
        ).strip()
        if success_event == "task_map.ready":
            trigger = str(getattr(stage, "trigger", "") or "").strip()
            if trigger:
                return trigger
    return fallback


def plan_missing_replan_resynth_events(
    events: Sequence[ZfEvent],
    *,
    config: object,
) -> list[ZfEvent]:
    """Repair a replan marker whose follow-through used another Flow trigger."""

    latest_by_scope: dict[tuple[str, str], tuple[int, ZfEvent]] = {}
    for index, event in enumerate(events):
        if event.type != "orchestrator.replan_requested":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        flow_kind = str(payload.get("flow_kind") or "").strip()
        if not flow_kind:
            continue
        trace_id = str(
            payload.get("workflow_run_id")
            or payload.get("trace_id")
            or event.correlation_id
            or ""
        ).strip()
        pdd_id = str(payload.get("pdd_id") or payload.get("feature_id") or "").strip()
        latest_by_scope[(trace_id, pdd_id)] = (index, event)

    repairs: list[ZfEvent] = []
    for (trace_id, pdd_id), (source_index, marker) in latest_by_scope.items():
        payload = marker.payload if isinstance(marker.payload, dict) else {}
        plan_data = {
            "pdd_id": pdd_id,
            "trace_id": trace_id,
            "target_ref": str(payload.get("target_ref") or ""),
            "source_event_id": str(payload.get("rework_of") or ""),
            "attempt": int(payload.get("rework_attempt") or 0),
            "source_event_type": str(payload.get("rework_source") or ""),
            "feedback": tuple(_string_list(payload.get("rework_feedback"))),
            "failure_categories": tuple(
                _string_list(payload.get("rework_categories"))
            ),
            "rework_summary": (
                payload.get("rework_summary")
                if isinstance(payload.get("rework_summary"), dict)
                else {}
            ),
            "classification": str(payload.get("classification") or ""),
            "failed_task_ids": tuple(_string_list(payload.get("failed_task_ids"))),
            "task_ids": tuple(_string_list(payload.get("task_ids"))),
            "downstream_task_ids": tuple(
                _string_list(payload.get("downstream_task_ids"))
            ),
            "resume_scope": str(payload.get("resume_scope") or ""),
        }
        for key in _CANDIDATE_REWORK_IDENTITY_KEYS:
            value = payload.get(key)
            if value not in (None, "", [], {}):
                plan_data[key] = value
        desired = build_replan_resynth_event(
            plan=SimpleNamespace(**plan_data),
            events=events,
            config=config,
        )
        if desired is None:
            continue
        desired_rework_of = str(desired.payload.get("rework_of") or "")
        advanced = False
        for later in events[source_index + 1:]:
            later_payload = later.payload if isinstance(later.payload, dict) else {}
            later_trace = str(
                later_payload.get("workflow_run_id")
                or later_payload.get("trace_id")
                or later.correlation_id
                or ""
            ).strip()
            later_pdd = str(
                later_payload.get("pdd_id")
                or later_payload.get("feature_id")
                or ""
            ).strip()
            if later_trace and trace_id and later_trace != trace_id:
                continue
            if later_pdd and pdd_id and later_pdd != pdd_id:
                continue
            if (
                later.type == desired.type
                and str(later_payload.get("rework_of") or "") == desired_rework_of
            ):
                advanced = True
                break
            if later.type in {"task_map.ready", "candidate.ready"}:
                advanced = True
                break
        if not advanced:
            repairs.append(desired)
    return repairs


def _string_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if str(item).strip()]


def _latest_trigger_payload(
    events: Sequence[ZfEvent],
    *,
    trigger: str,
    trace_id: str,
) -> dict:
    payload: dict = {}
    for event in events:
        if event.type != trigger:
            continue
        candidate = event.payload if isinstance(event.payload, dict) else {}
        event_trace = str(candidate.get("trace_id") or event.correlation_id or "")
        if trace_id and event_trace and event_trace != trace_id:
            continue
        payload = dict(candidate)
    return payload
