"""avbs-r4 F3: 高频 hook 唤醒过滤 + watcher lag 自监控。

r4 三次 watcher 死亡螺旋(3.6s/事件 × 13k 事件,lag 峰值 4864s):
codex tool-use hook 每条都触发完整 run_once + decision.recorded 写入
(日志自我膨胀),而 post_tool_use handler 是 no-op、pre_tool_use 仅
deny 有意义。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.runtime.wake_patterns import wake_worthy


def test_post_tool_use_never_wakes() -> None:
    assert wake_worthy(ZfEvent(type="codex.hook.post_tool_use", payload={})) is False


def test_pre_tool_use_wakes_only_on_deny() -> None:
    allow = ZfEvent(type="codex.hook.pre_tool_use", payload={"permissionDecision": "allow"})
    deny = ZfEvent(type="codex.hook.pre_tool_use", payload={"permissionDecision": "deny"})
    assert wake_worthy(allow) is False
    assert wake_worthy(deny) is True


def test_lifecycle_hooks_and_workflow_events_wake() -> None:
    for etype in ("codex.hook.stop", "codex.hook.session_start", "task_map.ready", "review.rejected"):
        assert wake_worthy(ZfEvent(type=etype, payload={})) is True


def test_run_once_emits_lag_warning_when_backlogged(tmp_path: Path) -> None:
    from tests.test_writer_fanout_runtime import _state

    state_dir, log, transport, orch = _state(tmp_path)
    stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=1800)).isoformat()
    stale_event = ZfEvent(
        type="dev.build.done", actor="dev-1", ts=stale_ts,
        payload={"status": "completed"},
    )
    orch.run_once(events=[stale_event])
    warnings = [e for e in log.read_all() if e.type == "runtime.watcher.lag_warning"]
    assert len(warnings) == 1
    assert warnings[0].payload["trigger_lag_s"] > 300

    # 自身节流:同 run 内再次积压事件不重复告警
    orch.run_once(events=[ZfEvent(
        type="dev.build.done", actor="dev-1", ts=stale_ts,
        payload={"status": "completed"},
    )])
    warnings = [e for e in log.read_all() if e.type == "runtime.watcher.lag_warning"]
    assert len(warnings) == 1


def test_run_once_no_lag_warning_when_fresh(tmp_path: Path) -> None:
    from tests.test_writer_fanout_runtime import _state

    state_dir, log, transport, orch = _state(tmp_path)
    orch.run_once(events=[ZfEvent(type="dev.build.done", actor="dev-1", payload={})])
    assert not [e for e in log.read_all() if e.type == "runtime.watcher.lag_warning"]


def test_durable_nonwake_hook_batch_uses_layer1_fast_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tests.test_writer_fanout_runtime import _state
    from zf.core.events.writer import EventWriter
    from zf.runtime import orchestrator_periodic_sweep

    _state_dir, log, _transport, orch = _state(tmp_path)
    orch.session_store.create(project_root=str(tmp_path))  # type: ignore[attr-defined]
    hooks = [
        EventWriter(log).append(ZfEvent(
            type="codex.hook.pre_tool_use",
            actor="dev-1",
            payload={"permissionDecision": "allow"},
        )),
        EventWriter(log).append(ZfEvent(
            type="codex.hook.post_tool_use",
            actor="dev-1",
            payload={"tool_name": "Read"},
        )),
    ]
    durable_boundary = log.current_offset()
    housekept: list[str] = []
    callback_appends: list[ZfEvent] = []
    original_housekeeping = orch._apply_housekeeping  # type: ignore[attr-defined]

    def counted_housekeeping(event):  # noqa: ANN001
        housekept.append(event.id)
        original_housekeeping(event)
        if not callback_appends:
            callback_appends.append(EventWriter(log).append(ZfEvent(
                type="test.housekeeping.follow_up",
                actor="runtime",
                payload={"source_event_id": event.id},
            )))

    monkeypatch.setattr(orch, "_apply_housekeeping", counted_housekeeping)
    monkeypatch.setattr(
        orchestrator_periodic_sweep,
        "run_replay_sweep",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("pure Hook catch-up must not run replay sweep")
        ),
    )
    monkeypatch.setattr(
        orch,
        "_capture_logs",
        lambda: (_ for _ in ()).throw(
            AssertionError("pure Hook catch-up must not capture transports")
        ),
    )

    assert orch.run_once() == []
    assert housekept == [event.id for event in hooks]
    assert orch._load_offset() == durable_boundary  # type: ignore[attr-defined]
    remaining, _new_offset = log.read_from_offset(durable_boundary)
    assert [event.id for event in remaining] == [callback_appends[0].id]


def test_durable_hook_batch_with_control_event_uses_normal_cycle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tests.test_writer_fanout_runtime import _state
    from zf.core.events.writer import EventWriter
    from zf.runtime import orchestrator_periodic_sweep

    _state_dir, log, _transport, orch = _state(tmp_path)
    orch.session_store.create(project_root=str(tmp_path))  # type: ignore[attr-defined]
    EventWriter(log).append(ZfEvent(
        type="codex.hook.post_tool_use",
        actor="dev-1",
        payload={"tool_name": "Read"},
    ))
    EventWriter(log).append(ZfEvent(
        type="user.message",
        actor="owner",
        payload={"text": "continue"},
    ))
    durable_boundary = log.current_offset()
    replay_calls: list[object] = []
    monkeypatch.setattr(
        orchestrator_periodic_sweep,
        "run_replay_sweep",
        lambda runtime, *, events: replay_calls.append((runtime, events)),
    )

    orch.run_once()

    assert replay_calls == [(orch, None)]
    assert orch._load_offset() == durable_boundary  # type: ignore[attr-defined]


def test_durable_nonhook_observation_uses_normal_cycle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tests.test_writer_fanout_runtime import _state
    from zf.core.events.writer import EventWriter
    from zf.runtime import orchestrator_periodic_sweep

    _state_dir, log, _transport, orch = _state(tmp_path)
    orch.session_store.create(project_root=str(tmp_path))  # type: ignore[attr-defined]
    EventWriter(log).append(ZfEvent(
        type="runtime.snapshot.recorded",
        actor="zf-cli",
        payload={"snapshot_ref": "runtime-snapshot.json"},
    ))
    replay_calls: list[object] = []
    monkeypatch.setattr(
        orchestrator_periodic_sweep,
        "run_replay_sweep",
        lambda runtime, *, events: replay_calls.append((runtime, events)),
    )

    orch.run_once()

    assert replay_calls == [(orch, None)]
