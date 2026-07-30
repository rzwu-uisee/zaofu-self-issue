from __future__ import annotations

import pytest

from zf.runtime.workflow_origin import (
    WorkflowOriginError,
    assert_same_workflow_origin,
    build_workflow_origin_binding,
    normalize_workflow_origin_binding,
    workflow_origin_digest,
    workflow_origin_from_manifest,
    workflow_origin_from_request,
)


def test_channel_origin_is_canonical_and_digest_stable() -> None:
    binding = build_workflow_origin_binding(
        source="kanban-agent",
        project_id="demo",
        channel_id="product-review",
        thread_id="scope",
        conversation_id="ignored",
        thread_key="ignored",
    )

    assert binding == {
        "schema_version": "workflow-origin-binding.v1",
        "surface": "channel",
        "source": "kanban-agent",
        "project_id": "demo",
        "channel_id": "product-review",
        "thread_id": "scope",
        "conversation_id": "",
        "thread_key": "",
    }
    assert workflow_origin_digest(binding) == workflow_origin_digest(
        dict(reversed(list(binding.items())))
    )


def test_origin_mismatch_fails_closed() -> None:
    expected = build_workflow_origin_binding(
        source="kanban-agent",
        project_id="demo",
        channel_id="product-review",
        thread_id="scope",
    )
    actual = {
        **expected,
        "thread_id": "other",
    }

    with pytest.raises(WorkflowOriginError, match="canonical request origin"):
        assert_same_workflow_origin(expected, actual)


def test_legacy_request_origin_is_read_compatible() -> None:
    binding = workflow_origin_from_request({
        "source": "legacy",
        "channel_id": "legacy-channel",
        "thread_id": "",
    })

    assert binding["surface"] == "channel"
    assert binding["thread_id"] == "main"
    assert binding["project_id"] == ""


def test_legacy_channel_manifest_without_project_is_read_compatible() -> None:
    binding = workflow_origin_from_manifest({
        "source": "legacy",
        "channel_id": "legacy-channel",
        "thread_id": "",
    })

    assert binding["surface"] == "channel"
    assert binding["thread_id"] == "main"
    assert binding["project_id"] == ""


def test_invalid_origin_surface_is_rejected() -> None:
    with pytest.raises(WorkflowOriginError, match="surface"):
        normalize_workflow_origin_binding({
            "schema_version": "workflow-origin-binding.v1",
            "surface": "email",
            "project_id": "demo",
        })
