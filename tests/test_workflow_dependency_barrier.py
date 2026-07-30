from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import (
    FanoutAggregateConfig,
    ProjectConfig,
    RoleConfig,
    WorkflowConfig,
    WorkflowPortConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.events.model import ZfEvent
from zf.core.workflow.graph import compile_workflow_graph
from zf.runtime.execution_patterns import project_execution_patterns
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.workflow_dependency_barrier import (
    BLOCKED_EVENT,
    SATISFIED_EVENT,
    reconcile_dependency_barriers,
)


class _RecordingTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Path, str, object]] = []

    def send_task(
        self,
        role_name,
        briefing_path,
        prompt,
        *,
        context=None,
    ) -> None:
        self.sent.append((role_name, briefing_path, prompt, context))

    def is_alive(self, role_name) -> bool:
        return True

    def capture_log(self, role_name, lines=200) -> str:
        return ""

    def poll_events(self) -> list:
        return []


def _config() -> ZfConfig:
    aggregate_a = FanoutAggregateConfig(
        mode="wait_for_all",
        success_event="collect-a.completed",
        failure_event="collect-a.failed",
    )
    aggregate_b = FanoutAggregateConfig(
        mode="wait_for_all",
        success_event="collect-b.completed",
        failure_event="collect-b.failed",
    )
    aggregate_synth = FanoutAggregateConfig(
        mode="wait_for_all",
        success_event="synthesize.completed",
        failure_event="synthesize.failed",
    )
    return ZfConfig(
        project=ProjectConfig(name="barrier-test"),
        roles=[
            RoleConfig(
                name="collector-a",
                backend="mock",
                role_kind="reader",
            ),
            RoleConfig(
                name="collector-b",
                backend="mock",
                role_kind="reader",
            ),
            RoleConfig(
                name="synthesizer",
                backend="mock",
                role_kind="reader",
            ),
        ],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="collect-a",
                trigger="scope.completed",
                flow_kind="workflow",
                topology="fanout_reader",
                roles=["collector-a"],
                aggregate=aggregate_a,
            ),
            WorkflowStageConfig(
                id="collect-b",
                trigger="scope.completed",
                flow_kind="workflow",
                topology="fanout_reader",
                roles=["collector-b"],
                aggregate=aggregate_b,
            ),
            WorkflowStageConfig(
                id="synthesize",
                trigger=SATISFIED_EVENT,
                flow_kind="workflow",
                topology="fanout_reader",
                operation="agent.synthesize",
                dependencies=["collect-a", "collect-b"],
                dependency_events=[
                    "collect-a.completed",
                    "collect-b.completed",
                ],
                dependency_failure_events=[
                    "collect-a.failed",
                    "collect-b.failed",
                ],
                dependency_barrier_id="barrier:synthesize:test",
                dependency_barrier_digest="a" * 64,
                input_ports=[
                    WorkflowPortConfig(
                        name="evidence-a",
                        kind="evidence/bundle",
                        source="collect-a.evidence",
                    ),
                    WorkflowPortConfig(
                        name="evidence-b",
                        kind="evidence/bundle",
                        source="collect-b.evidence",
                    ),
                ],
                output_ports=[
                    WorkflowPortConfig(
                        name="report",
                        kind="report/markdown",
                    )
                ],
                roles=["synthesizer"],
                aggregate=aggregate_synth,
            ),
        ]),
    )


def _event(
    event_type: str,
    *,
    run_id: str = "run-1",
    revision: str = "1",
    payload: dict | None = None,
) -> ZfEvent:
    return ZfEvent(
        type=event_type,
        actor="zf-cli",
        correlation_id=run_id,
        payload={
            "workflow_run_id": run_id,
            "flow_kind": "workflow",
            "request_revision": revision,
            **(payload or {}),
        },
    )


def _anchor(run_id: str) -> ZfEvent:
    return _event("workflow.invoke.requested", run_id=run_id)


def test_barrier_requires_same_run_and_generation() -> None:
    config = _config()

    cross_run = reconcile_dependency_barriers(config, [
        _anchor("run-1"),
        _anchor("run-2"),
        _event("collect-a.completed", run_id="run-1"),
        _event("collect-b.completed", run_id="run-2"),
    ])
    cross_generation = reconcile_dependency_barriers(config, [
        _anchor("run-1"),
        _event("collect-a.completed", revision="1"),
        _event("collect-b.completed", revision="2"),
    ])

    assert cross_run == []
    assert cross_generation == []


def test_barrier_blocks_failure_then_allows_later_success() -> None:
    config = _config()
    base = [
        _anchor("run-1"),
        _event("collect-a.completed"),
        _event("collect-b.failed"),
    ]

    blocked = reconcile_dependency_barriers(config, base)
    blocked_event = blocked[0].to_event()
    passed = reconcile_dependency_barriers(
        config,
        [*base, blocked_event, _event("collect-b.completed")],
    )

    assert [item.event_type for item in blocked] == [BLOCKED_EVENT]
    assert [item.event_type for item in passed] == [SATISFIED_EVENT]


def test_barrier_replay_is_idempotent() -> None:
    config = _config()
    base = [
        _anchor("run-1"),
        _event("collect-a.completed"),
        _event("collect-b.completed"),
    ]
    first = reconcile_dependency_barriers(config, base)

    second = reconcile_dependency_barriers(
        config,
        [*base, first[0].to_event()],
    )

    assert len(first) == 1
    assert first[0].event_type == SATISFIED_EVENT
    assert second == []


def test_barrier_propagates_refs_from_every_dependency() -> None:
    config = _config()
    claim_ref = "artifacts/goal-closure/claim-sets/claim.json"
    required_artifacts = [
        {
            "name": "report",
            "kind": "report/markdown",
            "source_ref": "synthesize.report",
            "required_for": "standard",
        }
    ]
    first_source = _event(
        "collect-a.completed",
        payload={
            "goal_id": "goal-1",
            "workflow_intent": "research",
            "required_delivery_artifacts": required_artifacts,
            "goal_claim_set_ref": claim_ref,
            "goal_claim_set_digest": "b" * 64,
            "artifact_refs": [
                {"path": "artifacts/shared.json", "sha256": "a" * 64},
                "artifacts/collect-a.json",
            ],
            "evidence_refs": ["event:collect-a"],
            "input_result_refs": [
                "artifacts/call-results/envelopes/collect-a.json",
            ],
        },
    )
    second_source = _event(
        "collect-b.completed",
        payload={
            "goal_id": "goal-1",
            "workflow_intent": "research",
            "required_delivery_artifacts": required_artifacts,
            "goal_claim_set_ref": claim_ref,
            "goal_claim_set_digest": "b" * 64,
            "artifact_refs": [
                {"path": "artifacts/shared.json", "sha256": "a" * 64},
                "artifacts/collect-b.json",
            ],
            "input_refs": ["artifacts/input.json"],
            "input_result_refs": [
                "artifacts/call-results/envelopes/collect-b.json",
            ],
        },
    )

    decision = reconcile_dependency_barriers(
        config,
        [_anchor("run-1"), first_source, second_source],
    )[0]

    assert decision.payload["artifact_refs"] == [
        {"path": "artifacts/shared.json", "sha256": "a" * 64},
        "artifacts/collect-a.json",
        "artifacts/collect-b.json",
    ]
    assert decision.payload["evidence_refs"] == ["event:collect-a"]
    assert decision.payload["input_refs"] == ["artifacts/input.json"]
    assert decision.payload["input_result_refs"] == [
        "artifacts/call-results/envelopes/collect-a.json",
        "artifacts/call-results/envelopes/collect-b.json",
    ]
    assert decision.payload["goal_id"] == "goal-1"
    assert decision.payload["workflow_intent"] == "research"
    assert decision.payload["required_delivery_artifacts"] == (
        required_artifacts
    )
    assert decision.payload["goal_claim_set_ref"] == claim_ref
    assert decision.payload["goal_claim_set_digest"] == "b" * 64
    assert decision.payload["propagation_digest"]
    assert decision.payload["dependency_sources"] == [
        {
            "dependency": "collect-a",
            "event_type": "collect-a.completed",
            "event_id": first_source.id,
            "artifact_refs": [
                {"path": "artifacts/shared.json", "sha256": "a" * 64},
                "artifacts/collect-a.json",
            ],
            "evidence_refs": ["event:collect-a"],
            "input_result_refs": [
                "artifacts/call-results/envelopes/collect-a.json",
            ],
        },
        {
            "dependency": "collect-b",
            "event_type": "collect-b.completed",
            "event_id": second_source.id,
            "artifact_refs": [
                {"path": "artifacts/shared.json", "sha256": "a" * 64},
                "artifacts/collect-b.json",
            ],
            "input_refs": ["artifacts/input.json"],
            "input_result_refs": [
                "artifacts/call-results/envelopes/collect-b.json",
            ],
        },
    ]


def test_barrier_repairs_legacy_satisfied_event_without_propagated_refs() -> None:
    config = _config()
    base = [
        _anchor("run-1"),
        _event(
            "collect-a.completed",
            payload={"artifact_refs": ["artifacts/collect-a.json"]},
        ),
        _event(
            "collect-b.completed",
            payload={"artifact_refs": ["artifacts/collect-b.json"]},
        ),
    ]
    current = reconcile_dependency_barriers(config, base)[0].to_event()
    legacy = ZfEvent(
        type=SATISFIED_EVENT,
        actor="orchestrator",
        correlation_id="run-1",
        payload={
            key: value
            for key, value in current.payload.items()
            if key not in {
                "artifact_refs",
                "dependency_sources",
                "propagation_digest",
            }
        },
    )

    repaired = reconcile_dependency_barriers(config, [*base, legacy])
    replayed = reconcile_dependency_barriers(
        config,
        [*base, legacy, repaired[0].to_event()],
    )

    assert len(repaired) == 1
    assert repaired[0].payload["artifact_refs"] == [
        "artifacts/collect-a.json",
        "artifacts/collect-b.json",
    ]
    assert replayed == []


def test_barrier_reopens_when_same_generation_gets_new_source_event() -> None:
    config = _config()
    base = [
        _anchor("run-1"),
        _event("collect-a.completed"),
        _event("collect-b.completed"),
    ]
    first = reconcile_dependency_barriers(config, base)[0].to_event()
    replacement = _event(
        "collect-b.completed",
        payload={"artifact_refs": ["artifacts/collect-b-v2.json"]},
    )

    refreshed = reconcile_dependency_barriers(
        config,
        [*base, first, replacement],
    )

    assert len(refreshed) == 1
    assert refreshed[0].payload["source_event_ids"][-1] == replacement.id
    assert refreshed[0].payload["artifact_refs"] == [
        "artifacts/collect-b-v2.json",
    ]


def test_barrier_ignores_late_duplicate_that_drops_existing_refs() -> None:
    config = _config()
    base = [
        _anchor("run-1"),
        _event(
            "collect-a.completed",
            payload={"artifact_refs": ["artifacts/collect-a.json"]},
        ),
        _event(
            "collect-b.completed",
            payload={"artifact_refs": ["artifacts/collect-b.json"]},
        ),
    ]
    first = reconcile_dependency_barriers(config, base)[0].to_event()
    late_duplicate = _event("collect-b.completed")

    refreshed = reconcile_dependency_barriers(
        config,
        [*base, first, late_duplicate],
    )

    assert refreshed == []


def test_orchestrator_run_once_reconciles_and_dispatches_downstream(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    transport = _RecordingTransport()
    orchestrator = Orchestrator(
        state_dir,
        _config(),
        transport,
    )
    incoming = [
        _anchor("run-1"),
        _event("collect-a.completed"),
        _event("collect-b.completed"),
    ]
    for event in incoming:
        orchestrator.event_writer.append(event)

    orchestrator.run_once(events=[incoming[-1]])
    orchestrator.run_once(events=[])

    events = orchestrator.event_log.read_all()
    satisfied = [
        event for event in events if event.type == SATISFIED_EVENT
    ]
    assert len(satisfied) == 1
    assert satisfied[0].payload["source_event_ids"] == [
        incoming[1].id,
        incoming[2].id,
    ]
    assert [item[0] for item in transport.sent].count("synthesizer") == 1


def test_graph_and_execution_pattern_show_dependency_barrier(
    tmp_path: Path,
) -> None:
    config = _config()
    graph = compile_workflow_graph(config).to_dict()
    nodes = {node["node_id"]: node for node in graph["nodes"]}
    edges = graph["edges"]

    assert nodes["dependency-barrier:synthesize"]["type"] == (
        "dependency_barrier"
    )
    assert nodes["stage:synthesize"]["metadata"]["operation"] == (
        "agent.synthesize"
    )
    assert {
        (edge["from_node"], edge["to_node"], edge["kind"])
        for edge in edges
        if edge["to_node"] == "dependency-barrier:synthesize"
    } == {
        (
            "stage:collect-a",
            "dependency-barrier:synthesize",
            "dependency",
        ),
        (
            "stage:collect-b",
            "dependency-barrier:synthesize",
            "dependency",
        ),
    }

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    patterns = project_execution_patterns(
        config,
        state_dir=state_dir,
    )["patterns"]
    synth = next(
        item for item in patterns if item["pattern_id"] == "synthesize"
    )
    assert synth["operation"] == "agent.synthesize"
    assert synth["dependencies"] == ["collect-a", "collect-b"]
    assert synth["input_ports"][0]["kind"] == "evidence/bundle"
    assert synth["barrier"]["required_events"] == [
        "collect-a.completed",
        "collect-b.completed",
    ]
