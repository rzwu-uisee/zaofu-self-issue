"""Autoresearch resident transport for provider-backed evolution requests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter


EVOLUTION_REQUEST_TYPES = frozenset({
    "evolution.trial.requested",
    "evolution.canary.requested",
    "evolution.skill_optimizer.proposal.requested",
})
EVOLUTION_EXECUTION_ACCEPTED = "evolution.trial.execution.accepted"
EVOLUTION_EXECUTION_STARTED = "evolution.trial.execution.started"
EVOLUTION_EXECUTION_COMPLETED = "evolution.trial.execution.completed"
EVOLUTION_EXECUTION_FAILED = "evolution.trial.execution.failed"
OPTIMIZER_EXECUTION_ACCEPTED = "evolution.skill_optimizer.execution.accepted"
OPTIMIZER_EXECUTION_STARTED = "evolution.skill_optimizer.execution.started"
OPTIMIZER_EXECUTION_COMPLETED = "evolution.skill_optimizer.execution.completed"
OPTIMIZER_EXECUTION_FAILED = "evolution.skill_optimizer.execution.failed"


def pending_evolution_requests(events: list[ZfEvent]) -> list[ZfEvent]:
    terminal_request_ids = {
        str(_payload(event).get("request_event_id") or "")
        for event in events
        if event.type in {
            EVOLUTION_EXECUTION_COMPLETED,
            EVOLUTION_EXECUTION_FAILED,
            OPTIMIZER_EXECUTION_COMPLETED,
            OPTIMIZER_EXECUTION_FAILED,
        }
    }
    return [
        event for event in events
        if event.type in EVOLUTION_REQUEST_TYPES and event.id not in terminal_request_ids
    ]


def evolution_accepted_ids(events: list[ZfEvent]) -> set[str]:
    return {
        str(_payload(event).get("request_event_id") or "")
        for event in events
        if event.type in {EVOLUTION_EXECUTION_ACCEPTED, OPTIMIZER_EXECUTION_ACCEPTED}
        and str(_payload(event).get("request_event_id") or "")
    }


def plan_evolution_actions(
    events: list[ZfEvent],
    *,
    state_dir: Path,
    action_factory: Callable[..., Any],
) -> list[Any]:
    actions: list[Any] = []
    for event in pending_evolution_requests(events):
        payload = _payload(event)
        optimizer = event.type == "evolution.skill_optimizer.proposal.requested"
        actions.append(action_factory(
            loop_request_id=event.id,
            kind="skill_optimizer" if optimizer else "evolution_trial",
            action=("run_skill_optimizer_agent" if optimizer else "run_evolution_trial"),
            reason=f"execute {event.type}",
            command=[
                sys.executable, "-m", "zf.cli.main", "evolution",
                (
                    "skill-opt-agent-execute"
                    if optimizer else "trial-execute"
                ),
                "--state-dir", str(state_dir),
                "--request-event-id", event.id,
            ],
            budget_cap={
                "max_minutes": max(
                    1, int(payload.get("timeout_seconds") or 300) // 60 + 1
                ),
            },
        ))
    return actions


def evolution_acceptance(
    action: Any,
    *,
    events: list[ZfEvent],
) -> tuple[str, dict[str, Any]] | None:
    if action.action not in {"run_evolution_trial", "run_skill_optimizer_agent"}:
        return None
    event_type = (
        OPTIMIZER_EXECUTION_ACCEPTED
        if action.action == "run_skill_optimizer_agent"
        else EVOLUTION_EXECUTION_ACCEPTED
    )
    return event_type, {
        **_identity_payload(action, events=events),
        "loop_request_id": action.loop_request_id,
        "queued": True,
        "command": action.command,
    }


def run_evolution_action(
    *,
    writer: EventWriter,
    action: Any,
    events: list[ZfEvent],
    enabled: bool,
    timeout_s: int,
    runner: Callable[..., subprocess.CompletedProcess],
) -> bool:
    """Execute an evolution action and return whether it was consumed."""

    if action.action not in {"run_evolution_trial", "run_skill_optimizer_agent"}:
        return False
    if not enabled:
        return True
    identity = _identity_payload(action, events=events)
    optimizer = action.action == "run_skill_optimizer_agent"
    writer.append(ZfEvent(
        type=OPTIMIZER_EXECUTION_STARTED if optimizer else EVOLUTION_EXECUTION_STARTED,
        actor="zf-autoresearch-resident",
        causation_id=action.loop_request_id,
        payload={**identity, "command": action.command},
    ))
    try:
        proc = runner(
            action.command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        proc = subprocess.CompletedProcess(
            action.command,
            returncode=124,
            stdout=str(exc.stdout or "")[-2000:],
            stderr=f"evolution trial timed out after {timeout_s}s",
        )
    writer.append(ZfEvent(
        type=(
            (OPTIMIZER_EXECUTION_COMPLETED if optimizer else EVOLUTION_EXECUTION_COMPLETED)
            if proc.returncode == 0
            else (OPTIMIZER_EXECUTION_FAILED if optimizer else EVOLUTION_EXECUTION_FAILED)
        ),
        actor="zf-autoresearch-resident",
        causation_id=action.loop_request_id,
        payload={
            **identity,
            "returncode": proc.returncode,
            "stdout_tail": (proc.stdout or "")[-2000:],
            "stderr_tail": (proc.stderr or "")[-2000:],
        },
    ))
    return True


def _identity_payload(action: Any, *, events: list[ZfEvent]) -> dict[str, Any]:
    request = next((event for event in events if event.id == action.loop_request_id), None)
    payload = _payload(request) if request is not None else {}
    return {
        "request_event_id": action.loop_request_id,
        "request_event_type": request.type if request is not None else "",
        "campaign_id": str(payload.get("campaign_id") or ""),
        "trial_id": str(payload.get("trial_id") or ""),
        "asset_id": str(payload.get("asset_id") or ""),
        "version": int(payload.get("version") or 0),
        "optimizer_request_key": str(payload.get("request_key") or ""),
    }


def _payload(event: ZfEvent | None) -> dict[str, Any]:
    if event is None or not isinstance(event.payload, dict):
        return {}
    return event.payload


__all__ = [
    "evolution_accepted_ids",
    "evolution_acceptance",
    "pending_evolution_requests",
    "plan_evolution_actions",
    "run_evolution_action",
]
