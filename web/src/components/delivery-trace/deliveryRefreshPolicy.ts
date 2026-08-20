import type { DeliveryTrace, RecentEvent } from "../../api/types";
import { isRefreshNoiseEventType } from "../../app/refreshEventPolicy.js";
import type { LiveState } from "../../app/sharedTypes";

export const DELIVERY_LIVE_COALESCE_MS = 250;
export const DELIVERY_RECONCILE_INTERVAL_MS = 15_000;
export const DELIVERY_BOUNDED_RECONCILE_INTERVAL_MS = 60_000;

export function deliveryReconcileInterval(
  liveState: LiveState,
  scope: Pick<DeliveryRefreshScope, "taskIdsTruncated">,
): number | null {
  if (liveState !== "live") return DELIVERY_RECONCILE_INTERVAL_MS;
  return scope.taskIdsTruncated ? DELIVERY_BOUNDED_RECONCILE_INTERVAL_MS : null;
}

export function shouldReconcileDelivery(
  liveState: LiveState,
  visibilityState: DocumentVisibilityState,
  scope: Pick<DeliveryRefreshScope, "taskIdsTruncated"> = { taskIdsTruncated: false },
): boolean {
  return visibilityState === "visible" && deliveryReconcileInterval(liveState, scope) !== null;
}

export function isCancelledDeliveryRequest(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

export interface DeliveryRefreshScope {
  cycleIds: ReadonlySet<string>;
  fanoutIds: ReadonlySet<string>;
  featureId: string;
  goalIds: ReadonlySet<string>;
  graphIds: ReadonlySet<string>;
  runIds: ReadonlySet<string>;
  taskIds: ReadonlySet<string>;
  taskIdsTruncated: boolean;
  taskMapRefs: ReadonlySet<string>;
  traceIds: ReadonlySet<string>;
  workflowRunIds: ReadonlySet<string>;
}

export function deliveryRefreshScope(
  trace: DeliveryTrace | null,
  featureId: string,
): DeliveryRefreshScope {
  const taskIds = new Set<string>();
  const traceIds = new Set<string>();
  const runIds = new Set<string>();
  const fanoutIds = new Set<string>();
  const workflowRunIds = new Set<string>();
  const taskMapRefs = new Set<string>();
  const cycleIds = new Set<string>();
  const graphIds = new Set<string>();
  const goalIds = new Set<string>([featureId]);
  const add = (target: Set<string>, value: unknown) => {
    const normalized = typeof value === "string" ? value.trim() : "";
    if (normalized) target.add(normalized);
  };
  const addMany = (target: Set<string>, values: unknown) => {
    if (!Array.isArray(values)) return;
    for (const value of values) add(target, value);
  };

  const result = {
    cycleIds,
    fanoutIds,
    featureId,
    goalIds,
    graphIds,
    runIds,
    taskIds,
    taskIdsTruncated: false,
    taskMapRefs,
    traceIds,
    workflowRunIds,
  };
  if (!trace || (trace.feature_id && trace.feature_id !== featureId)) return result;
  const boundedTaskScope = trace.refresh_scope;
  if (trace.schema_version === "delivery-trace.v2") {
    result.taskIdsTruncated = !boundedTaskScope
      || boundedTaskScope.task_ids_truncated
      || boundedTaskScope.task_ids_omitted > 0
      || boundedTaskScope.task_ids_included < boundedTaskScope.task_ids_total;
  }
  for (const value of boundedTaskScope?.task_ids ?? []) {
    const taskId = typeof value === "string" ? value.trim() : "";
    if (taskId.length > 96) {
      result.taskIdsTruncated = true;
    } else {
      add(taskIds, taskId);
    }
  }
  add(traceIds, trace.trace_id);
  for (const ref of trace.canonical_trace_refs ?? []) {
    add(traceIds, typeof ref === "string" ? ref : ref.trace_id);
    if (typeof ref !== "string") {
      add(taskIds, ref.task_id);
      addMany(taskIds, ref.task_ids);
    }
  }
  for (const node of trace.execution_graph?.nodes ?? []) add(taskIds, node.task_id);
  for (const wave of trace.execution_graph?.waves ?? []) addMany(taskIds, wave.task_ids);
  for (const stage of trace.run_chain?.stages ?? []) addMany(taskIds, stage.task_ids);
  for (const phase of trace.phases ?? []) {
    addMany(taskIds, phase.task_ids);
    for (const run of phase.agent_runs ?? []) {
      add(taskIds, run.task_id);
      add(fanoutIds, run.fanout_id);
    }
  }
  for (const cycle of [...(trace.cycles ?? []), ...(trace.autoresearch_cycles ?? [])]) {
    add(cycleIds, cycle.cycle_id);
    addMany(taskIds, cycle.task_ids);
  }
  for (const entry of trace.task_map_history ?? []) {
    add(taskMapRefs, entry.artifact_id);
    add(taskMapRefs, entry.ref);
  }
  const workflow = trace.workflow_trace;
  if (workflow) {
    add(taskMapRefs, workflow.task_map_ref);
    for (const stage of workflow.stage_runs ?? []) {
      add(fanoutIds, stage.fanout_id);
      addMany(taskIds, stage.task_ids);
      for (const child of stage.fanout_child_runs ?? []) {
        add(runIds, child.child_id);
        add(fanoutIds, child.child_id);
        add(taskIds, child.task_id);
      }
    }
  }
  for (const stage of trace.task_flow?.stages ?? []) {
    addMany(taskIds, stage.task_ids);
    addMany(taskIds, stage.active_task_ids);
    addMany(runIds, stage.run_group_ids);
    for (const task of stage.tasks ?? []) add(taskIds, task.task_id);
  }
  for (const group of trace.run_groups ?? []) {
    add(runIds, group.group_id);
    addMany(taskIds, group.task_ids);
  }
  for (const [taskId, lifecycle] of Object.entries(trace.task_lifecycle?.tasks ?? {})) {
    add(taskIds, taskId);
    for (const attempt of lifecycle.tries ?? []) add(runIds, attempt.dispatch_id);
  }
  for (const taskId of Object.keys(trace.task_lifecycle?.task_statuses ?? {})) add(taskIds, taskId);
  for (const node of trace.closed_loop?.nodes ?? []) {
    add(taskIds, node.task_id);
    add(runIds, node.run_id);
    add(traceIds, node.trace_id);
    addMany(fanoutIds, node.fanout_ids);
  }
  const goalIdentity = trace.goal_coverage_graph?.identity;
  if (goalIdentity) {
    add(workflowRunIds, goalIdentity.workflow_run_id);
    add(taskMapRefs, goalIdentity.task_map_ref);
    add(goalIds, goalIdentity.goal_id);
  }
  for (const node of trace.goal_coverage_graph?.nodes ?? []) {
    add(taskIds, node.task_id);
    addMany(taskIds, node.task_ids);
  }
  for (const graph of trace.trace?.autoresearch_graphs ?? []) {
    add(graphIds, graph.graph_id);
  }
  return result;
}

function stringValues(payload: Record<string, unknown>, keys: readonly string[]): string[] {
  const values: string[] = [];
  for (const key of keys) {
    const value = payload[key];
    if (Array.isArray(value)) {
      for (const item of value) {
        if (typeof item === "string" && item.trim()) values.push(item.trim());
      }
    } else if (typeof value === "string" && value.trim()) {
      values.push(value.trim());
    }
  }
  return values;
}

export function isDeliveryRefreshEvent(
  event: RecentEvent,
  scope: DeliveryRefreshScope,
): boolean {
  const eventType = event.type || "";
  if (isRefreshNoiseEventType(eventType)) return false;
  const payload = event.payload ?? {};
  const featureIds = stringValues(payload, ["feature_id", "feature_ids", "pdd_id"]);
  if (featureIds.length) return featureIds.includes(scope.featureId);
  if (stringValues(payload, ["goal_id", "goal_ids"])
    .some((goalId) => scope.goalIds.has(goalId))) return true;
  const businessIds = stringValues(payload, ["flow_id", "module_id"]);
  if (businessIds.includes(scope.featureId)) return true;
  const taskIds = stringValues(payload, [
    "task_id",
    "task_ids",
    "active_task_ids",
    "completed_task_ids",
    "failed_task_ids",
  ]);
  if (event.task_id?.trim()) taskIds.push(event.task_id.trim());
  if (taskIds.includes(scope.featureId)) return true;
  if (taskIds.some((taskId) => scope.taskIds.has(taskId))) return true;

  const traceIds = stringValues(payload, ["trace_id", "trace_ids"]);
  if (event.correlation_id?.trim()) traceIds.push(event.correlation_id.trim());
  if (traceIds.some((traceId) => scope.traceIds.has(traceId))) return true;
  if (stringValues(payload, ["run_id", "run_ids", "dispatch_id", "dispatch_ids"])
    .some((runId) => scope.runIds.has(runId))) return true;
  if (stringValues(payload, ["workflow_run_id", "workflow_run_ids"])
    .some((runId) => scope.workflowRunIds.has(runId))) return true;
  if (stringValues(payload, ["fanout_id", "fanout_ids", "fanout_run_id", "fanout_run_ids"])
    .some((fanoutId) => scope.fanoutIds.has(fanoutId))) return true;
  const nestedEval = payload.eval && typeof payload.eval === "object" && !Array.isArray(payload.eval)
    ? payload.eval as Record<string, unknown>
    : {};
  const taskMapRefs = [
    ...stringValues(payload, [
      "task_map_ref",
      "old_task_map_ref",
      "new_task_map_ref",
      "candidate_task_map_ref",
      "expected_current_task_map_ref",
    ]),
    ...stringValues(nestedEval, [
      "task_map_ref",
      "old_task_map_ref",
      "new_task_map_ref",
      "candidate_task_map_ref",
      "expected_current_task_map_ref",
    ]),
  ];
  if (taskMapRefs.some((ref) => scope.taskMapRefs.has(ref))) return true;
  if (stringValues(payload, ["cycle_id", "cycle_ids"])
    .some((cycleId) => scope.cycleIds.has(cycleId))) return true;
  return stringValues(payload, ["graph_id", "graph_ids"])
    .some((graphId) => scope.graphIds.has(graphId));
}
