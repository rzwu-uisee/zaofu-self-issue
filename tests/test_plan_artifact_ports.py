from __future__ import annotations

import pytest

from zf.runtime.plan_artifact_ports import (
    PLAN_ARTIFACT_PORT_ADAPTER_VERSION,
    adapt_issue_requirement_port,
    coerce_plan_port_descriptors,
    normalize_plan_ports,
    plan_port_adapter,
)


@pytest.mark.parametrize(
    ("source", "canonical"),
    [
        ("product_spec", "requirement_spec"),
        ("prd_ref", "requirement_spec"),
        ("issue_ref", "issue_spec"),
        ("task_map", "task_map"),
    ],
)
def test_plan_port_adapter_has_one_versioned_mapping(source, canonical):
    assert plan_port_adapter(source) == {
        "logical_name": canonical,
        "source_logical_name": source,
        "adapter_version": PLAN_ARTIFACT_PORT_ADAPTER_VERSION,
    }


def test_normalize_plan_ports_rejects_alias_collisions():
    with pytest.raises(ValueError, match="duplicate canonical"):
        normalize_plan_ports([
            {"logical_name": "product_spec", "ref": "a", "sha256": "1"},
            {"logical_name": "prd_ref", "ref": "b", "sha256": "2"},
        ])


def test_coerce_plan_port_descriptors_accepts_name_to_body_shorthand():
    body = {
        "schema_version": "acceptance-matrix.v1",
        "status": "ready",
        "rows": [{"acceptance_id": "AC-1"}],
    }

    assert coerce_plan_port_descriptors({"acceptance_matrix": body}) == [{
        "logical_name": "acceptance_matrix",
        "schema_version": "acceptance-matrix.v1",
        "body": body,
    }]


def test_explicit_issue_spec_takes_precedence_over_requirement_adapter():
    ports = {
        "requirement_spec": {"ref": "requirement.json", "sha256": "req"},
        "issue_spec": {"ref": "issue.json", "sha256": "issue"},
    }

    adapt_issue_requirement_port(ports, flow_kind="issue")

    assert ports["issue_spec"] == {
        "ref": "issue.json",
        "sha256": "issue",
    }
