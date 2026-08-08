# ZaoFu Capability Coverage Catalog

> Generated from `capability-coverage.yaml`; do not edit by hand.
> This maps release-facing capabilities to manual/code/test evidence; it is not a module inventory.

Last manually verified: `2026-08-03`.

| Capability | Status | Manual | Implementation | Tests |
|---|---|---|---|---|
| `project-bootstrap`<br>Project initialization and bootstrap | `implemented` | [`20-project-bootstrap-workflow-ignition.md`](../20-project-bootstrap-workflow-ignition.md)<br>[`20-project-bootstrap-workflow-ignition.en.md`](../20-project-bootstrap-workflow-ignition.en.md) | `src/zf/cli/project.py`<br>`src/zf/core/workspace/project_admission.py` | `tests/test_cli_project_init_source.py`<br>`tests/test_project_admission.py` |
| `channel-to-prd`<br>Multi-Agent Channel clarification to PRD | `implemented` | [`channel-to-prd.md`](../workflows/channel-to-prd.md)<br>[`15-channel-collaboration.md`](../15-channel-collaboration.md)<br>[`channel-to-prd.en.md`](../workflows/channel-to-prd.en.md)<br>[`15-channel-collaboration.en.md`](../15-channel-collaboration.en.md) | `src/zf/runtime/channel_contracts.py`<br>`src/zf/runtime/channel_discussion.py` | `tests/test_channel_templates.py`<br>`tests/test_channel_discussion_driver.py` |
| `controlled-workflow-start`<br>Task-bound controlled Workflow start | `implemented` | [`controlled-workflow-start.md`](../workflows/controlled-workflow-start.md)<br>[`controlled-workflow-start.en.md`](../workflows/controlled-workflow-start.en.md) | `src/zf/runtime/workflow_start.py`<br>`src/zf/cli/workflow.py` | `tests/test_workflow_start_service.py` |
| `task-map-kernel-dispatch`<br>Task Map compilation and Kernel dispatch | `implemented` | [`13-plan-task-map-orchestrator-dispatch.md`](../13-plan-task-map-orchestrator-dispatch.md)<br>[`13-plan-task-map-orchestrator-dispatch.en.md`](../13-plan-task-map-orchestrator-dispatch.en.md) | `src/zf/runtime/task_map.py`<br>`src/zf/runtime/product_delivery.py`<br>`src/zf/runtime/orchestrator_dispatch.py` | `tests/test_task_map.py`<br>`tests/test_product_delivery.py` |
| `bounded-agent-swarm`<br>Bounded multi-Agent swarm and elastic Workers | `implemented` | [`architecture.md`](../architecture.md)<br>[`13-plan-task-map-orchestrator-dispatch.md`](../13-plan-task-map-orchestrator-dispatch.md)<br>[`18-product-fanout-real-e2e.md`](../18-product-fanout-real-e2e.md)<br>[`architecture.en.md`](../architecture.en.md)<br>[`13-plan-task-map-orchestrator-dispatch.en.md`](../13-plan-task-map-orchestrator-dispatch.en.md)<br>[`18-product-fanout-real-e2e.en.md`](../18-product-fanout-real-e2e.en.md) | `src/zf/runtime/fanout.py`<br>`src/zf/runtime/agent_view_runtime.py`<br>`src/zf/runtime/role_lifecycle_runtime.py` | `tests/test_reader_fanout_runtime.py`<br>`tests/test_writer_fanout_runtime.py`<br>`tests/test_role_lifecycle_runtime.py` |
| `delivery-observability`<br>Delivery, Runs, Graph, and Loop observability | `implemented` | [`observe-delivery.md`](../operations/observe-delivery.md)<br>[`06-web-observability-e2e.md`](../06-web-observability-e2e.md)<br>[`observe-delivery.en.md`](../operations/observe-delivery.en.md)<br>[`06-web-observability-e2e.en.md`](../06-web-observability-e2e.en.md) | `src/zf/runtime/delivery_trace.py`<br>`web/src/components/delivery-trace/DeliveryTracePage.tsx` | `tests/test_delivery_trace.py`<br>`tests/test_web_delivery_trace.py` |
| `goal-dossier`<br>Goal Dossier delivery sign-off | `implemented` | [`observe-delivery.md`](../operations/observe-delivery.md)<br>[`observe-delivery.en.md`](../operations/observe-delivery.en.md) | `src/zf/runtime/goal_dossier.py`<br>`src/zf/runtime/goal_dossier_delivery.py` | `tests/test_goal_dossier.py`<br>`tests/test_goal_dossier_owner_delivery.py` |
| `goal-coverage`<br>Claim-centered Goal Coverage | `implemented` | [`observe-delivery.md`](../operations/observe-delivery.md)<br>[`observe-delivery.en.md`](../operations/observe-delivery.en.md) | `src/zf/runtime/goal_coverage_graph.py`<br>`src/zf/runtime/task_map_goal_coverage.py` | `tests/test_goal_coverage_graph.py` |
| `long-run-continuation`<br>Long-horizon Run continuation and recovery | `implemented` | [`recover-long-running-run.md`](../operations/recover-long-running-run.md)<br>[`recover-long-running-run.en.md`](../operations/recover-long-running-run.en.md) | `src/zf/runtime/run_continuation.py`<br>`src/zf/runtime/task_attempt_runtime.py` | `tests/test_run_continuation.py`<br>`tests/test_task_attempt_recovery.py` |
| `artifact-query-handoff`<br>Artifact query, required read, and Agent handoff | `implemented` | [`context-handoff-artifacts.md`](../operations/context-handoff-artifacts.md)<br>[`context-handoff-artifacts.en.md`](../operations/context-handoff-artifacts.en.md) | `src/zf/runtime/artifact_query/service.py`<br>`src/zf/runtime/artifact_query/handoff.py` | `tests/test_artifact_query_service.py`<br>`tests/test_artifact_query_consumers.py` |
| `autoresearch`<br>Autoresearch diagnosis and isolated repair | `implemented` | [`10-autoresearch-usage.md`](../10-autoresearch-usage.md)<br>[`10-autoresearch-usage.en.md`](../10-autoresearch-usage.en.md) | `src/zf/autoresearch/orchestrator.py`<br>`src/zf/autoresearch/self_repair.py` | `tests/test_autoresearch_orchestrator.py`<br>`tests/test_autoresearch_self_repair.py` |
| `feishu-ai-native`<br>Feishu AI-native collaboration loop | `implemented` | [`19-feishu-ai-native-direct-bridge.md`](../19-feishu-ai-native-direct-bridge.md)<br>[`19-feishu-ai-native-direct-bridge.en.md`](../19-feishu-ai-native-direct-bridge.en.md) | `src/zf/integrations/feishu/lark_cli.py`<br>`src/zf/integrations/feishu/project_group_binding.py`<br>`src/zf/runtime/feishu_projection_sidecar.py`<br>`src/zf/runtime/feishu_inbound_sidecar.py` | `tests/test_feishu_ai_native_loop_e2e.py`<br>`tests/test_feishu_project_group_binding.py`<br>`tests/test_feishu_projection_sidecar.py`<br>`tests/test_workspace_feishu_bridge.py` |
| `automations`<br>Daily, weekly, and Project automations | `implemented` | [`11-feishu-automation-kanban-sync.md`](../11-feishu-automation-kanban-sync.md)<br>[`11-feishu-automation-kanban-sync.en.md`](../11-feishu-automation-kanban-sync.en.md) | `src/zf/runtime/automation_projection.py`<br>`src/zf/integrations/feishu/automation_renderer.py` | `tests/test_automation_projection.py`<br>`tests/test_automation_metrics.py` |
| `supervisor-inspection`<br>Supervisor inspection and attention triage | `implemented` | [`12-supervisor-inspection-usage.md`](../12-supervisor-inspection-usage.md)<br>[`12-supervisor-inspection-usage.en.md`](../12-supervisor-inspection-usage.en.md) | `src/zf/runtime/supervisor_inspection.py`<br>`src/zf/runtime/supervisor_control_loop.py` | `tests/test_supervisor_inspection.py`<br>`tests/test_supervisor_control_loop.py` |

## Release Smoke Metadata

### `project-bootstrap` - Project initialization and bootstrap

- **Activation**: Use Add Project or `zf project init`, then run bootstrap explicitly.
- **Readback**: Verify the Project registry, generated context, and cold-start validation.
- **Rollback**: Remove only the newly registered Project through sanctioned workspace actions; preserve its source tree and configured state for audit.
- **Authority**: Initialization creates Project/control-plane state only; it does not create a Task or start a Workflow.

### `channel-to-prd` - Multi-Agent Channel clarification to PRD

- **Activation**: Create/select a Channel, add authorized members, and explicitly choose Discuss or Finalize.
- **Readback**: Confirm conversation mode, discussion rounds, draft generation, and exact Owner confirmation in Channel history.
- **Rollback**: Reject or supersede the draft before Owner confirmation; do not delete the conversation ledger.
- **Authority**: Ordinary messages do not auto-fanout; Finalize/Owner confirm publishes a PRD/Task source but never starts a Workflow.

### `controlled-workflow-start` - Task-bound controlled Workflow start

- **Activation**: Query active Task routes, preview, propose, then apply the exact proposal with operator authorization.
- **Readback**: Confirm `workflow.invoke.requested` binds the same Task, route, objective, parameters, and proposal event.
- **Rollback**: Reject/supersede an unapplied proposal, or use the Run's sanctioned cancel/recovery action after admission.
- **Authority**: Plan and route selection are not approval; provider Agents never receive the action token.

### `task-map-kernel-dispatch` - Task Map compilation and Kernel dispatch

- **Activation**: Publish an accepted artifact package with task-map/source-index/coverage, then admit its Run.
- **Readback**: Inspect canonical Task contracts, wave readiness, TaskAttempt, dispatch token, and evidence chain.
- **Rollback**: Produce a new artifact/task-map generation and controlled replan; never hand-edit existing canonical Tasks.
- **Authority**: Agents own semantic decomposition; the Kernel owns deterministic validation, readiness, dispatch, and transitions.

### `bounded-agent-swarm` - Bounded multi-Agent swarm and elastic Workers

- **Activation**: Select an admitted fanout/lane topology and configure role replicas, autoscale bounds, or on-demand lifecycle in `zf.yaml`.
- **Readback**: Correlate fanout manifests, child attempts, isolated workdirs, aggregate results, Agent pool state, Graph, and Delivery evidence.
- **Rollback**: Pause or cancel through controlled Run actions, supersede the Task Map generation, and retire only idle autoscaled Workers with clean workdirs.
- **Authority**: The Kernel owns readiness, WIP, leases, dispatch, and aggregation; children report typed artifacts/evidence and cannot recursively create canonical Tasks or mutate topology.

### `delivery-observability` - Delivery, Runs, Graph, and Loop observability

- **Activation**: Start Web against the intended Project/state and select a Feature under Delivery.
- **Readback**: Cross-check Overview, Runs, Graph, Loop, Goal Dossier, and Task trace against the event/store/artifact sources.
- **Rollback**: Rebuild or disable the affected read projection; do not mutate canonical state to make the UI look healthy.
- **Authority**: Web/SQLite/Trace/Graph/Loop are read projections and cannot become dispatch truth.

### `goal-dossier` - Goal Dossier delivery sign-off

- **Activation**: Open a Run's Goal Dossier after Goal Claims and evidence-producing Tasks exist.
- **Readback**: Verify mandatory Claim coverage, Task terminal state, evidence refs, verdicts, and current generation.
- **Rollback**: Rebuild the dossier projection or supersede stale evidence through a new generation; never edit the rendered dossier.
- **Authority**: Goal Dossier synthesizes owner-readable evidence but does not replace Task/Event/Artifact authorities.

### `goal-coverage` - Claim-centered Goal Coverage

- **Activation**: Bind mandatory Goal Claims to Task Map tasks, verification producers, and verdict evidence.
- **Readback**: Inspect missing/orphan Claims, producer coverage, closure status, and generation freshness.
- **Rollback**: Correct the source Task Map/Claim generation and rebuild the graph projection.
- **Authority**: Coverage is derived from canonical contracts and evidence; a green visualization cannot waive missing mandatory proof.

### `long-run-continuation` - Long-horizon Run continuation and recovery

- **Activation**: Enable the profile's continuation/recovery policy and run the normal watcher.
- **Readback**: Inspect continuation decisions, no-progress counters, checkpoints, attempts, and terminal convergence.
- **Rollback**: Pause/cancel through controlled Run actions or restore the prior policy generation; preserve checkpoints and evidence.
- **Authority**: Recovery may retry mechanics automatically, but semantic replan remains proposal/approval driven.

### `artifact-query-handoff` - Artifact query, required read, and Agent handoff

- **Activation**: Publish required artifacts with refs/digests and request a Task/attempt-scoped catalog before work.
- **Readback**: Verify catalog lineage, required-read evidence, hydrated object content, and handoff currentness.
- **Rollback**: Rebuild the query index or publish a superseding artifact generation; never replace required bodies with event previews.
- **Authority**: The query store is rebuildable; artifact/sidecar bodies and canonical refs remain authoritative.

### `autoresearch` - Autoresearch diagnosis and isolated repair

- **Activation**: Trigger a bounded scenario/campaign from a qualifying failure signal or explicit operator request.
- **Readback**: Inspect hypothesis, experiment, score, holdout, artifact refs, and repair proposal/validation.
- **Rollback**: Stop the campaign and remove only its isolated clean worktree after preserving reports; apply nothing without the owner gate.
- **Authority**: Autoresearch diagnoses or prepares candidates; default policy is proposal-only and cannot directly write mainline truth.

### `feishu-ai-native` - Feishu AI-native collaboration loop

- **Activation**: Configure sanctioned Feishu targets/credentials and start the enabled projection/inbound sidecars.
- **Readback**: Verify outbound topic/thread projection, inbound identity/intent refs, queue status, and event causation.
- **Rollback**: Disable the integration or drain/retry its projection queue; do not delete canonical ZaoFu state.
- **Authority**: Feishu is an interaction/projection surface and must use EventWriter, controlled actions, and sanctioned sidecar writers.

### `automations` - Daily, weekly, and Project automations

- **Activation**: Enable the selected schedule/target in `zf.yaml` and start the runtime/Feishu sidecar.
- **Readback**: Inspect Automations last/next run, generated report artifact, delivery target, and failure diagnostics.
- **Rollback**: Disable the schedule or target and retain prior run/report history for audit.
- **Authority**: Automations may publish reports or intents but cannot bypass canonical stores or controlled external effects.

### `supervisor-inspection` - Supervisor inspection and attention triage

- **Activation**: Enable Supervisor inspection/control-loop policy and run the watcher.
- **Readback**: Inspect findings, attention candidates, owner-visible decisions, and linked evidence.
- **Rollback**: Disable the observer loop or reject its proposal; preserve inspection history.
- **Authority**: Supervisor observes and proposes; it does not kill Workers, rewrite Tasks, or become a second control plane.
