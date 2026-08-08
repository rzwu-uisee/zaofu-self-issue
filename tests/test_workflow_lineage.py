from __future__ import annotations

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.workflow_lineage import (
    WorkflowLineageError,
    bind_workflow_task_lineage,
    resolve_workflow_run_lineage,
)


def test_workflow_lineage_uses_direct_admission_anchor() -> None:
    events = [ZfEvent(
        id="evt-accepted",
        type="workflow.invoke.accepted",
        task_id="FLOW-1",
        payload={"workflow_run_id": "run-1", "task_id": "FLOW-1"},
        correlation_id="run-1",
    )]

    lineage = resolve_workflow_run_lineage(events, "run-1")
    payload: dict[str, str] = {}
    task_id, parent_task_id = bind_workflow_task_lineage(
        events,
        workflow_run_id="run-1",
        payload=payload,
    )

    assert lineage.parent_task_id == "FLOW-1"
    assert lineage.source_event_ids == ("evt-accepted",)
    assert (task_id, parent_task_id) == ("FLOW-1", "FLOW-1")
    assert payload == {
        "workflow_run_id": "run-1",
        "task_id": "FLOW-1",
        "parent_task_id": "FLOW-1",
    }


def test_workflow_lineage_rejects_conflicting_parent_anchors() -> None:
    events = [
        ZfEvent(
            type="workflow.invoke.accepted",
            task_id=task_id,
            payload={"workflow_run_id": "run-1", "task_id": task_id},
        )
        for task_id in ("FLOW-1", "FLOW-2")
    ]

    with pytest.raises(WorkflowLineageError, match="conflicting parent tasks"):
        resolve_workflow_run_lineage(events, "run-1")
