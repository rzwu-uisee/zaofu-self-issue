from __future__ import annotations

from zf.core.task.schema import Task, TaskContract
from zf.runtime.workflow_request_acceptance import inherit_task_acceptance


def test_inherits_canonical_task_acceptance_when_request_omits_it() -> None:
    task = Task(
        id="TASK-1",
        title="delivery",
        contract=TaskContract(acceptance_criteria=[{
            "id": "AC-1",
            "statement": "The regression command passes.",
        }]),
    )

    parameters = inherit_task_acceptance({"target_ref": "HEAD"}, task)

    assert parameters["acceptance"] == ["The regression command passes."]


def test_explicit_request_acceptance_is_not_overwritten() -> None:
    task = Task(
        id="TASK-1",
        title="delivery",
        contract=TaskContract(acceptance_criteria=["canonical acceptance"]),
    )

    parameters = inherit_task_acceptance(
        {"acceptance": ["approved revision"]},
        task,
    )

    assert parameters["acceptance"] == ["approved revision"]
