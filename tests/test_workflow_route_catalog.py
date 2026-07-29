from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from zf.core.config.loader import load_config
from zf.runtime.workflow_route_catalog import (
    resolve_workflow_route,
    workflow_route_catalog,
)


ROOT = Path(__file__).resolve().parents[1]


def test_root_catalog_projects_delivery_and_fixed_research_routes() -> None:
    config = load_config(ROOT / "zf.yaml")

    first = workflow_route_catalog(config)
    second = workflow_route_catalog(config)

    assert first == second
    assert first["config_digest"].startswith("sha256:")
    routes = {
        route["route_id"]: route
        for route in first["routes"]
    }
    delivery = routes["delivery:prd:standard"]
    assert delivery["entry_pattern_id"] == "prd-scan"
    assert delivery["lane_count"] == 2
    assert delivery["topology"] == "multi_lane"
    assert set(delivery["writer_roles"]) == {"dev-lane-0", "dev-lane-1"}
    assert {"verify-lane-0", "verify-lane-1"} <= set(
        delivery["verify_roles"]
    )

    research = routes["research:fixed"]
    assert research["topology"] == "fanout_reader"
    assert research["lane_count"] == 0
    assert research["writer_roles"] == []
    assert research["roles"] == [
        "source_researcher",
        "product_analyst",
        "technical_analyst",
        "risk_critic",
        "synthesizer",
    ]


def test_catalog_only_exposes_registered_reader_general_entries() -> None:
    config = SimpleNamespace(
        roles=[
            SimpleNamespace(
                name="reviewer",
                instance_id="reviewer",
                role_kind="reader",
            ),
            SimpleNamespace(
                name="writer",
                instance_id="writer",
                role_kind="writer",
            ),
        ],
        workflow=SimpleNamespace(
            kind_routes={},
            affinity_lanes={},
            stages=[
                SimpleNamespace(
                    id="architecture-review",
                    trigger="workflow.invoke.requested",
                    topology="fanout_reader",
                    roles=["reviewer"],
                    flow_kind="",
                ),
                SimpleNamespace(
                    id="internal-review",
                    trigger="review.requested",
                    topology="fanout_reader",
                    roles=["reviewer"],
                    flow_kind="",
                ),
                SimpleNamespace(
                    id="unsafe-general-writer",
                    trigger="workflow.invoke.requested",
                    topology="fanout_writer",
                    roles=["writer"],
                    flow_kind="",
                ),
            ],
        ),
    )

    catalog = workflow_route_catalog(config)

    assert [route["route_id"] for route in catalog["routes"]] == [
        "general:architecture-review",
    ]
    assert resolve_workflow_route(
        config,
        "general:architecture-review",
        expected_config_digest=catalog["config_digest"],
    ) is not None
    assert resolve_workflow_route(
        config,
        "general:architecture-review",
        expected_config_digest="sha256:stale",
    ) is None
