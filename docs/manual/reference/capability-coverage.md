# ZaoFu 能力覆盖清单

> 从 `capability-coverage.yaml` 生成，禁止手工修改。
> 它是发布面能力到 manual/code/test 的证据目录，不是全仓库模块清单。

最后人工核实：`2026-08-08`。

| 能力 | 状态 | 用户手册 | 实现 | 测试 |
|---|---|---|---|---|
| `project-bootstrap`<br>Project 初始化与 Bootstrap | `implemented` | [`20-project-bootstrap-workflow-ignition.md`](../20-project-bootstrap-workflow-ignition.md)<br>[`20-project-bootstrap-workflow-ignition.en.md`](../20-project-bootstrap-workflow-ignition.en.md) | `src/zf/cli/project.py`<br>`src/zf/core/workspace/project_admission.py` | `tests/test_cli_project_init_source.py`<br>`tests/test_project_admission.py` |
| `channel-to-prd`<br>Channel 多 Agent 澄清到 PRD | `implemented` | [`channel-to-prd.md`](../workflows/channel-to-prd.md)<br>[`15-channel-collaboration.md`](../15-channel-collaboration.md)<br>[`channel-to-prd.en.md`](../workflows/channel-to-prd.en.md)<br>[`15-channel-collaboration.en.md`](../15-channel-collaboration.en.md) | `src/zf/runtime/channel_contracts.py`<br>`src/zf/runtime/channel_discussion.py` | `tests/test_channel_templates.py`<br>`tests/test_channel_discussion_driver.py` |
| `controlled-workflow-start`<br>Task 绑定的受控 Workflow 启动 | `implemented` | [`controlled-workflow-start.md`](../workflows/controlled-workflow-start.md)<br>[`controlled-workflow-start.en.md`](../workflows/controlled-workflow-start.en.md) | `src/zf/runtime/workflow_start.py`<br>`src/zf/cli/workflow.py` | `tests/test_workflow_start_service.py` |
| `task-map-kernel-dispatch`<br>Task Map 编译与 Kernel 调度 | `implemented` | [`13-plan-task-map-orchestrator-dispatch.md`](../13-plan-task-map-orchestrator-dispatch.md)<br>[`13-plan-task-map-orchestrator-dispatch.en.md`](../13-plan-task-map-orchestrator-dispatch.en.md) | `src/zf/runtime/task_map.py`<br>`src/zf/runtime/product_delivery.py`<br>`src/zf/runtime/orchestrator_dispatch.py` | `tests/test_task_map.py`<br>`tests/test_product_delivery.py` |
| `controlled-workflow-synthesis`<br>受控 Workflow 合成与冻结 | `partial` | [`04-harness-runtime.md`](../04-harness-runtime.md)<br>[`13-plan-task-map-orchestrator-dispatch.md`](../13-plan-task-map-orchestrator-dispatch.md)<br>[`controlled-workflow-start.md`](../workflows/controlled-workflow-start.md)<br>[`04-harness-runtime.en.md`](../04-harness-runtime.en.md)<br>[`13-plan-task-map-orchestrator-dispatch.en.md`](../13-plan-task-map-orchestrator-dispatch.en.md)<br>[`controlled-workflow-start.en.md`](../workflows/controlled-workflow-start.en.md) | `src/zf/runtime/workflow_synthesis_generic.py`<br>`src/zf/runtime/workflow_start.py` | `tests/test_generic_workflow.py`<br>`tests/test_workflow_synthesis.py` |
| `role-provider-session-lifecycle`<br>Role 级 Provider Session 与按需生命周期 | `partial` | [`02-zf-yaml-control-plane.md`](../02-zf-yaml-control-plane.md)<br>[`architecture.md`](../architecture.md)<br>[`02-zf-yaml-control-plane.en.md`](../02-zf-yaml-control-plane.en.md)<br>[`architecture.en.md`](../architecture.en.md) | `src/zf/runtime/provider_session_config.py`<br>`src/zf/runtime/role_lifecycle_runtime.py` | `tests/test_provider_session_config.py`<br>`tests/test_role_lifecycle_runtime.py` |
| `orchestrator-semantic-control`<br>Orchestrator Agent 语义 Checkpoint | `partial` | [`architecture.md`](../architecture.md)<br>[`04-harness-runtime.md`](../04-harness-runtime.md)<br>[`13-plan-task-map-orchestrator-dispatch.md`](../13-plan-task-map-orchestrator-dispatch.md)<br>[`architecture.en.md`](../architecture.en.md)<br>[`04-harness-runtime.en.md`](../04-harness-runtime.en.md)<br>[`13-plan-task-map-orchestrator-dispatch.en.md`](../13-plan-task-map-orchestrator-dispatch.en.md) | `src/zf/runtime/orchestrator_agent_policy.py`<br>`src/zf/runtime/orchestrator_agent_reactor.py` | `tests/test_orchestrator_agent_policy.py`<br>`tests/e2e/test_orchestrator_agent_semantic_control_mock_e2e.py` |
| `task-pipeline-v4`<br>Task Pipeline v4 与弹性 Stage Worker | `partial` | [`02-zf-yaml-control-plane.md`](../02-zf-yaml-control-plane.md)<br>[`13-plan-task-map-orchestrator-dispatch.md`](../13-plan-task-map-orchestrator-dispatch.md)<br>[`18-product-fanout-real-e2e.md`](../18-product-fanout-real-e2e.md)<br>[`02-zf-yaml-control-plane.en.md`](../02-zf-yaml-control-plane.en.md)<br>[`13-plan-task-map-orchestrator-dispatch.en.md`](../13-plan-task-map-orchestrator-dispatch.en.md)<br>[`18-product-fanout-real-e2e.en.md`](../18-product-fanout-real-e2e.en.md) | `src/zf/runtime/task_pipeline_runtime.py`<br>`src/zf/runtime/task_pipeline_reconciler.py` | `tests/test_task_pipeline_profile.py`<br>`tests/test_task_pipeline_rollout.py`<br>`tests/test_task_pipeline_fault_matrix.py` |
| `research-generation-five-workflow-terminal`<br>Research Generation 与五类 Workflow 终态签收 | `partial` | [`18-product-fanout-real-e2e.md`](../18-product-fanout-real-e2e.md)<br>[`18-product-fanout-real-e2e.en.md`](../18-product-fanout-real-e2e.en.md) | `src/zf/runtime/research_generation.py`<br>`src/zf/runtime/control_actions_product.py` | `tests/test_control_actions_research.py`<br>`tests/e2e/test_five_workflow_terminal_runner.py` |
| `bounded-agent-swarm`<br>受控多 Agent 蜂群与弹性 Worker | `implemented` | [`architecture.md`](../architecture.md)<br>[`13-plan-task-map-orchestrator-dispatch.md`](../13-plan-task-map-orchestrator-dispatch.md)<br>[`18-product-fanout-real-e2e.md`](../18-product-fanout-real-e2e.md)<br>[`architecture.en.md`](../architecture.en.md)<br>[`13-plan-task-map-orchestrator-dispatch.en.md`](../13-plan-task-map-orchestrator-dispatch.en.md)<br>[`18-product-fanout-real-e2e.en.md`](../18-product-fanout-real-e2e.en.md) | `src/zf/runtime/fanout.py`<br>`src/zf/runtime/agent_view_runtime.py`<br>`src/zf/runtime/role_lifecycle_runtime.py` | `tests/test_reader_fanout_runtime.py`<br>`tests/test_writer_fanout_runtime.py`<br>`tests/test_role_lifecycle_runtime.py` |
| `delivery-observability`<br>Delivery、Runs、Graph、Loop 可观测性 | `implemented` | [`observe-delivery.md`](../operations/observe-delivery.md)<br>[`06-web-observability-e2e.md`](../06-web-observability-e2e.md)<br>[`observe-delivery.en.md`](../operations/observe-delivery.en.md)<br>[`06-web-observability-e2e.en.md`](../06-web-observability-e2e.en.md) | `src/zf/runtime/delivery_trace.py`<br>`web/src/components/delivery-trace/DeliveryTracePage.tsx` | `tests/test_delivery_trace.py`<br>`tests/test_web_delivery_trace.py` |
| `goal-dossier`<br>Goal Dossier 交付签收 | `implemented` | [`observe-delivery.md`](../operations/observe-delivery.md)<br>[`observe-delivery.en.md`](../operations/observe-delivery.en.md) | `src/zf/runtime/goal_dossier.py`<br>`src/zf/runtime/goal_dossier_delivery.py` | `tests/test_goal_dossier.py`<br>`tests/test_goal_dossier_owner_delivery.py` |
| `goal-coverage`<br>Claim 中心的 Goal Coverage | `implemented` | [`observe-delivery.md`](../operations/observe-delivery.md)<br>[`observe-delivery.en.md`](../operations/observe-delivery.en.md) | `src/zf/runtime/goal_coverage_graph.py`<br>`src/zf/runtime/task_map_goal_coverage.py` | `tests/test_goal_coverage_graph.py` |
| `long-run-continuation`<br>Long-Horizon Run Continuation 与恢复 | `implemented` | [`recover-long-running-run.md`](../operations/recover-long-running-run.md)<br>[`recover-long-running-run.en.md`](../operations/recover-long-running-run.en.md) | `src/zf/runtime/run_continuation.py`<br>`src/zf/runtime/task_attempt_runtime.py` | `tests/test_run_continuation.py`<br>`tests/test_task_attempt_recovery.py` |
| `artifact-query-handoff`<br>Artifact 查询、Required Read 与 Agent Handoff | `implemented` | [`context-handoff-artifacts.md`](../operations/context-handoff-artifacts.md)<br>[`context-handoff-artifacts.en.md`](../operations/context-handoff-artifacts.en.md) | `src/zf/runtime/artifact_query/service.py`<br>`src/zf/runtime/artifact_query/handoff.py` | `tests/test_artifact_query_service.py`<br>`tests/test_artifact_query_consumers.py` |
| `autoresearch`<br>Autoresearch 诊断与隔离修复 | `implemented` | [`10-autoresearch-usage.md`](../10-autoresearch-usage.md)<br>[`10-autoresearch-usage.en.md`](../10-autoresearch-usage.en.md) | `src/zf/autoresearch/orchestrator.py`<br>`src/zf/autoresearch/self_repair.py` | `tests/test_autoresearch_orchestrator.py`<br>`tests/test_autoresearch_self_repair.py` |
| `feishu-ai-native`<br>Feishu AI Native 协作闭环 | `implemented` | [`19-feishu-ai-native-direct-bridge.md`](../19-feishu-ai-native-direct-bridge.md)<br>[`19-feishu-ai-native-direct-bridge.en.md`](../19-feishu-ai-native-direct-bridge.en.md) | `src/zf/integrations/feishu/lark_cli.py`<br>`src/zf/integrations/feishu/project_group_binding.py`<br>`src/zf/runtime/feishu_projection_sidecar.py`<br>`src/zf/runtime/feishu_inbound_sidecar.py` | `tests/test_feishu_ai_native_loop_e2e.py`<br>`tests/test_feishu_project_group_binding.py`<br>`tests/test_feishu_projection_sidecar.py`<br>`tests/test_workspace_feishu_bridge.py` |
| `automations`<br>Daily、Weekly 与 Project Automations | `implemented` | [`11-feishu-automation-kanban-sync.md`](../11-feishu-automation-kanban-sync.md)<br>[`11-feishu-automation-kanban-sync.en.md`](../11-feishu-automation-kanban-sync.en.md) | `src/zf/runtime/automation_projection.py`<br>`src/zf/integrations/feishu/automation_renderer.py` | `tests/test_automation_projection.py`<br>`tests/test_automation_metrics.py` |
| `supervisor-inspection`<br>Supervisor 观察与 Attention 分诊 | `implemented` | [`12-supervisor-inspection-usage.md`](../12-supervisor-inspection-usage.md)<br>[`12-supervisor-inspection-usage.en.md`](../12-supervisor-inspection-usage.en.md) | `src/zf/runtime/supervisor_inspection.py`<br>`src/zf/runtime/supervisor_control_loop.py` | `tests/test_supervisor_inspection.py`<br>`tests/test_supervisor_control_loop.py` |

## 发布 Smoke 元数据

### `project-bootstrap` - Project 初始化与 Bootstrap

- **Activation**: Use Add Project or `zf project init`, then run bootstrap explicitly.
- **Readback**: Verify the Project registry, generated context, and cold-start validation.
- **Rollback**: Remove only the newly registered Project through sanctioned workspace actions; preserve its source tree and configured state for audit.
- **Authority**: Initialization creates Project/control-plane state only; it does not create a Task or start a Workflow.

### `channel-to-prd` - Channel 多 Agent 澄清到 PRD

- **Activation**: Create/select a Channel, add authorized members, and explicitly choose Discuss or Finalize.
- **Readback**: Confirm conversation mode, discussion rounds, draft generation, and exact Owner confirmation in Channel history.
- **Rollback**: Reject or supersede the draft before Owner confirmation; do not delete the conversation ledger.
- **Authority**: Ordinary messages do not auto-fanout; Finalize/Owner confirm publishes a PRD/Task source but never starts a Workflow.

### `controlled-workflow-start` - Task 绑定的受控 Workflow 启动

- **Activation**: Query active Task routes, preview, propose, then apply the exact proposal with operator authorization.
- **Readback**: Confirm `workflow.invoke.requested` binds the same Task, route, objective, parameters, and proposal event.
- **Rollback**: Reject/supersede an unapplied proposal, or use the Run's sanctioned cancel/recovery action after admission.
- **Authority**: Plan and route selection are not approval; provider Agents never receive the action token.

### `task-map-kernel-dispatch` - Task Map 编译与 Kernel 调度

- **Activation**: Publish an accepted artifact package with task-map/source-index/coverage, then admit its Run.
- **Readback**: Inspect canonical Task contracts, wave readiness, TaskAttempt, dispatch token, and evidence chain.
- **Rollback**: Produce a new artifact/task-map generation and controlled replan; never hand-edit existing canonical Tasks.
- **Authority**: Agents own semantic decomposition; the Kernel owns deterministic validation, readiness, dispatch, and transitions.

### `controlled-workflow-synthesis` - 受控 Workflow 合成与冻结

- **Activation**: Build a typed static-safe FlowSpec/proposal from the requirement, then approve the exact Task-bound proposal.
- **Readback**: Inspect the frozen effective config, route, artifact package, Task Map generation, and invoke causation.
- **Rollback**: Reject or supersede the proposal before admission, or cancel the admitted Run and create a new immutable generation.
- **Authority**: Agents own semantic synthesis; the Kernel validates and freezes registered operations, schemas, references, and side effects. Runtime-authored dynamic graph fragments remain deferred.

### `role-provider-session-lifecycle` - Role 级 Provider Session 与按需生命周期

- **Activation**: Configure supported `provider_session` options and an eager, resident, or on-demand role lifecycle in `zf.yaml`.
- **Readback**: Verify normalized Provider options, immutable session digest, RoleSession identity, activation, idle retirement, and preserved workdir/affinity.
- **Rollback**: Restore the prior role config in a new runtime generation; retire only idle processes whose workdir and session evidence remain preserved.
- **Authority**: Lifecycle owns process and pane placement only; it cannot mutate Task truth, transfer one Task's conversation to another, or make the orchestrator role on-demand.

### `orchestrator-semantic-control` - Orchestrator Agent 语义 Checkpoint

- **Activation**: Explicitly select `semantic_control` and an allowed checkpoint/pilot profile; otherwise retain `exception_advisor`.
- **Readback**: Inspect the OA operation, required reads, typed decision, admission verdict, resulting graph delta or narrative, and exact Run lineage.
- **Rollback**: Return the profile to `exception_advisor` or shadow in a new Run; preserve prior OA operations and decisions for audit.
- **Authority**: OA owns semantic proposals at bounded checkpoints; Kernel/WRC retains schema, currentness, Task readiness, leases, dispatch, transitions, and side effects. P15 real canary remains HOLD.

### `task-pipeline-v4` - Task Pipeline v4 与弹性 Stage Worker

- **Activation**: Use a `preferred: false` v4 canary and explicitly select shadow or an approved blocking pilot; v3 remains the default.
- **Readback**: Correlate Task pipeline and operation generations, Task Workspace, Stage Worker placement, Impl/Verify receipts, Candidate integration, and Goal Closure.
- **Rollback**: Start a fresh Run on the v3 profile or default-off shadow mode; preserve v4 attempts, workspaces, receipts, and terminal evidence.
- **Authority**: v4 changes scheduling and placement, not Task/Event/Store/Artifact or Stage semantic contracts. Implementation is complete, but rollout remains NO-GO.

### `research-generation-five-workflow-terminal` - Research Generation 与五类 Workflow 终态签收

- **Activation**: Run suite preflight, freeze an isolated family case, and start exactly one approved Workflow against the frozen implementation and Project identity.
- **Readback**: Verify generation binding, exact family terminal, Task/Run/Operation/Attempt/Role evidence, artifact lineage, and Docker Playwright capture when required.
- **Rollback**: Supersede or cancel a stale Research generation and clean only its isolated runtime after preserving the terminal evidence bundle.
- **Authority**: The immutable generation and read-only runner observe canonical facts; they do not create Tasks, emit business success, or become another scheduler. Real-Provider 5/5 remains pending.

### `bounded-agent-swarm` - 受控多 Agent 蜂群与弹性 Worker

- **Activation**: Select an admitted fanout/lane topology and configure role replicas, autoscale bounds, or on-demand lifecycle in `zf.yaml`.
- **Readback**: Correlate fanout manifests, child attempts, isolated workdirs, aggregate results, Agent pool state, Graph, and Delivery evidence.
- **Rollback**: Pause or cancel through controlled Run actions, supersede the Task Map generation, and retire only idle autoscaled Workers with clean workdirs.
- **Authority**: The Kernel owns readiness, WIP, leases, dispatch, and aggregation; children report typed artifacts/evidence and cannot recursively create canonical Tasks or mutate topology.

### `delivery-observability` - Delivery、Runs、Graph、Loop 可观测性

- **Activation**: Start Web against the intended Project/state and select a Feature under Delivery.
- **Readback**: Cross-check Overview, Runs, Graph, Loop, Goal Dossier, and Task trace against the event/store/artifact sources.
- **Rollback**: Rebuild or disable the affected read projection; do not mutate canonical state to make the UI look healthy.
- **Authority**: Web/SQLite/Trace/Graph/Loop are read projections and cannot become dispatch truth.

### `goal-dossier` - Goal Dossier 交付签收

- **Activation**: Open a Run's Goal Dossier after Goal Claims and evidence-producing Tasks exist.
- **Readback**: Verify mandatory Claim coverage, Task terminal state, evidence refs, verdicts, and current generation.
- **Rollback**: Rebuild the dossier projection or supersede stale evidence through a new generation; never edit the rendered dossier.
- **Authority**: Goal Dossier synthesizes owner-readable evidence but does not replace Task/Event/Artifact authorities.

### `goal-coverage` - Claim 中心的 Goal Coverage

- **Activation**: Bind mandatory Goal Claims to Task Map tasks, verification producers, and verdict evidence.
- **Readback**: Inspect missing/orphan Claims, producer coverage, closure status, and generation freshness.
- **Rollback**: Correct the source Task Map/Claim generation and rebuild the graph projection.
- **Authority**: Coverage is derived from canonical contracts and evidence; a green visualization cannot waive missing mandatory proof.

### `long-run-continuation` - Long-Horizon Run Continuation 与恢复

- **Activation**: Enable the profile's continuation/recovery policy and run the normal watcher.
- **Readback**: Inspect continuation decisions, no-progress counters, checkpoints, attempts, and terminal convergence.
- **Rollback**: Pause/cancel through controlled Run actions or restore the prior policy generation; preserve checkpoints and evidence.
- **Authority**: Recovery may retry mechanics automatically, but semantic replan remains proposal/approval driven.

### `artifact-query-handoff` - Artifact 查询、Required Read 与 Agent Handoff

- **Activation**: Publish required artifacts with refs/digests and request a Task/attempt-scoped catalog before work.
- **Readback**: Verify catalog lineage, required-read evidence, hydrated object content, and handoff currentness.
- **Rollback**: Rebuild the query index or publish a superseding artifact generation; never replace required bodies with event previews.
- **Authority**: The query store is rebuildable; artifact/sidecar bodies and canonical refs remain authoritative.

### `autoresearch` - Autoresearch 诊断与隔离修复

- **Activation**: Trigger a bounded scenario/campaign from a qualifying failure signal or explicit operator request.
- **Readback**: Inspect hypothesis, experiment, score, holdout, artifact refs, and repair proposal/validation.
- **Rollback**: Stop the campaign and remove only its isolated clean worktree after preserving reports; apply nothing without the owner gate.
- **Authority**: Autoresearch diagnoses or prepares candidates; default policy is proposal-only and cannot directly write mainline truth.

### `feishu-ai-native` - Feishu AI Native 协作闭环

- **Activation**: Configure sanctioned Feishu targets/credentials and start the enabled projection/inbound sidecars.
- **Readback**: Verify outbound topic/thread projection, inbound identity/intent refs, queue status, and event causation.
- **Rollback**: Disable the integration or drain/retry its projection queue; do not delete canonical ZaoFu state.
- **Authority**: Feishu is an interaction/projection surface and must use EventWriter, controlled actions, and sanctioned sidecar writers.

### `automations` - Daily、Weekly 与 Project Automations

- **Activation**: Enable the selected schedule/target in `zf.yaml` and start the runtime/Feishu sidecar.
- **Readback**: Inspect Automations last/next run, generated report artifact, delivery target, and failure diagnostics.
- **Rollback**: Disable the schedule or target and retain prior run/report history for audit.
- **Authority**: Automations may publish reports or intents but cannot bypass canonical stores or controlled external effects.

### `supervisor-inspection` - Supervisor 观察与 Attention 分诊

- **Activation**: Enable Supervisor inspection/control-loop policy and run the watcher.
- **Readback**: Inspect findings, attention candidates, owner-visible decisions, and linked evidence.
- **Rollback**: Disable the observer loop or reject its proposal; preserve inspection history.
- **Authority**: Supervisor observes and proposes; it does not kill Workers, rewrite Tasks, or become a second control plane.
