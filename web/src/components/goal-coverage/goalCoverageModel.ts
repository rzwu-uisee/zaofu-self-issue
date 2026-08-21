import type { GoalCoverageGraph, GoalCoverageNode } from "../../api/types";

export type GoalCoverageClaimNode = GoalCoverageNode & {
  kind: "goal_claim";
  goal_claim_id: string;
};

export function claimNodes(graph: GoalCoverageGraph | null): GoalCoverageClaimNode[] {
  if (!graph) return [];
  return graph.nodes.filter((node): node is GoalCoverageClaimNode => (
    node.kind === "goal_claim" && Boolean(node.goal_claim_id)
  ));
}

export function taskNodesById(graph: GoalCoverageGraph | null): Map<string, GoalCoverageNode> {
  const tasks = new Map<string, GoalCoverageNode>();
  for (const node of graph?.nodes ?? []) {
    if (node.kind === "task" && node.task_id) tasks.set(node.task_id, node);
  }
  return tasks;
}

export function missingTaskDetailCount(
  graph: GoalCoverageGraph | null,
  claim: GoalCoverageClaimNode,
  tasks: ReadonlyMap<string, GoalCoverageNode>,
): number {
  const declaredMissing = Math.max(0, Number(claim.task_details?.missing_count ?? 0));
  const declaredTotal = Math.max(0, Number(claim.task_details?.total ?? 0));
  const unresolvedTotal = Math.max(0, declaredTotal - (claim.task_ids ?? []).filter((taskId) => tasks.has(taskId)).length);
  if (!graph?.nodes_truncated) return Math.max(declaredMissing, unresolvedTotal);
  const missingRetainedRefs = (claim.task_ids ?? []).filter((taskId) => !tasks.has(taskId)).length;
  return Math.max(declaredMissing, unresolvedTotal, missingRetainedRefs);
}

export function hasUnavailableTaskDetails(
  graph: GoalCoverageGraph | null,
  claim: GoalCoverageClaimNode,
  tasks: ReadonlyMap<string, GoalCoverageNode>,
): boolean {
  return missingTaskDetailCount(graph, claim, tasks) > 0;
}

export function resultNodesByTask(graph: GoalCoverageGraph | null): Map<string, GoalCoverageNode[]> {
  const results = new Map<string, GoalCoverageNode[]>();
  for (const node of graph?.nodes ?? []) {
    if (node.kind !== "verification_result" || !node.task_id) continue;
    const rows = results.get(node.task_id) ?? [];
    rows.push(node);
    results.set(node.task_id, rows);
  }
  return results;
}

export function filterClaims(
  graph: GoalCoverageGraph | null,
  query: string,
): GoalCoverageClaimNode[] {
  const claims = claimNodes(graph);
  const normalized = query.trim().toLowerCase();
  if (!normalized) return claims;
  const tasks = taskNodesById(graph);
  return claims.filter((claim) => {
    const taskText = (claim.task_ids ?? []).map((taskId) => {
      const task = tasks.get(taskId);
      return `${taskId} ${task?.title ?? ""}`;
    }).join(" ");
    return [
      claim.goal_claim_id,
      claim.title,
      claim.source_ref ?? "",
      taskText,
    ].join(" ").toLowerCase().includes(normalized);
  });
}

export function preferredClaimId(
  graph: GoalCoverageGraph | null,
  currentId: string,
): string {
  const claims = claimNodes(graph);
  if (claims.some((claim) => claim.goal_claim_id === currentId)) return currentId;
  return claims.find((claim) => claim.plan_coverage === "uncovered")?.goal_claim_id
    ?? claims.find((claim) => ["open", "blocked"].includes(claim.closure ?? ""))?.goal_claim_id
    ?? claims[0]?.goal_claim_id
    ?? "";
}

export function statusTone(status: string | undefined): "ok" | "warn" | "err" | "info" | "muted" {
  if (["closed", "done", "completed", "passed", "covered", "waived", "shipped"].includes(status ?? "")) return "ok";
  if (["blocked", "failed", "rejected", "uncovered"].includes(status ?? "")) return "err";
  if (["running", "in_progress", "active", "dispatched", "review", "test", "judge", "in_flight"].includes(status ?? "")) return "info";
  if (["open", "pending", "waiting", "stale", "unverified", "unknown", "not_started"].includes(status ?? "")) return "warn";
  return "muted";
}
