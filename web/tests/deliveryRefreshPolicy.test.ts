import {
  DELIVERY_BOUNDED_RECONCILE_INTERVAL_MS,
  DELIVERY_LIVE_COALESCE_MS,
  DELIVERY_RECONCILE_INTERVAL_MS,
  deliveryReconcileInterval,
  deliveryRefreshScope,
  isDeliveryRefreshEvent,
  isCancelledDeliveryRequest,
  shouldReconcileDelivery,
} from "../src/components/delivery-trace/deliveryRefreshPolicy.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

assert(!shouldReconcileDelivery("live", "visible"), "healthy SSE must disable idle reconciliation");
assert(!shouldReconcileDelivery("degraded", "hidden"), "hidden pages must not reconcile");
assert(shouldReconcileDelivery("degraded", "visible"), "degraded visible pages should reconcile");
assert(shouldReconcileDelivery("reconnecting", "visible"), "reconnecting visible pages should reconcile");
assert(DELIVERY_RECONCILE_INTERVAL_MS >= 15_000, "degraded reconciliation must stay low-frequency");
assert(
  DELIVERY_BOUNDED_RECONCILE_INTERVAL_MS >= 60_000,
  "bounded healthy reconciliation must be lower-frequency than the degraded watchdog",
);
assert(DELIVERY_LIVE_COALESCE_MS >= 200, "live event refreshes need a trailing coalesce window");

const controller = new AbortController();
controller.abort();
assert(
  isCancelledDeliveryRequest(new DOMException("cancelled", "AbortError")),
  "AbortError should be treated as an expected request cancellation",
);
assert(!isCancelledDeliveryRequest(new Error("network")), "network failures must remain visible");

const scope = deliveryRefreshScope({
  feature_id: "FEATURE-1",
  trace_id: "TRACE-1",
  execution_graph: {
    nodes: [{ task_id: "TASK-1" }],
    waves: [],
  },
  workflow_trace: {
    workflow_id: "default",
    task_map_ref: "artifact://task-map.json",
    stage_runs: [{ fanout_id: "FANOUT-1", task_ids: ["TASK-2"] }],
  },
  run_groups: [{ group_id: "RUN-1", task_ids: ["TASK-3"] }],
  task_lifecycle: {
    tasks: { "TASK-1": { tries: [{ dispatch_id: "DISPATCH-1" }] } },
  },
  goal_coverage_graph: {
    identity: { workflow_run_id: "WORKFLOW-RUN-1", goal_id: "GOAL-1" },
    nodes: [],
  },
} as never, "FEATURE-1");
assert(
  !isDeliveryRefreshEvent({ type: "run.manager.tick.completed", payload: {} }, scope),
  "control-loop ticks must not reload Delivery",
);
assert(
  !isDeliveryRefreshEvent({ type: "task.updated", payload: {} }, scope),
  "unbound task events must fail closed",
);
assert(
  isDeliveryRefreshEvent({ type: "feature.updated", payload: { feature_id: "FEATURE-1" } }, scope),
  "feature-bound events should refresh their Delivery scope",
);
assert(
  isDeliveryRefreshEvent({ type: "task.done", task_id: "TASK-1", payload: {} }, scope),
  "known task events should refresh their Delivery scope",
);
assert(
  isDeliveryRefreshEvent({ type: "autoresearch.cycle.completed", payload: { task_ids: ["TASK-2"] } }, scope),
  "task arrays from a scoped workflow should refresh Delivery",
);
assert(
  isDeliveryRefreshEvent({ type: "replan.applied", payload: { task_map_ref: "artifact://task-map.json" } }, scope),
  "task-map-bound replans should refresh Delivery",
);
assert(
  isDeliveryRefreshEvent({ type: "plan.insight.ready", task_id: "TASK-1", payload: {} }, scope),
  "projection events must be selected by typed scope rather than a prefix guess",
);
assert(
  isDeliveryRefreshEvent({ type: "artifact.manifest.published", payload: { task_map_ref: "artifact://task-map.json" } }, scope),
  "task-map-bound artifact events should refresh Delivery",
);
assert(
  isDeliveryRefreshEvent({ type: "fanout.completed", payload: { fanout_id: "FANOUT-1" } }, scope),
  "known fanout events should refresh Delivery",
);
assert(
  isDeliveryRefreshEvent({ type: "run.goal.completed", payload: { run_id: "RUN-1" } }, scope),
  "known run events should refresh Delivery",
);
assert(
  isDeliveryRefreshEvent({ type: "dev.build.done", payload: { dispatch_id: "DISPATCH-1" } }, scope),
  "dispatch-bound lifecycle events should refresh Delivery",
);
assert(
  isDeliveryRefreshEvent({ type: "goal.closure.synthesized", payload: { goal_id: "GOAL-1" } }, scope),
  "goal-bound closure events should refresh Delivery",
);
assert(
  isDeliveryRefreshEvent({ type: "workflow.completed", payload: { workflow_run_id: "WORKFLOW-RUN-1" } }, scope),
  "known workflow-run events should refresh Delivery",
);
assert(
  isDeliveryRefreshEvent({ type: "feature.changed", payload: { feature_ids: ["FEATURE-1"] } }, scope),
  "feature arrays should refresh their Delivery scope",
);
assert(
  isDeliveryRefreshEvent({ type: "task_map.ready", task_id: "FEATURE-1", payload: {} }, scope),
  "legacy task-map events may bind the feature through event.task_id",
);
assert(
  isDeliveryRefreshEvent({ type: "replan.applied", payload: { new_task_map_ref: "artifact://task-map.json" } }, scope),
  "task-map variants should refresh Delivery",
);
assert(
  !isDeliveryRefreshEvent({ type: "replan.applied", payload: { fanout_id: "artifact://task-map.json" } }, scope),
  "same text in a different identity namespace must not cross-match",
);
assert(
  !isDeliveryRefreshEvent({ type: "task.done", task_id: "TASK-OTHER", payload: {} }, scope),
  "another feature's task must not refresh the selected Delivery scope",
);
assert(
  !isDeliveryRefreshEvent({ type: "worker.progress", payload: {} }, scope),
  "unbound projection events must fail closed",
);
const switchedScope = deliveryRefreshScope({
  feature_id: "FEATURE-1",
  trace_id: "TRACE-OLD",
  execution_graph: { nodes: [{ task_id: "TASK-OLD" }], waves: [] },
} as never, "FEATURE-2");
assert(
  !isDeliveryRefreshEvent({ type: "task.done", task_id: "TASK-OLD", payload: {} }, switchedScope),
  "a newly selected feature must not inherit the previous trace's identities",
);
assert(
  isDeliveryRefreshEvent({ type: "feature.updated", payload: { feature_id: "FEATURE-2" } }, switchedScope),
  "feature-only scope remains live while its initial trace is loading",
);

const v2Scope = deliveryRefreshScope({
  schema_version: "delivery-trace.v2",
  view: "runs",
  feature_id: "FEATURE-V2",
  trace_id: "",
  refresh_scope: {
    task_ids: ["TASK-FROM-REF", "TASK-FROM-LIFECYCLE", "TASK-FROM-STATUS"],
    task_ids_total: 3,
    task_ids_included: 3,
    task_ids_omitted: 0,
    task_ids_truncated: false,
  },
  canonical_trace_refs: [{
    trace_id: "TRACE-V2",
    membership: "trace-v2-source-event",
    task_ids: ["TASK-FROM-REF"],
  }],
  task_lifecycle: {
    schema_version: "task-lifecycle.v2",
    task_statuses: { "TASK-FROM-STATUS": "running" },
    tasks: {
      "TASK-FROM-LIFECYCLE": { state_history: [], tries: [] },
    },
  },
} as never, "FEATURE-V2");
assert(
  isDeliveryRefreshEvent({ type: "task.updated", task_id: "TASK-FROM-REF", payload: {} }, v2Scope),
  "v2 canonical trace task membership must refresh Runs",
);
assert(
  isDeliveryRefreshEvent({ type: "task.done", task_id: "TASK-FROM-LIFECYCLE", payload: {} }, v2Scope),
  "v2 task lifecycle keys must refresh Runs",
);
assert(
  isDeliveryRefreshEvent({ type: "task.updated", task_id: "TASK-FROM-STATUS", payload: {} }, v2Scope),
  "v2 bounded task status keys must refresh Runs",
);
assert(
  !isDeliveryRefreshEvent({ type: "task.done", task_id: "TASK-UNRELATED", payload: {} }, v2Scope),
  "v2 task scope must remain feature-bound",
);

const overviewScope = deliveryRefreshScope({
  schema_version: "delivery-trace.v2",
  view: "overview",
  feature_id: "FEATURE-OVERVIEW",
  refresh_scope: {
    task_ids: ["TASK-OVERVIEW"],
    task_ids_total: 1,
    task_ids_included: 1,
    task_ids_omitted: 0,
    task_ids_truncated: false,
  },
} as never, "FEATURE-OVERVIEW");
assert(
  isDeliveryRefreshEvent({ type: "task.updated", task_id: "TASK-OVERVIEW", payload: {} }, overviewScope),
  "summary-only Overview must refresh from canonical task membership",
);
assert(
  deliveryReconcileInterval("live", overviewScope) === null,
  "complete Overview membership keeps healthy SSE at zero polling",
);

const boundedScope = deliveryRefreshScope({
  schema_version: "delivery-trace.v2",
  view: "graph",
  feature_id: "FEATURE-BOUNDED",
  refresh_scope: {
    task_ids: ["TASK-VISIBLE"],
    task_ids_total: 3,
    task_ids_included: 1,
    task_ids_omitted: 2,
    task_ids_truncated: true,
  },
} as never, "FEATURE-BOUNDED");
assert(
  deliveryReconcileInterval("live", boundedScope) === DELIVERY_BOUNDED_RECONCILE_INTERVAL_MS,
  "truncated membership enables only the bounded healthy reconcile",
);
assert(
  deliveryReconcileInterval("degraded", boundedScope) === DELIVERY_RECONCILE_INTERVAL_MS,
  "degraded transport keeps its existing watchdog interval",
);
assert(
  shouldReconcileDelivery("live", "visible", boundedScope),
  "visible truncated projections reconcile even with healthy SSE",
);
assert(
  !shouldReconcileDelivery("live", "hidden", boundedScope),
  "hidden truncated projections must not reconcile",
);

const oversizedTaskId = `TASK-${"x".repeat(100)}`;
const opaqueScope = deliveryRefreshScope({
  schema_version: "delivery-trace.v2",
  view: "overview",
  feature_id: "FEATURE-OPAQUE",
  refresh_scope: {
    task_ids: [oversizedTaskId],
    task_ids_total: 1,
    task_ids_included: 1,
    task_ids_omitted: 0,
    task_ids_truncated: false,
  },
} as never, "FEATURE-OPAQUE");
assert(opaqueScope.taskIdsTruncated, "oversized wire identities must fail over to bounded reconcile");
assert(
  !isDeliveryRefreshEvent({ type: "task.updated", task_id: oversizedTaskId, payload: {} }, opaqueScope),
  "oversized identities must not pretend to be exact event membership",
);
