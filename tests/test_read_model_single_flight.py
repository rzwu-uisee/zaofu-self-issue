"""RF-5: concurrent tail reads schedule one catch-up without joining it."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.web.projections import read_model


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "kanban.json").write_text("[]", encoding="utf-8")
    log = EventLog(sd / "events.jsonl")
    for i in range(50):
        log.append(ZfEvent(type="session.started", actor="t", payload={"i": i}))
    return sd


def test_concurrent_ensure_requested_runs_one_rebuild(state_dir, monkeypatch):
    read_model.rebuild(state_dir)  # baseline build
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(type="session.started", actor="t", payload={"tail": True}),
    )  # tail growth -> tail_behind

    calls = []
    barrier = threading.Barrier(6)
    rebuild_started = threading.Event()
    release_rebuild = threading.Event()
    real_rebuild = read_model.rebuild

    def counting_rebuild(sd, *, config=None):
        calls.append(threading.current_thread().name)
        rebuild_started.set()
        release_rebuild.wait(timeout=10)
        return real_rebuild(sd, config=config)

    monkeypatch.setattr(read_model, "rebuild", counting_rebuild)

    results = []

    def hit():
        barrier.wait()
        results.append(read_model.ensure_requested(state_dir))

    threads = [threading.Thread(target=hit, name=f"req-{i}") for i in range(6)]
    for t in threads:
        t.start()
    try:
        for t in threads:
            t.join(timeout=2)

        assert rebuild_started.wait(timeout=2)
        assert len(calls) == 1, f"expected single-flight, got {len(calls)} rebuilds"
        assert len(results) == 6
        assert all(r["projection_state"] == "ready" for r in results)
        assert all(r["tail_behind"] is True for r in results)
    finally:
        release_rebuild.set()

    job = read_model._JOBS.get(str(read_model.db_path(state_dir).resolve()))
    if job is not None:
        job.join(timeout=10)
    assert read_model.projection_status(state_dir)["tail_behind"] is False
