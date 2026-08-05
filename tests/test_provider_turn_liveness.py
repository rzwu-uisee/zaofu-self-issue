from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.provider_turn_liveness import active_codex_turn


def test_loop_stop_closes_prior_codex_turn_generation(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    writer.append(ZfEvent(
        type="codex.hook.user_prompt_submit",
        actor="dev",
        payload={"session_id": "session-old", "turn_id": "turn-old"},
    ))

    assert active_codex_turn(log, state_dir, "dev") is not None

    writer.append(ZfEvent(type="loop.stopped", actor="zf-cli"))

    assert active_codex_turn(log, state_dir, "dev") is None

    writer.append(ZfEvent(
        type="codex.hook.user_prompt_submit",
        actor="dev",
        payload={"session_id": "session-new", "turn_id": "turn-new"},
    ))

    active = active_codex_turn(log, state_dir, "dev")
    assert active is not None
    assert active["turn_id"] == "turn-new"


def test_worker_launch_closes_prior_codex_turn_generation(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    writer.append(ZfEvent(
        type="codex.hook.user_prompt_submit",
        actor="dev",
        payload={"session_id": "session-resumed", "turn_id": "turn-old"},
    ))

    writer.append(ZfEvent(
        type="worker.launch_artifact.written",
        actor="zf-cli",
        payload={
            "instance_id": "dev",
            "backend": "codex",
            "launch_attempt": 2,
            "is_resume": True,
        },
    ))

    assert active_codex_turn(log, state_dir, "dev") is None

    writer.append(ZfEvent(
        type="codex.hook.user_prompt_submit",
        actor="dev",
        payload={"session_id": "session-resumed", "turn_id": "turn-new"},
    ))

    active = active_codex_turn(log, state_dir, "dev")
    assert active is not None
    assert active["turn_id"] == "turn-new"
