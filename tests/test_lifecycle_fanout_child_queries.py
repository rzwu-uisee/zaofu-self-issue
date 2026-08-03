"""_active_fanout_child_for_instance:fanout 级终局必须使 child 失效。

ZF-STOP-TAIL-01 邻居(07-16 实弹):被 supersede 取消的 fanout 其 child
被当 active,respawn recovery 反复给死 child 重注简报,worker 完成申报
在 flow 层永远无人承接(任务真相 review / 流程真相无此工序)。
"""
from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.lifecycle_evidence_queries import LifecycleEvidenceQueriesMixin


class _Host(LifecycleEvidenceQueriesMixin):
    def __init__(self, path: Path) -> None:
        self.event_log = EventLog(path)


def _dispatched(
    fanout_id: str,
    child_id: str,
    instance: str,
    *,
    run_id: str | None = None,
    task_id: str = "",
) -> ZfEvent:
    return ZfEvent(type="fanout.child.dispatched", actor="zf-cli", payload={
        "fanout_id": fanout_id,
        "child_id": child_id,
        "run_id": run_id or f"run-{fanout_id}-{child_id}",
        "role_instance": instance,
        "task_id": task_id,
    }, task_id=task_id or None)


def test_child_of_cancelled_fanout_is_not_active(tmp_path: Path) -> None:
    host = _Host(tmp_path / "events.jsonl")
    host.event_log.append(_dispatched("fanout-impl-1", "queued-T2-2", "dev-lane-0"))
    host.event_log.append(ZfEvent(
        type="fanout.cancelled", actor="zf-cli",
        payload={"fanout_id": "fanout-impl-1", "reason": "superseded_by_latest_fanout"},
    ))
    assert host._active_fanout_child_for_instance("dev-lane-0") is None


def test_child_of_live_fanout_stays_active(tmp_path: Path) -> None:
    host = _Host(tmp_path / "events.jsonl")
    host.event_log.append(_dispatched("fanout-impl-1", "queued-T2-2", "dev-lane-0"))
    # 另一个 fanout 被取消不影响本 child
    host.event_log.append(ZfEvent(
        type="fanout.cancelled", actor="zf-cli",
        payload={"fanout_id": "fanout-impl-OTHER"},
    ))
    child = host._active_fanout_child_for_instance("dev-lane-0")
    assert child is not None
    assert child["child_id"] == "queued-T2-2"


def test_timed_out_pending_children_are_terminal(tmp_path: Path) -> None:
    # 此前 fanout.timed_out 有处理分支但从未被扫描命中(标记过滤先行)
    host = _Host(tmp_path / "events.jsonl")
    host.event_log.append(_dispatched("fanout-impl-1", "queued-T2-2", "dev-lane-0"))
    host.event_log.append(ZfEvent(
        type="fanout.timed_out", actor="zf-cli",
        payload={"fanout_id": "fanout-impl-1", "pending_children": ["queued-T2-2"]},
    ))
    assert host._active_fanout_child_for_instance("dev-lane-0") is None


def test_stale_completion_terminates_exact_superseded_child(tmp_path: Path) -> None:
    host = _Host(tmp_path / "events.jsonl")
    host.event_log.append(_dispatched(
        "fanout-impl-old",
        "dev-lane-1-T1",
        "dev-lane-1",
        run_id="run-old",
        task_id="T1",
    ))
    host.event_log.append(ZfEvent(
        type="fanout.child.stale_completion",
        actor="zf-cli",
        task_id="T1",
        payload={
            "fanout_id": "fanout-impl-old",
            "child_id": "dev-lane-1-T1",
            "run_id": "run-old",
            "role_instance": "dev-lane-1",
            "task_id": "T1",
            "reason": "superseded_by_latest_fanout",
        },
    ))

    assert host._active_fanout_child_for_instance("dev-lane-1") is None
    assert host._fanout_task_state_for_instance("dev-lane-1", "T1") == "terminal"


def test_late_stale_completion_does_not_terminate_newer_run(tmp_path: Path) -> None:
    host = _Host(tmp_path / "events.jsonl")
    host.event_log.append(_dispatched(
        "fanout-impl",
        "dev-lane-1-T1",
        "dev-lane-1",
        run_id="run-old",
        task_id="T1",
    ))
    host.event_log.append(_dispatched(
        "fanout-impl",
        "dev-lane-1-T1",
        "dev-lane-1",
        run_id="run-new",
        task_id="T1",
    ))
    host.event_log.append(ZfEvent(
        type="fanout.child.stale_completion",
        actor="zf-cli",
        task_id="T1",
        payload={
            "fanout_id": "fanout-impl",
            "child_id": "dev-lane-1-T1",
            "run_id": "run-old",
            "role_instance": "dev-lane-1",
            "task_id": "T1",
            "reason": "superseded_by_latest_fanout",
        },
    ))

    child = host._active_fanout_child_for_instance("dev-lane-1")
    assert child is not None
    assert child["run_id"] == "run-new"
    assert host._fanout_task_state_for_instance("dev-lane-1", "T1") == "active"
