import type {
  DeliveryTaskTry,
  DeliveryTrace,
  DeliveryTraceNode,
  GoalCoverageNode,
} from "../../api/types";
import {
  claimNodes,
  resultNodesByTask,
  taskNodesById,
  type GoalCoverageClaimNode,
} from "../goal-coverage/goalCoverageModel.js";

const DONE_STATUSES = new Set(["done", "completed", "passed", "shipped", "cancelled"]);
const RUNNING_STATUSES = new Set(["in_progress", "running", "review", "test", "judge", "dispatched"]);

export interface DeliveryWorkTask {
  taskId: string;
  taskIdOpaque: boolean;
  title: string;
  status: string;
  owner: string;
  blockedBy: string[];
  claimIds: string[];
  primaryClaimId: string;
  alsoClaimIds: string[];
  tries: DeliveryTaskTry[];
  results: GoalCoverageNode[];
  evidenceRefs: string[];
  executionNode: DeliveryTraceNode | null;
  lifecycleDetailsOmitted: boolean;
  triesOmitted: number;
  triesTruncated: boolean;
  relationsTruncated: boolean;
}

export interface DeliveryWorkClaim {
  claim: GoalCoverageClaimNode;
  tasks: DeliveryWorkTask[];
  linkedTasks: DeliveryWorkTask[];
  taskTotal: number;
  taskDetailsOmitted: number;
}

export interface DeliveryWorkModel {
  goal: GoalCoverageNode | null;
  claims: DeliveryWorkClaim[];
  unclaimedTasks: DeliveryWorkTask[];
  tasks: DeliveryWorkTask[];
  summary: {
    total: number;
    done: number;
    running: number;
    blocked: number;
    verified: number;
  };
  bounded: {
    lifecyclePartial: boolean;
    relationsPartial: boolean;
    tasksOmitted: number;
    verificationPartial: boolean;
  };
}

export function buildDeliveryWorkModel(trace: DeliveryTrace): DeliveryWorkModel {
  const graph = trace.goal_coverage_graph ?? null;
  const claims = claimNodes(graph);
  const claimById = new Map(claims.map((claim) => [claim.goal_claim_id, claim]));
  const graphTasks = taskNodesById(graph);
  const resultsByTask = resultNodesByTask(graph);
  const executionByTask = new Map(
    (trace.execution_graph?.nodes ?? []).map((node) => [node.task_id, node]),
  );
  // The bounded Work execution projection is the sole visibility authority.
  // Goal task nodes only enrich selected rows; unioning them would bypass the
  // backend's global node cap and relabel mapped-but-omitted work as unmapped.
  const orderedTaskIds = [...executionByTask.keys()];

  const tasks = orderedTaskIds.map((taskId): DeliveryWorkTask => {
    const task = graphTasks.get(taskId);
    const executionNode = executionByTask.get(taskId) ?? null;
    const lifecycleEntry = trace.task_lifecycle?.tasks?.[taskId];
    const inferredClaimIds = claims
      .filter((claim) => claim.task_ids?.includes(taskId))
      .map((claim) => claim.goal_claim_id);
    const claimIds = unique([
      ...(task?.goal_claim_ids ?? []),
      ...(executionNode?.goal_claim_ids ?? []),
      ...inferredClaimIds,
    ]).filter((claimId) => claimById.has(claimId));
    const primaryClaimId = claimIds[0] ?? "";
    const results = resultsByTask.get(taskId) ?? [];
    return {
      taskId,
      taskIdOpaque: Boolean(executionNode?.task_id_opaque || task?.task_id_opaque),
      title: task?.title || executionNode?.title || taskId,
      status: trace.task_lifecycle?.task_statuses?.[taskId]
        || executionNode?.actual.status
        || task?.status
        || "pending",
      owner: task?.owner
        || executionNode?.actual.assigned_to
        || executionNode?.planned.owner_instance
        || executionNode?.planned.owner_role
        || "unassigned",
      blockedBy: unique(executionNode?.planned.blocked_by ?? []),
      claimIds,
      primaryClaimId,
      alsoClaimIds: claimIds.slice(1),
      tries: lifecycleEntry?.tries ?? [],
      results,
      evidenceRefs: unique([
        ...(executionNode?.actual.evidence_events ?? []),
        ...results.flatMap((result) => result.evidence_refs ?? []),
      ]),
      executionNode,
      lifecycleDetailsOmitted: !lifecycleEntry && Boolean(trace.task_lifecycle?.tasks_truncated),
      triesOmitted: Math.max(
        0,
        lifecycleEntry?.tries_omitted
          ?? (lifecycleEntry?.tries_total ?? lifecycleEntry?.tries?.length ?? 0)
            - (lifecycleEntry?.tries?.length ?? 0),
      ),
      triesTruncated: Boolean(lifecycleEntry?.tries_truncated),
      relationsTruncated: Boolean(
        executionNode?.goal_claim_ids_truncated
        || executionNode?.planned.blocked_by_truncated
        || executionNode?.actual.evidence_events_truncated
        || task?.goal_claim_ids_truncated
        || task?.evidence_refs_truncated
        || results.some((result) => result.evidence_refs_truncated)
      ),
    };
  });

  const workClaims = claims.map((claim): DeliveryWorkClaim => {
    const primaryTasks = tasks.filter((task) => task.primaryClaimId === claim.goal_claim_id);
    const linkedTasks = tasks.filter((task) => task.alsoClaimIds.includes(claim.goal_claim_id));
    const visibleTaskCount = primaryTasks.length + linkedTasks.length;
    const taskTotal = Math.max(
      visibleTaskCount,
      claim.task_details?.total
        ?? claim.task_ids_total
        ?? claim.task_ids?.length
        ?? 0,
    );
    return {
      claim,
      tasks: primaryTasks,
      linkedTasks,
      taskTotal,
      taskDetailsOmitted: Math.max(0, taskTotal - visibleTaskCount),
    };
  });
  const unclaimedTasks = tasks.filter((task) => !task.primaryClaimId);
  const tasksOmitted = Math.max(
    0,
    trace.execution_graph?.nodes_omitted
      ?? (trace.execution_graph?.nodes_total ?? tasks.length) - tasks.length,
  );
  const visibleDone = tasks.filter((task) => DONE_STATUSES.has(task.status)).length;
  const visibleRunning = tasks.filter((task) => RUNNING_STATUSES.has(task.status)).length;
  const visibleBlocked = tasks.filter((task) => ["blocked", "failed"].includes(task.status)).length;

  return {
    goal: graph?.nodes.find((node) => node.kind === "goal") ?? null,
    claims: workClaims,
    unclaimedTasks,
    tasks,
    summary: {
      total: trace.execution_graph?.task_count ?? tasks.length,
      done: trace.execution_graph?.done_count ?? visibleDone,
      running: trace.execution_graph?.in_progress_count ?? visibleRunning,
      blocked: trace.execution_graph?.blocked_count ?? visibleBlocked,
      verified: tasks.filter((task) => task.results.some((result) => (
        result.current !== false && result.status === "passed"
      ))).length,
    },
    bounded: {
      lifecyclePartial: Boolean(
        trace.task_lifecycle?.tasks_truncated
        || trace.task_lifecycle?.task_statuses_truncated
        || trace.task_lifecycle?.tries_truncated
        || trace.task_lifecycle?.gate_results_truncated
      ),
      relationsPartial: tasks.some((task) => task.relationsTruncated)
        || Boolean(graph?.nodes_truncated || graph?.edges_truncated),
      tasksOmitted,
      verificationPartial: tasksOmitted > 0 || Boolean(graph?.nodes_truncated),
    },
  };
}

export function latestTry(task: DeliveryWorkTask): DeliveryTaskTry | null {
  return task.tries[task.tries.length - 1] ?? null;
}

export function resultStatus(task: DeliveryWorkTask): string {
  return task.results.find((result) => result.current !== false)?.status
    ?? task.results[0]?.status
    ?? "unverified";
}

function unique(values: string[]): string[] {
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))];
}
