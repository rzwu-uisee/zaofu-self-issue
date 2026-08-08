from pathlib import Path

from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.runtime.channel_synthesis_repair import reject_synthesis_contract


def test_reject_synthesis_contract_records_finding_and_bounded_repair(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))

    reject_synthesis_contract(
        state_dir=state_dir,
        writer=writer,
        channel_id="ch-test",
        thread_id="main",
        member_id="synth",
        request={"task_id": "TASK-SYNTH"},
        reply='{"channel_synthesis":',
        reply_event_id="evt-invalid",
        actor="test",
        source="test",
        status="invalid_channel_synthesis",
        reason="invalid JSON",
        synthesis_request_id="request-1",
        synthesis_repair_revision=0,
    )

    events = writer.event_log.read_all()
    finding = next(event for event in events if event.type == "channel.finding.recorded")
    repair = next(
        event for event in events
        if event.type == "channel.synthesis.repair.requested"
    )
    assert finding.payload["contract_error"] == "invalid JSON"
    assert repair.payload["repair_revision"] == 1
    assert (state_dir / repair.payload["invalid_reply_ref"]["ref"]).is_file()
