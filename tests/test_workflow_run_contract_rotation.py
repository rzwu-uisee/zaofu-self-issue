from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.workflow_preflight import (
    _terminal_run_contract_rotation_context,
)


def _append(log: EventLog, event_type: str, *, run_id: str, **payload: str) -> None:
    log.append(ZfEvent(
        type=event_type,
        actor="test",
        correlation_id=run_id,
        payload={"run_id": run_id, **payload},
    ))


def _bind(log: EventLog, *, run_id: str, digest: str) -> None:
    _append(
        log,
        "config.run_contract.request_bound",
        run_id=run_id,
        contract_digest=digest,
    )


def test_terminal_bound_run_allows_distinct_request_rotation(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    _bind(log, run_id="run-old", digest="digest-old")
    _append(log, "run.admission.admitted", run_id="run-old")
    _append(log, "run.goal.blocked", run_id="run-old", status="blocked")

    result = _terminal_run_contract_rotation_context(
        state_dir=state_dir,
        previous={"contract_digest": "digest-old"},
        current_request_id="run-new",
    )

    assert result["prior_run_id"] == "run-old"
    assert result["prior_terminal_type"] == "run.goal.blocked"
    assert result["prior_terminal_event_id"]


def test_active_bound_run_cannot_rotate_contract(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    _bind(log, run_id="run-old", digest="digest-old")
    _append(log, "run.admission.admitted", run_id="run-old")

    assert _terminal_run_contract_rotation_context(
        state_dir=state_dir,
        previous={"contract_digest": "digest-old"},
        current_request_id="run-new",
    ) == {}


def test_same_request_cannot_rotate_terminal_contract(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    _bind(log, run_id="run-old", digest="digest-old")
    _append(log, "run.goal.blocked", run_id="run-old", status="blocked")

    assert _terminal_run_contract_rotation_context(
        state_dir=state_dir,
        previous={"contract_digest": "digest-old"},
        current_request_id="run-old",
    ) == {}


def test_terminal_contract_cannot_rotate_while_another_run_is_active(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    _bind(log, run_id="run-old", digest="digest-old")
    _append(log, "run.goal.blocked", run_id="run-old", status="blocked")
    _append(log, "run.admission.admitted", run_id="run-other")

    assert _terminal_run_contract_rotation_context(
        state_dir=state_dir,
        previous={"contract_digest": "digest-old"},
        current_request_id="run-new",
    ) == {}
