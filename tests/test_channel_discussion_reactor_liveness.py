"""doc 122 live-reactor gate — channel discussion progression must fire on the
event that lands, not wait for the periodic deadline sweep.

Regression guard for the racing-codex e2e (rounds 1-3): every discussion
`phase.changed` showed `source="deadline-sweep"`, because the taskless
`channel.agent.reply.completed` event (correlation_id=channel_id, no task_id)
was dropped by both the Layer-1 (task_id-gated) and Layer-2-active
(terminal-ownership-gated) dispatch paths in `Orchestrator._react_to_events`.

The existing `test_channel_discussion_driver.py` calls `advance_discussion`
and `sweep_discussion_deadlines` DIRECTLY, so it never exercised the
`_react_to_events` gate. This suite drives the event through the real
`run_once` loop under an L2-active config (an `orchestrator` role is present),
proving the deterministic `_on_channel_discussion_event` handler runs live via
the `_KERNEL_LIVENESS_EVENTS` fast-path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.session import SessionStore
from zf.runtime.channel_projection import project_channel
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


CHANNEL_ID = "ch-disc"
TRIGGER = "m-req"
ROSTER = ["pm-1", "arch-1", "critic-1"]


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    EventLog(sd / "events.jsonl").append(ZfEvent(type="loop.started", actor="zf-cli"))
    SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
    (sd / "kanban.json").write_text("[]\n")
    return sd


@pytest.fixture
def config() -> ZfConfig:
    # An `orchestrator` role makes _react_to_events layer2_active=True — the
    # harder path where taskless mechanical events are otherwise forwarded to
    # the L2 agent instead of run by the deterministic handler.
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(name="orchestrator", backend="mock"),
            RoleConfig(name="dev", backend="mock"),
        ],
    )


@pytest.fixture
def transport() -> TmuxTransport:
    return TmuxTransport(TmuxSession(session_name="t", dry_run=True))


def _seed_phase1_two_of_three_replied(log: EventLog) -> None:
    """Fold a discussion sitting in phase1_blind with pm-1 + arch-1 already
    completed and critic-1 still pending — one reply short of phase-1 complete.
    """
    log.append(ZfEvent(
        type="channel.created",
        actor="web",
        payload={"channel_id": CHANNEL_ID, "name": "disc", "source": "web"},
        correlation_id=CHANNEL_ID,
    ))
    for member in ROSTER:
        log.append(ZfEvent(
            type="channel.member.added",
            actor="web",
            payload={
                "channel_id": CHANNEL_ID,
                "thread_id": "main",
                "member_id": member,
                "member_type": "provider_agent",
                "provider": "codex",
                "backend": "codex",
                "permissions": ["read", "message"],
                "source": "web",
            },
            correlation_id=CHANNEL_ID,
        ))
    log.append(ZfEvent(
        type="channel.discussion.started",
        actor="channel-discussion",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "trigger": "@all 做一个 three.js 赛车小游戏",
            "roster": ROSTER,
            "synthesizer": "pm-1",
            "requirement_message_id": TRIGGER,
            "source": "runtime",
        },
        correlation_id=CHANNEL_ID,
    ))
    for member in ROSTER:
        log.append(ZfEvent(
            type="channel.agent.reply.requested",
            actor="channel-discussion",
            payload={
                "channel_id": CHANNEL_ID,
                "thread_id": "main",
                "request_id": f"reply-{member}",
                "message_id": TRIGGER,
                "target_member_id": member,
                "status": "pending",
                "source": "runtime",
            },
            correlation_id=CHANNEL_ID,
        ))
    # pm-1 + arch-1 finish; critic-1 stays pending.
    for member in ("pm-1", "arch-1"):
        log.append(ZfEvent(
            type="channel.agent.reply.completed",
            actor=member,
            payload={
                "channel_id": CHANNEL_ID,
                "thread_id": "main",
                "request_id": f"reply-{member}",
                "message_id": TRIGGER,
                "target_member_id": member,
                "source": "runtime",
            },
            correlation_id=CHANNEL_ID,
        ))


def _phase_changes(log: EventLog) -> list[ZfEvent]:
    return [e for e in log.read_all() if e.type == "channel.discussion.phase.changed"]


def test_final_reply_completed_advances_phase_live_not_via_deadline_sweep(
    state_dir: Path, config: ZfConfig, transport: TmuxTransport,
) -> None:
    """The last roster member's `channel.agent.reply.completed`, dispatched
    through the real `run_once` loop, must transition the discussion to
    phase2_relay within that same cycle — with a source that is NOT
    `deadline-sweep`. Without the `_KERNEL_LIVENESS_EVENTS` entry the event is
    forwarded to the L2 agent and no phase.changed is produced at all.
    """
    log = EventLog(state_dir / "events.jsonl")
    _seed_phase1_two_of_three_replied(log)

    # Precondition: two replies in, still phase1_blind (critic-1 pending).
    detail = project_channel(state_dir, CHANNEL_ID)
    assert detail["discussions"]["main"]["state"] == "phase1_blind"
    assert _phase_changes(log) == []

    orch = Orchestrator(state_dir, config, transport)
    assert orch._find_role_by_name("orchestrator") is not None  # L2-active path

    final = orch.event_writer.emit(
        "channel.agent.reply.completed",
        actor="critic-1",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "request_id": "reply-critic-1",
            "message_id": TRIGGER,
            "target_member_id": "critic-1",
            "source": "runtime",
        },
        correlation_id=CHANNEL_ID,
    )

    orch.run_once(events=[final])

    changes = _phase_changes(log)
    to_phase2 = [e for e in changes if e.payload.get("phase") == "phase2_relay"]
    assert len(to_phase2) == 1, (
        "final reply.completed must drive exactly one live phase1->phase2 "
        f"transition through run_once; got {[e.payload for e in changes]}"
    )
    assert to_phase2[0].payload.get("source") != "deadline-sweep", (
        "phase transition fired via the deadline-sweep fallback, not the live "
        f"reactor: {to_phase2[0].payload}"
    )
    assert to_phase2[0].payload.get("reason") == "blind_round_complete"

    detail = project_channel(state_dir, CHANNEL_ID)
    assert detail["discussions"]["main"]["state"] == "phase2_relay"


def test_non_final_reply_completed_does_not_advance(
    state_dir: Path, config: ZfConfig, transport: TmuxTransport,
) -> None:
    """A reply.completed that still leaves a roster member pending must be a
    no-op through run_once — the live-reactor tick is idempotent against the
    folded state, so it neither over-advances nor errors."""
    log = EventLog(state_dir / "events.jsonl")
    # Only pm-1 has replied; arch-1 + critic-1 still pending.
    log.append(ZfEvent(
        type="channel.created", actor="web",
        payload={"channel_id": CHANNEL_ID, "name": "disc", "source": "web"},
        correlation_id=CHANNEL_ID,
    ))
    log.append(ZfEvent(
        type="channel.discussion.started", actor="channel-discussion",
        payload={
            "channel_id": CHANNEL_ID, "thread_id": "main", "trigger": "@all x",
            "roster": ROSTER, "synthesizer": "pm-1",
            "requirement_message_id": TRIGGER, "source": "runtime",
        },
        correlation_id=CHANNEL_ID,
    ))
    for member in ROSTER:
        log.append(ZfEvent(
            type="channel.agent.reply.requested", actor="channel-discussion",
            payload={
                "channel_id": CHANNEL_ID, "thread_id": "main",
                "request_id": f"reply-{member}", "message_id": TRIGGER,
                "target_member_id": member, "status": "pending", "source": "runtime",
            },
            correlation_id=CHANNEL_ID,
        ))

    orch = Orchestrator(state_dir, config, transport)
    first = orch.event_writer.emit(
        "channel.agent.reply.completed", actor="pm-1",
        payload={
            "channel_id": CHANNEL_ID, "thread_id": "main",
            "request_id": "reply-pm-1", "message_id": TRIGGER,
            "target_member_id": "pm-1", "source": "runtime",
        },
        correlation_id=CHANNEL_ID,
    )

    orch.run_once(events=[first])

    assert _phase_changes(log) == []
    detail = project_channel(state_dir, CHANNEL_ID)
    assert detail["discussions"]["main"]["state"] == "phase1_blind"


def test_synthesis_request_dispatches_live_through_layer2_kernel_path(
    state_dir: Path, config: ZfConfig, transport: TmuxTransport,
) -> None:
    """A taskless synthesis request is a mechanical Channel side effect.

    It must execute through the real Layer-2-active ``run_once`` path instead
    of being forwarded to an orchestrator role that does not own Channel
    runtime state.
    """
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="channel.created",
        actor="web",
        payload={"channel_id": CHANNEL_ID, "name": "disc", "source": "web"},
        correlation_id=CHANNEL_ID,
    ))
    log.append(ZfEvent(
        type="channel.member.added",
        actor="web",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "member_id": "pm-1",
            "member_type": "provider_agent",
            "provider": "fake",
            "backend": "fake",
            "permissions": ["read", "message"],
            "source": "web",
        },
        correlation_id=CHANNEL_ID,
    ))

    orch = Orchestrator(state_dir, config, transport)
    requested = orch.event_writer.emit(
        "channel.synthesis.requested",
        actor="channel-discussion",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "request_id": "synth-live-1",
            "target_member_id": "pm-1",
            "prompt": "Synthesize the decision.",
            "source": "runtime",
        },
        correlation_id=CHANNEL_ID,
    )

    orch.run_once(events=[requested])

    events = log.read_all()
    synthesis_messages = [
        event for event in events
        if event.type == "channel.message.posted"
        and isinstance(event.payload.get("refs"), dict)
        and event.payload["refs"].get("synthesis_request_id") == "synth-live-1"
    ]
    assert len(synthesis_messages) == 1
    proposals = [
        event for event in events
        if event.type == "channel.synthesis.proposed"
        and event.payload.get("request_id") == "synth-live-1"
    ]
    assert len(proposals) == 1


def test_synthesis_replay_routes_message_left_before_reply_request(
    state_dir: Path, config: ZfConfig, transport: TmuxTransport,
) -> None:
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    writer.emit(
        "channel.created",
        actor="web",
        correlation_id=CHANNEL_ID,
        payload={"channel_id": CHANNEL_ID, "name": "disc", "source": "web"},
    )
    writer.emit(
        "channel.member.added",
        actor="web",
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "member_id": "pm-1",
            "member_type": "provider_agent",
            "provider": "fake",
            "backend": "fake",
            "permissions": ["read", "message"],
            "source": "web",
        },
    )
    requested = writer.emit(
        "channel.synthesis.requested",
        actor="channel-discussion",
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "request_id": "synth-replay-1",
            "target_member_id": "pm-1",
            "prompt": "Synthesize the decision.",
            "source": "runtime",
        },
    )
    writer.emit(
        "channel.message.posted",
        actor="orchestrator-reactor",
        causation_id=requested.id,
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "message_id": "msg-synth-replay-1",
            "member_id": "operator",
            "role": "user",
            "source": "runtime",
            "text": "@pm-1 Synthesize the decision.",
            "mentions": ["pm-1"],
            "refs": {"synthesis_request_id": "synth-replay-1"},
        },
    )
    orch = Orchestrator(state_dir, config, transport)

    orch.run_once(events=[requested])

    events = log.read_all()
    assert len([
        event for event in events
        if event.type == "channel.message.posted"
        and event.payload.get("refs", {}).get("synthesis_request_id")
        == "synth-replay-1"
    ]) == 1
    assert len([
        event for event in events
        if event.type == "channel.synthesis.proposed"
        and event.payload.get("request_id") == "synth-replay-1"
    ]) == 1
